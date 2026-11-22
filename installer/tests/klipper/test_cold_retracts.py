from __future__ import annotations

import hashlib
import logging
import sys
import threading
import types
import unittest
from unittest import mock

from installer.tests.helpers import REPO_ROOT, load_source
from installer.tests.klipper_fakes import CommandError, load_extra

cold_cut = load_extra("cold_cut")

# Klipper's greenlet, which only names the running command here.
sys.modules.setdefault("greenlet", types.SimpleNamespace(getcurrent=lambda: None))
box_flow_recovery = load_extra("box_flow_recovery")
exclude_object = load_source("klipper_exclude_object", REPO_ROOT / "installer/tests/fixtures/klipper_exclude_object.py")

MIN_EXTRUDE_TEMP = 135.


class Params:
    """Klipper's GCodeCommand for a command line."""

    def __init__(self, line):
        self.line = line
        self.params = dict(arg.split("=", 1) for arg in line.split()[1:] if "=" in arg)

    def get_commandline(self):
        return self.line

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_command_parameters(self):
        return dict(self.params)

    def respond_info(self, message):
        pass

    def get_float(self, name, default=None, above=None):
        if name not in self.params:
            if default is None:
                raise CommandError("Error on '%s': missing %s" % (self.line, name))
            return default
        value = float(self.params[name])
        if above is not None and value <= above:
            raise CommandError("Error on '%s': %s must be above %s" % (self.line, name, above))
        return value


class Heater:
    """QIDI's heater: can_extrude follows every temperature reading."""

    def __init__(self, temp):
        self.lock = threading.Lock()
        self.min_extrude_temp = MIN_EXTRUDE_TEMP
        self.smoothed_temp = temp
        self.can_extrude = temp >= MIN_EXTRUDE_TEMP

    def temperature_callback(self):
        with self.lock:
            self.can_extrude = self.smoothed_temp >= self.min_extrude_temp


class Extruder:
    """QIDI's extruder check, which refuses retracts too."""

    def __init__(self, heater):
        self.heater = heater

    def get_heater(self):
        return self.heater

    def check_move(self, move):
        if not self.heater.can_extrude:
            raise CommandError("Extrude below minimum temp")


class Printer:
    def __init__(self, temp, print_state="standby", task=None, cut_error=None):
        self.heater = Heater(temp)
        self.extruder = Extruder(self.heater)
        self.command_error = CommandError
        self.scripts = []
        self.moves = []
        self.handlers = {}
        self.cut_error = cut_error
        self.shutdown = False
        printer = self
        toolhead = types.SimpleNamespace(
            get_extruder=lambda: self.extruder,
            get_status=lambda e: {"max_accel": 20000.},
            get_position=lambda: [0., 0., 0., sum(self.moves)],
        )
        statuses = {
            "toolhead": toolhead,
            "print_stats": types.SimpleNamespace(get_status=lambda e: {"state": print_state}),
            "multi_color_controller": types.SimpleNamespace(
                get_status=lambda e: {"task": task or {"flow_id": None, "current_queue": [],
                                                       "current_step": None, "is_paused": False}}),
        }

        class GCode:
            def register_command(self, name, handler, desc=None):
                printer.handlers[name] = handler

            def run_script_from_command(self, script):
                printer.scripts.append(script)
                command = script.split()[0]
                if command in printer.handlers:
                    printer.handlers[command](Params(script))
                elif script == cold_cut.CUT_MACRO:
                    self.run_script_from_command("_VO_CUTTER_STRIKE EXIT_X=15 EXIT_F=8000")
                elif script == cold_cut.STRIKE_MACRO:
                    printer.heater.temperature_callback()
                    printer.move(-4.)
                    if printer.cut_error:
                        raise CommandError(printer.cut_error)

        self.objects = dict(statuses, gcode=GCode())

    def move(self, e):
        self.extruder.check_move(types.SimpleNamespace(axes_d=[0., 0., 0., e]))
        self.moves.append(e)

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_reactor(self):
        return types.SimpleNamespace(monotonic=lambda: 0., pause=lambda waketime: waketime)

    def is_shutdown(self):
        return self.shutdown


class GCmd:
    def __init__(self):
        self.messages = []

    def error(self, message):
        return CommandError(message)

    def respond_info(self, message):
        self.messages.append(message)


def make(printer):
    config = types.SimpleNamespace(get_printer=lambda: printer)
    return cold_cut.ColdCut(config)


class ColdRetractsTest(unittest.TestCase):
    """Only retracts get past the cold-extrusion check, and only while held."""

    def test_cold_nozzle_retracts_but_does_not_extrude_while_held(self) -> None:
        printer = Printer(25.)
        module = make(printer)
        with self.assertRaises(CommandError):
            printer.move(-4.)
        with module.cold_retracts():
            printer.heater.temperature_callback()
            printer.move(-4.)
            printer.move(-120.)
            with self.assertRaises(CommandError):
                printer.move(5.)
        with self.assertRaises(CommandError):
            printer.move(-4.)
        printer.heater.temperature_callback()
        self.assertEqual(printer.moves, [-4., -120.])
        self.assertEqual(printer.heater.min_extrude_temp, MIN_EXTRUDE_TEMP)
        self.assertFalse(printer.heater.can_extrude)
        self.assertNotIn("check_move", vars(printer.extruder))

    def test_hot_nozzle_still_extrudes_while_held(self) -> None:
        printer = Printer(200.)
        with make(printer).cold_retracts():
            printer.move(5.)
        printer.move(5.)
        self.assertEqual(printer.moves, [5., 5.])
        self.assertTrue(printer.heater.can_extrude)

    def test_error_inside_restores_the_check(self) -> None:
        printer = Printer(25.)
        with self.assertRaises(RuntimeError):
            with make(printer).cold_retracts():
                raise RuntimeError("step failed")
        with self.assertRaises(CommandError):
            printer.move(-4.)


class CutterStrikeTest(unittest.TestCase):
    """Every cut presses the cutter through _VO_CUTTER_STRIKE, which pulls
    back at any nozzle temperature and always leaves the cutter."""

    LEAVE = "G1 X15.000 F8000.000"

    def strike(self, printer):
        make(printer)
        printer.handlers["_VO_CUTTER_STRIKE"](Params("_VO_CUTTER_STRIKE EXIT_X=15 EXIT_F=8000"))

    def test_cold_cut_by_hand_or_while_paused_pulls_back_and_leaves(self) -> None:
        for state in ("standby", "paused", "printing"):
            with self.subTest(state=state):
                printer = Printer(25., print_state=state)
                self.strike(printer)
                self.assertEqual(printer.moves, [-4.])
                self.assertEqual(printer.scripts[-1], self.LEAVE)
                with self.assertRaises(CommandError):
                    printer.move(-4.)

    def test_failed_strike_still_leaves_the_cutter(self) -> None:
        printer = Printer(25., cut_error="Move out of range")
        with self.assertRaisesRegex(CommandError, "Move out of range"):
            self.strike(printer)
        self.assertEqual(printer.scripts[-1], self.LEAVE)
        with self.assertRaises(CommandError):
            printer.move(-4.)

    def test_no_move_after_a_shutdown(self) -> None:
        printer = Printer(25., cut_error="MCU shutdown")
        printer.shutdown = True
        with self.assertRaises(CommandError):
            self.strike(printer)
        self.assertNotIn(self.LEAVE, printer.scripts)


class ColdCutCommandTest(unittest.TestCase):
    def run_cut(self, printer):
        make(printer)
        gcmd = GCmd()
        printer.handlers["VO_CUT_FILAMENT"](gcmd)
        return gcmd

    def test_cuts_cold_after_homing_and_restores_state(self) -> None:
        printer = Printer(25.)
        gcmd = self.run_cut(printer)
        self.assertEqual(printer.scripts[:3], [
            "SAVE_GCODE_STATE NAME=vo_cut_filament",
            "G28 O X Y",
            "_VO_CUT_FILAMENT_MOTION",
        ])
        self.assertEqual(printer.scripts[-2:], [
            "G1 X15.000 F8000.000",
            "SET_VELOCITY_LIMIT ACCEL=20000.000\nRESTORE_GCODE_STATE NAME=vo_cut_filament",
        ])
        self.assertEqual(printer.moves, [-4.])
        self.assertEqual(gcmd.messages, ["Filament cut."])
        with self.assertRaises(CommandError):
            printer.move(-4.)

    def test_failed_cut_still_restores_state_and_the_check(self) -> None:
        printer = Printer(25., cut_error="Move out of range")
        with self.assertRaises(CommandError):
            self.run_cut(printer)
        self.assertEqual(printer.scripts[-1],
                         "SET_VELOCITY_LIMIT ACCEL=20000.000\nRESTORE_GCODE_STATE NAME=vo_cut_filament")
        with self.assertRaises(CommandError):
            printer.move(-4.)

    def test_refused_during_a_print_or_box_flow(self) -> None:
        for kwargs in ({"print_state": "printing"}, {"print_state": "paused"},
                       {"task": {"flow_id": 3, "current_queue": ["BOX_UNLOAD"],
                                 "current_step": "BOX_UNLOAD", "is_paused": False}},
                       {"task": {"flow_id": 1, "current_queue": [],
                                 "current_step": "BOX_LOAD", "is_paused": True}}):
            with self.subTest(**kwargs):
                printer = Printer(25., **kwargs)
                with self.assertRaises(CommandError):
                    self.run_cut(printer)
                self.assertEqual((printer.scripts, printer.moves), ([], []))



class Dispatcher:
    """Klipper's G-code dispatch: registered handlers, G1 E moves, and the
    rest recorded."""

    def __init__(self, printer):
        self.printer = printer
        self.handlers = {}
        self.gcode_help = {}
        self.sent = []
        self.heated = []
        self.messages = []
        self.handlers["M109"] = lambda gcmd: self.heated.append(gcmd.line)

    def register_command(self, name, handler, desc=None):
        if handler is None:
            return self.handlers.pop(name, None)
        assert name not in self.handlers, name
        self.handlers[name] = handler
        if desc is not None:
            self.gcode_help[name] = desc

    def run_script_from_command(self, script):
        for line in script.split("\n"):
            line = line.strip()
            if not line:
                continue
            self.sent.append(line)
            command = line.split()[0]
            if command == "G1" and " E" in line:
                self.printer.move(float(line.split(" E")[1].split()[0]))
            elif command == "CUT_FILAMENT_1":
                self.run_script_from_command("_VO_CUTTER_STRIKE EXIT_X=15 EXIT_F=3000")
            elif command == cold_cut.STRIKE_MACRO:
                self.printer.move(-4.)
            elif command in self.handlers:
                self.handlers[command](Params(line))

    def respond_info(self, message):
        self.messages.append(message)

    def respond_raw(self, message):
        self.messages.append(message)


class Task:
    def __init__(self, steps):
        self.queue = list(steps)
        self.current_step = None
        self.is_paused = False
        self.is_cancelled = False


class Manager:
    """QIDI's TaskQueueManager on firmware 01.01.07.01, as the harness ran it."""

    FLOW_MAP = {0: [], 3: ["BOX_HEAT", "BOX_CUT", "BOX_UNLOAD"], 4: ["BOX_EJECT"],
                5: ["BOX_HEAT", "BOX_CUT", "BOX_UNLOAD", "BOX_EJECT"],
                6: ["EXT_HEAT", "EXT_LOAD", "EXT_WIPE"],
                7: ["EXT_HEAT", "EXT_CUT", "EXT_UNLOAD"]}

    def __init__(self, gcode, unload_failures, unload_errors):
        self.gcode = gcode
        self.current_task = None
        self.current_flow_temp = None
        self.unload_failures = unload_failures
        self.unload_errors = unload_errors
        self.wipe_errors = 0
        # The Box slot whose unload from the toolhead BOX_UNLOAD runs, if any.
        self.stepper = None
        run = gcode.run_script_from_command
        self.method_map = {
            "BOX_HEAT": lambda: run("MOVE_TO_TRASH\nM109 S%s" % (self.current_flow_temp,)) or True,
            "BOX_CUT": lambda: run("CUT_FILAMENT_1") or True,
            "BOX_UNLOAD": self.unload,
            "BOX_EJECT": lambda: run("MOVE_TO_TRASH\n") or True,
            "EXT_HEAT": lambda: run("MOVE_TO_TRASH\nM109 S%s" % (self.current_flow_temp,)) or True,
            "EXT_CUT": lambda: run("CUT_FILAMENT_1") or True,
            "EXT_UNLOAD": lambda: run("M83\nG1 E-60 F300") or True,
            "EXT_LOAD": lambda: True,
            "EXT_WIPE": self.external_wipe,
        }

    def external_wipe(self):
        # QIDI's ExternalAdapter.wipe: its script's errors raise, and it
        # returns False once the script has run.
        self.gcode.run_script_from_command("MOVE_TO_TRASH\nWIPE_NOZZLE_DROP\nM118 cleaning complete")
        if self.wipe_errors:
            self.wipe_errors -= 1
            raise CommandError("Move out of range")
        return False

    def unload(self):
        run = self.gcode.run_script_from_command
        run("MOVE_TO_TRASH\n")
        run("M109 S%s" % (self.current_flow_temp if self.current_flow_temp is not None else 0,))
        if self.stepper is not None:
            return self.stepper.extruder_unload_single_func()
        for line in ("M83", "G1 E-30 F300", "M400", "G1 E-120 F600", "M400"):
            run(line)
        if self.unload_errors:
            self.unload_errors -= 1
            raise CommandError("Error during homing b_endstop: Communication timeout during homing")
        if self.unload_failures:
            self.unload_failures -= 1
            return False
        return True

    def start_flow(self, flow_id, on_complete):
        self.current_task = Task(self.FLOW_MAP[flow_id])
        self.on_complete = on_complete
        self._run()

    def _run(self):
        task = self.current_task
        while task.queue:
            task.current_step = task.queue[0]
            if not self.method_map[task.current_step]():
                task.is_paused = True
                self.gcode.respond_info("Step %s failed. Pausing flow." % (task.current_step,))
                return
            task.queue.pop(0)
        self.on_complete(task)
        self.current_task = None
        self.current_flow_temp = None

    def retry(self, can_next=0):
        if self.current_flow_temp is not None:
            self.gcode.run_script_from_command("M109 S%s\n" % (self.current_flow_temp,))
        self.current_task.is_paused = False
        self._run()

    def stop(self):
        self.current_task = None


class Controller:
    def __init__(self, gcode, flow, unload_failures=0, unload_errors=0):
        self.gcode = gcode
        self.flow = flow
        self.task_queue_manager = Manager(gcode, unload_failures, unload_errors)
        for name in ("UNLOAD_FILAMENT_CONTROL", "EJECT_FILAMENT_CONTROL"):
            gcode.register_command(name, self.cmd_FILAMENT_CONTROL)
        gcode.register_command("FLOW_STOP", lambda gcmd: self.task_queue_manager.stop())
        gcode.register_command("FLOW_RETRY", lambda gcmd: self.task_queue_manager.retry())
        gcode.register_command("T_MANAGER", lambda gcmd: None)

    def cmd_FILAMENT_CONTROL(self, gcmd):
        manager = self.task_queue_manager
        manager.current_flow_temp = 260
        manager.start_flow(self.flow, self.normal_complete_script)

    def normal_complete_script(self, task):
        self.gcode.run_script_from_command(
            "MOVE_TO_TRASH\nM104 S0\nSET_STEPPER_ENABLE STEPPER=extruder enable=0\n")
        if self.task_queue_manager.current_flow_temp is not None:
            self.gcode.run_script_from_command("G4 P5000\nWIPE_NOZZLE_DROP\n")


class FilamentCommand:
    def __init__(self, **params):
        self.params = params

    def get_int(self, name, default=None, minval=None, maxval=None):
        return int(self.params.get(name, default)) if self.params.get(name, default) is not None else None

    def get(self, name, default=None):
        return self.params.get(name, default)

    def error(self, message):
        return CommandError(message)

    def respond_info(self, message):
        pass


class ColdBoxFlowTest(unittest.TestCase):
    """COLD=1 unloads and ejects run QIDI's flow without heating, retracting cold."""

    def setup(self, flow=3, unload_failures=0, unload_errors=0, print_state="standby"):
        printer = Printer(25., print_state=print_state)
        gcode = printer.objects["gcode"] = Dispatcher(printer)
        controller = Controller(gcode, flow, unload_failures, unload_errors)
        printer.objects["multi_color_controller"] = controller
        config = types.SimpleNamespace(get_printer=lambda: printer)
        printer.objects["tltg_cold_cut"] = cold_cut.ColdCut(config)
        printer.objects["toolhead"].get_status = lambda e: {"max_accel": 20000.}
        printer.register_event_handler = lambda event, handler: None
        recovery = box_flow_recovery.BoxFlowRecovery(config)
        recovery._handle_ready()
        return printer, gcode, controller.task_queue_manager

    def unload(self, gcode, **params):
        gcode.handlers["UNLOAD_FILAMENT_CONTROL"](FilamentCommand(SLOT=2, **params))

    def test_cold_unload_cuts_and_pulls_back_without_heating(self) -> None:
        printer, gcode, manager = self.setup()
        self.unload(gcode, COLD=1)
        self.assertEqual(gcode.heated, [])
        self.assertEqual(printer.moves, [-4., -30., -120.])
        self.assertIsNone(manager.current_task)
        self.assertIn("M104 S0", gcode.sent)
        self.assertNotIn("WIPE_NOZZLE_DROP", gcode.sent)
        with self.assertRaises(CommandError):
            printer.move(-4.)
        gcode.run_script_from_command("M109 S240")
        self.assertEqual(gcode.heated, ["M109 S240"])

    def test_unload_without_cold_heats_as_before(self) -> None:
        printer, gcode, manager = self.setup()
        printer.heater.smoothed_temp = 260.
        printer.heater.temperature_callback()
        self.unload(gcode)
        self.assertEqual(gcode.heated, ["M109 S260", "M109 S260"])
        self.assertIn("WIPE_NOZZLE_DROP", gcode.sent)

    def test_retry_of_a_paused_cold_unload_stays_cold(self) -> None:
        for failure in ({"unload_failures": 1}, {"unload_errors": 1}):
            with self.subTest(**failure):
                printer, gcode, manager = self.setup(**failure)
                try:
                    self.unload(gcode, COLD=1)
                except CommandError:
                    pass
                self.assertTrue(manager.current_task.is_paused)
                with self.assertRaises(CommandError):
                    printer.move(-4.)
                gcode.handlers["FLOW_RETRY"](FilamentCommand())
                self.assertIsNone(manager.current_task)
                self.assertEqual(gcode.heated, [])
                self.assertEqual(printer.moves, [-4., -30., -120., -30., -120.])

    def test_flow_with_other_steps_heats_and_says_so(self) -> None:
        printer, gcode, manager = self.setup(flow=7)
        printer.heater.smoothed_temp = 260.
        printer.heater.temperature_callback()
        self.unload(gcode, COLD=1)
        self.assertEqual(gcode.heated, ["M109 S260"])
        self.assertTrue(any("heats the nozzle" in message for message in gcode.messages))

    def test_cold_refused_during_a_print(self) -> None:
        for state in ("printing", "paused"):
            with self.subTest(state=state):
                printer, gcode, manager = self.setup(print_state=state)
                with self.assertRaises(CommandError):
                    self.unload(gcode, COLD=1)
                self.assertEqual((gcode.sent, printer.moves), ([], []))


class BoxStepper:
    """QIDI's Box slot, as the harness ran its unload from the toolhead on the
    Max 4, Plus 5, and Smart 5: after the pull-back, 10 mm pulls while the
    toolhead sensor detects filament, up to seven, and three attempts in all
    before QDE_004_008. The Box pulls only once the sensor is clear."""

    def __init__(self, printer, gcode, clears_after, pull="G1 E-10 F300"):
        self.printer = printer
        self.gcode = gcode
        self.clears_after = clears_after
        self.pull = pull
        self.box_pulls = 0
        self.start = None
        self.runout_helper = self

    @property
    def filament_present(self):
        pulled = -sum(self.printer.moves[self.start:]) if self.start is not None else 0.
        return int(self.clears_after is None or pulled < self.clears_after)

    def extruder_unload_single_func(self):
        self.start = len(self.printer.moves)
        getattr(self, "during_unload", lambda: None)()
        # QIDI aborts auto-feed, unbinds the slot, and ties it to the extruder for the pull-back.
        extruder_stepper = getattr(self, "extruder_stepper", None)
        if extruder_stepper is not None:
            self.printer.lookup_object("box_autofeed").auto_abort()
            extruder_stepper.sync_to_extruder("extruder")
        sensor = self.printer.lookup_object(box_flow_recovery.TOOLHEAD_SENSOR)
        for attempt in range(3):
            if attempt:
                self.gcode.run_script_from_command("G1 Y380 F9000\nG1 X30 F9000\nG1 X30 Y30 F15000\nM400")
            for line in ("M83\n", "G1 E-30 F300\n", "M400\n", "G1 E-55 F600\n", "M400\n"):
                self.gcode.run_script_from_command(line)
            for _ in range(7):
                if not sensor.runout_helper.filament_present:
                    break
                for line in ("M83\n", self.pull + "\n", "M400\n"):
                    self.gcode.run_script_from_command(line)
            if not sensor.runout_helper.filament_present:
                self.box_pulls += 1
                self.printer.objects["box_extras"].b_endstop_state = 1
                return True
        self.gcode.run_script_from_command("MOVE_TO_TRASH\nM400")
        self.gcode.respond_info("Code:QDE_004_008; Message:Extruder unloading failure.")
        return False


class ToolheadUnloadTest(unittest.TestCase):
    """Filament caught at the toolhead stops a Box unload 20 mm past its
    pull-back, instead of three rounds of pull-backs, and a tool change
    that QIDI's auto-reload bug stops says which slot ran out."""

    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def setup(self, clears_after, print_state="standby", **stepper):
        printer = Printer(260., print_state=print_state)
        gcode = printer.objects["gcode"] = Dispatcher(printer)
        controller = Controller(gcode, 3)
        manager = controller.task_queue_manager
        stepper = manager.stepper = BoxStepper(printer, gcode, clears_after, **stepper)
        printer.objects["multi_color_controller"] = controller
        printer.objects[box_flow_recovery.TOOLHEAD_SENSOR] = stepper
        printer.objects["box_extras"] = types.SimpleNamespace(b_endstop_state=0)
        printer.lookup_objects = lambda prefix: [("box_stepper slot2", stepper)]
        printer.register_event_handler = lambda event, handler: None
        gcode.handlers["PAUSE"] = lambda gcmd: gcode.sent.append("PAUSED")

        def tool_change(gcmd):
            # T_MANAGER runs a print's tool change steps itself.
            manager.current_task = Task(["BOX_CUT", "BOX_UNLOAD", "BOX_LOAD"])
            manager.method_map["BOX_LOAD"] = lambda: True
            manager._run()
        gcode.handlers["T_MANAGER"] = tool_change
        config = types.SimpleNamespace(get_printer=lambda: printer)
        box_flow_recovery.BoxFlowRecovery(config)._handle_ready()
        return printer, gcode, manager, stepper

    def unload(self, gcode):
        gcode.handlers["UNLOAD_FILAMENT_CONTROL"](FilamentCommand(SLOT=2))

    def test_filament_that_clears_within_two_pulls_unloads_as_qidis_does(self) -> None:
        for clears_after, pulls in ((40., []), (95., [-10.]), (105., [-10., -10.])):
            with self.subTest(clears_after=clears_after):
                printer, gcode, manager, stepper = self.setup(clears_after)
                self.unload(gcode)
                self.assertEqual(printer.moves, [-30., -55.] + pulls)
                self.assertEqual(stepper.box_pulls, 1)
                self.assertIsNone(manager.current_task)

    def test_caught_filament_stops_the_idle_unload_before_the_box_pulls(self) -> None:
        printer, gcode, manager, stepper = self.setup(None)
        with self.assertRaises(CommandError) as caught:
            self.unload(gcode)
        self.assertEqual(printer.moves, [-30., -55., -10., -10.])
        self.assertEqual(stepper.box_pulls, 0)
        message = str(caught.exception)
        self.assertIn("1C", message)
        self.assertIn("105 mm", message)
        self.assertIn("in the Box", message)
        self.assertIn("FLOW_RETRY", message)
        self.assertTrue(manager.current_task.is_paused)
        self.assertEqual(manager.current_task.current_step, "BOX_UNLOAD")
        # Freed by hand, the retry unloads it.
        stepper.clears_after = 0.
        gcode.handlers["FLOW_RETRY"](FilamentCommand())
        self.assertEqual(stepper.box_pulls, 1)
        self.assertIsNone(manager.current_task)

    def test_caught_filament_in_a_tool_change_pauses_the_print(self) -> None:
        printer, gcode, manager, stepper = self.setup(None, print_state="printing")
        gcode.handlers["T_MANAGER"](Params("T_MANAGER T=3"))
        self.assertEqual(printer.moves, [-30., -55., -10., -10.])
        self.assertEqual(stepper.box_pulls, 0)
        self.assertIn("PAUSED", gcode.sent)
        self.assertTrue(manager.current_task.is_paused)
        self.assertTrue(any("1C" in m and "RESUME" in m and "FLOW_RETRY" not in m for m in gcode.messages))

    def test_pulls_klipper_did_not_move_say_so(self) -> None:
        printer, gcode, manager, stepper = self.setup(40.)
        printer.move = lambda e: None
        with self.assertRaises(CommandError) as caught:
            self.unload(gcode)
        self.assertEqual(stepper.box_pulls, 0)
        message = str(caught.exception)
        self.assertIn("didn't move the extruder for the 105 mm", message)
        self.assertNotIn("Something is holding", message)

    def test_slot_step_clock_is_reset_with_auto_feed_aborted_before_the_pull_back(self) -> None:
        printer, gcode, manager, stepper = self.setup(40.)
        events = []

        class ExtruderStepper:
            def __init__(self):
                self.stepper = types.SimpleNamespace(note_homing_end=lambda: events.append("reset clock"))

            def sync_to_extruder(self, name):
                events.append("sync %s" % (name,))

        stepper.extruder_stepper = ExtruderStepper()
        printer.objects["box_autofeed"] = types.SimpleNamespace(auto_abort=lambda: events.append("abort feed"))
        printer.objects["toolhead"].wait_moves = lambda: events.append("wait moves")
        printer.objects["toolhead"].flush_step_generation = lambda: None
        gcode.handlers["SET_LIMIT_A"] = lambda gcmd: events.append("no feed signal" if gcmd.get("STATE") == "0" else gcmd.line)
        real_move = printer.move
        printer.move = lambda e: (events.append("pull"), real_move(e))
        for unloads in (1, 2):
            events.clear()
            self.unload(gcode)
            first_pull = events.index("pull")
            self.assertEqual(events[:first_pull], ["abort feed", "wait moves", "no feed signal", "abort feed", "abort feed",
                                                   "reset clock", "sync extruder"])
            self.assertEqual(stepper.box_pulls, unloads)
        # The hook is gone after the unload.
        self.assertNotIn("sync_to_extruder", vars(stepper.extruder_stepper))

    def test_unload_log_counts_the_box_buffer_feeds_before_and_during_it(self) -> None:
        logging.disable(logging.NOTSET)
        root = logging.getLogger()
        lines = []
        capture = logging.Handler(logging.INFO)
        capture.emit = lambda record: lines.append(record.getMessage())
        root.addHandler(capture)
        self.addCleanup(root.removeHandler, capture)
        self.addCleanup(root.setLevel, root.level)
        root.setLevel(logging.INFO)
        printer, gcode, manager, stepper = self.setup(40.)
        feed = "[MCB] limit_a_event: raw_state=0 normalized=1"
        for _ in range(3):
            logging.info(feed)
            logging.info("[MCB] limit_a_event: raw_state=1 normalized=0")
        stepper.during_unload = lambda: logging.info(feed)
        # Klipper makes the slot motor's steps, synced to the extruder, when the M400 after a pull runs.
        flushed = []
        real_run = gcode.run_script_from_command
        gcode.run_script_from_command = lambda script: (real_run(script), script.strip() == "M400"
                                                        and flushed.append(sum(printer.moves)))
        stepper.stepper = types.SimpleNamespace(get_mcu_position=lambda: flushed[-1] * 100 if flushed else 0,
                                                get_step_dist=lambda: .01)
        self.unload(gcode)
        self.unload(gcode)
        unloads = [line for line in lines if "1C unload from the toolhead" in line]
        self.assertIn("slot motor moved -85 mm during the pulls", unloads[0])
        self.assertIn("3 feeds before it since the last unload from the toolhead, 1 during it, switch pressed", unloads[0])
        self.assertIn("0 feeds before it since the last unload from the toolhead, 1 during it", unloads[1])

    def test_box_code_with_other_pulls_stops_after_the_same_20_mm(self) -> None:
        printer, gcode, manager, stepper = self.setup(None, pull="G1 E-5 F600")
        with self.assertRaises(CommandError):
            self.unload(gcode)
        self.assertEqual(printer.moves, [-30., -55., -5., -5., -5., -5.])
        self.assertEqual(stepper.box_pulls, 0)
        self.assertTrue(manager.current_task.is_paused)

    def test_tool_change_after_a_slot_ran_out_names_the_slot_and_pauses_the_print(self) -> None:
        printer, gcode, manager, stepper = self.setup(None, print_state="printing")
        printer.objects["save_variables"] = types.SimpleNamespace(allVariables={"last_load_slot": "slot2"})

        def tool_change(gcmd):
            raise AttributeError("'BoxExtruderStepper' object has no attribute 'loaded_stepper'")
        recovery = box_flow_recovery.BoxFlowRecovery(types.SimpleNamespace(get_printer=lambda: printer))
        recovery.originals["T_MANAGER"] = tool_change
        recovery.manager = manager
        recovery.cmd_T_MANAGER(Params("T_MANAGER T=3"))
        self.assertIn("PAUSED", gcode.sent)
        self.assertTrue(any("1C ran out" in m and "RESUME" in m for m in gcode.messages))


class Motion:
    """Klipper's gcode_move and toolhead, as far as exclude_object uses them: G1 in absolute or relative coordinates
    goes through the move transform to the toolhead, which keeps every place the nozzle went. Extrusion is
    relative, as QIDI's and the slicers' M83 make it."""

    def __init__(self, printer):
        self.printer = printer
        self.position = [0., 0., 0., 0.]
        self.path = []
        self.transform = self
        self.last_position = [0., 0., 0., 0.]
        self.relative = False
        self.saved = False

    def get_extruder(self):
        return types.SimpleNamespace(get_name=lambda: "extruder")

    def move(self, newpos, speed):
        if newpos[3] != self.position[3]:
            self.printer.moves.append(round(newpos[3] - self.position[3], 3))
        self.position = list(newpos)
        self.path.append(tuple(round(v, 3) for v in newpos[:3]))

    def get_position(self):
        return list(self.position)

    def set_move_transform(self, transform, force=False):
        previous, self.transform = self.transform, transform
        return previous

    def reset_last_position(self):
        self.last_position = self.transform.get_position()

    def run(self, line):
        words = line.split()
        if words[0] in ("G90", "G91"):
            self.relative = words[0] == "G91"
        elif words[0] == "SAVE_GCODE_STATE":
            self.saved = self.relative
        elif words[0] == "RESTORE_GCODE_STATE":
            self.relative = self.saved
        elif words[0] in ("G0", "G1"):
            newpos = list(self.last_position)
            for word in words[1:]:
                axis = "XYZE".find(word[0])
                if axis >= 0:
                    value = float(word[1:])
                    newpos[axis] = newpos[axis] + value if axis == 3 or self.relative else value
            self.transform.move(newpos, 100.)
            self.last_position = newpos


class MotionDispatcher(Dispatcher):
    def __init__(self, printer, motion):
        super().__init__(printer)
        self.motion = motion

    def run_script_from_command(self, script):
        for line in script.split("\n"):
            line = line.strip()
            if not line:
                continue
            self.sent.append(line)
            command = line.split()[0]
            if command in self.handlers:
                self.handlers[command](Params(line))
            else:
                self.motion.run(line)


class CancelledPartTest(unittest.TestCase):
    """A filament change inside a cancelled part still cuts, unloads, and purges, the print paused inside one
    still moves, and the part's own lines stay dropped."""

    def setUp(self) -> None:
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        printer = self.printer = Printer(260.)
        self.state = "printing"
        motion = self.motion = Motion(printer)
        gcode = self.gcode = printer.objects["gcode"] = MotionDispatcher(printer, motion)
        controller = Controller(gcode, 3)
        manager = self.manager = controller.task_queue_manager
        stepper = self.stepper = manager.stepper = BoxStepper(printer, gcode, 40.)
        printer.objects.update({
            "multi_color_controller": controller,
            box_flow_recovery.TOOLHEAD_SENSOR: stepper,
            "box_extras": types.SimpleNamespace(b_endstop_state=0),
            "toolhead": motion,
            "tuning_tower": types.SimpleNamespace(is_active=lambda: False),
            "print_stats": types.SimpleNamespace(get_status=lambda e: {"state": self.state}),
        })
        printer.lookup_objects = lambda prefix: [("box_stepper slot2", stepper)]
        self.events = {}
        printer.register_event_handler = lambda event, handler: self.events.setdefault(event, []).append(handler)
        printer.load_object = lambda config, name: motion
        gcode.handlers["PAUSE"] = lambda gcmd: gcode.sent.append("PAUSED")
        gcode.handlers["CUT_FILAMENT_1"] = lambda gcmd: gcode.run_script_from_command(
            "G1 Y0 F15000\nG1 X3 F15000\nM83\nG1 E-4 F1800")
        gcode.handlers["TOOL_CHANGE_START"] = gcode.handlers["TOOL_CHANGE_END"] = lambda gcmd: None

        def tool_change(gcmd):
            manager.current_task = Task(["BOX_CUT", "BOX_UNLOAD", "BOX_LOAD"])
            manager.method_map["BOX_LOAD"] = lambda: run("G1 E40 F300") or True
            manager.on_complete = lambda task: None
            manager._run()
        run = gcode.run_script_from_command
        gcode.handlers["T_MANAGER"] = tool_change
        config = types.SimpleNamespace(get_printer=lambda: printer)
        exclude = printer.objects["exclude_object"] = exclude_object.ExcludeObject(config)
        exclude._handle_connect()
        box_flow_recovery.BoxFlowRecovery(config)._handle_ready()
        # A is cancelled, B prints, and the print reaches A.
        self.send("EXCLUDE_OBJECT_DEFINE NAME=A", "EXCLUDE_OBJECT_DEFINE NAME=B", "M83", "EXCLUDE_OBJECT NAME=A",
                  "EXCLUDE_OBJECT_START NAME=B", "G1 X10 Y10 Z0.2 F600",
                  *("G1 X%d E1" % (x,) for x in range(11, 17)), "EXCLUDE_OBJECT_END NAME=B",
                  "EXCLUDE_OBJECT_START NAME=A", "G1 X100 Y100", "G1 X110 E1")

    def send(self, *lines):
        for line in lines:
            self.gcode.run_script_from_command(line)

    def went(self, x=None, y=None, z=None):
        return any((x is None or p[0] == x) and (y is None or p[1] == y) and (z is None or p[2] == z)
                   for p in self.motion.path)

    def test_filament_change_inside_a_cancelled_part_cuts_unloads_and_purges(self) -> None:
        before = len(self.motion.path)
        self.send("G1 Z3.2 F1200", "TOOL_CHANGE_START F=0 T=1", "G1 E-2 F1800", "T_MANAGER T=1",
                 "G1 Y403.5 F2000", "G1 E30 F300", "TOOL_CHANGE_END", "G1 E3 F300",
                 "G1 X120 Y100 Z0.2", "G1 X130 E1", "EXCLUDE_OBJECT_END NAME=A",
                 "EXCLUDE_OBJECT_START NAME=B", "G1 X30 Y10 Z0.4", "G1 X40 E1", "EXCLUDE_OBJECT_END NAME=B")
        # Straight up from where B ended to the slicer's lift, before moving anywhere else.
        self.assertEqual(self.motion.path[before:before + 2], [(16., 10., 3.21), (16., 10., 3.2)])
        self.assertTrue(self.went(3., 0., 3.2), "never reached the cutter")
        self.assertEqual(self.stepper.box_pulls, 1)
        self.assertNotIn("PAUSED", self.gcode.sent)
        self.assertIn(40., self.printer.moves)
        self.assertTrue(self.went(y=403.5), "never reached the waste chute")
        self.assertFalse(self.went(x=120.) or self.went(x=130.), "printed the cancelled part")
        self.assertTrue(self.went(40., 10., 0.4))

    def test_paused_inside_a_cancelled_part_still_moves(self) -> None:
        self.state = "paused"
        self.send("G91", "G1 Z5", "G90", "G1 X200 Y200", "G1 E-5")
        self.assertEqual(self.motion.path[-3:], [(16., 10., 5.2), (200., 200., 5.2), (200., 200., 5.2)])
        self.assertEqual(self.printer.moves[-1], -5.)
        self.state = "printing"
        self.send("G1 X140 Y100 Z0.2 E1", "EXCLUDE_OBJECT_END NAME=A",
                 "EXCLUDE_OBJECT_START NAME=B", "G1 X50 Y10 Z0.2 E1")
        self.assertFalse(self.went(x=140.), "printed the cancelled part")
        self.assertEqual(self.motion.path[-1], (50., 10., 0.2))

    def test_cancelled_part_stays_cancelled_after_a_filament_change_that_never_ends(self) -> None:
        for end in (("EXCLUDE_OBJECT_END NAME=A", "EXCLUDE_OBJECT_START NAME=A"), ()):
            with self.subTest(end=end):
                self.setUp()
                self.send("TOOL_CHANGE_START F=0 T=1", "EXCLUDE_OBJECT_END NAME=A", *end)
                if not end:
                    for handler in self.events["virtual_sdcard:reset_file"]:
                        handler()
                    self.send("EXCLUDE_OBJECT NAME=A", *("G1 X%d E1" % (x,) for x in range(5)),
                              "EXCLUDE_OBJECT_START NAME=A")
                self.send("G1 X150 Y100 E1")
                self.assertFalse(self.went(x=150.), "printed the cancelled part")


class ExternalLoadTest(unittest.TestCase):
    """QIDI's external-spool load ends with a wipe that reports failure after
    it has run."""

    def load(self, reviewed, wipe_errors=0):
        printer = Printer(25.)
        gcode = printer.objects["gcode"] = Dispatcher(printer)
        controller = Controller(gcode, 6)
        manager = controller.task_queue_manager
        manager.wipe_errors = wipe_errors
        printer.objects["multi_color_controller"] = controller
        config = types.SimpleNamespace(get_printer=lambda: printer)
        printer.register_event_handler = lambda event, handler: None
        with open(__file__, "rb") as f:
            build = hashlib.sha256(f.read()).hexdigest()
        builds = frozenset((build,)) if reviewed else box_flow_recovery.EXTERNAL_WIPE_FALSE_BUILDS
        with mock.patch.object(box_flow_recovery, "EXTERNAL_WIPE_FALSE_BUILDS", builds):
            box_flow_recovery.BoxFlowRecovery(config)._handle_ready()
            completed = []
            try:
                manager.start_flow(6, completed.append)
            except CommandError:
                pass
        return gcode, manager, completed

    def test_load_completes_after_the_wipe_runs(self) -> None:
        gcode, manager, completed = self.load(reviewed=True)
        self.assertIn("M118 cleaning complete", gcode.sent)
        self.assertEqual(len(completed), 1)
        self.assertIsNone(manager.current_task)
        self.assertFalse(any("failed" in message for message in gcode.messages))

    def test_wipe_error_still_pauses_the_flow(self) -> None:
        gcode, manager, completed = self.load(reviewed=True, wipe_errors=1)
        self.assertEqual(completed, [])
        self.assertTrue(manager.current_task.is_paused)
        self.assertIn("Step EXT_WIPE failed. Pausing flow.", gcode.messages)

    def test_other_builds_keep_qidis_result(self) -> None:
        gcode, manager, completed = self.load(reviewed=False)
        self.assertEqual(completed, [])
        self.assertTrue(manager.current_task.is_paused)
        self.assertIn("Step EXT_WIPE failed. Pausing flow.", gcode.messages)


if __name__ == "__main__":
    unittest.main()
