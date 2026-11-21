from __future__ import annotations

import hashlib
import logging
import sys
import threading
import types
import unittest
from unittest import mock

from installer.tests.klipper_fakes import CommandError, load_extra

cold_cut = load_extra("cold_cut")

# Klipper's greenlet, which only names the running command here.
sys.modules.setdefault("greenlet", types.SimpleNamespace(getcurrent=lambda: None))
box_flow_recovery = load_extra("box_flow_recovery")

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
        return types.SimpleNamespace(monotonic=lambda: 0.)

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
