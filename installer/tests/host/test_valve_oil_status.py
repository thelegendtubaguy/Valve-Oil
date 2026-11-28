from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import replace
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from installer.runtime import progress, update_window, valve_oil_status as status_file
from installer.tests.helpers import FIXTURES_ROOT, REPO_ROOT, load_source, temp_path


def _load_server():
    path = Path(__file__).resolve().parents[2] / "system/vo_status.py"
    return load_source("vo_status_under_test", path)


class _Process:
    """An update-now run that stays running until released."""

    def __init__(self) -> None:
        self.pid = os.getpid()
        self.released = threading.Event()

    def wait(self) -> int:
        self.released.wait(10)
        return 0


class StatusServiceTests(unittest.TestCase):
    """The status service feeds both UIs and flips automatic updates, so only marked same-origin JSON posts may change
    anything; the switch touches only the enrollment marker, and update now never runs twice at once."""

    @classmethod
    def setUpClass(cls) -> None:
        server = _load_server()
        root = temp_path("vo-status-")
        cls.paths = type("Paths", (), {"printer_data_root": root / "printer_data"})()
        cls.paths.printer_data_root.mkdir()
        server.STATUS_FILE = status_file.status_path(cls.paths)
        server.PROGRESS_FILE = status_file.progress_path(cls.paths)
        server.ENROLLMENT_FILE = cls.paths.printer_data_root / ".vo_auto_update_enrolled"
        server.UPDATE_SETTINGS_FILE = update_window.settings_path(cls.paths)
        server.EXPERIMENTAL_FILE = cls.paths.printer_data_root / ".vo_experimental_updates"
        server.AUTO_UPDATE_STATE_FILE = cls.paths.printer_data_root / "config/vo_auto_update_state.json"
        server.MACRO_GLOBALS = root / "globals.cfg"
        server.FIRMWARE_MANIFEST = root / "firmware_manifest.json"
        server.AUTO_UPDATE_TIMER = root / "missing.timer"
        server.ADMISSION_FILE = cls.paths.printer_data_root / ".vo_board_admission.json"
        server.STATE_DIR = root / "cache"
        server.JOB_FILE, server.JOB_LOG, server.JOB_RC = (server.STATE_DIR / name for name in ("job.json", "job.log", "job.rc"))
        server.BUNDLE = root / "vo-macros"
        server.RELEASE_METADATA = server.BUNDLE / "RELEASE_METADATA.yaml"
        server.LOGS = cls.paths.printer_data_root / "logs"
        server.CACHE = root / "valve-oil-cache"
        server.BOX_CFG = root / "box.cfg"
        server.FIRMWARE_UPDATE_RESULT = cls.paths.printer_data_root / ".vo_firmware_update_result.json"
        (server.BUNDLE / "installer/runtime").mkdir(parents=True)
        (server.BUNDLE / "installer/runtime/bootstrap.py").write_text("")
        server.INSTALLER_LOCK = cls.paths.printer_data_root / ".vo_installer.lock"
        server.RECOVERY_FILES = {cls.paths.printer_data_root / path.name: step for path, step in server.RECOVERY_FILES.items()}
        server.FIRMWARE_UPDATE_RECORD = cls.paths.printer_data_root / ".vo_firmware_update_pending.json"
        server.read_timer_property = lambda name: None
        cls.live: tuple[str, ...] | None = ()
        server.live_printer = lambda: cls.live
        server.log = lambda message: None
        cls.processes: list[_Process] = []
        cls.jobs: list[str] = []
        cls.commands: list[tuple[str, ...]] = []

        def start(output, job_id, command):
            cls.jobs.append(job_id)
            cls.commands.append(command)
            cls.processes.append(_Process())
            return cls.processes[-1]

        server.start_job = start
        # Valve Oil's Klipper: the launcher's check, the Klipper Moonraker knows of, and the restart it's asked for.
        server.KLIPPER_CHOICE_FILE = cls.paths.printer_data_root / ".vo_klipper.json"
        server.KLIPPER_STATUS_FILE = cls.paths.printer_data_root / ".vo_klipper_status.json"
        server.KLIPPER_READY_FILE = cls.paths.printer_data_root / ".vo_klipper_ready.json"
        server.KLIPPER_LAUNCHER = root / "vo_klipper_launch.py"
        server.KLIPPER_TREE = Path("/home/qidi/vo-klipper")
        server.KLIPPER_RESTART_DELAY = 0.0
        cls.klipper_check: dict | None = None
        cls.klipper_checks = 0

        def check():
            cls.klipper_checks += 1
            return cls.klipper_check

        server.run_klipper_check = check
        cls.process = {"moonraker": True, "state": "ready", "path": "/home/qidi/klipper"}
        server.klipper_process = lambda: dict(cls.process)
        cls.restarts = threading.Semaphore(0)
        cls.restart_fails = False

        cls.restarted = threading.Event()

        def restart():
            # Until Klipper has restarted, the Klipper Moonraker reports is the one being replaced.
            cls.restarts.release()
            cls.restarted.wait(5)
            if cls.restart_fails:
                raise OSError("Moonraker went away")

        server.restart_klipper = restart
        server.firmware_restart_if_board_shut_down = lambda: None
        server.MACRO_GLOBALS.write_text('[gcode_macro _vo_globals]\nvariable_package_version: "26.11.11.3"\n')
        server.FIRMWARE_MANIFEST.write_text(json.dumps({"SOC": {"version": "01.01.06.02"}}))
        # The Diagnostics check runs the bundle's checker, which waits for the test to let it go.
        checker = server.BUNDLE / "installer/runtime/diagnostics.py"
        shutil.copy(REPO_ROOT / "installer/runtime/diagnostics.py", server.BUNDLE / "installer/runtime/checker.py")
        cls.checker_go = root / "checker-go"
        checker.write_text(
            "import os, runpy, sys, time\n"
            f"while not os.path.exists({str(cls.checker_go)!r}):\n    time.sleep(0.01)\n"
            "sys.argv[0] = 'checker.py'\n"
            f"runpy.run_path({str(server.BUNDLE / 'installer/runtime/checker.py')!r}, run_name='__main__')\n")
        server.DIAGNOSTICS = (sys.executable, "-I", str(checker))
        server.DIAGNOSTICS_FILE = server.STATE_DIR / "diagnostics.json"
        server.DIAGNOSTICS_REPORT = server.STATE_DIR / "diagnostics.txt"
        server.Handler.jobs = server.Jobs()
        server.Handler.diagnostics = server.Diagnostics()
        cls.server = server
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=cls.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        for process in cls.processes:
            process.released.set()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    GOOD = {"X-Valve-Oil": "status", "Content-Type": "application/json", "Host": "printer.local", "Origin": "http://printer.local"}

    def request(self, path: str, *, method: str = "POST", headers: dict[str, str] | None = None, body: bytes = b"{}") -> tuple[int, dict]:
        request = urllib.request.Request(self.base + path, data=body if method == "POST" else None, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)

    def test_only_exclusive_locks_block_actions_with_the_correct_reason(self) -> None:
        # Linux's /proc/locks, as a print start in Klipper (READ) and an installer run (WRITE) leave it.
        lock = self.server.INSTALLER_LOCK
        lock.touch()
        info = lock.stat()
        device = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
        table = self.paths.printer_data_root / "proc-locks"
        with mock.patch.object(self.server, "PROC_LOCKS", table), \
                mock.patch.object(self.server, "_run_active", return_value=False), \
                mock.patch.object(self.server.Handler.jobs, "busy", return_value=False):
            table.write_text(f"1: FLOCK  ADVISORY  READ  4242 {device} 0 EOF\n", encoding="utf-8")
            body = self.request("/api/status", method="GET")[1]
            self.assertFalse(body["busy"])
            self.assertTrue(body["update_now"]["allowed"])
            self.assertTrue(body["uninstall"]["allowed"])
            table.write_text(f"1: FLOCK  ADVISORY  WRITE 4242 {device} 0 EOF\n", encoding="utf-8")
            body = self.request("/api/status", method="GET")[1]
            self.assertTrue(body["busy"])
            for action in ("update_now", "uninstall"):
                self.assertFalse(body[action]["allowed"])
                self.assertIn("installer_busy", [item["code"] for item in body[action]["blocked_by"]])

    def test_only_marked_same_origin_json_posts_switch_enrollment(self) -> None:
        marker = self.server.ENROLLMENT_FILE
        before = sorted(path.name for path in self.paths.printer_data_root.iterdir())
        rejected = (
            {key: value for key, value in self.GOOD.items() if key != "X-Valve-Oil"},
            {**self.GOOD, "X-Valve-Oil": "addons"},
            {**self.GOOD, "Content-Type": "text/plain"},
            {**self.GOOD, "Origin": "http://evil.example"},
            {**self.GOOD, "Origin": "null"},
        )
        for headers in rejected:
            with self.subTest(headers=headers):
                self.assertIn(self.request("/api/auto-update", headers=headers, body=b'{"enabled": true}')[0], {400, 403})
        for body in (b'{"enabled": "yes"}', b'{"enabled": 1}', b'{"enabled": true, "x": 1}', b"[]", b"not json", b"{" + b" " * 2000 + b"}"):
            with self.subTest(body=body[:20]):
                self.assertEqual(self.request("/api/auto-update", headers=self.GOOD, body=body)[0], 400)
        self.assertFalse(marker.exists())
        self.assertEqual(self.request("/api/auto-update", method="GET")[0], 405)
        self.assertEqual(self.request("/api/status", headers=self.GOOD)[0], 405)
        for path in ("/api/status/x", "/api//status", "/status", "/api/../api/status", "/api/reboot"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, method="GET")[0], 404)

        # The switch writes or removes only the marker the update check reads.
        status, body = self.request("/api/auto-update", headers=self.GOOD, body=b'{"enabled": true}')
        self.assertEqual((status, body["auto_update"]["enabled"]), (200, True))
        self.assertEqual(marker.read_text(), "1\n")
        self.assertEqual(sorted(path.name for path in self.paths.printer_data_root.iterdir()), sorted([*before, marker.name]))
        status, body = self.request("/api/auto-update", headers=self.GOOD, body=b'{"enabled": false}')
        self.assertEqual((status, body["auto_update"]["enabled"]), (200, False))
        self.assertEqual(sorted(path.name for path in self.paths.printer_data_root.iterdir()), before)

    def test_the_update_window_is_saved_for_the_check_and_judged_live(self) -> None:
        marker, settings = self.server.ENROLLMENT_FILE, self.server.UPDATE_SETTINGS_FILE
        status_file.record_check(
            self.paths, enrolled=True, printer=status_file.PrinterInfo(), installed_release=None,
            pending=(status_file.PendingItem(kind=status_file.RELEASE, version="99.1.1.1", blocked_by=(
                status_file.Blocker.of("outside_window", "it's outside the update window, 01:00–02:00 UTC"),)),))
        hour = datetime.now(ZoneInfo("Asia/Tokyo")).hour
        try:
            for body in (b'{"window": {"start": 2, "end": 2}}', b'{"window": {"start": 2, "end": 24}}',
                         b'{"window": {"start": true, "end": 3}}', b'{"window": [2, 5]}',
                         b'{"timezone": "Mars/Olympus_Mons"}', b'{"timezone": "../../etc/passwd"}', b"{}"):
                with self.subTest(body=body):
                    self.assertEqual(self.request("/api/auto-update", headers=self.GOOD, body=body)[0], 400)
            self.assertFalse(settings.exists())

            # A window that wraps past midnight, saved with the browser's timezone and automatic updates on at once.
            outside = {"start": (hour + 1) % 24, "end": hour}
            status, body = self.request("/api/auto-update", headers=self.GOOD, body=json.dumps(
                {"enabled": True, "window": outside, "timezone": "Asia/Tokyo"}).encode())
            self.assertEqual(status, 200)
            self.assertEqual(body["auto_update"], {"enabled": True, "experimental": False, "installed_build": None,
                                               "available_build": None, "scheduled": False, "window": outside,
                                                   "timezone": "Asia/Tokyo", "timezone_source": "browser",
                                                   "in_window": False})
            saved = update_window.load_settings(self.paths)
            self.assertEqual((saved.window, saved.timezone, saved.in_window()),
                             (update_window.Window(**outside), "Asia/Tokyo", False))
            reasons = body["pending"][0]["blocked_by"]
            self.assertEqual([item["code"] for item in reasons], ["outside_window"])
            self.assertIn("Asia/Tokyo", reasons[0]["message"])

            # The window as it is now, not as the check found it; the timezone stays when only the window changes.
            body = self.request("/api/auto-update", headers=self.GOOD,
                                body=json.dumps({"window": {"start": hour, "end": (hour + 1) % 24}}).encode())[1]
            self.assertEqual((body["auto_update"]["in_window"], body["auto_update"]["timezone"]), (True, "Asia/Tokyo"))
            self.assertEqual(body["pending"][0]["blocked_by"], [])
            body = self.request("/api/auto-update", headers=self.GOOD, body=b'{"window": null}')[1]
            self.assertEqual((body["auto_update"]["window"], body["auto_update"]["in_window"]), (None, True))
        finally:
            marker.unlink(missing_ok=True)
            settings.unlink(missing_ok=True)

    def test_experimental_updates_are_printer_owned_and_preserve_checksum_state(self) -> None:
        state = self.server.AUTO_UPDATE_STATE_FILE
        state.parent.mkdir(exist_ok=True)
        status_file.record_check(self.paths, enrolled=False, printer=status_file.PrinterInfo(name="QIDI Max 4"),
                                 installed_release="26.11.11.3", pending=())
        recorded = {"installed_version": "26.11.11.3", "latest_checksum": "a" * 64, "latest_channel": "dev",
                    "installed_checksum": "b" * 64, "installed_channel": "dev",
                    "available_checksum": "a" * 64, "available_channel": "dev"}
        state.write_text(json.dumps(recorded), encoding="utf-8")
        try:
            self.assertFalse(self.request("/api/status", method="GET")[1]["auto_update"]["experimental"])
            status, body = self.request("/api/auto-update", headers=self.GOOD, body=b'{"experimental": true}')
            self.assertEqual((status, body["auto_update"]["experimental"]), (200, True))
            saved = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(saved, recorded)
            self.assertEqual(body["auto_update"]["installed_build"], "b" * 64)
            self.assertEqual(body["auto_update"]["available_build"], "a" * 64)
            self.assertTrue(self.server.EXPERIMENTAL_FILE.is_file())
            self.assertTrue(self.request("/api/status", method="GET")[1]["auto_update"]["experimental"])
            body = self.request("/api/auto-update", headers=self.GOOD, body=b'{"experimental": false}')[1]
            self.assertFalse(body["auto_update"]["experimental"])
            self.assertFalse(self.server.EXPERIMENTAL_FILE.exists())
            self.assertIsNone(body["auto_update"]["available_build"])
            self.assertEqual(body["auto_update"]["installed_build"], "b" * 64)
            self.assertEqual(self.request("/api/auto-update", headers=self.GOOD,
                                          body=b'{"experimental": "true"}')[0], 400)
        finally:
            state.unlink(missing_ok=True)

    def test_an_install_publishes_its_steps_for_the_browser(self) -> None:
        def reported() -> dict:
            return self.request("/api/status", method="GET")[1]["progress"]

        steps = (progress.CHECK, progress.CONFIGURATION, progress.SYSTEM_CHANGES, progress.FINISH)
        log = "valve-oil-install-20261118-091402.log"
        try:
            with status_file.progress_published(self.paths, log=log):
                progress.begin(steps, origin=progress.ORIGIN_USB, from_version="26.11.15.1", to_version="26.11.18.2")
                progress.step(progress.CONFIGURATION)
                progress.detail("restarting Moonraker")
                running = reported()
                # A step the run didn't expect counts where it starts.
                progress.step(progress.box_firmware([1]))
                self.assertEqual(reported()["steps"][2], "QIDI Box 1 firmware")
                raise RuntimeError("Box 1 didn't answer")
        except RuntimeError:
            pass
        finally:
            progress.end()
        self.assertEqual(
            {key: running[key] for key in ("state", "origin", "index", "detail", "from_version", "to_version", "log")},
            {"state": "running", "origin": "usb", "index": 2, "detail": "restarting Moonraker",
             "from_version": "26.11.15.1", "to_version": "26.11.18.2", "log": log})
        self.assertEqual(running["steps"], list(steps))
        failed = reported()
        self.assertEqual((failed["state"], failed["index"]), ("failed", 3))
        self.assertIsNotNone(failed["finished_at"])

        # An install whose process is gone was interrupted, and only a run log's name is passed on as its log.
        path = status_file.progress_path(self.paths)
        data = json.loads(path.read_text())
        path.write_text(json.dumps({**data, "state": "running", "pid": 2 ** 22 + 1, "log": "../../config/printer.cfg"}))
        stale = reported()
        self.assertEqual((stale["state"], stale["log"]), ("interrupted", None))

        # Uninstall removes it with the status file.
        status_file.remove_status(self.paths)
        self.assertIsNone(reported())

    def test_an_install_that_hands_off_never_writes_over_the_firmware_updates_own_install(self) -> None:
        # Over SSH, the install hands off to vo-firmware-update.service, whose install can start and finish before
        # the SSH install's own publisher ends; the browser then shows that install's result, not "handed off".
        steps = (progress.CHECK, progress.QIDI_FIRMWARE.format(target="01.01.07.02"), progress.FINISH)
        path = status_file.progress_path(self.paths)
        try:
            with status_file.progress_published(self.paths) as publisher:
                progress.begin(steps, origin=progress.ORIGIN_SSH, from_version="26.11.23.2", to_version="26.11.23.3")
                progress.step(steps[1])
                self.assertEqual(json.loads(path.read_text())["state"], "running")
                child = {**json.loads(path.read_text()), "state": "succeeded", "pid": os.getpid() + 1,
                         "finished_at": status_file.now()}
                path.write_text(json.dumps(child))
                progress.detail("waiting for the firmware update")
                publisher.outcome = status_file.HANDED_OFF
        finally:
            progress.end()
        self.assertEqual(json.loads(path.read_text()), child)

        # Ended first, it says it handed off, until the firmware update's install writes its own progress.
        path.unlink()
        try:
            with status_file.progress_published(self.paths) as publisher:
                progress.begin(steps, origin=progress.ORIGIN_SSH, from_version="26.11.23.2", to_version="26.11.23.3")
                publisher.outcome = status_file.HANDED_OFF
        finally:
            progress.end()
        self.assertEqual(json.loads(path.read_text())["state"], "handed_off")

    def test_an_update_now_job_from_before_a_reboot_is_not_running(self) -> None:
        # The job file outlives a power cut; after it, the job's process ID can belong to something else that is running.
        root = self.paths.printer_data_root.parent
        boot_id, uptime = root / "boot_id", root / "uptime"
        boot_id.write_text("boot-2\n")
        uptime.write_text("600.0 1200.0\n")
        alive = os.getpid()
        records = (
            {"boot_id": "boot-1", "started_at": "2026-11-23T08:00:00Z"},
            # Recorded by an earlier release of the service, without the boot it started in.
            {"started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))},
        )
        try:
            with mock.patch.object(self.server, "BOOT_ID", boot_id), mock.patch.object(self.server, "PROC_UPTIME", uptime):
                for record in records:
                    self.server.JOB_FILE.write_text(json.dumps(
                        {"id": "job-1", "state": "running", "finished_at": None, "pid": alive, "run_id": None, **record}))
                    jobs = self.server.Jobs()
                    self.assertFalse(jobs.busy())
                    self.assertEqual(jobs.current()["state"], "failed")
                    self.assertFalse(self.request("/api/status", method="GET")[1]["busy"])
                # A job started in this boot runs while its process does.
                self.server.JOB_FILE.write_text(json.dumps(
                    {"id": "job-2", "state": "running", "started_at": "2026-11-23T08:00:00Z", "finished_at": None,
                     "pid": alive, "boot_id": "boot-2", "run_id": None}))
                self.assertTrue(self.server.Handler.jobs.busy())
                self.assertTrue(self.request("/api/status", method="GET")[1]["busy"])
        finally:
            for path in (self.server.JOB_FILE, self.server.JOB_LOG):
                path.unlink(missing_ok=True)

    def test_status_reports_pending_work_and_update_now_runs_once(self) -> None:
        status_file.record_check(
            self.paths,
            enrolled=False,
            printer=status_file.PrinterInfo(id="max4", name="QIDI Max 4", firmware="01.01.06.01", target_firmware="01.01.07.01"),
            installed_release="26.11.11.2",
            pending=(
                # A release the operator has since installed by hand, or gone past, is no longer waiting.
                status_file.PendingItem(kind=status_file.RELEASE, version="26.11.11.3", current="26.11.11.2"),
                status_file.PendingItem(kind=status_file.RELEASE, version="26.11.9.1", current="26.11.8.1"),
                # The installed release, found with its installed state missing or drifted, is installed again.
                status_file.PendingItem(kind=status_file.RELEASE, version="26.11.11.3", current="26.11.11.3",
                                        reinstall=True),
                status_file.PendingItem(kind=status_file.FIRMWARE, version="01.01.07.01", current="01.01.06.02",
                                        blocked_by=(status_file.Blocker.of("heaters_on"),)),
                status_file.PendingItem(kind=status_file.BOARD, board="box2", version="valve-oil-v1"),
            ),
            boards=(status_file.BoardStatus(id="box2", state=status_file.NEEDS_IMAGE, firmware="1.0.3", build="stock"),
                    status_file.BoardStatus(id="thr", state=status_file.CURRENT),
                    status_file.BoardStatus(id="mcu", state=status_file.NEEDS_IMAGE, waiting_for_firmware_update=True,
                                            detail="will be updated after the QIDI firmware update to 01.01.07.01")),
        )
        # A run recorded as running whose process is gone was interrupted; it is a QIDI firmware update, which isn't
        # continued by request until its record is kept as failed.
        stale = status_file.begin_run(self.paths, kind="automatic", step="soc", step_label="Installing QIDI's firmware package",
                                      firmware_update="fw-1")
        status_file.update_status(self.paths, lambda current: replace(current, run=replace(current.run, pid=2 ** 22 + 1)))
        record = self.server.FIRMWARE_UPDATE_RECORD
        record.write_text(json.dumps({"schema": 1, "id": "fw-1", "state": "running"}))

        status, body = self.request("/api/status", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(body["schema"], 1)
        self.assertEqual(body["release"], {"installed": "26.11.11.3", "channel": None, "commit": None})
        self.assertEqual((body["printer"]["firmware"], body["printer"]["target_firmware"]), ("01.01.06.02", "01.01.07.01"))
        self.assertEqual([(item["kind"], item["reinstall"]) for item in body["pending"]],
                         [("release", True), ("firmware", False), ("board", False)])
        self.assertEqual(body["pending"][0]["version"], body["release"]["installed"])
        # Automatic updates are off, so no reason is given for why it hasn't run.
        self.assertEqual(body["pending"][1]["blocked_by"], [])
        self.assertEqual([(board["id"], board["name"], board["state"], board["waiting_for_firmware_update"])
                          for board in body["boards"]],
                         [("thr", "THR", "current", False), ("mcu", "Mainboard", "needs_image", True),
                          ("box2", "Box 2", "needs_image", False)])
        self.assertEqual((body["run"]["id"], body["run"]["state"], body["busy"]), (stale.id, "interrupted", False))
        self.assertEqual((body["run"]["firmware_update"], body["run"]["can_continue"]), (True, False))
        # Kept as failed, it can be continued, unless a release found it couldn't carry it on.
        for kept, can_continue in (({"state": "failed"}, True), ({"state": "failed", "continuable": False}, False)):
            record.write_text(json.dumps({"schema": 1, "id": "fw-1", **kept}))
            self.assertEqual(self.request("/api/status", method="GET")[1]["run"]["can_continue"], can_continue)
        record.unlink()

        # An install's board admission newer than the last check shows its boards; only this project's issue form is
        # passed on as a link.
        issue = "https://github.com/thelegendtubaguy/valve-oil/issues/new?template=printer-support.yml&x=1"
        self.server.ADMISSION_FILE.write_text(json.dumps({"schema_version": 1, "printer": "max4", "boards": [
            {"board": "thr", "state": "unrecognized_bootloader", "issue_url": issue},
            {"board": "box1", "state": "unread", "issue_url": "javascript:alert(1)", "detail": "dry run"},
        ]}))
        # The closed-loop drivers, which an install doesn't read, stay as a firmware update or a check recorded them.
        status_file.record_boards(self.paths, (status_file.BoardStatus(id="closed_loop", state="held"),))
        boards = self.request("/api/status", method="GET")[1]["boards"]
        self.assertEqual([(b["id"], b["state"], b["issue_url"]) for b in boards],
                         [("thr", "unrecognized_bootloader", issue), ("closed_loop", "held", None),
                          ("box1", "unknown", None)])
        status_file.record_boards(self.paths, (), remove=("closed_loop",))
        self.server.ADMISSION_FILE.unlink()

        status, body = self.request("/api/update-now", headers=self.GOOD)
        self.assertEqual((status, body["job"]["state"], body["job"]["run_id"]), (202, "running", None))
        self.assertEqual(self.jobs, [body["job"]["id"]])
        conflict = self.request("/api/update-now", headers=self.GOOD)
        self.assertEqual((conflict[0], [item["code"] for item in conflict[1]["blocked_by"]]), (409, ["run_in_progress"]))
        self.assertEqual(self.request("/api/update-now", headers=self.GOOD, body=b'{"force": true}')[0], 400)
        self.assertEqual(len(self.processes), 1)
        # The job names its run once the run begins, and the run its install hands off to, such as a firmware update.
        run = status_file.begin_run(self.paths, kind="update_now", step="check", job=body["job"]["id"])
        self.assertEqual(self.request("/api/status", method="GET")[1]["job"]["run_id"], run.id)
        handed_off = status_file.begin_run(self.paths, kind="update_now", step="soc")
        status = self.request("/api/status", method="GET")[1]
        self.assertEqual((status["job"]["run_id"], status["run"]["id"]), (handed_off.id, handed_off.id))
        status_file.finish_run(self.paths, handed_off.id, state=status_file.SUCCEEDED)
        self.processes[0].released.set()

    def test_uninstall_runs_the_installed_bundles_uninstall_once_the_printer_allows_it(self) -> None:
        def codes() -> list[str]:
            return [item["code"] for item in self.request("/api/status", method="GET")[1]["uninstall"]["blocked_by"]]

        record = self.server.FIRMWARE_UPDATE_RECORD
        started = len(self.commands)
        try:
            # A print, or a QIDI firmware update that hasn't finished, comes first; a halted Klipper doesn't, as
            # uninstalling may be what lets it start.
            type(self).live = ("printing",)
            record.write_text('{"schema": 1, "id": "fw-1", "state": "failed"}')
            self.assertEqual(codes(), ["printing", "firmware_update_pending"])
            refused = self.request("/api/uninstall", headers=self.GOOD)
            self.assertEqual((refused[0], [item["code"] for item in refused[1]["blocked_by"]]),
                             (409, ["printing", "firmware_update_pending"]))
            record.unlink()
            type(self).live = ("klipper_not_ready",)
            self.assertEqual(codes(), [])
            # Only marked same-origin posts with known options start it.
            self.assertEqual(self.request("/api/uninstall", headers={**self.GOOD, "X-Valve-Oil": "x"})[0], 403)
            for body in (b'{"keep_system_optimizations": "yes"}', b'{"reboot": true}'):
                self.assertEqual(self.request("/api/uninstall", headers=self.GOOD, body=body)[0], 400)
            self.assertEqual(len(self.commands), started)

            # Kept optimizations need no restart; undoing them restarts the printer only if they need it.
            for body, flag in ((b'{"keep_system_optimizations": true}', "--keep-system-optimizations"), (b"{}", "--reboot-host")):
                status, answer = self.request("/api/uninstall", headers=self.GOOD, body=body)
                self.assertEqual((status, answer["job"]["kind"], answer["job"]["state"]), (202, "uninstall", "running"))
                self.assertEqual(self.commands[-1][-4:], ("uninstall", "--yes", "--plain", flag))
                self.assertTrue(str(self.commands[-1][-5]).endswith("installer/runtime/bootstrap.py"))
                # One run at a time, whichever it is.
                status = self.request("/api/status", method="GET")[1]
                self.assertEqual((status["busy"], status["job"]["kind"]), (True, "uninstall"))
                self.assertEqual(codes(), ["run_in_progress"])
                self.assertEqual(self.request("/api/update-now", headers=self.GOOD)[0], 409)
                self.processes[-1].released.set()
                for _ in range(500):
                    if not self.request("/api/status", method="GET")[1]["busy"]:
                        break
                    time.sleep(0.01)
            # An uninstall that stopped without an exit status says so.
            job = self.request("/api/status", method="GET")[1]["job"]
            self.assertEqual((job["state"], job["message"]), ("failed", "The uninstall stopped before it finished."))
        finally:
            type(self).live = ()
            record.unlink(missing_ok=True)
            for path in (self.server.JOB_FILE, self.server.JOB_LOG, self.server.JOB_RC):
                path.unlink(missing_ok=True)

    def test_volatile_reasons_and_update_now_are_read_live(self) -> None:
        marker = self.server.ENROLLMENT_FILE
        status_file.record_check(
            self.paths, enrolled=True, printer=status_file.PrinterInfo(), installed_release=None,
            pending=(status_file.PendingItem(kind=status_file.FIRMWARE, version="01.01.07.01", blocked_by=(
                status_file.Blocker.of("heaters_on"), status_file.Blocker.of("free_space"))),),
            boards=(status_file.BoardStatus(id="thr", state=status_file.UNRECOGNIZED_BOOTLOADER,
                                            issue_url=self.server.ISSUE_URL_PREFIX + "x=" + "a" * 9000),))
        try:
            marker.write_text("1\n")
            type(self).live = ("printing",)
            body = self.request("/api/status", method="GET")[1]
            self.assertEqual([item["code"] for item in body["pending"][0]["blocked_by"]], ["printing", "free_space"])
            self.assertEqual((body["printer_busy"], body["update_now"]["allowed"]), (True, False))
            refused = self.request("/api/update-now", headers=self.GOOD)
            self.assertEqual((refused[0], [item["code"] for item in refused[1]["blocked_by"]]), (409, ["printing"]))
            # Moonraker can't be read: the check's reasons stand, and update now would stop at once.
            type(self).live = None
            body = self.request("/api/status", method="GET")[1]
            self.assertEqual([item["code"] for item in body["pending"][0]["blocked_by"]], ["heaters_on", "free_space"])
            self.assertEqual([item["code"] for item in body["update_now"]["blocked_by"]], ["printer_state_unknown"])
            # Idle, but a board's rescue has to run first, which update now names.
            type(self).live = ()
            rescue = self.paths.printer_data_root / ".vo_mainboard_firmware_pending.json"
            rescue.write_text("")
            body = self.request("/api/status", method="GET")[1]
            self.assertEqual([item["code"] for item in body["pending"][0]["blocked_by"]], ["free_space"])
            blocker, = body["update_now"]["blocked_by"]
            self.assertEqual(blocker["code"], "recovery_required")
            self.assertIn("./mainboard-firmware.sh rescue", blocker["message"])
            rescue.unlink()
            self.assertEqual(self.request("/api/status", method="GET")[1]["update_now"], {"allowed": True, "blocked_by": []})
            # An installer recovery comes first too, except while update now would continue a recorded firmware
            # update: only a board's rescue stops that (firmware_update.resume_blocker).
            sentinel = self.paths.printer_data_root / ".vo_recovery_required"
            sentinel.write_text("")
            self.assertIn("./restore.sh", self.request("/api/status", method="GET")[1]["update_now"]["blocked_by"][0]["message"])
            self.server.FIRMWARE_UPDATE_RECORD.write_text('{"schema": 1, "id": "kept", "state": "failed"}')
            try:
                self.assertEqual(self.request("/api/status", method="GET")[1]["update_now"], {"allowed": True, "blocked_by": []})
            finally:
                self.server.FIRMWARE_UPDATE_RECORD.unlink()
                sentinel.unlink()
            # With automatic updates off, nothing is said about why an update hasn't run.
            marker.unlink()
            self.assertEqual(self.request("/api/status", method="GET")[1]["pending"][0]["blocked_by"], [])
        finally:
            type(self).live = ()
            marker.unlink(missing_ok=True)
        # A link too long to pass on leads to the bare support form.
        self.assertEqual(body["boards"][0]["issue_url"], self.server.ISSUE_FORM_URL)
        # The next check's time comes from the timer, when it is installed.
        self.assertIsNone(body["check"]["next_at"])
        self.server.AUTO_UPDATE_TIMER.write_text("")
        self.server.read_timer_property = lambda name: 1_800_000_000_000_000 if name == "NextElapseUSecRealtime" else None
        self.server._next_check.update(until=0.0)
        try:
            self.assertEqual(self.request("/api/status", method="GET")[1]["check"]["next_at"], "2027-01-15T08:00:00Z")
        finally:
            self.server.AUTO_UPDATE_TIMER.unlink()
            self.server.read_timer_property = lambda name: None
            self.server._next_check.update(until=0.0)

    def test_dev_builds_and_the_support_bundle(self) -> None:
        """A dev build says so, for the UIs to leave out its failing update check; the support issue links only to
        this project's form and leaves Logs and files for the bundle; the bundle holds the newest part of each log and is only fetched."""
        metadata = self.server.RELEASE_METADATA
        logs = self.server.LOGS
        logs.mkdir(exist_ok=True)
        self.server.SUPPORT_LOG_BYTES = 64
        try:
            metadata.write_text('package_version: "26.11.11.3"\nchannel: "dev"\ngit_short_sha: "a8dda52"\n')
            self.server.AUTO_UPDATE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.server.AUTO_UPDATE_STATE_FILE.write_text(json.dumps({
                "installed_version": "26.11.11.3", "installed_channel": "dev", "installed_commit": "a8dda52",
                "installed_build_id": "manual-a", "installed_checksum": None,
            }))
            status_file.record_check(self.paths, enrolled=False, printer=status_file.PrinterInfo(name="QIDI Max 4"),
                                     installed_release="26.11.11.3", pending=(), failed="Could not fetch latest release checksum.")
            body = self.request("/api/status", method="GET")[1]
            self.assertEqual(body["release"], {"installed": "26.11.11.3", "channel": "dev", "commit": "a8dda52"})
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(body["support"]["issue_url"]).query)
            self.assertTrue(body["support"]["issue_url"].startswith(self.server.ISSUE_URL_PREFIX))
            self.assertEqual((query["template"], query["printer"], query["install_source"]),
                             (["bug-report.yml"], ["QIDI Max 4"], ["Dev prerelease"]))
            self.assertIn("Valve Oil: 26.11.11.3 (dev build a8dda52)", query["printer_details"][0])
            # Filled in, the required Logs and files let issues be sent without the bundle (#410, #426).
            self.assertNotIn("logs", query)
            # Neither a same-version candidate nor another version can claim to be installed.
            metadata.write_text('package_version: "26.11.11.3"\nchannel: "dev"\ngit_short_sha: "deadbee"\n')
            self.assertEqual(self.request("/api/status", method="GET")[1]["release"]["commit"], "a8dda52")
            self.assertEqual(body["auto_update"]["installed_build"], "manual-a")
            metadata.write_text('package_version: "26.11.15.1"\nchannel: "dev"\n')
            self.assertEqual(self.request("/api/status", method="GET")[1]["release"]["channel"], "dev")
            self.assertEqual(self.server.release_build("26.11.15.1"), (None, None))

            (logs / "klippy.log").write_text("".join(f"line {number}\n" for number in range(40)))
            (logs / "klippy.log.2026-11-15").write_text("yesterday\n")
            (logs / "klippy.log.2026-11-14").write_text("older\n")
            os.utime(logs / "klippy.log.2026-11-14", (1, 1))
            (logs / "moonraker.log").write_text("moonraker\n")
            self.assertEqual(self.request("/api/support-bundle", headers=self.GOOD)[0], 405)
            with urllib.request.urlopen(self.base + "/api/support-bundle", timeout=5) as response:
                self.assertEqual(response.headers["Content-Type"], "application/zip")
                self.assertRegex(response.headers["Content-Disposition"], r'^attachment; filename="valve-oil-support-printer-\d{8}T\d{6}Z\.zip"$')
                archive = zipfile.ZipFile(io.BytesIO(response.read()))
            self.assertEqual(sorted(archive.namelist()), ["firmware_manifest.json", "logs/klippy.log", "logs/klippy.log.2026-11-15", "logs/moonraker.log",
                                                          "printer-details.txt", "valve-oil-status.json",
                                                          "valve-oil/RELEASE_METADATA.yaml"])
            klippy = archive.read("logs/klippy.log").decode().splitlines()
            self.assertTrue(klippy[0].startswith("[The start of this") and klippy[-1] == "line 39")
            self.assertTrue(all(line.startswith("line ") for line in klippy[1:]))
            self.assertEqual(list(self.server.STATE_DIR.glob("tmp*")), [])
        finally:
            self.server.SUPPORT_LOG_BYTES = 16 * 1024 * 1024
            metadata.unlink(missing_ok=True)
            self.server.AUTO_UPDATE_STATE_FILE.unlink(missing_ok=True)
            shutil.rmtree(logs, ignore_errors=True)


    def test_klipper_switch(self) -> None:
        """Valve Oil's Klipper is off until chosen; a switch writes only the choice, drops the launcher's stale records,
        and restarts Klipper, but never during a print or an installer run, and never to a Valve Oil Klipper that isn't
        there. QIDI's Klipper can always be chosen back while no print runs, Klipper ready or not."""
        server, data = self.server, self.paths.printer_data_root
        choice, launch, ready = server.KLIPPER_CHOICE_FILE, server.KLIPPER_STATUS_FILE, server.KLIPPER_READY_FILE

        def klipper() -> dict:
            return self.request("/api/status", method="GET")[1]["klipper"]

        def switch(body: dict) -> tuple[int, dict]:
            return self.request("/api/klipper", headers=self.GOOD, body=json.dumps(body).encode())

        def codes(answer: tuple[int, dict]) -> tuple[int, list[str]]:
            return answer[0], [item["code"] for item in answer[1].get("blocked_by", [])]

        try:
            # Not installed: QIDI's runs, and Valve Oil's can't be chosen.
            state = klipper()
            self.assertEqual({key: state[key] for key in ("available", "unavailable_reason", "selected", "running",
                                                          "fallback_reason")},
                             {"available": False, "unavailable_reason": "not installed", "selected": "qidi",
                              "running": "qidi", "fallback_reason": None})
            self.assertEqual((state["switch"]["to"], state["switch"]["allowed"]), ("valve_oil", False))
            self.assertEqual(codes(switch({"use": "valve_oil"})), (409, ["valve_oil_unavailable"]))
            self.assertEqual(self.klipper_checks, 0)
            for body in ({"use": "VALVE_OIL"}, {"use": "qidi", "now": True}, {"use": "qidi", "source": "web"}, {}):
                with self.subTest(body=body):
                    self.assertEqual(switch(body)[0], 400)
            self.assertEqual(self.request("/api/klipper", method="GET")[0], 405)
            self.assertFalse(choice.exists())

            # Installed. A print, a kept firmware update, or a board's rescue waiting refuse the switch.
            server.KLIPPER_LAUNCHER.write_text("")
            type(self).klipper_check = {"available": True, "unavailable_reason": None, "selected": "qidi",
                                        "would_run": "qidi", "reason": None, "version": "v0.14.0-vo1"}
            state = klipper()
            self.assertEqual((state["available"], state["version"], state["switch"]["allowed"]), (True, "v0.14.0-vo1", True))
            type(self).live = ("printing",)
            self.assertEqual(codes(switch({"use": "valve_oil"})), (409, ["printing"]))
            type(self).live = ()
            server.FIRMWARE_UPDATE_RECORD.write_text('{"schema": 1, "id": "kept", "state": "failed"}')
            rescue = data / ".vo_mainboard_firmware_pending.json"
            rescue.write_text("")
            self.assertEqual(codes(switch({"use": "valve_oil"})), (409, ["firmware_update_pending", "recovery_required"]))
            server.FIRMWARE_UPDATE_RECORD.unlink()
            rescue.unlink()
            self.assertFalse(choice.exists())
            self.assertFalse(self.restarts.acquire(timeout=0.05))

            # Chosen: the choice is written, the launcher's records of the last start go, and Klipper restarts.
            launch.write_text(json.dumps({"selected": "qidi", "running": "qidi", "reason": None, "launch_id": "a"}))
            ready.write_text('{"launch_id": "a"}')
            status, body = switch({"use": "valve_oil"})
            self.assertEqual((status, body["klipper"]["selected"], body["klipper"]["running"]), (202, "valve_oil", None))
            self.assertTrue(self.restarts.acquire(timeout=5))
            self.assertIsNone(klipper()["running"])
            self.restarted.set()
            for _ in range(100):
                if klipper()["running"]:
                    break
            self.assertEqual(klipper()["running"], "qidi")
            self.restarted.clear()
            saved = json.loads(choice.read_text())
            self.assertEqual((saved["use"], saved["source"]), ("valve_oil", "ui"))
            self.assertFalse(launch.exists() or ready.exists())
            self.assertEqual([path.name for path in data.glob(".vo_klipper.json.*")], [])

            # It fell back: QIDI's runs, and the launcher's record says why.
            launch.write_text(json.dumps({"selected": "valve_oil", "running": "qidi", "launch_id": "b", "attempts": 2,
                                          "reason": "Valve Oil's Klipper didn't become ready"}))
            state = klipper()
            self.assertEqual((state["running"], state["fallback_reason"], state["switch"]["to"]),
                             ("qidi", "Valve Oil's Klipper didn't become ready", "qidi"))
            self.assertIn("Klipper: QIDI's running, Valve Oil's selected; Valve Oil's Klipper v0.14.0-vo1",
                          server.printer_details(self.request("/api/status", method="GET")[1]))
            self.process["path"] = "/home/qidi/vo-klipper"
            self.assertEqual((klipper()["running"], klipper()["fallback_reason"]), ("valve_oil", None))

            # Back to QIDI's from the console, with Klipper halted on an error and Valve Oil's gone, and an update
            # waiting: only a print or a running installer would stop it.
            type(self).live = None
            self.process.update(state="error", path=None)
            server.KLIPPER_LAUNCHER.unlink()
            server.FIRMWARE_UPDATE_RECORD.write_text('{"schema": 1, "id": "kept", "state": "failed"}')
            type(self).restart_fails = True
            status, body = switch({"use": "qidi", "source": "console"})
            self.assertEqual((status, body["klipper"]["selected"]), (202, "qidi"))
            self.assertTrue(self.restarts.acquire(timeout=5))
            self.restarted.set()
            self.assertEqual(json.loads(choice.read_text())["source"], "console")
            for _ in range(100):
                if klipper()["restart_error"]:
                    break
            self.assertIn("restart Klipper", klipper()["restart_error"])
            # Moonraker doesn't answer: whether a print runs isn't known, so nothing switches.
            self.process.update(moonraker=False, state=None)
            self.assertEqual(codes(switch({"use": "qidi"})), (409, ["printer_state_unknown"]))
        finally:
            self.restarted.set()
            type(self).live = ()
            type(self).restart_fails = False
            type(self).klipper_check = None
            self.process.update(moonraker=True, state="ready", path="/home/qidi/klipper")
            server._klipper_restart["error"] = None
            for path in (choice, launch, ready, server.KLIPPER_LAUNCHER, server.FIRMWARE_UPDATE_RECORD):
                path.unlink(missing_ok=True)

    def test_diagnostics_check_the_logs_one_at_a_time_and_the_support_bundle_carries_the_report(self) -> None:
        logs = self.server.LOGS
        logs.mkdir(exist_ok=True)
        shutil.copy(FIXTURES_ROOT / "diagnostics/step-clock-offset.log", logs / "klippy.log")
        try:
            self.assertEqual(self.request("/api/diagnostics", method="GET"), (200, {
                "state": "idle", "since": None, "started_at": None, "finished_at": None, "error": None, "result": None,
                "report": None}))
            self.assertEqual(self.request("/api/diagnostics", headers=self.GOOD, body=b'{"since": "week"}')[0], 400)
            self.assertEqual(self.request("/api/diagnostics", body=b'{"since": "all"}')[0], 403)
            status, check = self.request("/api/diagnostics", headers=self.GOOD, body=b'{"since": "all"}')
            self.assertEqual((status, check["state"], check["since"]), (202, "running", "all"))
            self.assertEqual(self.request("/api/diagnostics", headers=self.GOOD, body=b'{"since": "all"}')[0], 409)
            self.checker_go.touch()
            for _ in range(500):
                check = self.request("/api/diagnostics", method="GET")[1]
                if check["state"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(check["state"], "done", check.get("error"))
            self.assertEqual({finding["id"] for finding in check["result"]["findings"]},
                             {"autofeed_rearmed_during_feed", "box_step_clock_offset", "unload_toolhead_sensor_stuck",
                              "qidi_error"})
            # The installed release (26.11.11.3) predates the fix, so the offset needs attention.
            offset = next(f for f in check["result"]["findings"] if f["id"] == "box_step_clock_offset")
            self.assertEqual(offset["severity"], "fault")
            self.assertTrue(check["report"].startswith("Printer details:\nValve Oil: 26.11.11.3"))
            with urllib.request.urlopen(self.base + "/api/support-bundle", timeout=5) as response:
                archive = zipfile.ZipFile(io.BytesIO(response.read()))
            self.assertEqual(archive.read("valve-oil/diagnostics.txt").decode(), check["report"])
        finally:
            shutil.rmtree(logs, ignore_errors=True)
            for path in (self.server.DIAGNOSTICS_FILE, self.server.DIAGNOSTICS_REPORT, self.checker_go):
                path.unlink(missing_ok=True)


class UpdateNowRunTests(unittest.TestCase):
    def test_failed_child_installer_restores_the_previous_bundle(self) -> None:
        from installer.runtime import auto_update
        from installer.runtime.cli import resolve_runtime_paths

        root = temp_path("vo-restore-bundle-")
        paths = resolve_runtime_paths(bundle_root=root / "valve-oil", environ={"VO_PRINTER_DATA_ROOT": str(root)})
        paths.bundle_root.mkdir()
        (paths.bundle_root / "identity").write_text("previous", encoding="utf-8")
        stage = root / "stage"
        staged = stage / "extract" / paths.bundle_root.name
        staged.mkdir(parents=True)
        (staged / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (stage / "bundle.tar.gz").write_bytes(b"downloaded archive")
        with mock.patch.object(auto_update, "_new_stage", return_value=stage), \
                mock.patch.object(auto_update, "_download_archive"), \
                mock.patch.object(auto_update, "_verify_archive_checksum"), \
                mock.patch.object(auto_update, "_extract_validated_archive"), \
                mock.patch("installer.runtime.qidi_package.exchange_paths", return_value=False):
            with self.assertRaises(auto_update.AutoUpdateError):
                auto_update.run_latest_installer(
                    paths=paths, archive_url="https://example.invalid/bundle.tar.gz", expected_checksum="a" * 64,
                    urlopen=mock.Mock(), run=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1), environ={},
                )
        self.assertEqual((paths.bundle_root / "identity").read_text(encoding="utf-8"), "previous")

    def test_update_now_uses_the_persisted_experimental_feed(self) -> None:
        from installer.runtime import auto_update, update_now
        from installer.runtime.cli import resolve_runtime_paths
        from installer.runtime.reporter import create_reporter

        root = temp_path("vo-update-feed-")
        paths = resolve_runtime_paths(bundle_root=root / "bundle", environ={"VO_PRINTER_DATA_ROOT": str(root)})
        auto_update.set_experimental_updates(paths, True)
        installs = []

        def install(**kwargs):
            installs.append(kwargs["archive_url"])

        seams = [
            mock.patch.object(update_now.firmware_update, "load_record", return_value=None),
            mock.patch.object(update_now.safety, "ensure_no_recovery_sentinel"),
            mock.patch.object(update_now.safety, "ensure_printer_idle"),
            mock.patch.object(update_now.safety, "ensure_klipper_ready"),
            mock.patch.object(update_now, "fetch_latest_checksum", return_value="a" * 64),
            mock.patch.object(update_now, "run_latest_installer", side_effect=install),
        ]
        with contextlib.ExitStack() as stack:
            for seam in seams:
                stack.enter_context(seam)
            self.assertTrue(update_now.run_update_now(paths=paths, reporter=create_reporter(io.StringIO(), prefer_plain=True), environ={}))
        self.assertEqual(installs, [auto_update.DEFAULT_DEV_ARCHIVE_URL])
        self.assertEqual(json.loads(auto_update.state_path(paths).read_text())["latest_channel"], "dev")

        auto_update.set_experimental_updates(paths, False)
        with contextlib.ExitStack() as stack:
            for seam in seams:
                stack.enter_context(seam)
            self.assertTrue(update_now.run_update_now(paths=paths, reporter=create_reporter(io.StringIO(), prefer_plain=True), environ={}))
        self.assertEqual(installs, [auto_update.DEFAULT_DEV_ARCHIVE_URL, auto_update.DEFAULT_ARCHIVE_URL])

    def test_pending_firmware_cannot_replace_a_run_started_after_attach(self):
        from installer.runtime import update_now
        from installer.runtime.cli import resolve_runtime_paths
        from installer.runtime.locking import acquire
        from installer.runtime.reporter import create_reporter

        root = temp_path("vo-update-resume-")
        paths = resolve_runtime_paths(bundle_root=root / "bundle", environ={"VO_PRINTER_DATA_ROOT": str(root)})
        output = io.StringIO()
        acquired = []

        def lost_attach(*args):
            self.enterContext(acquire(paths.lock_path))
            acquired.append(status_file.begin_run(paths, kind="automatic", step="install"))
            return False

        with mock.patch.object(update_now.firmware_update, "load_record", return_value={"id": "pending"}), \
                mock.patch.object(status_file, "attach_job", side_effect=lost_attach), \
                mock.patch.object(update_now.firmware_update, "resume_firmware_update",
                                  return_value=update_now.firmware_update.FirmwareUpdateResult("failed")) as resume:
            finished = update_now.run_update_now(paths=paths, reporter=create_reporter(output, prefer_plain=True),
                                                 environ={update_now.JOB_ENV: "new-job"})
        self.assertFalse(finished)
        resume.assert_not_called()
        self.assertEqual(status_file.load_status(paths).run, acquired[0])


    def test_pending_firmware_follow_releases_lock_and_preserves_the_job(self):
        from installer.runtime import update_now
        from installer.runtime.cli import resolve_runtime_paths
        from installer.runtime.locking import acquire
        from installer.runtime.reporter import create_reporter

        root = temp_path("vo-update-follow-")
        paths = resolve_runtime_paths(bundle_root=root / "bundle", environ={"VO_PRINTER_DATA_ROOT": str(root)})
        def resume(**kwargs):
            with acquire(paths.lock_path):
                run = status_file.begin_run(paths, kind="update_now", step="firmware", firmware_update="pending")
                self.assertEqual(run.job, "follow-job")
                status_file.finish_run(paths, run.id, state=status_file.SUCCEEDED)
            return update_now.firmware_update.FirmwareUpdateResult(update_now.firmware_update.SUCCEEDED)

        with mock.patch.object(update_now.firmware_update, "load_record", return_value={"id": "pending"}), \
                mock.patch.object(update_now.firmware_update, "resume_firmware_update", side_effect=resume):
            finished = update_now.run_update_now(paths=paths, reporter=create_reporter(io.StringIO(), prefer_plain=True),
                                                 environ={update_now.JOB_ENV: "follow-job"})
        self.assertTrue(finished)
        completed = status_file.load_status(paths).run
        assert completed is not None
        self.assertEqual(completed.step, "firmware")
        self.assertEqual(completed.state, status_file.SUCCEEDED)

    def test_update_now_waits_for_other_installer_runs_and_reports_why(self) -> None:
        import io

        from installer.runtime import update_now
        from installer.runtime.cli import resolve_runtime_paths
        from installer.runtime.locking import acquire
        from installer.runtime.reporter import create_reporter

        root = temp_path("vo-update-now-")
        paths = resolve_runtime_paths(bundle_root=root / "bundle", environ={"VO_PRINTER_DATA_ROOT": str(root)})
        output = io.StringIO()
        with acquire(paths.lock_path):
            active = status_file.begin_run(paths, kind="automatic", step="install")
            before = status_file.status_path(paths).read_bytes()
            finished = update_now.run_update_now(paths=paths, reporter=create_reporter(output, prefer_plain=True), environ={})
            self.assertEqual(status_file.status_path(paths).read_bytes(), before)
            self.assertTrue(status_file.run_active(status_file.load_status(paths).run))
        self.assertFalse(finished)
        self.assertIn("already in progress", output.getvalue())
        status_file.finish_run(paths, active.id, state=status_file.SUCCEEDED)
        completed = status_file.load_status(paths).run
        assert completed is not None
        self.assertEqual(completed.state, status_file.SUCCEEDED)


if __name__ == "__main__":
    unittest.main()


class SwitchFirmwareRestartTests(unittest.TestCase):
    """A board left shut down by the Klipper before a switch gets a firmware restart; other states don't."""

    def run_followup(self, info):
        server = _load_server()
        posts = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def urlopen(request, timeout=None):
            if isinstance(request, str):
                return Response(json.dumps({"result": info}).encode())
            posts.append(request.full_url.rsplit("/", 2)[-2:])
            return Response(b"{}")

        with mock.patch.object(server.urllib.request, "urlopen", urlopen), \
                mock.patch.object(server.time, "sleep", lambda _s: None):
            server.firmware_restart_if_board_shut_down(wait=10.0)
        return posts

    def test_firmware_restart_only_for_a_shut_down_board(self):
        shut_down = {"state": "error", "state_message": "Can not update MCU 'mcu' config as it is shutdown\n..."}
        self.assertEqual(self.run_followup(shut_down), [["printer", "firmware_restart"]])
        for info in ({"state": "ready", "state_message": "Printer is ready"},
                     {"state": "error", "state_message": "Option 'x' is not valid in section 'y'"}):
            with self.subTest(state=info["state_message"]):
                self.assertEqual(self.run_followup(info), [])
