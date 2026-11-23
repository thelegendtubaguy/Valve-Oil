"""What the installed macros must hold that takes computing: moves checked against each printer's travel, cuts
compared with QIDI's, and rules that span every installed file. The order of commands inside a macro, and commands a
macro must never issue, are path contracts (openspec/contracts/gcode-paths/, scripts/check_gcode_paths.py)."""
from __future__ import annotations

import re
import types
import unittest

from installer.tests.helpers import REPO_ROOT
from scripts.klipper_macro_source import (
    PRINTER_MACRO_ROOT,
    extract_gcode_lines,
    installed_macro_files,
    iter_sections,
    parse_macro_definitions,
)


MAX4_MACRO_ROOT = PRINTER_MACRO_ROOT / "max4"
NEWEST_STOCK = {
    "max4": REPO_ROOT / "installer/stock/qidi-max4-defaults/firmwares/01.01.07.01",
    "plus5": REPO_ROOT / "installer/stock/qidi-plus5-defaults/firmwares/01.01.02.01",
    "smart5": REPO_ROOT / "installer/stock/qidi-smart5-defaults/firmwares/01.01.02.01",
}
# Behind the bed some printers' toolheads fit only around the chute: (rear Y, lowest X, highest X).
REAR_CHUTE_WINDOW = {
    # Behind Y320 the Plus 5's toolhead can only be between X75 and X231 (the Max 4's rear is open in X).
    "plus5": (320, 75, 231),
    # Behind the Smart 5's bed QIDI moves only between X66 and X100.
    "smart5": (180, 66, 100),
}
TRAVEL = {"plus5": {"X": (-7, 326), "Y": (-5, 340)}, "smart5": {"X": (-2, 185), "Y": (-4, 200)}}


class MacroContractTests(unittest.TestCase):
    def setUp(self):
        files = installed_macro_files("max4")
        self.macros, duplicates = parse_macro_definitions(files)
        if duplicates:
            self.fail("Optimized macro duplicate definitions are invalid for this test.")

    def test_probe_signal_check_cannot_issue_motion_heating_or_calibration_commands(self):
        gcode = self._macro_gcode("VO_TEST_PROBE")
        commands = set(re.findall(r"^\s*([A-Za-z_][A-Za-z_0-9]*)(?=\s|$)", gcode, re.MULTILINE))
        self.assertTrue(commands)
        self.assertLessEqual(
            {command.upper() for command in commands},
            {"_VO_TEST_PROBE"},
        )

    def test_max4_vendor_cut_and_chute_moves_follow_newest_stock(self):
        def commands(text):
            return [line.split("#")[0].strip() for line in text.splitlines() if line.split("#")[0].strip()]

        def chute_path(lines):
            # The run to the chute entrance is faster than QIDI's; the path and the push into the chute are QIDI's.
            return [
                line.replace("F{opt.trash_travel_speed_xy}", "F{km.travel_speed_xy}")
                for line in lines
                if not line.startswith("M204") and "_vo_globals" not in line
            ]

        stock_root = NEWEST_STOCK["max4"] / "config/klipper-macros-qd"
        stock_cut = commands(self._section_gcode(stock_root / "filament.cfg", "gcode_macro CUT_FILAMENT_1"))
        self.assertEqual(stock_cut[-1], "G4 P2000")
        motion = MAX4_MACRO_ROOT / "nozzle_motion.cfg"
        cut = self._expand_cutter_strike(motion, self._section_gcode(motion, "gcode_macro CUT_FILAMENT_1"))
        self.assertEqual(commands(cut), stock_cut[:-1])

        stock_chute = commands(self._section_gcode(stock_root / "qd_macro.cfg", "gcode_macro MOVE_TO_TRASH"))
        chute = commands(self._section_gcode(MAX4_MACRO_ROOT / "nozzle_motion.cfg", "gcode_macro MOVE_TO_TRASH"))
        self.assertEqual(chute_path(chute), chute_path(stock_chute))
        accel = None
        for line in chute:
            if line.startswith("M204"):
                accel = line
            elif "F2000" in line:
                self.assertEqual(accel, "M204 S10000", line)

    def test_plus5_cut_presses_the_cutter_once(self):
        for section in ("gcode_macro CUT_FILAMENT_1", "gcode_macro _VO_CUT_FILAMENT_MOTION"):
            with self.subTest(section=section):
                motion = PRINTER_MACRO_ROOT / "plus5/nozzle_motion.cfg"
                gcode = self._expand_cutter_strike(motion, self._section_gcode(motion, section))
                xs = [float(x) for x in re.findall(r"^\s*G1 X(-?[\d.]+)", gcode, re.M)]
                strikes = sum(1 for before, after in zip([15.0] + xs, xs) if after <= 0 < before)
                self.assertEqual(strikes, 1, gcode)
                self.assertNotIn("G4 P1000", gcode)

    def test_cuts_press_the_cutter_only_through_the_strike(self):
        # _VO_CUTTER_STRIKE lets the pull-back through with the nozzle cold and leaves the cutter after an error,
        # so neither can leave the toolhead pressed against the cutter.
        for printer in NEWEST_STOCK:
            for section in ("gcode_macro CUT_FILAMENT_1", "gcode_macro _VO_CUT_FILAMENT_MOTION"):
                with self.subTest(printer=printer, section=section):
                    gcode = self._section_gcode(PRINTER_MACRO_ROOT / printer / "nozzle_motion.cfg", section)
                    self.assertEqual(len(re.findall(r"^\s*_VO_CUTTER_STRIKE EXIT_X=\S+ EXIT_F=\S+\s*$", gcode, re.M)), 1)
                    self.assertNotRegex(gcode, r"(?m)^\s*G1\b.*\sE-?[\d.]")

    def test_printer_folders_define_the_same_sections(self):
        # Driver sections only add options to the printer's own stock driver sections, which differ by printer.
        # Bed screw positions are set up only for printers whose screws have been measured, and the mainboard
        # temperature sensor only on the GD32F425 mainboards (not the Smart 5's).
        mainboard_sensor = {"adc_temperature gd32f425", "temperature_sensor mainboard_mcu"}
        sections = {}
        for printer, stock_root in NEWEST_STOCK.items():
            stock = {
                name.lower()
                for name, _, _ in iter_sections((stock_root / "config/printer.cfg").read_text(encoding="utf-8").splitlines())
            }
            names = [
                name.lower()
                for path in (PRINTER_MACRO_ROOT / printer).glob("*.cfg")
                for name, _, _ in iter_sections(path.read_text(encoding="utf-8").splitlines())
            ]
            drivers = {name for name in names if name.startswith("tmc")}
            with self.subTest(printer=printer):
                self.assertLessEqual(drivers, stock)
            sections[printer] = sorted(name for name in names if name not in drivers and name != "screws_tilt_adjust"
                                       and name not in mainboard_sensor)
            with self.subTest(printer=printer):
                self.assertEqual(mainboard_sensor <= set(names), printer != "smart5")
        self.assertEqual(sections["plus5"], sections["max4"])
        self.assertEqual(sections["smart5"], sections["max4"])

    def test_nozzle_moves_stay_inside_the_printers_travel(self):
        for printer, limits in TRAVEL.items():
            with self.subTest(printer=printer):
                self._assert_nozzle_moves_inside(printer, limits)

    def _assert_nozzle_moves_inside(self, printer, limits):
        motion = PRINTER_MACRO_ROOT / printer / "nozzle_motion.cfg"
        text = self._expand_cutter_strike(motion, motion.read_text(encoding="utf-8"))
        profile = (PRINTER_MACRO_ROOT / printer / "printer_profile.cfg").read_text(encoding="utf-8")
        relative = False
        for line in text.splitlines():
            command = line.split("#")[0].strip()
            if command in ("G90", "G91"):
                relative = command == "G91"
            if relative or not command.startswith(("G0 ", "G1 ")):
                continue
            for axis, value in re.findall(r"\b([XY])(-?\d+(?:\.\d+)?)\b", command):
                low, high = limits[axis]
                self.assertTrue(low <= float(value) <= high, line)
        for axis, value in re.findall(r"^variable_\w+_([xy]): (-?[\d.]+)$", profile, re.M):
            low, high = limits[axis.upper()]
            self.assertTrue(low <= float(value) <= high, f"{axis} {value}")

    def test_rear_moves_stay_inside_the_chute_x_window(self):
        for printer, window in REAR_CHUTE_WINDOW.items():
            with self.subTest(printer=printer):
                self._assert_rear_moves_inside(printer, *window)

    def _assert_rear_moves_inside(self, printer, rear_y, x_low, x_high):
        globals_cfg = (NEWEST_STOCK[printer] / "config/klipper-macros-qd/globals.cfg").read_text(encoding="utf-8")
        km = types.SimpleNamespace(**{
            name: float(re.search(rf"^variable_{name}: *([\d.]+)", globals_cfg, re.M).group(1))
            for name in ("park_x", "park_y")
        })
        km.print_max = tuple(map(float, re.search(r"^variable_print_max: *\((\d+), *(\d+)\)", globals_cfg, re.M | re.I).groups()))
        motion = PRINTER_MACRO_ROOT / printer / "nozzle_motion.cfg"
        bodies = {
            match.group(1): match.group(2)
            for match in re.finditer(
                r"^\[gcode_macro (\S+)\]\n(.*?)(?=^\[|\Z)",
                self._expand_cutter_strike(motion, motion.read_text(encoding="utf-8")),
                re.M | re.S,
            )
        }
        # The strike runs only inside the cuts, where it is inlined above.
        del bodies["_VO_CUTTER_STRIKE_MOTION"]
        # Pure-motion entry points the common macros and slicer packs call.
        calls = {
            "VO_WIPE_NOZZLE_DROP": ["_VO_WIPE_NOZZLE", "_VO_CHUTE_DROP"],
            "_VO_FINISH_START_SCRAPE": ["OPTIMIZED_MOVE_TO_TRASH", "VO_WIPE_NOZZLE_DROP"],
            "OPTIMIZED_MOVE_TO_TRASH": ["_VO_MOVE_TO_TRASH_MOTION"],
        }

        def check(name, start, end):
            (x0, y0), (x1, y1) = start, end
            points = [(x, y) for x, y in (start, end) if y > rear_y]
            if (y0 > rear_y) != (y1 > rear_y):
                points.append((x0 + (x1 - x0) * (rear_y - y0) / (y1 - y0), rear_y))
            for x, y in points:
                self.assertTrue(x_low <= x <= x_high, f"{name}: X{x:g} Y{y:g} on {start} -> {end}")

        def branch(condition, position):
            # Conditions on the toolhead position are evaluated; any other condition runs its block.
            if position is None or "gcode_move.position" not in condition:
                return True
            where = types.SimpleNamespace(x=position[0], y=position[1])
            printer = types.SimpleNamespace(gcode_move=types.SimpleNamespace(position=where))
            return bool(eval(condition, {"printer": printer, "km": km}))

        def run(name, text, position):
            relative = False
            active = []
            for line in text.splitlines():
                command = line.split("#")[0].split(";")[0].strip()
                block = re.fullmatch(r"\{%-? *(if|elif|else|endif|for|endfor)\b(.*?)-?%\}", command)
                if block:
                    keyword, condition = block.group(1), block.group(2).strip()
                    if keyword == "if":
                        taken = branch(condition, position)
                        active.append([taken, taken])
                    elif keyword in ("elif", "else") and active:
                        run_it = not active[-1][1] and (keyword == "else" or branch(condition, position))
                        active[-1] = [run_it, active[-1][1] or run_it]
                    elif keyword == "endif" and active:
                        active.pop()
                    continue
                if not all(state[0] for state in active):
                    continue
                word = command.split(" ")[0]
                if word in calls:
                    for called in calls[word]:
                        position = run(called, bodies.get(called, called), position)
                elif word in bodies:
                    position = run(word, bodies[word], position)
                elif command in ("G90", "G91"):
                    relative = command == "G91"
                elif word in ("G0", "G1"):
                    target = list(position) if position else [None, None]
                    for axis, value in re.findall(r"\b([XY])(\{[^}]*\}|-?[\d.]+)", command):
                        index = "XY".index(axis)
                        try:
                            number = float(eval(value.strip("{}"), {"km": km}) if value.startswith("{") else value)
                        except Exception:
                            target[index] = None
                            continue
                        if relative:
                            target[index] = None if target[index] is None else target[index] + number
                        else:
                            target[index] = number
                    target = tuple(target) if None not in target else None
                    if position and target:
                        check(name, position, target)
                    position = target
            return position

        # Unknown, mid-bed, parked in the chute, and at the wipe's far end.
        center = km.print_max[0] / 2
        wipe_end = {"plus5": (140.0, km.park_y), "smart5": (66.0, km.park_y)}[printer]
        starts = (None, (center, center), (km.park_x, km.park_y), wipe_end)
        for name in bodies:
            for start in starts:
                with self.subTest(macro=name, start=start):
                    run(name, bodies[name], start)
        for pack in (f"orcaslicer_gcode/{printer}", f"qidistudio_gcode/{printer}"):
            for path in sorted((REPO_ROOT / pack).glob("*.gcode")):
                with self.subTest(file=f"{pack}/{path.name}"):
                    run(path.name, path.read_text(encoding="utf-8"), None)

    def test_macros_do_not_copy_large_status_objects(self):
        # Klipper deep-copies an object's whole status on a macro's first read of it, on its main
        # thread; these take 9-50 ms each, against QIDI's 50 ms Box endstop sync window.
        large = re.compile(r'printer(?:\.|\[")(?:configfile|gcode|multi_color_controller|box_extras)\b')
        for path in installed_macro_files("max4"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertEqual(large.findall(text), [])
                self.assertEqual(
                    text.count("set box_available ="),
                    text.count("{% set box_available = printer.vo_macro_status.box_available %}"),
                )

    def test_plus5_macros_have_no_closed_loop_commands(self):
        # The Plus 5 homes sensorless; QIDI's closed-loop commands exist only on the Max 4's drivers.
        text = "\n".join(path.read_text(encoding="utf-8") for path in (PRINTER_MACRO_ROOT / "plus5").glob("*.cfg"))
        for command in ("ENFORCE_MODE", "SET_HOMING_MODE", "SET_HOMING_STATE", "cl_interface", "closed_loop"):
            self.assertNotIn(command, text)

    def test_only_the_raw_z_home_issues_klippers_own_z_home(self):
        # G28.6245197 is Klipper's G28 under homing_override; a second caller would home Z outside the safe point.
        for printer in NEWEST_STOCK:
            with self.subTest(printer=printer):
                text = "\n".join(path.read_text(encoding="utf-8") for path in installed_macro_files(printer))
                self.assertEqual(text.count("G28.6245197 Z"), 1)

    def test_nothing_installed_depends_on_qidis_compiled_cleanup_or_smart_pins(self):
        # QIDI 01.01.07.01 flows no longer call the compiled chute cleanup commands, and firmware 01.01.06.05
        # replaced the smart_output_pin sections, so neither may be relied on or redefined.
        for printer in NEWEST_STOCK:
            packs = (f"orcaslicer_gcode/{printer}", f"qidistudio_gcode/{printer}")
            files = (*installed_macro_files(printer),
                     *(path for pack in packs for path in sorted((REPO_ROOT / pack).glob("*.gcode"))))
            for path in files:
                text = path.read_text(encoding="utf-8")
                for forbidden in ("CLEAR_FLUSH", "CLEAR_OOZE", "[smart_output_pin polar_cooler]",
                                  "[smart_output_pin beeper]"):
                    with self.subTest(printer=printer, file=path.name, forbidden=forbidden):
                        self.assertNotIn(forbidden, text)

    def _macro_gcode(self, name: str) -> str:
        macro = self.macros[name]
        return "\n".join(line for _, line in macro.gcode_lines)

    def _expand_cutter_strike(self, path, text: str) -> str:
        """Inlines _VO_CUTTER_STRIKE as what it runs: the printer's strike macro, then the move off the cutter."""
        strike = [line.strip() for line in self._section_gcode(path, "gcode_macro _VO_CUTTER_STRIKE_MOTION").splitlines()]

        def inline(match):
            lines = [line for line in strike if line] + [f"G1 X{match.group(2)} F{match.group(3)}"]
            return "\n".join(match.group(1) + line for line in lines)

        return re.sub(r"^([ \t]*)_VO_CUTTER_STRIKE EXIT_X=(\S+) EXIT_F=(\S+)[ \t]*$", inline, text, flags=re.M)

    def _section_gcode(self, path, name: str) -> str:
        lines = path.read_text(encoding="utf-8").splitlines()
        for section_name, header_index, end_index in iter_sections(lines):
            if section_name.lower() == name.lower():
                return "\n".join(line for _, line in extract_gcode_lines(lines, header_index + 1, end_index))
        self.fail(f"Missing section [{name}] in {path}")
