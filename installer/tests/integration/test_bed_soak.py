from __future__ import annotations

import importlib.util
import json
import math
import tempfile
import types
import unittest
from pathlib import Path

from installer.tests.helpers import REPO_ROOT


def _load(name):
    spec = importlib.util.spec_from_file_location(
        "tltg_" + name, REPO_ROOT / f"installer/klipper/tltg/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bed_soak = _load("bed_soak")
probe_report = _load("probe_report")

GRID = [15. + 90. * i for i in range(5)]
MESH_SECONDS = 120.


class CommandError(Exception):
    pass


class Heater:
    def __init__(self, printer, name, max_temp, rate):
        self.printer, self.name = printer, name
        self.min_temp, self.max_temp = 0., max_temp
        self.rate = rate
        self.target = 0.
        self.temp = 25.

    def set_temp(self, value):
        self.target = value

    def get_status(self, eventtime):
        return {"temperature": self.temp, "target": self.target, "power": 0.}


class Mutex:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ZMesh:
    def __init__(self, matrix, name):
        self.matrix, self.name = matrix, name

    def get_probed_matrix(self):
        return self.matrix

    def get_profile_name(self):
        return self.name

    def get_mesh_params(self):
        return {"min_x": 15., "max_x": 375., "min_y": 15., "max_y": 375.,
                "x_count": 5, "y_count": 5}


class Printer:
    """A Max 4 whose bed takes 10 minutes to reach 100 C, domes unevenly
    while it heats, as the test Max 4's did, and settles about 20 minutes
    later."""

    def __init__(self, run_dir, chamber=True, jump_at=None, retry_point=None):
        self.now = 1000.
        self.config_dir = run_dir
        self.timers = []
        self.callbacks = []
        self.commands = {}
        self.endpoints = {}
        self.output = []
        self.scripts = []
        self.saved_profiles = []
        self.state = "standby"
        self.jump_at = jump_at
        self.jump = 0.
        self.retry_point = retry_point
        self.shutdown = False
        self.heaters_on = None
        self.heater = {"extruder": Heater(self, "extruder", 375., 2.),
                       "heater_bed": Heater(self, "heater_bed", 125., .125)}
        if chamber:
            self.heater["chamber"] = Heater(self, "chamber", 68., .02)
        self.handlers = []
        printer = self
        self.reactor = types.SimpleNamespace(
            NOW=0., NEVER=9e99, monotonic=lambda: printer.now,
            register_timer=self._register_timer,
            unregister_timer=lambda t: printer.timers.remove(t),
            register_callback=lambda cb: printer.callbacks.append(cb))
        self.gcode = types.SimpleNamespace(
            register_command=lambda name, fn, desc=None: self.commands.__setitem__(name, fn),
            register_output_handler=self.handlers.append,
            respond_info=self._respond,
            get_mutex=Mutex,
            run_script_from_command=self._run_script,
            ready_gcode_handlers={"_BED_MESH_CALIBRATE": None},
            create_gcode_command=None)
        self.webhooks = types.SimpleNamespace(
            register_endpoint=lambda path, fn: self.endpoints.__setitem__(path, fn))
        self.pheaters = types.SimpleNamespace(lookup_heater=self._lookup_heater)
        self.mesh = ZMesh([[0.] * 5 for _ in range(5)], "kamp")
        self.helper = types.SimpleNamespace(start_probe=self._probe_mesh)
        self.bed_mesh = types.SimpleNamespace(
            bmc=types.SimpleNamespace(
                orig_config={"mesh_min": (15., 15.), "mesh_max": (375., 375.)},
                probe_mgr=types.SimpleNamespace(probe_helper=self.helper)),
            horizontal_move_z=5.,
            get_mesh=lambda: printer.mesh,
            set_mesh=self._set_mesh,
            save_profile=self.saved_profiles.append)
        self.probe = types.SimpleNamespace(
            last_z=0., get_offsets=lambda: (0., 0., 0.),
            get_status=lambda e: {"last_z_result": printer.probe.last_z})
        self.objects = {
            "gcode": self.gcode, "webhooks": self.webhooks, "heaters": self.pheaters,
            "bed_mesh": self.bed_mesh, "probe": self.probe,
            "print_stats": types.SimpleNamespace(get_status=lambda e: {"state": printer.state}),
            "virtual_sdcard": types.SimpleNamespace(is_active=lambda: False),
            "gcode_macro _km_globals": types.SimpleNamespace(
                variables={"start_extruder_probing_temp": 140}),
            "gcode_macro _tltg_printer": types.SimpleNamespace(
                variables={"z_tilt_staging_x": 15, "z_tilt_staging_y": 202.5}),
        }
        self.events = {}
        config = types.SimpleNamespace(get_printer=lambda: self)
        self.report = probe_report.load_config(config)
        self.objects["tltg_probe_report"] = self.report
        self.soak = bed_soak.load_config(config)
        for handler in self.events["klippy:connect"]:
            handler()

    # Klipper's printer object
    command_error = CommandError

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return {"config_file": str(Path(self.config_dir) / "printer.cfg")}

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def register_event_handler(self, event, handler):
        self.events.setdefault(event, []).append(handler)

    def is_shutdown(self):
        return self.shutdown

    def _lookup_heater(self, name):
        if name not in self.heater:
            raise Exception("Unknown heater")
        return self.heater[name]

    def _register_timer(self, callback, waketime):
        timer = [callback, self.now if waketime == 0. else waketime]
        self.timers.append(timer)
        return timer

    def _respond(self, msg):
        self.output.append(msg)
        for handler in self.handlers:
            handler("// " + msg)

    # The bed model
    def surface(self, x, y):
        if self.heaters_on is None:
            return 0.
        t = self.now - self.heaters_on
        settle = 1. - math.exp(-t / 300.)
        bow = ((x - 195.) ** 2 + (y - 195.) ** 2) / (180. ** 2 * 2)
        dome = 0.
        if t < 600.:
            dome = .15 * math.sin(math.pi * t / 600.) + .03 * (int(t / 130.) % 2)
        return -.06 * settle - .04 * settle * bow + dome * (1. - bow) + self.jump

    def advance(self, seconds):
        end = self.now + seconds
        while self.now < end:
            self.now = min(end, self.now + 1.)
            for heater in self.heater.values():
                if heater.temp < heater.target:
                    heater.temp = min(heater.target, heater.temp + heater.rate)
                elif heater.target == 0.:
                    heater.temp = max(25., heater.temp - heater.rate)
            for timer in list(self.timers):
                if timer in self.timers and timer[1] <= self.now:
                    timer[1] = timer[0](self.now)

    def _probe_mesh(self, gcmd):
        matrix = []
        for row, y in enumerate(GRID):
            values = []
            for col, x in enumerate(GRID):
                self.advance(MESH_SECONDS / 25.)
                if self.jump_at is not None and self.heaters_on is not None and \
                        self.now - self.heaters_on >= self.jump_at:
                    self.jump, self.jump_at = .05, None
                z = self.surface(x, y)
                if self.retry_point == (row, col):
                    self._respond("probe at %.3f,%.3f is z=%.6f" % (x, y, z))
                    self._respond("probe at %.3f,%.3f is z=%.6f" % (x, y, z + .03))
                    self._respond("Probe samples exceed tolerance. Retrying...")
                self._respond("probe at %.3f,%.3f is z=%.6f" % (x, y, z))
                self._respond("probe at %.3f,%.3f is z=%.6f" % (x, y, z))
                values.append(z)
            matrix.append(values)
        self.mesh = ZMesh(matrix, "tltg_bed_soak")
        self.bed_mesh.save_profile("tltg_bed_soak")

    def _set_mesh(self, mesh):
        self.mesh = mesh

    def _run_script(self, script):
        for line in script.split("\n"):
            self.scripts.append(line)
            word = line.split(" ")[0]
            args = dict(a.split("=", 1) if "=" in a else (a[0], a[1:])
                        for a in line.split(" ")[1:])
            if word in ("M104", "M109"):
                self.heater["extruder"].set_temp(float(args["S"]))
            elif word == "M140":
                self.heater["heater_bed"].set_temp(float(args["S"]))
                if float(args["S"]) > 0 and self.heaters_on is None:
                    self.heaters_on = self.now
            elif word == "M141" and "chamber" in self.heater:
                self.heater["chamber"].set_temp(float(args["S"]))
            elif word == "PROBE":
                self.advance(5.)
                self.probe.last_z = self.surface(195., 195.)
            elif word == "_BED_MESH_CALIBRATE":
                self.mesh = None
                self.helper.start_probe(None)
            elif word in ("G28", "Z_TILT_ADJUST", "_OPTIMIZED_HOME_Z_FROM_SAFE_POINT"):
                self.advance(10.)

    # Test driving
    def command(self, line):
        name, *rest = line.split(" ")
        params = dict(a.split("=", 1) for a in rest)
        printer = self

        class Gcmd:
            def get_float(self, key, default=None, minval=None, maxval=None):
                return float(params[key]) if key in params else default

            def get_int(self, key, default=None, minval=None, maxval=None):
                value = int(params[key]) if key in params else default
                if (minval is not None and value < minval) or \
                        (maxval is not None and value > maxval):
                    raise CommandError("bad %s" % key)
                return value

            def error(self, msg):
                return CommandError(msg)

            def respond_info(self, msg):
                printer.output.append(msg)

        self.commands[name](Gcmd())

    def run_test(self):
        while self.callbacks:
            self.callbacks.pop(0)(self.now)

    def records(self):
        path = Path(self.config_dir) / self.soak.status["file"]
        return [json.loads(line) for line in path.read_text().splitlines()]


class BedSoakTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def printer(self, **kwargs):
        return Printer(self.tmp.name, **kwargs)

    def test_runs_until_the_bed_stops_moving_and_leaves_the_printer_as_found(self):
        printer = self.printer()
        before = printer.mesh

        printer.command("TLTG_BED_SOAK BED=100 CHAMBER=45")
        printer.run_test()

        status = printer.soak.get_status()
        self.assertEqual(status["state"], "finished")
        self.assertEqual(status["reason"], "The bed stopped moving")
        ms = status["milestones"]
        self.assertIsNotNone(ms["bed"])
        self.assertIsNotNone(ms["chamber"])
        self.assertGreaterEqual(ms["settled"], ms["bed"])
        self.assertEqual(ms["ready"], max(ms["settled"], ms["chamber"]))
        self.assertLess(status["latest"]["speed"], bed_soak.SPEED_LIMIT)
        # Settled: a 60 um rise plus half of the 40 um edge bow, on average.
        self.assertAlmostEqual(status["latest"]["height"], -.08, delta=.01)
        self.assertEqual(status["problems"], {"points": 0, "jumps": 0})
        # The cold mesh came before any heating, after homing and Z tilt.
        heat = printer.scripts.index("M140 S100")
        self.assertLess(printer.scripts.index("Z_TILT_ADJUST"), heat)
        self.assertLess(printer.scripts.index("_BED_MESH_CALIBRATE PROFILE=tltg_bed_soak "
                                              "MESH_MIN=15,15 MESH_MAX=375,375 PROBE_COUNT=5,5"), heat)
        # As found: no profile saved, the old mesh back, heaters off.
        self.assertEqual(printer.saved_profiles, [])
        self.assertIs(printer.mesh, before)
        self.assertEqual([h.target for h in printer.heater.values()], [0., 0., 0.])
        records = printer.records()
        self.assertEqual([r["type"] for r in records[:2]], ["settings", "heaters_on"])
        self.assertEqual(records[-1]["type"], "result")
        meshes = [r for r in records if r["type"] == "mesh"]
        self.assertEqual(len(meshes), status["meshes"])
        self.assertLess(meshes[0]["time"], 0)

    def test_keep_heating_leaves_the_bed_and_chamber_on(self):
        printer = self.printer()

        printer.command("TLTG_BED_SOAK BED=100 CHAMBER=45 KEEP_HEATING=1")
        printer.run_test()

        self.assertEqual(printer.heater["heater_bed"].target, 100.)
        self.assertEqual(printer.heater["chamber"].target, 45.)
        self.assertEqual(printer.heater["extruder"].target, 0.)

    def test_ends_at_the_time_limit_without_stopping_early(self):
        printer = self.printer(chamber=False)

        printer.command("TLTG_BED_SOAK BED=100 TIME_LIMIT=20 STOP_WHEN_SETTLED=0")
        printer.run_test()

        status = printer.soak.get_status()
        self.assertEqual(status["reason"], "Reached the time limit")
        self.assertGreaterEqual(status["elapsed"], 20 * 60)
        self.assertIsNone(status["milestones"]["chamber"])

    def test_refusals_leave_the_printer_alone(self):
        printer = self.printer(chamber=False)
        for line in ("TLTG_BED_SOAK BED=200", "TLTG_BED_SOAK BED=100 CHAMBER=50",
                     "TLTG_BED_SOAK"):
            with self.subTest(line=line), self.assertRaises(CommandError):
                printer.command(line)
        printer.state = "printing"
        with self.assertRaises(CommandError):
            printer.command("TLTG_BED_SOAK BED=100")
        self.assertEqual(printer.callbacks, [])
        self.assertEqual(printer.scripts, [])

    def test_stop_turns_heaters_off_at_once(self):
        printer = self.printer()
        printer.command("TLTG_BED_SOAK BED=100 CHAMBER=45")
        stopped = []

        def stop_mid_mesh(gcmd, probe=printer.helper.start_probe):
            if printer.heaters_on is not None and printer.now - printer.heaters_on > 600 and not stopped:
                printer.endpoints["tltg_bed_soak/stop"](
                    types.SimpleNamespace(send=stopped.append))
                self.assertEqual(printer.heater["heater_bed"].target, 0.)
                self.assertEqual(printer.heater["chamber"].target, 0.)
            probe(gcmd)
        printer.helper.start_probe = stop_mid_mesh
        printer.run_test()

        status = printer.soak.get_status()
        self.assertEqual(stopped, [{"stopping": True}])
        self.assertEqual(status["state"], "stopped")
        self.assertEqual(printer.heater["extruder"].target, 0.)
        self.assertEqual(printer.records()[-1]["state"], "stopped")

    def test_a_heater_changed_elsewhere_ends_the_test_and_is_left_alone(self):
        printer = self.printer()
        printer.command("TLTG_BED_SOAK BED=100")

        def change_bed(gcmd, probe=printer.helper.start_probe):
            if printer.heaters_on is not None:
                printer.heater["heater_bed"].set_temp(60.)
            probe(gcmd)
        printer.helper.start_probe = change_bed
        printer.run_test()

        status = printer.soak.get_status()
        self.assertEqual(status["state"], "failed")
        self.assertIn("bed target", status["reason"])
        self.assertEqual(printer.heater["heater_bed"].target, 60.)

    def test_probe_jump_drops_the_mesh_and_corrects_later_heights(self):
        printer = self.printer(jump_at=1500.)

        printer.command("TLTG_BED_SOAK BED=100 STOP_WHEN_SETTLED=0 TIME_LIMIT=45")
        printer.run_test()

        status = printer.soak.get_status()
        records = printer.records()
        jumps = [r for r in records if r["type"] == "jump"]
        self.assertEqual(len(jumps), 1)
        self.assertAlmostEqual(jumps[0]["size"], .05, delta=.01)
        self.assertEqual(status["problems"]["jumps"], 1)
        self.assertTrue(records[-1]["warning"])
        last = [r for r in records if r["type"] == "mesh"][-1]
        self.assertAlmostEqual(last["height"], status["latest"]["height"])
        self.assertAlmostEqual(last["height"], -.08, delta=.01)

    def test_heating_up_is_not_mistaken_for_problems(self):
        printer = self.printer()

        printer.command("TLTG_BED_SOAK BED=100 STOP_WHEN_SETTLED=0 TIME_LIMIT=30")
        printer.run_test()

        records = printer.records()
        self.assertEqual([r for r in records if r["type"] == "jump"], [])
        self.assertEqual(records[-1]["problem_points"], [])

    def test_retried_points_are_flagged_and_reported_for_any_mesh(self):
        printer = self.printer(retry_point=(2, 3))

        printer.command("TLTG_BED_SOAK BED=100 STOP_WHEN_SETTLED=0 TIME_LIMIT=15")
        printer.run_test()

        points = printer.records()[-1]["problem_points"]
        self.assertEqual([(p["row"], p["col"], p["x"], p["y"]) for p in points],
                         [(2, 3, 285., 195.)])
        self.assertGreater(len(points[0]["meshes"]), 1)
        report = printer.report.get_status()
        self.assertEqual(report["points"], [{"x": 285., "y": 195., "tries": 2, "spread": .03}])
        # The soak test put the old mesh back, so the report isn't for it.
        self.assertFalse(report["loaded"])
        printer.helper.start_probe(None)
        self.assertTrue(printer.report.get_status()["loaded"])


class StoppedMovingTest(unittest.TestCase):
    def mesh(self, time, speed, used=True):
        return {"start": time - 60, "time": time, "speed": speed, "used": used}

    def test_counts_only_the_last_stretch_under_the_limit(self):
        speeds = [.01, .001, .001, .001, .005, .001, .0015, .001, .0012]
        meshes = [self.mesh(150 * i, s) for i, s in enumerate(speeds)]

        self.assertEqual(bed_soak.settled_time(meshes, 0), 750)
        self.assertIsNone(bed_soak.settled_time(meshes[:-1] + [self.mesh(1200, .004)], 0))

    def test_waits_for_the_gate(self):
        meshes = [self.mesh(150 * i, .001) for i in range(6)]

        self.assertEqual(bed_soak.settled_time(meshes, 300), 450)
        self.assertIsNone(bed_soak.settled_time(meshes, 400))
        self.assertIsNone(bed_soak.settled_time(meshes, None))


if __name__ == "__main__":
    unittest.main()
