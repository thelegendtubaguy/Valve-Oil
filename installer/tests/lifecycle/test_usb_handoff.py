"""The USB hand-off (installer/usb/): what QIDI's offline update runs from the drive, through the installer's start."""
from __future__ import annotations

import hashlib
import subprocess
import unittest
import zipfile

from installer.tests.helpers import temp_path
from installer.tests.usb_harness import STARTING_PNG, FakePrinter, make_bundle, moonraker
from scripts import usb_drive_image

ZIP = "QD_MAX4_ValveOil_test.zip"
DEB = usb_drive_image.deb_name("test")
SETS = [
    ("01.01.06.04_20260609", {"firmware_manifest.json": b'{"MCU": "first"}\n', "QD_MAX4_MCU_A.bin": b"first image"}),
    ("01.01.06.04_20260612", {"firmware_manifest.json": b'{"MCU": "second"}\n', "QD_MAX4_MCU_B.bin": b"second image"}),
    ("01.01.07.01", {"firmware_manifest.json": b'{"MCU": "07.01"}\n', "QD_MAX4_MCU_C.bin": b"07.01 image"}),
]


class UsbHandoffTests(unittest.TestCase):
    def printer(self, *, qidi_version: str | None = "01.01.06.04", sets=SETS, bundle=None) -> FakePrinter:
        root = temp_path("usb-handoff-")
        printer = FakePrinter(root, qidi_version=qidi_version)
        bundle = bundle or make_bundle(root / "bundle.tar.gz")
        printer_zip = usb_drive_image.printer_zip(
            printer_id="max4", zip_name=ZIP, deb=usb_drive_image.build_deb("test"), deb_file=DEB,
            bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(), restore_sets=sets,
            starting_png=STARTING_PNG)
        printer.put_on_drive(ZIP, printer_zip, bundle)
        return printer

    def assert_installer_started(self, printer: FakePrinter, result) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        options = printer.unit_options()
        self.assertTrue(any(option.startswith("--unit=valve-oil-usb-install-") for option in options), options)
        self.assertIn("--collect", options)
        self.assertTrue(any(option.startswith("--description=") for option in options), options)
        self.assertIn(f"--property=ExecStopPost=/bin/sh {printer.run_dir}/launch.sh cleanup", options)

    def test_restores_the_latest_package_of_the_installed_firmware_and_starts_the_installer(self):
        printer = self.printer()

        result = printer.offline_update(ZIP)

        self.assert_installer_started(printer, result)
        # QIDI published 01.01.06.04 twice; the later package's files are restored, and nothing of the zip is left.
        restored = {path.name: path.read_bytes() for path in printer.update.iterdir()}
        self.assertEqual(set(restored), {"qidi.deb", *SETS[1][1]})
        self.assertEqual({name: restored[name] for name in SETS[1][1]}, SETS[1][1])
        self.assertTrue(all(printer.update.joinpath(name).stat().st_mode & 0o777 == 0o644 for name in SETS[1][1]))
        self.assertEqual(printer.status["result"], "restored")
        self.assertEqual(printer.status["set"], "01.01.06.04_20260612")
        self.assertEqual(printer.status["drive"], str(printer.drive))
        self.assertEqual(printer.cached_bundle.read_bytes(), (printer.drive / "valve-oil/valve-oil.tar.gz").read_bytes())

        with moonraker("ready") as url:
            launched = printer.launch(moonraker_url=url)

        self.assertEqual(launched.returncode, 0, printer.log.read_text())
        self.assertEqual(printer.exec_arguments(), [
            "-I", "-S", str(printer.home / "valve-oil/installer/runtime/bootstrap.py"), "usb", "--drive",
            str(printer.drive)])
        self.assertTrue((printer.home / "valve-oil/install.sh").is_file())
        calls = printer.calls()
        self.assertIn(f"dpkg --purge {usb_drive_image.HANDOFF_PACKAGE}", calls)
        # Valve Oil's starting screen replaces the restarted app before anything else waits, and the installer finds
        # the app stopped for it to take over.
        self.assertLess(calls.index("systemctl stop qidi-client.service"), calls.index(f"draw {printer.run_dir}/starting.png"))
        self.assertLess(calls.index(f"draw {printer.run_dir}/starting.png"),
                        calls.index(f"dpkg --purge {usb_drive_image.HANDOFF_PACKAGE}"))
        self.assertNotIn("systemctl start qidi-client.service", calls)
        self.assertTrue(printer.touchscreen_stopped.is_file())
        sudoers = (printer.sudoers / "valve-oil-usb-install").read_text()
        self.assertIn(f"{printer.printer_data.owner()} ALL=(ALL) NOPASSWD: ALL", sudoers)
        self.assertIn(f"r {printer.sudoers}/valve-oil-usb-install",
                      (printer.tmpfiles / "valve-oil-usb-install.conf").read_text())

        # The unit's ExecStopPost, after an installer that never took the app over, starts it.
        printer.cleanup()
        self.assertFalse((printer.sudoers / "valve-oil-usb-install").exists())
        self.assertFalse((printer.tmpfiles / "valve-oil-usb-install.conf").exists())
        self.assertIn("systemctl start qidi-client.service", printer.calls())
        self.assertFalse(printer.touchscreen_stopped.exists())

    def test_the_app_comes_back_when_the_starting_screen_cannot_be_drawn_and_not_after_the_installer_took_it(self):
        for case in ("not drawn", "taken over"):
            with self.subTest(case):
                printer = self.printer()
                printer.offline_update(ZIP)
                if case == "not drawn":
                    (printer.fake / "screen-exit").write_text("1")

                with moonraker("ready") as url:
                    launched = printer.launch(moonraker_url=url)

                self.assertEqual(launched.returncode, 0, printer.log.read_text())
                self.assertIsNotNone(printer.exec_arguments())
                if case == "taken over":
                    printer.touchscreen_stopped.unlink()  # what install_screen.held does
                started = printer.calls().count("systemctl start qidi-client.service")
                self.assertEqual(started, 1 if case == "not drawn" else 0)
                printer.cleanup()
                self.assertEqual(printer.calls().count("systemctl start qidi-client.service"), started)

    def test_a_firmware_without_a_set_or_with_a_corrupt_set_restores_nothing_and_still_starts_the_installer(self):
        cases = {"no-set": "01.01.06.05", "bad-set": "01.01.07.01"}
        for expected, qidi_version in cases.items():
            with self.subTest(expected):
                printer = self.printer(qidi_version=qidi_version)
                if expected == "bad-set":
                    # The drive's copy of a file was changed after the build wrote SHA256SUMS.
                    path = printer.drive / "QD_Update" / ZIP
                    with zipfile.ZipFile(path) as archive:
                        members = {info.filename: archive.read(info) for info in archive.infolist()}
                    members["valve-oil-restore/01.01.07.01/QD_MAX4_MCU_C.bin"] = b"changed"
                    with zipfile.ZipFile(path, "w") as archive:
                        for name, data in members.items():
                            archive.writestr(name, data)

                result = printer.offline_update(ZIP)

                self.assert_installer_started(printer, result)
                self.assertEqual(sorted(path.name for path in printer.update.iterdir()),
                                 [".valve-oil-no-restore-set", "qidi.deb"])
                self.assertEqual(printer.status["result"], expected)
                self.assertTrue(printer.cached_bundle.is_file())

    def test_a_launch_that_fails_leaves_the_app_running_and_its_log_on_the_drive(self):
        root = temp_path("usb-handoff-bundles-")
        bundle = make_bundle(root / "bundle.tar.gz")
        cases = {
            "app never restarted": dict(restarted=False),
            "unsafe bundle": dict(bundle=make_bundle(root / "link.tar.gz", symlink="valve-oil/link")),
            "bundle outside its folder": dict(bundle=make_bundle(root / "outside.tar.gz", {
                "valve-oil/install.sh": b"", "other/file": b""})),
            "bundle changed after the copy": dict(tamper=True),
        }
        for case, settings in cases.items():
            with self.subTest(case):
                printer = FakePrinter(temp_path("usb-launch-"))
                printer.stage_launch(settings.get("bundle", bundle))
                if settings.get("tamper"):
                    printer.cached_bundle.write_bytes(b"changed")

                with moonraker("ready") as url:
                    launched = printer.launch(moonraker_url=url, restarted=settings.get("restarted", True))

                self.assertNotEqual(launched.returncode, 0)
                self.assertIsNone(printer.exec_arguments())
                self.assertIn("systemctl start qidi-client.service", printer.calls())
                self.assertFalse(printer.touchscreen_stopped.exists())
                self.assertFalse((printer.sudoers / "valve-oil-usb-install").exists())
                self.assertFalse((printer.home / "valve-oil").exists())
                logs = list((printer.drive / "valve-oil/logs").iterdir())
                self.assertEqual(len(logs), 1)
                self.assertIn("FAILED", logs[0].read_text())

if __name__ == "__main__":
    unittest.main()
