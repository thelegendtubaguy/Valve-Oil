from __future__ import annotations

import fcntl
import json
import unittest
from pathlib import Path

from installer.runtime import box_config
from installer.tests.helpers import REPO_ROOT, temp_path

MONITOR = box_config.monitor()


class Printer:
    """printer_data and /dev/serial/by-id, with a clock the monitor's waits advance and events at given times."""

    def __init__(self, box_cfg: str):
        root = temp_path("box-monitor-")
        self.data = root / "printer_data"
        (self.data / "config").mkdir(parents=True)
        self.box_cfg = self.data / "config/box.cfg"
        self.box_cfg.write_text(box_cfg, encoding="utf-8")
        self.by_id = root / "by-id"
        self.by_id.mkdir()
        (self.by_id / "usb-Klipper_QIDI_MAIN_V2_1.0.12_M41N-if00").write_text("")
        self.now, self.events, self.restarts, self.busy = 0.0, [], 0, None

    def box(self, serial: str) -> str:
        return str(self.by_id / f"usb-Klipper_QIDI_BOX_V2_1.2.1_{serial}-if00")

    def plug(self, *serials: str) -> None:
        for serial in serials:
            Path(self.box(serial)).write_text("")

    def unplug(self, *serials: str) -> None:
        for serial in serials:
            Path(self.box(serial)).unlink()

    def at(self, seconds: float, action) -> None:
        self.events.append((self.now + seconds, action))

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        for event in [event for event in self.events if event[0] <= self.now]:
            self.events.remove(event)
            event[1]()

    def run(self, *, patched=True):
        def restart():
            self.restarts += 1

        return MONITOR.run(str(self.data), boxes_now=lambda: MONITOR.connected(str(self.by_id)),
                           patched=lambda: patched, busy=lambda: self.busy, restart=restart,
                           clock=lambda: self.now, sleep=self.sleep, say=lambda message: None)

    def record(self) -> dict:
        return json.loads((self.data / MONITOR.RECORD).read_text())["boxes"]

    def waiting(self) -> dict | None:
        path = self.data / MONITOR.STATE
        return json.loads(path.read_text())["waiting"] if path.exists() else None


class BoxConfigTests(unittest.TestCase):
    def test_box_cfg_follows_the_boxes_in_qidis_layout_and_each_box_keeps_its_number(self):
        # QIDI's touchscreen app's box.cfg for two Boxes, with their serials redacted, is exactly what Valve Oil writes.
        qidi = (REPO_ROOT / "config/box.cfg").read_text(encoding="utf-8")
        redacted = qidi.split("serial: ")[1].split("\n")[0]
        self.assertEqual(MONITOR.generate([redacted, redacted]), qidi)
        self.assertEqual(MONITOR.generate([]), "# No Box Config\n")

        # An existing printer: the Boxes keep the numbers box.cfg gives them, against the order of their names, and
        # nothing is written or restarted.
        printer = Printer("")
        printer.plug("AAA", "BBB")
        printer.box_cfg.write_text(MONITOR.generate([printer.box("BBB"), printer.box("AAA")]), encoding="utf-8")
        before = printer.box_cfg.read_text()
        printer.run()
        self.assertEqual((printer.box_cfg.read_text(), printer.restarts), (before, 0))
        self.assertEqual(printer.record(), {printer.box("BBB"): 1, printer.box("AAA"): 2})
        self.assertEqual(oct((printer.data / MONITOR.RECORD).stat().st_mode & 0o777), "0o600")

        # A Box board reset drops both Boxes off USB for a few seconds: the list returns to what box.cfg holds.
        printer.unplug("AAA", "BBB")
        printer.at(1.0, lambda: printer.plug("BBB"))
        printer.at(4.0, lambda: printer.plug("AAA"))
        printer.run()
        self.assertEqual((printer.box_cfg.read_text(), printer.restarts), (before, 0))

        # Box 1 is disconnected: box.cfg lists Box 2 alone, without a gap, and Klipper restarts once.
        printer.unplug("BBB")
        printer.run()
        self.assertEqual(printer.box_cfg.read_text(), MONITOR.generate([printer.box("AAA")]))
        self.assertEqual(printer.restarts, 1)
        self.assertEqual((printer.data / MONITOR.PREVIOUS).read_text(), before)

        # It comes back, on whatever port: both Boxes have their numbers and slots again.
        printer.plug("BBB")
        printer.run()
        self.assertEqual(printer.box_cfg.read_text(), before)

        # A new Box takes the lowest number no connected Box has: Box 1's while Box 1 is away.
        printer.unplug("BBB")
        printer.plug("CCC")
        printer.run()
        self.assertEqual(printer.box_cfg.read_text(), MONITOR.generate([printer.box("CCC"), printer.box("AAA")]))
        self.assertEqual(printer.record(), {printer.box("CCC"): 1, printer.box("AAA"): 2})

        # Every Box gone.
        printer.unplug("AAA", "CCC")
        printer.run()
        self.assertEqual((printer.box_cfg.read_text(), printer.restarts), ("# No Box Config\n", 4))

    def test_a_box_change_waits_for_an_idle_printer_and_for_board_work(self):
        printer = Printer("# No Box Config\n")
        printer.plug("AAA")
        lock = (printer.data / MONITOR.INSTALLER_LOCK).open("a+")
        self.addCleanup(lock.close)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        printer.busy = "a print is running"

        def install_ends():
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

        def print_ends():
            # Meanwhile nothing changed, and a printer warning said why.
            self.assertEqual(printer.box_cfg.read_text(), "# No Box Config\n")
            self.assertEqual(printer.waiting(), {"reason": "a print is running", "boxes": 1})
            printer.busy = None

        printer.at(60, install_ends)
        printer.at(600, print_ends)
        printer.run()
        self.assertGreaterEqual(printer.now, 600)
        self.assertEqual(printer.box_cfg.read_text(), MONITOR.generate([printer.box("AAA")]))
        self.assertEqual(printer.restarts, 1)
        self.assertIsNone(printer.waiting())

        # A firmware update or a board waiting for its rescue owns the boards until it ends.
        printer.unplug("AAA")
        rescue = printer.data / ".vo_box_firmware_pending.json"
        rescue.write_text("{}")
        printer.at(30, rescue.unlink)
        printer.run()
        self.assertGreaterEqual(printer.now, 630)
        self.assertEqual((printer.box_cfg.read_text(), printer.restarts), ("# No Box Config\n", 2))

    def test_box_cfg_is_left_to_the_app_or_the_operator_when_valve_oil_doesnt_own_it(self):
        # QIDI's firmware package installs a box.cfg of comments, which lists no Box.
        printer = Printer("# [include box1.cfg]\r\n# [include box2.cfg]\r\n# [include box3.cfg]\r\n# [include box4.cfg]")
        printer.plug("AAA")
        package_file = printer.box_cfg.read_text()
        # A touchscreen app without Valve Oil's patch keeps writing box.cfg itself.
        printer.run(patched=False)
        self.assertEqual((printer.box_cfg.read_text(), printer.restarts), (package_file, 0))
        printer.run()
        self.assertEqual(printer.box_cfg.read_text(), MONITOR.generate([printer.box("AAA")]))
        # A box.cfg in another layout, such as one edited by hand, stays as it is.
        edited = MONITOR.generate([printer.box("AAA")]) + "[include my_box.cfg]\n"
        printer.box_cfg.write_text(edited, encoding="utf-8")
        printer.unplug("AAA")
        printer.run()
        self.assertEqual((printer.box_cfg.read_text(), printer.restarts), (edited, 1))


if __name__ == "__main__":
    unittest.main()
