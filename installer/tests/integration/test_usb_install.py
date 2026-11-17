from __future__ import annotations

import io
import unittest
from types import SimpleNamespace

from installer.runtime import install_flow, progress, touch_ui, usb_install
from installer.runtime.board_firmware import BoardAdmissionError
from installer.runtime.errors import OperationCancelled
from installer.runtime.manifest import load_manifest
from installer.runtime.reporter import create_reporter
from installer.runtime.run_log import LoggingReporter, RunLog
from installer.tests.helpers import REPO_ROOT, moonraker_urlopen, temp_path

MANIFEST = load_manifest(REPO_ROOT / "installer/package.yaml")


class Ui(touch_ui.FakeUi):
    """The touchscreen, checking every screen's fields and taps; names() lists the screens shown."""

    @property
    def screens(self):
        return self.shown

    def names(self):
        return [name for name, _ in self.shown]


def overview(**changes):
    values = dict(printer_id="max4", printer_name="QIDI Max 4", firmware="01.01.07.01", target_firmware="01.01.07.01",
                  firmware_update=False, package_version=MANIFEST.package.version, installed_version=None,
                  new_install=True, auto_updates_on=False, ai_detection_on=True, box_count=2, box_support_on=False,
                  legacy_manual_install=False, boards=("Box 1", "Box 2"),
                  questions=(install_flow.QUESTIONS[install_flow.AUTO_UPDATES],
                             install_flow.QUESTIONS[install_flow.AI_DETECTION]))
    values.update(changes)
    return install_flow.Overview(**values)


class UsbInstallTests(unittest.TestCase):
    def session(self, taps, *, run=None, found=None, state="standby", markers=(), refuse=None):
        root = temp_path("usb-install")
        drive = root / "USB"
        (drive / "QD_Update").mkdir(parents=True)
        firmware_manifest = root / "update/firmware_manifest.json"
        firmware_manifest.parent.mkdir()
        firmware_manifest.write_text(
            '{"SOC": {"version": "01.01.07.01", "file": "QD_MAX4_SOC_01.01.07.01_20260907.deb"}}', encoding="utf-8")
        paths = SimpleNamespace(printer_data_root=root, firmware_manifest_path=firmware_manifest,
                                moonraker_url="http://moonraker.invalid", installer_root=REPO_ROOT / "installer")
        log = RunLog(root / "logs/valve-oil-usb.log")
        self.addCleanup(lambda: self.assertNotIn("usb.screen.failed", log.path.read_text(encoding="utf-8")))
        self.addCleanup(log.close)
        reporter = LoggingReporter(create_reporter(stream=io.StringIO(), prefer_plain=True), log)
        self.ui = Ui(taps)
        self.calls = []

        def dispatch(argv, *, front_end=None, input_stream=None, errors_to=None):
            self.calls.append((argv, front_end))
            if refuse is not None:  # the boards are read before the first question
                front_end.wrap(reporter).emit_error(refuse)
                return 1
            try:
                answers = front_end.ask(found or overview())
            except OperationCancelled:
                return 0
            self.answers = answers
            progress.begin([progress.CHECK, progress.BACKUP, progress.FINISH], origin=progress.ORIGIN_USB)
            progress.step(progress.BACKUP)
            try:
                return (run or (lambda front_end: 0))(front_end)
            except Exception as exc:
                front_end.wrap(reporter).emit_error(exc)
                return 1
            finally:
                progress.end()

        self.drive = drive
        self.restarted = []
        session = usb_install.Session(paths=paths, manifest=MANIFEST, env={}, reporter=reporter, ui=self.ui,
                                      hold=SimpleNamespace(password="pw"), drive=drive, dispatch=dispatch,
                                      log_path=log.path, urlopen=moonraker_urlopen(state),
                                      sleep=lambda seconds: None, no_restore_set_markers=markers)
        session._restart = lambda: self.restarted.append(True)
        return session

    def test_a_new_install_asks_everything_before_it_runs_and_restarts_once_the_drive_is_out(self):
        def install(front_end):
            self.assertEqual(self.ui.names()[-1], "installing")  # the run's progress replaces the ready screen
            return 0

        session = self.session(["start", "yes", "back", "no", "yes", "install"], run=install)
        self.assertEqual(session.loop(), usb_install.DONE)
        self.assertEqual(self.ui.names()[:6], ["checking", "found", "question", "question", "question", "question"])
        self.assertEqual(self.ui.names()[6], "ready")
        self.assertEqual((self.answers.auto_updates, self.answers.disable_ai_detection), (False, True))
        ready = dict(self.ui.screens[6][1])
        self.assertIn(("Automatic updates", "Off"), ready["rows"])
        self.assertIn(("QIDI AI detection", "Turned off"), ready["rows"])

        # The finish screen comes once the log is on the drive; the printer restarts when the drive comes out.
        (self.drive / "QD_Update").rmdir()
        session.finish()
        self.assertTrue((self.drive / usb_install.DRIVE_LOG_NAME).is_file())
        self.assertEqual(self.ui.names()[-1], "done")
        self.assertEqual(self.restarted, [True])

    def test_quitting_before_install_changes_nothing(self):
        for taps in (["quit"], ["start", "back"], ["start", "yes", "yes", "back", "back", "back"]):
            with self.subTest(taps=taps):
                ran = []
                session = self.session(taps, run=lambda front_end: ran.append(True) or 0)
                self.assertEqual(session.loop(), usb_install.QUIT)
                self.assertEqual(ran, [])

    def test_an_installed_printer_updates_with_its_settings_or_changes_them(self):
        found = overview(installed_version="26.1.1.1", new_install=False, auto_updates_on=True, questions=())
        session = self.session(["update"], found=found)
        self.assertEqual(session.loop(), usb_install.DONE)
        self.assertEqual(self.ui.names()[1], "update")
        self.assertEqual(self.answers, install_flow.Answers())

        session = self.session(["settings", "no", "no", "install"], found=found)
        self.assertEqual(session.loop(), usb_install.DONE)
        self.assertEqual((self.answers.auto_updates, self.answers.disable_ai_detection), (False, False))

        current = overview(installed_version=MANIFEST.package.version, new_install=False, questions=())
        session = self.session(["again"], found=current)
        self.assertEqual(session.loop(), usb_install.DONE)
        self.assertEqual(self.ui.names()[1], "up_to_date")

    def test_a_failed_step_is_named_and_its_log_is_on_the_drive(self):
        def fail(front_end):
            raise RuntimeError("Moonraker did not come back")

        session = self.session(["start", "yes", "yes", "install", "view_log", "close", "quit"], run=fail)
        self.assertEqual(session.loop(), usb_install.QUIT)
        failed = [fields for name, fields in self.ui.screens if name == "failed"][0]
        self.assertIn(f"Step 2, {progress.BACKUP}, did not finish: Moonraker did not come back", failed["text"])
        self.assertIn("put back", failed["text"])
        self.assertIn("Moonraker did not come back",
                      (self.drive / usb_install.DRIVE_LOG_NAME).read_text(encoding="utf-8"))
        self.assertIn("log", self.ui.names())

    def test_a_printer_it_cannot_install_on_says_why_and_changes_nothing(self):
        # Busy: says so, and checks again.
        session = self.session(["again", "quit"], state="printing")
        self.assertEqual(session.loop(), usb_install.QUIT)
        self.assertEqual(self.ui.names(), ["checking", "busy", "checking", "busy"])
        self.assertEqual(self.calls, [])

        # QIDI's update folder could not be put back: says how to get QIDI's files.
        marker = temp_path("usb-marker") / "no-restore-set"
        marker.write_text("", encoding="utf-8")
        session = self.session(["back_to_qidi"], markers=(marker,))
        self.assertEqual(session.loop(), usb_install.QUIT)
        unsupported = dict(self.ui.screens[-1][1])
        self.assertIn("emptied", unsupported["text"])
        self.assertEqual(self.calls, [])

        # A board this release doesn't admit: named, with its issue link as a code.
        admission = SimpleNamespace(unadmitted=(SimpleNamespace(board="thr", label="THR", detail="Unknown bootloader",
                                                                issue_url="https://example.invalid/issue"),))

        error = BoardAdmissionError("not supported")
        error.admission = admission
        session = self.session(["back_to_qidi"], refuse=error)
        self.assertEqual(session.loop(), usb_install.QUIT)
        name, fields = self.ui.screens[-1]
        self.assertEqual((name, fields["qr_url"]), ("board_not_supported", "https://example.invalid/issue"))


if __name__ == "__main__":
    unittest.main()
