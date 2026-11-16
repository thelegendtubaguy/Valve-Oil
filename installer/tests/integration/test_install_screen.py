from __future__ import annotations

import io
import os
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from installer.runtime import install_screen, safety, system_optimizations, update_screen
from installer.runtime.reporter import create_reporter
from installer.tests.helpers import moonraker_urlopen, temp_path


class Host:
    """systemctl, systemd-run, and sudo on a printer whose touchscreen app runs."""

    def __init__(self):
        self.services = {install_screen.TOUCHSCREEN: "active"}
        self.commands: list[list[str]] = []
        self.screens: list[object] = []

    def run(self, command, **kwargs):
        if command[:3] == ["sudo", "-S", "-p"]:
            command = command[4:]
        self.commands.append(command)
        out = ""
        if command[:2] == ["systemctl", "is-active"]:
            out = self.services.get(command[2], "inactive") + "\n"
        elif command[:1] == ["systemctl"] and command[1] in ("start", "stop", "restart"):
            self.services[command[2]] = "inactive" if command[1] == "stop" else "active"
        return subprocess.CompletedProcess(command, 0, out, "")

    def draw(self, screen, **kwargs):
        self.screens.append(screen)
        return True

    def touchscreen_commands(self):
        return [command[:2] for command in self.commands
                if command[:1] == ["systemctl"] and install_screen.TOUCHSCREEN in command and command[1] != "is-active"]


class InstallScreenTests(unittest.TestCase):
    def setUp(self):
        root = temp_path("install-screen")
        self.paths = SimpleNamespace(printer_data_root=root, moonraker_url="http://moonraker.invalid",
                                     recovery_sentinel_path=root / ".tltg_recovery_required", restart_marker_path=root / "none")
        self.host = Host()
        self.output = io.StringIO()

    def hold(self, *, state="standby", environ=None):
        return install_screen.held(paths=self.paths, reporter=create_reporter(stream=self.output, prefer_plain=True),
                                   environ=environ or {}, run=self.host.run, urlopen=moonraker_urlopen(state),
                                   draw=self.host.draw)

    def test_the_install_shows_its_screen_and_starts_the_touchscreen_once_when_it_ends(self):
        with self.hold():
            self.assertTrue(install_screen.touchscreen_held())
            self.assertEqual(self.host.services[install_screen.TOUCHSCREEN], "inactive")
            self.assertEqual(self.host.screens, [update_screen.INSTALLING])
            # A watcher is armed before the app stops, so a closed session or a killed installer can't strand it.
            watcher = next(command for command in self.host.commands if command[0] == "systemd-run")
            self.assertLess(self.host.commands.index(watcher),
                            self.host.commands.index(["systemctl", "stop", install_screen.TOUCHSCREEN]))
            # systemd writes the description into the transient unit file unescaped, so a daemon-reload during the
            # install only reads the file cleanly when it's one line; without one, systemd-run uses the script.
            descriptions = [arg for arg in watcher if arg.startswith("--description=")]
            self.assertEqual(len(descriptions), 1)
            self.assertNotIn("\n", descriptions[0])
            self.assertTrue((self.paths.printer_data_root / install_screen.MARKER).exists())
            # A restart an install step asks for waits for the install's end.
            restarts = system_optimizations._ServiceRestarts(paths=self.paths, root=Path("/"), sudo_password="x",
                                                             run=self.host.run, urlopen=moonraker_urlopen())
            restarts.request(install_screen.TOUCHSCREEN, required=True)
            restarts.flush()
        self.assertFalse(install_screen.touchscreen_held())
        self.assertEqual(self.host.touchscreen_commands(), [["systemctl", "stop"], ["systemctl", "start"]])
        self.assertFalse((self.paths.printer_data_root / install_screen.MARKER).exists())

    def test_an_install_that_fails_still_gives_the_touchscreen_back(self):
        with self.assertRaises(RuntimeError), self.hold():
            raise RuntimeError("the install failed")
        self.assertEqual(self.host.services[install_screen.TOUCHSCREEN], "active")

    def test_a_board_left_needing_recovery_keeps_the_touchscreen_stopped(self):
        with self.hold():
            (self.paths.printer_data_root / safety.MAINBOARD_RECOVERY_MARKER).write_text("{}")
        self.assertEqual(self.host.services[install_screen.TOUCHSCREEN], "inactive")
        self.assertEqual(self.host.screens[-1], update_screen.INSTALL_STOPPED)
        self.assertFalse((self.paths.printer_data_root / install_screen.MARKER).exists())

    def test_a_firmware_update_the_install_starts_takes_the_touchscreen_over(self):
        marker = self.paths.printer_data_root / install_screen.MARKER
        # The update stops the app and shows its screen; it starts the app when it ends, and the install doesn't.
        with self.hold():
            install_screen.hand_over(self.paths)
            self.assertFalse(install_screen.touchscreen_held())
        self.assertEqual(self.host.touchscreen_commands(), [["systemctl", "stop"]])
        self.assertFalse(marker.exists())
        # An update that ends without changing anything starts the app again; the install, carrying on for the
        # current firmware, takes it and shows its screen again.
        self.host = Host()
        with self.hold() as hold:
            install_screen.hand_over(self.paths)
            self.host.services[install_screen.TOUCHSCREEN] = "active"
            hold.retake()
            self.assertTrue(install_screen.touchscreen_held())
            self.assertEqual(self.host.screens, [update_screen.INSTALLING, update_screen.INSTALLING])
        self.assertEqual(self.host.touchscreen_commands(),
                         [["systemctl", "stop"], ["systemctl", "stop"], ["systemctl", "start"]])

    def test_nothing_is_stopped_while_printing_or_inside_a_firmware_update(self):
        for state, environ in (("printing", {}), ("standby", {safety.FIRMWARE_UPDATE_RUN_ENV: "update-id"})):
            with self.subTest(state=state, environ=environ):
                self.host = Host()
                with self.hold(state=state, environ=environ):
                    pass
                self.assertEqual(self.host.touchscreen_commands(), [])
                self.assertEqual(self.host.screens, [])

    def test_the_bundled_images_are_ones_the_printer_can_show(self):
        for image in (update_screen.INSTALLING_IMAGE, update_screen.UPDATING_IMAGE):
            with self.subTest(image=image.name):
                rows = update_screen.decode_png(image.read_bytes())
                self.assertEqual((len(rows[0]) // 3, len(rows)), update_screen.IMAGE_SIZE)

    def test_the_watcher_starts_the_touchscreen_after_an_installer_that_did_not(self):
        bin_dir = temp_path("install-screen-bin")
        log = bin_dir / "systemctl.log"
        (bin_dir / "systemctl").write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
        (bin_dir / "systemctl").chmod(0o755)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
        marker = self.paths.printer_data_root / install_screen.MARKER
        recovery = self.paths.printer_data_root / safety.BOX_RECOVERY_MARKER
        ended = subprocess.Popen(["true"])
        ended.wait()
        for recovering, expected in ((False, "start qidi-client.service\n"), (True, None)):
            with self.subTest(recovering=recovering):
                log.unlink(missing_ok=True)
                marker.write_text("held\n")
                if recovering:
                    recovery.write_text("{}")
                subprocess.run(["/bin/sh", "-c", install_screen.WATCHER_SCRIPT, "sh", str(ended.pid), str(marker),
                                str(recovery)], env=env, check=True, timeout=10)
                self.assertFalse(marker.exists())
                self.assertEqual(log.read_text() if log.exists() else None, expected)


if __name__ == "__main__":
    unittest.main()
