from __future__ import annotations

import unittest
from pathlib import Path

from installer.runtime import diagnostics
from installer.tests.helpers import FIXTURES_ROOT, temp_path

LOGS = FIXTURES_ROOT / "diagnostics"
# Lines box_flow_recovery writes for an unload from the toolhead (26.11.22.2 and later): one whose pull-backs Klipper
# dropped inside a cancelled part, and one that cleared the toolhead sensor.
STAMP = "2026-11-22 12:22:34 - "
DROPPED_UNLOAD = [
    STAMP + "vo_box_flow_recovery: Box unload of 1A stopped: Klipper didn't move the extruder for the 105 mm of "
            "pull-back QIDI's unload asked for because the print is inside part_2, a cancelled part, where Klipper "
            "drops every move, so the toolhead filament sensor still detects filament and the Box hasn't pulled it. "
            "Check that the extruder turns.",
    STAMP + "vo_box_flow_recovery: 1A unload from the toolhead: toolhead sensor filament -> filament; hub sensor "
            "filament -> filament; extruder pulled back 105 mm, 20 of them after its pull-back, and moved 0 mm; slot "
            "motor moved 0 mm during the pulls; Box buffer 0 feeds before it since the last unload from the toolhead, "
            "0 during it, switch released; raised CommandError: Box unload of 1A stopped",
]
CLEARED_UNLOAD = [
    STAMP + "vo_box_flow_recovery: 1C unload from the toolhead: toolhead sensor filament -> clear, cleared after 85 mm; "
            "hub sensor filament -> clear; extruder pulled back 85 mm, 0 of them after its pull-back, and moved 85 mm; "
            "slot motor moved 85 mm during the pulls; Box buffer 3 feeds before it since the last unload from the "
            "toolhead, 0 during it, switch released; returned True",
]


def _log(lines: list[str], version: str = "26.11.22.2") -> Path:
    path = temp_path("vo-diagnostics-") / "klippy.log"
    path.write_text("\n".join([
        "2026-11-22 12:00:00 - Start printer at Sun Nov 22 12:00:00 2026 (1795347216.0 17.3)",
        f'variable_package_version = "{version}"',
        *lines,
    ]) + "\n")
    return path


def _found(path: Path, **options) -> dict[str, dict]:
    return {finding["id"]: finding for finding in diagnostics.check([path], **options)["findings"]}


class SignatureTest(unittest.TestCase):
    """Each signature against a log that shows it and one that doesn't; a signature ships only with both."""

    def test_signatures_in_logs_that_show_them(self) -> None:
        for name, path, expected in (
            ("auto-feed re-armed during a feed, an off step clock, and the unloads it stopped",
             LOGS / "step-clock-offset.log",
             {"autofeed_rearmed_during_feed", "box_step_clock_offset", "unload_toolhead_sensor_stuck", "qidi_error"}),
            ("pull-backs dropped inside a cancelled part", _log(DROPPED_UNLOAD),
             {"cancelled_part_dropped_moves"}),
        ):
            with self.subTest(name):
                self.assertEqual(set(_found(path)), expected)

    def test_logs_that_dont_show_them(self) -> None:
        for name, path in (
            ("feeds that keep their clock, and an idle re-arm", LOGS / "box-feeds-normal.log"),
            ("an unload whose pull-backs moved", _log(CLEARED_UNLOAD)),
        ):
            with self.subTest(name):
                self.assertEqual(_found(path), {})

    def test_step_clock_offset_names_how_far_off(self) -> None:
        finding = _found(LOGS / "step-clock-offset.log")["box_step_clock_offset"]
        self.assertEqual(finding["details"], ["Box 1 slot stepper oid 1: 21.8 s ahead of Klipper's"])
        self.assertTrue(finding["docs"].startswith(diagnostics.DOCS_URL + "#"))

    def test_severity_follows_the_release_that_ran_and_the_one_installed(self) -> None:
        offset = LOGS / "step-clock-offset.log"
        self.assertEqual(_found(offset)["box_step_clock_offset"]["severity"], "fault")
        self.assertEqual(_found(offset, installed="26.11.22.2")["box_step_clock_offset"]["severity"], "fixed")
        # A session whose unloads reset the slot clock repaired it, whatever release its config named.
        lines = offset.read_text().splitlines()
        repaired = _log([*lines, "2026-11-22 13:00:28 - vo_box_flow_recovery: 1A step clock reset before the unload's "
                                 "pull-back"], version="26.11.22.1")
        found = _found(repaired)
        self.assertEqual((found["box_step_clock_offset"]["severity"], found["box_step_clock_offset"]["repaired"]),
                         ("repaired", 1))
        # QIDI's error codes are for reference only.
        self.assertEqual(found["qidi_error"]["severity"], "info")


class BudgetTest(unittest.TestCase):
    """A check reads the newest part of the logs within its budget, and says how far back it reached."""

    def setUp(self) -> None:
        offset = (LOGS / "step-clock-offset.log").read_text()
        filler = "".join(f"2026-11-22 13:{minute:02d}:00 - Stats {minute}: gcodein=0 mcu: mcu_awake=0.04\n"
                         for minute in range(60)) * 40
        self.path = temp_path("vo-diagnostics-") / "klippy.log"
        self.path.write_text(offset + filler + "2026-11-22 14:00:00 - Start printer at Sun Nov 22 14:00:00 2026\n"
                             + filler.replace(" 13:", " 14:"))
        self.size = self.path.stat().st_size

    def test_the_whole_log_within_the_budget(self) -> None:
        result = diagnostics.check([self.path])
        self.assertTrue(result["checked"]["complete"])
        self.assertIn("box_step_clock_offset", {f["id"] for f in result["findings"]})

    def test_a_log_larger_than_the_budget_is_read_from_its_newest_part(self) -> None:
        result = diagnostics.check([self.path], byte_budget=self.size // 3)
        self.assertFalse(result["checked"]["complete"])
        self.assertGreater(result["checked"]["from"], "2026-11-22 13:00:00")
        self.assertEqual(result["findings"], [])
        self.assertIn("not the whole period", diagnostics.report(result))

    def test_since_klipper_last_started(self) -> None:
        result = diagnostics.check([self.path], since="start")
        self.assertTrue(result["checked"]["complete"])
        self.assertEqual(result["checked"]["from"], "2026-11-22 14:00:00")
        self.assertLess(result["checked"]["files"][0]["bytes_read"], self.size // 2)

    def test_a_check_that_runs_out_of_time_says_so(self) -> None:
        ticks = iter(range(0, 10**6, 100))
        result = diagnostics.check([self.path], time_budget=1, clock=lambda: next(ticks))
        self.assertEqual((result["checked"]["stopped"], result["checked"]["complete"]), ("time", False))


if __name__ == "__main__":
    unittest.main()
