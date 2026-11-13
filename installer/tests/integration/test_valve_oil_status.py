from __future__ import annotations

import importlib.util
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path

from installer.runtime import valve_oil_status as status_file
from installer.tests.helpers import temp_path


def _load_server():
    path = Path(__file__).resolve().parents[2] / "system/tltg_status.py"
    module_spec = importlib.util.spec_from_file_location("tltg_status_under_test", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


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
        server.ENROLLMENT_FILE = cls.paths.printer_data_root / ".tltg_optimized_auto_update_enrolled"
        server.MACRO_GLOBALS = root / "globals.cfg"
        server.FIRMWARE_MANIFEST = root / "firmware_manifest.json"
        server.AUTO_UPDATE_TIMER = root / "missing.timer"
        server.ADMISSION_FILE = cls.paths.printer_data_root / ".tltg_board_admission.json"
        server.STATE_DIR = root / "cache"
        server.JOB_FILE, server.JOB_LOG, server.JOB_RC = (server.STATE_DIR / name for name in ("job.json", "job.log", "job.rc"))
        server.BUNDLE = root / "tltg-optimized-macros"
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
        server.MACRO_GLOBALS.write_text('[gcode_macro _tltg_optimized_globals]\nvariable_package_version: "26.11.11.3"\n')
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

    def test_status_reports_pending_work_and_update_now_runs_once(self) -> None:
        status_file.record_check(
            self.paths,
            enrolled=False,
            printer=status_file.PrinterInfo(id="max4", name="QIDI Max 4", firmware="01.01.06.01", target_firmware="01.01.07.01"),
            installed_release="26.11.11.2",
            pending=(
                # A release the operator has since installed by hand is no longer waiting.
                status_file.PendingItem(kind=status_file.RELEASE, version="26.11.11.3", current="26.11.11.2"),
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
        self.assertEqual(body["release"], {"installed": "26.11.11.3"})
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
