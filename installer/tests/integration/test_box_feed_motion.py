from __future__ import annotations

import ast
import importlib.util
import unittest

from installer.tests.helpers import REPO_ROOT


def load_module():
    spec = importlib.util.spec_from_file_location(
        "tltg_box_feed_motion", REPO_ROOT / "installer/klipper/tltg/box_feed_motion.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MAX4_BUILD = "58ee8c61bffc3d4e18edc9e56a326495b6a218348bbfb13f7a012a946039b0a5"
PLUS5_BUILD = "fc20d82a58b740c0d5e23a4043c23f3d2b9dcb386e72cda4e4619292331f6589"


class CommandError(Exception):
    pass


class GCodeCommand:
    def __init__(self, params, responses):
        self.params = params
        self.responses = responses

    def get_command_parameters(self):
        return self.params

    def error(self, message):
        return CommandError(message)

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_int(self, name, default=None, minval=None, maxval=None):
        if name not in self.params:
            return default
        value = int(self.params[name])
        if (minval is not None and value < minval) or (maxval is not None and value > maxval):
            raise CommandError("%s out of range" % name)
        return value

    def respond_info(self, message):
        self.responses.append(message)


class GCode:
    def __init__(self):
        self.commands = {}
        self.responses = []

    def register_command(self, name, handler, desc=None):
        self.commands[name] = handler

    def create_gcode_command(self, command, line, params):
        return GCodeCommand(params, self.responses)

    def respond_info(self, message):
        self.responses.append(message)

    def run(self, command, **params):
        self.commands[command](GCodeCommand({k: str(v) for k, v in params.items()}, self.responses))


class SaveVariables:
    def __init__(self):
        self.allVariables = {}

    def cmd_SAVE_VARIABLE(self, gcmd):
        variables = dict(self.allVariables)
        variables[gcmd.get("VARIABLE")] = ast.literal_eval(gcmd.get("VALUE"))
        self.allVariables = variables


class BoxExtras:
    """QIDI's box_extras lookup: the saved variable if set, else the default passed."""

    def __init__(self, save_variables):
        self.save_variables = save_variables

    def get_value_by_key(self, key, default):
        return self.save_variables.allVariables.get(key, default)


class BoxStepper:
    def __init__(self):
        self.calls = []

    def do_home_three_steps(self, *args):
        self.calls.append(("load", args))

    def do_home_double_steps(self, *args):
        self.calls.append(("unload", args))


class Printer:
    def __init__(self, slots):
        self.gcode = GCode()
        self.save_variables = SaveVariables()
        self.box_extras = BoxExtras(self.save_variables)
        self.steppers = {"box_stepper slot%d" % i: BoxStepper() for i in range(slots)}
        self.handlers = {}

    def lookup_object(self, name, default=None):
        return {"gcode": self.gcode, "save_variables": self.save_variables, "box_extras": self.box_extras}.get(
            name, default
        )

    def lookup_objects(self, module):
        return list(self.steppers.items())

    def register_event_handler(self, event, handler):
        self.handlers[event] = handler


class Config:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer


ENDSTOPS_E = [(object(), "e_endstop")]
ENDSTOPS_B = [(object(), "b_endstop")]


class BoxFeedMotionTests(unittest.TestCase):
    def start(self, build):
        module = load_module()
        module._hash_file = lambda path: build
        printer = Printer(4)
        motion = module.BoxFeedMotion(Config(printer))
        printer.handlers["klippy:ready"]()
        return module, printer, motion

    def lookup(self, printer, key, default):
        return printer.box_extras.get_value_by_key(key, default)

    def test_plus5_unset_settings_keep_qidi_values(self):
        _module, printer, motion = self.start(PLUS5_BUILD)

        for key, default in (
            ("extruder_load_length_2", 720),
            ("extruder_load_length_1_speed", 85),
            ("extruder_load_length_2_speed", 100),
            ("extruder_load_length_3_speed", 25),
            ("extruder_unload_length_1_speed", 100),
            ("extruder_unload_length_2_speed", 80),
            ("exl_2", 100),
            ("extruder_load_length_3", 250),
        ):
            with self.subTest(key=key):
                self.assertEqual(self.lookup(printer, key, default), default)

        status = motion.get_status()
        self.assertTrue(status["active"])
        self.assertEqual(status["layout"], "single")
        self.assertEqual(status["fields"], status["global_only"])
        self.assertIn("hub_speed", status["fields"])
        self.assertNotIn("fast_speed_1", status["fields"])
        self.assertEqual(status["effective"]["fast_length"], 720)
        self.assertEqual(status["slots"], {})

    def test_plus5_settings_answer_qidis_lookups(self):
        _module, printer, motion = self.start(PLUS5_BUILD)

        printer.gcode.run("TLTG_BOX_MOTION", HUB_SPEED=120, PARK_SPEED=110, FAST_LENGTH=900, FAST_SPEED=130,
                          APPROACH_SPEED=30, UNLOAD_SPEED=140)
        self.assertEqual(self.lookup(printer, "extruder_load_length_1_speed", 85), 120)
        self.assertEqual(self.lookup(printer, "extruder_unload_length_2_speed", 80), 110)
        self.assertEqual(self.lookup(printer, "extruder_load_length_2", 720), 900)
        self.assertEqual(self.lookup(printer, "extruder_load_length_2_speed", 100), 130)
        self.assertEqual(self.lookup(printer, "extruder_load_length_3_speed", 25), 30)
        self.assertEqual(self.lookup(printer, "extruder_unload_length_1_speed", 100), 140)

        printer.gcode.run("TLTG_BOX_MOTION", FAST_LENGTH="vendor", HUB_SPEED="default")
        self.assertEqual(self.lookup(printer, "extruder_load_length_2", 720), 720)
        self.assertEqual(self.lookup(printer, "extruder_load_length_1_speed", 85), 85)

        for params in ({"SLOT": "0", "HUB_SPEED": 100}, {"FAST_SPEED_1": 90}):
            with self.subTest(params=params), self.assertRaises(CommandError):
                printer.gcode.run("TLTG_BOX_MOTION", **params)

    def test_max4_staged_load_takes_the_saved_approach_speed(self):
        _module, printer, motion = self.start(MAX4_BUILD)
        stepper = printer.steppers["box_stepper slot0"]

        stepper.do_home_three_steps(ENDSTOPS_E, 550, 380, 240, 85, 65, 10, 100, False)
        self.assertEqual(stepper.calls[-1][1][1:8], (550, 380, 240, 85, 65, 10, 100))

        printer.gcode.run("TLTG_BOX_MOTION", APPROACH_SPEED=25)
        stepper.do_home_three_steps(ENDSTOPS_E, 550, 380, 240, 85, 65, 10, 100, False)
        _kind, args = stepper.calls[-1]
        self.assertEqual(args[6], 25)
        self.assertEqual(args[1] + args[2], 930)
        self.assertEqual(motion.get_status()["slots"]["slot0"]["load"]["approach_speed"], 25)

    def test_max4_hub_and_park_speeds_answer_qidis_lookups(self):
        _module, printer, motion = self.start(MAX4_BUILD)

        self.assertEqual(self.lookup(printer, "extruder_load_length_1_speed", 85), 85)
        printer.gcode.run("TLTG_BOX_MOTION", HUB_SPEED=120, PARK_SPEED=100)
        self.assertEqual(self.lookup(printer, "extruder_load_length_1_speed", 85), 120)
        self.assertEqual(self.lookup(printer, "extruder_unload_length_2_speed", 80), 100)
        # The Max 4's load lengths stay with its three-stage home.
        self.assertEqual(self.lookup(printer, "extruder_load_length_2", 720), 720)

        status = motion.get_status()
        self.assertEqual(status["layout"], "staged")
        self.assertIn("park_speed", status["global_only"])
        self.assertNotIn("fast_speed_1", status["global_only"])
        with self.assertRaises(CommandError):
            printer.gcode.run("TLTG_BOX_MOTION", SLOT="0", PARK_SPEED=90)
        with self.assertRaises(CommandError):
            printer.gcode.run("TLTG_BOX_MOTION", FAST_SPEED=90)

    def test_unknown_build_stays_inactive(self):
        _module, printer, motion = self.start("0" * 64)

        self.assertFalse(motion.get_status()["active"])
        self.assertEqual(self.lookup(printer, "extruder_load_length_2", 720), 720)


if __name__ == "__main__":
    unittest.main()
