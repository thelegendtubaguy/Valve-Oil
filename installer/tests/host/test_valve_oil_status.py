from __future__ import annotations

import io
import json
import os
import shutil
import threading
import unittest
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
from installer.tests.helpers import load_source, temp_path


def _load_server():
    path = Path(__file__).resolve().parents[2] / "system/vo_status.py"
    return load_source("tltg_status_under_test", path)


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
        root = temp_path("tltg-status-")
        cls.paths = type("Paths", (), {"printer_data_root": root / "printer_data"})()
        cls.paths.printer_data_root.mkdir()
        server.STATUS_FILE = status_file.status_path(cls.paths)
        server.PROGRESS_FILE = status_file.progress_path(cls.paths)
        server.ENROLLMENT_FILE = cls.paths.printer_data_root / ".tltg_optimized_auto_update_enrolled"
        server.UPDATE_SETTINGS_FILE = update_window.settings_path(cls.paths)
        server.MACRO_GLOBALS = root / "globals.cfg"
        server.FIRMWARE_MANIFEST = root / "firmware_manifest.json"
        server.AUTO_UPDATE_TIMER = root / "missing.timer"
        server.ADMISSION_FILE = cls.paths.printer_data_root / ".tltg_board_admission.json"
        server.STATE_DIR = root / "cache"
        server.JOB_FILE, server.JOB_LOG, server.JOB_RC = (server.STATE_DIR / name for name in ("job.json", "job.log", "job.rc"))
        server.BUNDLE = root / "tltg-optimized-macros"
        server.RELEASE_METADATA = server.BUNDLE / "RELEASE_METADATA.yaml"
        server.LOGS = cls.paths.printer_data_root / "logs"
        server.CACHE = root / "valve-oil-cache"
        server.BOX_CFG = root / "box.cfg"
        server.FIRMWARE_UPDATE_RESULT = cls.paths.printer_data_root / ".tltg_firmware_update_result.json"
        (server.BUNDLE / "installer/runtime").mkdir(parents=True)
        (server.BUNDLE / "installer/runtime/bootstrap.py").write_text("")
        server.INSTALLER_LOCK = cls.paths.printer_data_root / ".tltg_optimized_installer.lock"
        server.RECOVERY_FILES = {cls.paths.printer_data_root / path.name: step for path, step in server.RECOVERY_FILES.items()}
        server.FIRMWARE_UPDATE_RECORD = cls.paths.printer_data_root / ".tltg_firmware_update_pending.json"
        server.read_timer_property = lambda name: None
        cls.live: tuple[str, ...] | None = ()
        server.live_printer = lambda: cls.live
        server.log = lambda message: None
        cls.processes: list[_Process] = []
        cls.jobs: list[str] = []

        def start(output, job_id):
            cls.jobs.append(job_id)
            cls.processes.append(_Process())
            return cls.processes[-1]

        server.start_update_now = start
        server.MACRO_GLOBALS.write_text('[gcode_macro _vo_globals]\nvariable_package_version: "26.11.11.3"\n')
        server.FIRMWARE_MANIFEST.write_text(json.dumps({"SOC": {"version": "01.01.06.02"}}))
        server.Handler.jobs = server.Jobs()
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
            self.assertEqual(body["auto_update"], {"enabled": True, "scheduled": False, "window": outside,
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
            rescue = self.paths.printer_data_root / ".tltg_mainboard_firmware_pending.json"
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
            sentinel = self.paths.printer_data_root / ".tltg_optimized_recovery_required"
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
        this project's form; the bundle holds the newest part of each log and is only fetched."""
        metadata = self.server.RELEASE_METADATA
        logs = self.server.LOGS
        logs.mkdir(exist_ok=True)
        self.server.SUPPORT_LOG_BYTES = 64
        try:
            metadata.write_text('package_version: "26.11.11.3"\nchannel: "dev"\ngit_short_sha: "a8dda52"\n')
            status_file.record_check(self.paths, enrolled=False, printer=status_file.PrinterInfo(name="QIDI Max 4"),
                                     installed_release="26.11.11.3", pending=(), failed="Could not fetch latest release checksum.")
            body = self.request("/api/status", method="GET")[1]
            self.assertEqual(body["release"], {"installed": "26.11.11.3", "channel": "dev", "commit": "a8dda52"})
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(body["support"]["issue_url"]).query)
            self.assertTrue(body["support"]["issue_url"].startswith(self.server.ISSUE_URL_PREFIX))
            self.assertEqual((query["template"], query["printer"], query["install_source"]),
                             (["bug-report.yml"], ["QIDI Max 4"], ["Dev prerelease"]))
            self.assertIn("Valve Oil: 26.11.11.3 (dev build a8dda52)", query["printer_details"][0])
            # The bundle in ~/valve-oil is for another version: what installed the installed release isn't known.
            metadata.write_text('package_version: "26.11.15.1"\nchannel: "dev"\n')
            self.assertEqual(self.request("/api/status", method="GET")[1]["release"]["channel"], None)

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
            shutil.rmtree(logs, ignore_errors=True)


class UpdateNowRunTests(unittest.TestCase):
    def test_update_now_waits_for_other_installer_runs_and_reports_why(self) -> None:
        import io

        from installer.runtime import update_now
        from installer.runtime.cli import resolve_runtime_paths
        from installer.runtime.locking import acquire
        from installer.runtime.reporter import create_reporter

        root = temp_path("tltg-update-now-")
        paths = resolve_runtime_paths(bundle_root=root / "bundle", environ={"TLTG_OPTIMIZED_PRINTER_DATA_ROOT": str(root)})
        with acquire(paths.lock_path):
            finished = update_now.run_update_now(paths=paths, reporter=create_reporter(io.StringIO(), prefer_plain=True), environ={})
        run = status_file.load_status(paths).run
        self.assertFalse(finished)
        self.assertEqual((run.kind, run.state), ("update_now", status_file.FAILED))
        self.assertIn("already in progress", run.message)


if __name__ == "__main__":
    unittest.main()
