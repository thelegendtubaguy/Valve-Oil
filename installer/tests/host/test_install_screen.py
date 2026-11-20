from __future__ import annotations

import fcntl
import hashlib
import io
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from installer.runtime import install_screen, progress, qr_code, safety, system_optimizations, touch_ui, update_screen
from installer.runtime.progress_screen import ProgressScreen
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

    def hold(self, *, state="standby", environ=None, stopped_marker=None):
        return install_screen.held(paths=self.paths, reporter=create_reporter(stream=self.output, prefer_plain=True),
                                   environ=environ or {}, run=self.host.run, urlopen=moonraker_urlopen(state),
                                   draw=self.host.draw, stopped_marker=stopped_marker)

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

    def test_an_app_the_usb_launcher_stopped_is_taken_over_and_started_when_the_install_ends(self):
        stopped = self.paths.printer_data_root / "touchscreen-stopped"
        for launcher_stopped_it in (True, False):
            with self.subTest(launcher_stopped_it=launcher_stopped_it):
                self.host = Host()
                self.host.services[install_screen.TOUCHSCREEN] = "inactive"
                if launcher_stopped_it:
                    stopped.write_text("launch.sh\n")
                with self.hold(stopped_marker=stopped):
                    # The launcher's cleanup must not start the app under the install once the hold has it.
                    self.assertEqual(install_screen.touchscreen_held(), launcher_stopped_it)
                    self.assertFalse(stopped.exists())
                # An app stopped by anyone else is left as it is.
                self.assertEqual(self.host.touchscreen_commands(),
                                 [["systemctl", "start"]] if launcher_stopped_it else [])

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


class ProgressScreenTests(unittest.TestCase):
    def test_the_progress_screen_follows_the_run_and_stops_while_another_run_has_the_screen(self):
        ui = touch_ui.FakeUi()
        has_screen = [True]
        screen = ProgressScreen(ui, where="QIDI Max 4", firmware="01.01.07.01", active=lambda: has_screen[0])
        screen.start()
        self.addCleanup(progress.end)
        self.addCleanup(screen.stop)
        progress.begin([progress.CHECK, progress.BACKUP, progress.FINISH], origin=progress.ORIGIN_AUTOMATIC,
                       from_version="26.1.1.1", to_version="26.11.16.1")
        progress.step(progress.BACKUP)
        name, fields = ui.shown[-1]
        self.assertEqual((name, fields["step"], fields["index"]), ("installing", progress.BACKUP, 2))
        self.assertTrue(fields["header_step"].startswith("Step 2 of 3"))
        self.assertIn("automatic update", fields["footer"])
        touch_ui.compose(name, **fields)  # every field is one the screen takes
        # A firmware update the install started has the screen: nothing is drawn over it.
        has_screen[0] = False
        progress.step(progress.FINISH)
        self.assertEqual(len(ui.shown), 1)


class TouchScreenTests(unittest.TestCase):
    """The touchscreen front end on a framebuffer and a touchscreen made of a file and a pipe."""

    def panel(self, geometry):
        path = temp_path("touch-ui") / "fb0"
        path.write_bytes(bytes(geometry.stride * geometry.height))
        fd = os.open(path, os.O_RDWR)
        self.addCleanup(os.close, fd)
        return touch_ui.Panel(fd, geometry), path

    def touchscreen(self, geometry):
        read, write = os.pipe()
        fcntl.fcntl(read, fcntl.F_SETFL, fcntl.fcntl(read, fcntl.F_GETFL) | os.O_NONBLOCK)
        self.addCleanup(os.close, read)
        self.addCleanup(os.close, write)
        touch = touch_ui.Touch.__new__(touch_ui.Touch)
        touch.fd = read
        touch.taps = touch_ui.Taps(touch_ui.TouchAxes(0, geometry.width - 1, 0, geometry.height - 1, geometry.width,
                                                      geometry.height))
        return touch, write

    @staticmethod
    def finger(pipe, down, x=0, y=0):
        """What the Goodix driver reports as a finger touches at framebuffer (x, y), or lifts."""
        event = touch_ui.INPUT_EVENT
        if down:
            events = [(touch_ui.EV_ABS, touch_ui.ABS_MT_TRACKING_ID, 7), (touch_ui.EV_ABS, touch_ui.ABS_MT_POSITION_X, x),
                      (touch_ui.EV_ABS, touch_ui.ABS_MT_POSITION_Y, y), (touch_ui.EV_KEY, touch_ui.BTN_TOUCH, 1),
                      (touch_ui.EV_ABS, touch_ui.ABS_X, x), (touch_ui.EV_ABS, touch_ui.ABS_Y, y)]
        else:
            events = [(touch_ui.EV_ABS, touch_ui.ABS_MT_TRACKING_ID, -1), (touch_ui.EV_KEY, touch_ui.BTN_TOUCH, 0)]
        os.write(pipe, b"".join(event.pack(0, 0, *item) for item in events + [(touch_ui.EV_SYN, 0, 0)]))

    def test_every_screen_composes_with_the_buttons_it_reports(self):
        for name, (screen, fields) in touch_ui.EXAMPLES.items():
            with self.subTest(example=name):
                composed = touch_ui.compose(screen, **fields)
                self.assertEqual(len(composed.canvas.pixels), touch_ui.WIDTH * touch_ui.HEIGHT * 3)
                self.assertEqual(tuple(composed.buttons), touch_ui.buttons_for(screen, fields))
                self.assertEqual(touch_ui.FakeUi().show(screen, **fields), tuple(composed.buttons))
                for x, y, w, h in (rect for rects in composed.buttons.values() for rect in rects):
                    self.assertTrue(0 <= x and 0 <= y and x + w <= touch_ui.WIDTH and y + h <= touch_ui.HEIGHT)
        with self.assertRaises(TypeError):
            touch_ui.compose("found", titel="Install")

    def test_touches_map_to_the_upright_screen_on_both_panels(self):
        landscape = touch_ui.TouchAxes(0, 799, 0, 479, 800, 480)
        self.assertEqual(landscape.to_upright(120, 400), (120, 400))
        # Smart 5, observed: Settings at upright (85, 295) is framebuffer (295, 714) and is reported there.
        portrait = touch_ui.TouchAxes(0, 479, 0, 799, 480, 800)
        self.assertEqual(portrait.to_upright(295, 714), (85, 295))
        self.assertEqual(portrait.to_upright(44, 758), (41, 44))

    def test_the_log_screen_has_no_log_button(self):
        # Every other screen's Log opens the log; on the log itself it would answer nothing.
        screen, fields = touch_ui.EXAMPLES["17-log"]
        self.assertEqual(tuple(touch_ui.compose(screen, **fields).buttons), ("close",))

    def test_a_tap_on_a_turned_panel_is_reported_on_release_inside_its_button(self):
        geometry = touch_ui.PORTRAIT
        panel, framebuffer = self.panel(geometry)
        touch, pipe = self.touchscreen(geometry)
        ui = touch_ui.TouchUi(panel, touch)
        screen, fields = touch_ui.EXAMPLES["02-welcome"]
        self.finger(pipe, True, 10, 10)  # still down from before the screen
        buttons = ui.show(screen, **fields)
        self.assertEqual(buttons, ("quit", "start", "log"))
        # Upright (x, y) shows at framebuffer (y, 799 - x), as QIDI's app draws the turned panel.
        canvas, data = ui.current.canvas, framebuffer.read_bytes()
        for x, y in ((0, 0), (799, 479), (130, 407), (480, 400), (45, 30)):
            at = (y + (geometry.height - 1 - x) * geometry.width) * 4
            self.assertEqual(data[at:at + 3][::-1], canvas.pixels[(y * 800 + x) * 3:(y * 800 + x) * 3 + 3])
        x, y, w, h = ui.current.buttons["start"][0]
        upright = (x + w // 2, y + h // 2)
        at = (upright[1], geometry.height - 1 - upright[0])  # where the turned panel reports it
        self.finger(pipe, False)
        self.assertIsNone(ui.wait(timeout=0.01), "a finger put down before the screen taps nothing")
        self.finger(pipe, True, *at)
        self.assertIsNone(ui.wait(timeout=0.01), "nothing is tapped until the finger lifts")
        self.finger(pipe, False)
        self.assertEqual(ui.wait(timeout=1), "start")
        self.finger(pipe, True, *at)
        self.finger(pipe, False)
        self.assertIsNone(ui.wait(["quit"], timeout=0.01), "a button the caller doesn't wait for")

    def test_an_update_writes_only_what_changed_and_leaves_the_whole_screen_right(self):
        geometry = update_screen.Geometry(800, 480, 4, ((16, 8), (8, 8), (0, 8)), 3200, 0)
        panel, framebuffer = self.panel(geometry)
        ui = touch_ui.TouchUi(panel, None)
        screen, fields = touch_ui.EXAMPLES["08-installing"]
        ui.show(screen, **fields)
        writes = []
        original = panel.write
        panel.write = lambda canvas, *rect: writes.append(rect) or original(canvas, *rect)
        ui.update(header_step="Step 4 of 10 · 1:53")
        self.assertTrue(writes and all(w * h < 2000 for _, _, w, h in writes), writes)
        ui.update(step="Fluidd and Valve Oil UI", index=5)
        expected, expected_path = self.panel(geometry)
        expected.write(touch_ui.compose(screen, **{**fields, "header_step": "Step 4 of 10 · 1:53",
                                                   "step": "Fluidd and Valve Oil UI", "index": 5}).canvas,
                       0, 0, 800, 480)
        self.assertEqual(framebuffer.read_bytes(), expected_path.read_bytes())
        with self.assertRaises(touch_ui.TouchUnavailable):
            ui.wait()

    def test_a_screen_that_cannot_be_composed_is_drawn_as_text(self):
        ui = touch_ui.TouchUi(None, None, device="/nonexistent/fb0")
        drawn = []
        with mock.patch.object(touch_ui, "load_assets", side_effect=FileNotFoundError("touch-ui.bin")), \
                mock.patch.object(update_screen, "draw", lambda lines, **kwargs: drawn.append(lines) or True):
            self.assertEqual(ui.show("installing", **touch_ui.EXAMPLES["08-installing"][1]), ())
        self.assertIn((4, update_screen.TEXT, "Installing"), drawn[0])

    def test_qr_codes_match_known_encodings(self):
        # Matrices qrencode 4.1 draws for the same text, level, and version.
        issue = ("https://github.com/thelegendtubaguy/valve-oil/issues/new?template=printer-support.yml&printer=QIDI+Max+4"
                 "&firmware=01.01.07.01&board_report=%22")
        for text, level, size, digest in (
                ("https://github.com/thelegendtubaguy/valve-oil", "M", 33,
                 "2d609b142090f652d57b62e96f7b7e5869e646e3a5903dd818ef2de4982a58c3"),
                (issue + "ab12" * 57, "L", 69, "076e7d8897a5b2b82e305295178b8a19ecd6633d37872fc96f052109284f106d"),
                (issue + "ab12" * 97, "L", 81, "9934284389a5ba3c6e2f19c46d6746b3e3ce8464a79c246480f06b751a87a431")):
            with self.subTest(length=len(text)):
                modules = qr_code.encode(text, level)
                self.assertEqual(len(modules), size)
                bits = "".join("1" if dark else "0" for row in modules for dark in row)
                self.assertEqual(hashlib.sha256(bits.encode()).hexdigest(), digest)
        with self.assertRaises(qr_code.DataTooLong):
            qr_code.encode("x" * 3000, "M")

    @unittest.skipUnless(shutil.which("zbarimg"), "zbarimg is not installed")
    def test_the_report_code_on_screen_scans(self):
        screen, fields = touch_ui.EXAMPLES["15b-board-not-supported"]
        image = temp_path("touch-ui-qr") / "screen.png"
        image.write_bytes(touch_ui.encode_png(800, 480, bytes(touch_ui.compose(screen, **fields).canvas.pixels)))
        out = subprocess.run(["zbarimg", "-q", "--raw", str(image)], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), fields["qr_url"])


if __name__ == "__main__":
    unittest.main()
