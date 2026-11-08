from __future__ import annotations

import importlib.util
import sys
import types
import unittest

from installer.tests.helpers import REPO_ROOT


class CommandError(Exception):
    pass


class Move:
    def calc_junction(self, prev_move):
        return "qidi"


class ToolHead:
    def cmd_M204(self, gcmd):
        return "qidi"


class TestAxis:
    def __init__(self, axis):
        self.vib_dir = (1., 0.) if axis == "x" else (0., 1.)

    def get_point(self, length):
        return (self.vib_dir[0] * length, self.vib_dir[1] * length)


class VibrationPulseTest:
    def __init__(self):
        self.accel_per_hz = 150.
        self.hz_per_sec = 1.
        self.runs = []

    def prepare_test(self, gcmd):
        self.freq_start = gcmd.get_float("FREQ_START", 5.)
        self.freq_end = gcmd.get_float("FREQ_END", 133.33)

    def get_start_test_points(self):
        return [(195., 195., 10.)]

    def get_max_freq(self):
        return self.freq_end

    def run_test(self, axis, gcmd):
        self.runs.append(self.accel_per_hz)


class ResonanceTester:
    def __init__(self):
        self.test = VibrationPulseTest()

    def _run_test(self, gcmd, axes, helper):
        return None

    def _get_max_calibration_freq(self):
        return 1.5 * self.test.get_max_freq()


class CalibrationData:
    def normalize_to_frequencies(self):
        return "qidi"


def _load():
    """Import tltg_resonance_sweep with stand-ins for the QIDI code it
    wraps."""
    saved = {name: sys.modules.get(name)
             for name in ("toolhead", "extras", "extras.resonance_tester",
                          "extras.shaper_calibrate")}
    sys.modules["toolhead"] = types.SimpleNamespace(Move=Move, ToolHead=ToolHead)
    package = types.ModuleType("extras")
    package.__path__ = []
    package.resonance_tester = types.SimpleNamespace(
        VibrationPulseTest=VibrationPulseTest, ResonanceTester=ResonanceTester)
    package.shaper_calibrate = types.SimpleNamespace(
        CalibrationData=CalibrationData, MIN_FREQ=5.)
    sys.modules["extras"] = package
    try:
        spec = importlib.util.spec_from_file_location(
            "extras.tltg_resonance_sweep", REPO_ROOT / "installer/klipper/tltg/resonance_sweep.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


resonance_sweep = _load()
STOCK = {
    "VibrationPulseTest": VibrationPulseTest,
    "ResonanceTester._run_test": ResonanceTester._run_test,
    "ResonanceTester._get_max_calibration_freq": ResonanceTester._get_max_calibration_freq,
    "Move.calc_junction": Move.calc_junction,
    "ToolHead.cmd_M204": ToolHead.cmd_M204,
    "CalibrationData.normalize_to_frequencies": CalibrationData.normalize_to_frequencies,
}


class GCmd:
    error = CommandError

    def __init__(self, **params):
        self.params = params
        self.messages = []

    def get_float(self, name, default, **kwargs):
        return float(self.params.get(name, default))

    def get_int(self, name, default, **kwargs):
        return int(self.params.get(name, default))

    def respond_info(self, msg):
        self.messages.append(msg)


class FakeToolHead:
    """Records where the test sends the toolhead."""

    def __init__(self, fail_after=None):
        self.max_accel = 20000.
        self.max_accel_cap = 30000.
        self.position = [195., 195., 10., 0.]
        self.positions = []
        self.fail_after = fail_after
        self.lookahead = types.SimpleNamespace(get_last=lambda: None)

    def get_position(self):
        return list(self.position)

    def get_status(self, eventtime):
        return {"max_accel": self.max_accel, "minimum_cruise_ratio": .5}

    def cmd_M204(self, params):
        self.max_accel = min(params["S"], self.max_accel_cap)

    def move(self, newpos, speed):
        if self.fail_after is not None and len(self.positions) >= self.fail_after:
            raise CommandError("Move out of range")
        self.position = list(newpos)
        self.positions.append(tuple(newpos))

    def dwell(self, delay):
        pass


class Printer:
    def __init__(self, variables=None, toolhead=None):
        self.toolhead = toolhead or FakeToolHead()
        self.scripts = []
        self.tester = ResonanceTester()
        gcode = types.SimpleNamespace(
            create_gcode_command=lambda cmd, line, params: params,
            run_script_from_command=self.scripts.append)
        self.objects = {"toolhead": self.toolhead, "gcode": gcode,
                        "resonance_tester": self.tester}
        if variables is not None:
            self.objects["save_variables"] = types.SimpleNamespace(
                get_status=lambda eventtime: {"variables": variables})
        self.handlers = {}

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_reactor(self):
        return types.SimpleNamespace(monotonic=lambda: 0., pause=lambda waketime: None)

    def register_event_handler(self, event, callback):
        self.handlers[event] = callback

    def connect(self):
        resonance_sweep.load_config(types.SimpleNamespace(get_printer=lambda: self))
        self.handlers["klippy:connect"]()
        return self.tester


class ResonanceSweepTest(unittest.TestCase):
    def setUp(self) -> None:
        saved = dict(resonance_sweep.ORIGINALS)
        self.addCleanup(resonance_sweep.ORIGINALS.update, saved)
        for label, obj in STOCK.items():
            resonance_sweep.ORIGINALS[label] = resonance_sweep.source_hash(obj)
        normalize = CalibrationData.__dict__["normalize_to_frequencies"]
        self.addCleanup(setattr, CalibrationData, "normalize_to_frequencies", normalize)

    def run_test(self, printer, **params):
        tester = printer.connect()
        gcmd = GCmd(**params)
        tester.test.prepare_test(gcmd)
        tester.test.run_test(TestAxis("x"), gcmd)
        return tester, gcmd

    def test_saved_variable_and_sweeping_period_choose_the_test(self) -> None:
        cases = [
            (None, {}, True),
            ({"tltg_resonance_sweep": 1}, {}, True),
            ({"tltg_resonance_sweep": 0}, {}, False),
            ({"tltg_resonance_sweep": 0}, {"SWEEPING_PERIOD": 1.2}, True),
            (None, {"SWEEPING_PERIOD": 0}, False),
        ]
        for variables, params, sweeps in cases:
            with self.subTest(variables=variables, params=params):
                printer = Printer(variables)
                tester, gcmd = self.run_test(printer, FREQ_END=20, ACCEL_PER_HZ=60, **params)
                stock = tester.test.stock
                self.assertEqual(stock.runs, [] if sweeps else [60.])
                self.assertEqual(stock.accel_per_hz, 150.)
                self.assertEqual(bool(printer.toolhead.positions), sweeps)
                self.assertIn("with sweeping" if sweeps else "without sweeping", gcmd.messages[0])

    def test_changed_qidi_code_keeps_qidi_test(self) -> None:
        resonance_sweep.ORIGINALS["Move.calc_junction"] = "0" * 64
        printer = Printer()
        tester = printer.connect()
        self.assertIsInstance(tester.test, VibrationPulseTest)
        self.assertIs(CalibrationData.__dict__["normalize_to_frequencies"],
                      STOCK["CalibrationData.normalize_to_frequencies"])

    def test_sweeping_run_stays_near_the_test_point(self) -> None:
        printer = Printer()
        self.run_test(printer)
        xs = [pos[0] - 195. for pos in printer.toolhead.positions]
        self.assertLess(max(abs(x) for x in xs), 25.)
        self.assertTrue(all(pos[1:] == (195., 10., 0.) for pos in printer.toolhead.positions))
        self.assertIs(Move.__dict__["calc_junction"], STOCK["Move.calc_junction"])
        self.assertEqual(printer.scripts[-1], "SET_VELOCITY_LIMIT ACCEL=20000.000 MINIMUM_CRUISE_RATIO=0.500")

    def test_qidi_planning_returns_after_a_failed_run(self) -> None:
        printer = Printer(toolhead=FakeToolHead(fail_after=10))
        with self.assertRaises(CommandError):
            self.run_test(printer, FREQ_END=20)
        self.assertIs(Move.__dict__["calc_junction"], STOCK["Move.calc_junction"])

    def test_test_beyond_max_accel_is_refused_before_moving(self) -> None:
        printer = Printer()
        with self.assertRaisesRegex(CommandError, "above max_accel 30000"):
            self.run_test(printer, ACCEL_PER_HZ=300)
        self.assertEqual(printer.toolhead.positions, [])


if __name__ == "__main__":
    unittest.main()
