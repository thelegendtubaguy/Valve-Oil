"""Valve Oil's QIDI Box slot modules: the feeder test, the slot jog, loaded-slot recovery, and the filament ID
guard, against one model of a Box whose slot motors move filament past its hub and toolhead sensors."""
from __future__ import annotations

import ast
import collections
import csv
import logging
import math
import tempfile
import types
import unittest
from pathlib import Path

from installer.tests.klipper_fakes import NEVER, CommandError, Reactor, load_extra

# Feed distance from a slot's parked filament to each sensor, in mm. Neither is a whole number of test moves.
HUB_AT = 35.
TOOLHEAD_AT = 121.3
STEP_DIST = 0.01
TOOLHEAD_SENSOR = "filament_switch_sensor filament_switch_sensor"
REQUIRED = object()

Move = collections.namedtuple("Move", "slot distance runout_sensor_on")


def calc_move_time(dist, speed, accel):
    # Klipper's force_move.calc_move_time.
    axis_r = 1. if dist >= 0. else -1.
    dist = abs(dist)
    accel_t = speed / accel
    accel_decel_d = accel_t * speed
    cruise_t = (dist - accel_decel_d) / speed
    if cruise_t < 0.:
        speed = math.sqrt(dist * accel)
        accel_t = speed / accel
        cruise_t = 0.
    return axis_r, accel_t, cruise_t, speed


class Ffi:
    """Klipper's chelper: a move put on a motion queue turns every motor following that queue."""

    NULL = "NULL"

    def __init__(self, box):
        self.box = box

    def gc(self, obj, free):
        return obj

    def trapq_alloc(self):
        return object()

    def cartesian_stepper_alloc(self, axis):
        return object()

    def free(self, obj):
        pass

    trapq_free = free

    def trapq_append(self, trapq, start, accel_t, cruise_t, decel_t, start_x, start_y, start_z,
                     axis_r, axis_r_y, axis_r_z, start_v, cruise_v, accel):
        self.box.queued += 1
        if self.box.queued > 5000:
            raise AssertionError("motion that never ends")
        self.box.turn(trapq, start_x, axis_r * cruise_v * (cruise_t + (accel_t + decel_t) / 2.))

    def trapq_finalize_moves(self, trapq, free_time, clear_history_time):
        pass


def load_module(name, ffi):
    """Import a Valve Oil module from Klipper's extras package, where chelper and force_move are importable."""
    extras = types.ModuleType("extras")
    extras.__path__ = []
    force_move = types.ModuleType("extras.force_move")
    force_move.calc_move_time = calc_move_time
    chelper = types.ModuleType("chelper")
    chelper.get_ffi = lambda: (ffi, ffi)
    return load_extra(name, "extras.tltg_" + name, stand_ins={
        "extras": extras, "extras.force_move": force_move, "chelper": chelper})


class Stepper:
    """Klipper's MCU_stepper for a slot motor, on QIDI's own motion queue until something borrows it."""

    def __init__(self, number):
        self.name = "slot%d" % number
        self.trapq, self.sk = "qidi-tq-%d" % number, "qidi-sk-%d" % number
        self.commanded = 0.
        self.travel = 0.          # mm the host has stepped this motor
        self.outside_steps = 0    # steps the MCU counted that the host never commanded
        self.filament = 0.        # mm of filament past the slot's park position
        self.furthest = 0.
        self._oid, self._invert_dir = number, False
        self._get_position_cmd = types.SimpleNamespace(
            send=lambda args: {"pos": self.get_mcu_position() + self.outside_steps})

    def feed(self, distance):
        self.filament += distance
        self.furthest = max(self.furthest, self.filament)

    def get_name(self):
        return self.name

    def get_trapq(self):
        return self.trapq

    def set_trapq(self, tq):
        old, self.trapq = self.trapq, tq
        return old

    def get_stepper_kinematics(self):
        return self.sk

    def set_stepper_kinematics(self, sk):
        old, self.sk = self.sk, sk
        return old

    def set_position(self, pos):
        self.commanded = pos[0]

    def get_commanded_position(self):
        return self.commanded

    def get_mcu_position(self):
        return round(self.travel / STEP_DIST)


class Slot:
    """QIDI's box_stepper slotN: the slot's motor and sensors, and the load and unload moves QIDI's flows call."""

    def __init__(self, number):
        self.stepper = Stepper(number)
        self.r_endstop_state = 0    # inlet sensor: 0 with filament
        self.runout_button = 0
        self.fault = None

    def get_status(self, eventtime):
        return {"runout_button": self.runout_button}

    def do_home_three_steps(self, distance):
        self.stepper.feed(distance)
        if self.fault is not None:
            raise self.fault
        return "fed"

    def do_home_double_steps(self, distance):
        self.stepper.feed(-distance)
        return "retracted"


class BoxExtras:
    """QIDI's box_extras: the Box hub sensor reads 0 with filament at the hub."""

    def __init__(self, box):
        self.box = box

    @property
    def b_endstop_state(self):
        return 0 if any(slot.stepper.filament >= HUB_AT for slot in self.box.slots) else 1


class RunoutHelper:
    """QIDI's toolhead filament sensor. While enabled, filament leaving it pauses and starts a Box reload."""

    def __init__(self, box):
        self.box = box
        self.sensor_enabled = True

    @property
    def filament_present(self):
        return any(slot.stepper.filament >= self.box.toolhead_at for slot in self.box.slots)


class BoxAdapterV1:
    """QIDI's USB Box adapter. Its unload step retracts the slot saved as last_load_slot, and refuses while
    filament is at the hub and that slot is slot16 or unsaved."""

    def __init__(self, box):
        self.box = box
        self.unloads = 0
        self.jammed = False

    def unload_filament(self):
        self.unloads += 1
        if self.jammed:
            return False
        saved = self.box.variables.allVariables.get("last_load_slot", "slot16")
        if saved == "slot16":
            return self.box.extras.b_endstop_state != 0
        slot = self.box.slot(int(saved[4:]))
        slot.do_home_double_steps(slot.stepper.filament)
        return True


class FlowManager:
    """QIDI's task_queue_manager: a flow runs each of its steps as method_map has it when the flow starts."""

    def __init__(self):
        self.method_map = {}

    def start_flow(self, steps):
        return [self.method_map[step]() for step in steps]


class Controller:
    """QIDI's multi_color_controller: its task status, and the flow manager whose steps QIDI's adapters register."""

    def __init__(self):
        self.task = {"flow_id": None, "current_queue": [], "current_step": "", "is_paused": False}
        self.task_queue_manager = FlowManager()

    def get_status(self, eventtime):
        return {"task": self.task}


class Status:
    def __init__(self, status):
        self.status = status

    def get_status(self, eventtime):
        return self.status


class SaveVariables:
    """QIDI's save_variables."""

    def __init__(self, variables):
        self.allVariables = dict(variables)
        self.saved = []
        self.fault = None

    def cmd_SAVE_VARIABLE(self, gcmd):
        if self.fault is not None:
            raise self.fault
        name, value = gcmd.get("VARIABLE"), ast.literal_eval(gcmd.get("VALUE"))
        self.saved.append((name, value))
        self.allVariables = dict(self.allVariables, **{name: value})


class Enable:
    def __init__(self):
        self.enabled = False

    def is_motor_enabled(self):
        return self.enabled

    def motor_enable(self, print_time):
        self.enabled = True

    def motor_disable(self, print_time):
        self.enabled = False


class StepperEnable:
    def __init__(self):
        self.lines = {}

    def lookup_enable(self, name):
        return self.lines.setdefault(name, Enable())


class Toolhead:
    def __init__(self):
        self.print_time = 10.
        self.fault = None
        self.extruder = types.SimpleNamespace(get_trapq=lambda: "extruder-tq")

    def get_trapq(self):
        return "toolhead-tq"

    def get_extruder(self):
        return self.extruder

    def get_last_move_time(self):
        return self.print_time

    def dwell(self, delay):
        self.print_time += max(0., delay)

    def flush_step_generation(self):
        pass

    def note_mcu_movequeue_activity(self, mq_time, set_step_gen_time=False):
        pass

    def wait_moves(self):
        if self.fault is not None:
            raise self.fault


class GCmd:
    """Klipper's GCodeCommand, with its range checks."""

    error = CommandError

    def __init__(self, params, responses):
        self.params, self.responses = params, responses

    def get(self, name, default=None):
        return self.params.get(name, default)

    def _number(self, kind, name, default, minval=None, maxval=None, above=None):
        if name not in self.params:
            if default is REQUIRED:
                raise CommandError("missing %s" % name)
            return default
        value = kind(self.params[name])
        if (minval is not None and value < minval) or (maxval is not None and value > maxval) \
                or (above is not None and value <= above):
            raise CommandError("%s out of range" % name)
        return value

    def get_int(self, name, default=REQUIRED, **limits):
        return self._number(int, name, default, **limits)

    def get_float(self, name, default=REQUIRED, **limits):
        return self._number(float, name, default, **limits)

    def respond_info(self, message):
        self.responses.append(message)


class GCode:
    """Klipper's gcode: runs registered commands, and keeps what went to the console and to other commands."""

    error = CommandError

    def __init__(self):
        self.commands, self.responses, self.scripts = {}, [], []

    def register_command(self, name, handler, desc=None):
        self.commands[name] = handler

    def create_gcode_command(self, command, commandline, params):
        return GCmd(params, self.responses)

    def respond_info(self, message):
        self.responses.append(message)

    def run_script(self, script):
        words = script.split()
        self.commands[words[0]](GCmd(dict(word.split("=", 1) for word in words[1:]), self.responses))

    def run_script_from_command(self, script):
        self.scripts.append(script)


class Printer:
    command_error = CommandError

    def __init__(self, objects, log_dir=None):
        self.objects, self.log_dir = objects, log_dir
        self.handlers = {}
        self.reactor = Reactor()
        self.reactor.on_pause = lambda waketime: setattr(self.reactor, "now", waketime)

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return {"log_file": str(self.log_dir / "klippy.log")}

    def is_shutdown(self):
        return False

    def register_event_handler(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    def send_event(self, event):
        for callback in self.handlers.get(event, []):
            callback()

    def lookup_object(self, name, default=REQUIRED):
        if name in self.objects:
            return self.objects[name]
        if default is REQUIRED:
            raise CommandError("Unknown config object '%s'" % name)
        return default

    def lookup_objects(self, module):
        return [(name, obj) for name, obj in self.objects.items() if name.split()[0] == module]


class Config:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer

    def getfloat(self, name, default, minval=None, maxval=None):
        return default


class Box:
    """One USB QIDI Box on an idle printer: four slots with filament parked at their inlets, nothing at the hub or
    the toolhead, and the named Valve Oil modules loaded."""

    def __init__(self, modules, *, variables=None, log_dir=None):
        self.toolhead_at = TOOLHEAD_AT
        self.moves = []
        self.queued = 0    # moves put on a motion queue, whether or not a motor still follows it
        self.during_move = {}
        self.slots = [Slot(number) for number in range(4)]
        self.toolhead = Toolhead()
        self.enables = StepperEnable()
        self.runout = RunoutHelper(self)
        self.extras = BoxExtras(self)
        self.variables = SaveVariables(variables or {})
        self.print_stats = Status({"state": "standby"})
        self.controller = Controller()
        self.adapter = BoxAdapterV1(self)
        self.gcode = GCode()
        objects = {
            "gcode": self.gcode,
            "toolhead": self.toolhead,
            "stepper_enable": self.enables,
            TOOLHEAD_SENSOR: types.SimpleNamespace(runout_helper=self.runout),
            "box_extras": self.extras,
            "save_variables": self.variables,
            "print_stats": self.print_stats,
            "multi_color_controller": self.controller,
        }
        objects.update(("box_stepper slot%d" % number, slot) for number, slot in enumerate(self.slots))
        self.printer = Printer(objects, log_dir)
        for name in modules:
            objects["tltg_" + name] = load_module(name, Ffi(self)).load_config(Config(self.printer))
        self.jog = objects.get("tltg_box_slot_jog")
        self.printer.send_event("klippy:ready")
        # QIDI's Box adapter registers its flow steps after Klipper is ready.
        self.controller.task_queue_manager.method_map["BOX_UNLOAD"] = self.adapter.unload_filament

    def turn(self, trapq, start, distance):
        for number, slot in enumerate(self.slots):
            if slot.stepper.trapq is trapq:
                slot.stepper.commanded = start + distance
                slot.stepper.travel += distance
                slot.stepper.feed(distance)
                self.moves.append(Move(number, distance, self.runout.sensor_enabled))
        action = self.during_move.pop(len(self.moves), None)
        if action is not None:
            action()

    def slot(self, number):
        return self.slots[number]

    def enable(self, number):
        return self.enables.lookup_enable(self.slot(number).stepper.get_name())

    def motor(self, number):
        """A slot motor's motion queue, kinematics, and whether it is energized."""
        stepper = self.slot(number).stepper
        return stepper.get_trapq(), stepper.get_stepper_kinematics(), self.enable(number).enabled

    def run(self, command):
        self.gcode.run_script(command)

    def tick(self):
        """Run each waiting timer once, as Klipper's reactor does when its time comes."""
        reactor = self.printer.reactor
        due = [timer for timer in reactor.timers if timer.waketime < NEVER]
        for timer in due:
            reactor.now = max(reactor.now, timer.waketime)
            timer.waketime = timer.callback(reactor.now)
        return bool(due)

    def run_timers(self):
        for _ in range(50):
            if not self.tick():
                return
        raise AssertionError("timers never stopped")

    def unload(self):
        """A QIDI flow with a Box unload step. True if QIDI retracted the filament."""
        (retracted,) = self.controller.task_queue_manager.start_flow(["BOX_UNLOAD"])
        return retracted


QIDIS_MOTOR_1 = ("qidi-tq-1", "qidi-sk-1", False)
BUSY = {
    "printing": lambda box: box.print_stats.status.update(state="printing"),
    "print paused": lambda box: box.print_stats.status.update(state="paused"),
    "QIDI Box flow running": lambda box: box.controller.task.update(flow_id=13),
    # Only an idle Box reports an empty step queue, whatever its flow ID says.
    "QIDI Box step queued": lambda box: box.controller.task.update(current_queue=["BOX_LOAD"]),
}


class QuietTestCase(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)


class BoxFeedTestTests(QuietTestCase):
    def box(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Box(("box_feed_test",), log_dir=Path(folder.name))

    def rows(self, box):
        (results,) = box.printer.log_dir.glob("*.csv")
        with results.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_session_is_refused_and_nothing_moves_unless_the_slot_is_free_to_feed(self):
        cases = dict(BUSY, **{
            # Filament loaded from any slot already fills the path this slot would feed into.
            "filament at the toolhead": lambda box: box.slot(0).stepper.feed(TOOLHEAD_AT),
            # A slot QIDI has synced to the extruder or toolhead queue turns with every other move on it.
            "slot follows the extruder": lambda box: box.slot(1).stepper.set_trapq("extruder-tq"),
            "slot follows the toolhead": lambda box: box.slot(1).stepper.set_trapq("toolhead-tq"),
            "no filament in the slot": lambda box: setattr(box.slot(1), "runout_button", 1),
        })
        for name, arrange in cases.items():
            with self.subTest(name):
                box = self.box()
                arrange(box)
                before = box.motor(1)
                with self.assertRaises(CommandError):
                    box.run("VO_BOX_FEED_TEST SLOT=1 FAST=100")
                box.run_timers()
                self.assertEqual(box.moves, [])
                self.assertEqual(box.motor(1), before)

    def test_a_slot_or_sensor_the_printer_doesnt_have_is_a_command_error(self):
        # Klipper shuts down on any exception from a G-code command other than its command error.
        cases = {
            "slot the printer doesn't have, checked": ("VO_BOX_FEED_CHECK SLOT=9", lambda box: None),
            "slot the printer doesn't have": ("VO_BOX_FEED_TEST SLOT=9 FAST=100", lambda box: None),
            "no toolhead filament sensor": ("VO_BOX_FEED_TEST SLOT=1 FAST=100",
                                            lambda box: box.printer.objects.pop(TOOLHEAD_SENSOR)),
        }
        for name, (command, arrange) in cases.items():
            with self.subTest(name):
                box = self.box()
                arrange(box)
                with self.assertRaises(CommandError):
                    box.run(command)
                box.run_timers()
                self.assertEqual(box.moves, [])

    def test_each_load_stops_within_a_step_of_the_toolhead_sensor_and_returns_to_park(self):
        box = self.box()
        box.run("VO_BOX_FEED_TEST SLOT=1 CYCLES=3 FAST=100 STEP=2 MARGIN_STEP=20")
        box.run_timers()
        trips = [float(row["trip_mm"]) for row in self.rows(box)]
        self.assertEqual(len(trips), 3)
        for trip in trips:
            self.assertGreaterEqual(trip, TOOLHEAD_AT)
            self.assertLessEqual(trip, TOOLHEAD_AT + 2.)
        # Later cycles start from the last trip point, and must still not run past the sensor at speed.
        self.assertLessEqual(box.slot(1).stepper.furthest, TOOLHEAD_AT + 2.)
        self.assertAlmostEqual(box.slot(1).stepper.filament, 0.)
        self.assertEqual({move.slot for move in box.moves}, {1})

    def test_motor_and_runout_sensor_are_borrowed_only_while_a_cycle_runs(self):
        # QIDI gets its motor back as it left it, energized or not.
        for energized in (False, True):
            with self.subTest(energized_before=energized):
                box = self.box()
                box.enable(1).enabled = energized
                box.run("VO_BOX_FEED_TEST SLOT=1 FAST=100")
                box.run_timers()
                self.assertTrue(box.moves)
                self.assertFalse(any(move.runout_sensor_on for move in box.moves))
                self.assertTrue(box.runout.sensor_enabled)
                self.assertEqual(box.motor(1), ("qidi-tq-1", "qidi-sk-1", energized))

    def test_load_that_misses_the_sensor_returns_the_filament_to_park_and_ends_the_session(self):
        cases = {
            # A jammed or slipping feed never shows at the toolhead.
            "sensor never reached": 5000.,
            # A shorter feed path than the fast move was told to expect.
            "sensor reached during the fast move": 60.,
        }
        for name, toolhead_at in cases.items():
            with self.subTest(name):
                box = self.box()
                box.toolhead_at = toolhead_at
                box.run("VO_BOX_FEED_TEST SLOT=1 CYCLES=3 FAST=100 MAX=150")
                box.run_timers()
                self.assertLessEqual(box.slot(1).stepper.furthest, 150.)
                self.assertAlmostEqual(box.slot(1).stepper.filament, 0.)
                self.assertEqual(len([move for move in box.moves if move.distance < 0]), 1)
                self.assertEqual([row["trip_mm"] for row in self.rows(box)], [""])
                self.assertEqual(box.motor(1), QIDIS_MOTOR_1)

    def test_interference_or_a_klipper_error_mid_cycle_ends_the_session_without_another_move(self):
        cases = {
            # QIDI's auto-reload and feed assist turn slot motors without the host commanding it.
            "motor stepped outside host control": lambda box: setattr(box.slot(1).stepper, "outside_steps", 40),
            "another slot's motor moved": lambda box: setattr(box.slot(2).stepper, "travel", 5.),
            "another slot joined the extruder": lambda box: box.slot(2).stepper.set_trapq("extruder-tq"),
            "the slot's filament ran out": lambda box: setattr(box.slot(1), "runout_button", 1),
            "QIDI Box flow started": BUSY["QIDI Box flow running"],
            "print started": BUSY["printing"],
            "Klipper error": lambda box: setattr(box.toolhead, "fault", CommandError("Klipper is shutting down")),
        }
        for name, interfere in cases.items():
            with self.subTest(name):
                box = self.box()
                box.during_move[1] = lambda: interfere(box)
                box.run("VO_BOX_FEED_TEST SLOT=1 CYCLES=3 FAST=100")
                box.run_timers()
                self.assertEqual(len(box.moves), 1)
                self.assertEqual(box.motor(1), QIDIS_MOTOR_1)
                self.assertTrue(box.runout.sensor_enabled)

    def test_motor_qidi_takes_mid_cycle_is_left_with_qidi(self):
        box = self.box()
        box.during_move[1] = lambda: box.slot(1).stepper.set_trapq("qidi-flow-tq")
        box.run("VO_BOX_FEED_TEST SLOT=1 CYCLES=3 FAST=100")
        box.run_timers()
        self.assertEqual(box.queued, 1)
        # Putting the motor back on its earlier queue, or switching it off, would break the move QIDI started.
        self.assertEqual(box.slot(1).stepper.get_trapq(), "qidi-flow-tq")
        self.assertTrue(box.enable(1).enabled)
        self.assertTrue(box.runout.sensor_enabled)

    def test_no_cycle_starts_after_the_operator_stops_the_session_or_klipper_shuts_down(self):
        stops = {
            # A cycle holds the G-code lock, so the operator's stop runs in the pause between two of them.
            "VO_BOX_FEED_STOP": lambda box: box.run("VO_BOX_FEED_STOP"),
            "Klipper shut down": lambda box: box.printer.send_event("klippy:shutdown"),
        }
        for name, stop in stops.items():
            with self.subTest(name):
                box = self.box()
                box.run("VO_BOX_FEED_TEST SLOT=1 CYCLES=3 FAST=100 PAUSE=600")
                box.tick()
                first_cycle = len(box.moves)
                self.assertTrue(first_cycle)
                stop(box)
                box.run_timers()
                self.assertEqual(len(box.moves), first_cycle)
                self.assertEqual(len(self.rows(box)), 1)


class BoxSlotJogTests(QuietTestCase):
    def test_jog_that_cant_run_is_refused_without_touching_a_motor(self):
        jog = "VO_BOX_SLOT_JOG SLOT=1 DISTANCE=30"
        cases = {name: (arrange, jog) for name, arrange in BUSY.items() if name != "print paused"}
        cases.update({
            "slot the Box doesn't have": (lambda box: None, "VO_BOX_SLOT_JOG SLOT=9 DISTANCE=30"),
            "no distance": (lambda box: None, "VO_BOX_SLOT_JOG SLOT=1 DISTANCE=0"),
        })
        for name, (arrange, command) in cases.items():
            with self.subTest(name):
                box = Box(("box_slot_jog",))
                arrange(box)
                with self.assertRaises(CommandError):
                    box.run(command)
                self.assertEqual(box.moves, [])
                self.assertEqual(box.motor(1), QIDIS_MOTOR_1)

    def test_jog_works_stuck_filament_while_a_print_or_a_failed_box_step_is_paused(self):
        def flow_paused(box):
            box.controller.task.update(flow_id=13, current_queue=["BOX_UNLOAD", "BOX_LOAD"],
                                       current_step="BOX_UNLOAD", is_paused=True)
        cases = {
            "print paused": BUSY["print paused"],
            "Box flow paused on a failed step": flow_paused,
            "tool change paused with its print": lambda box: (BUSY["print paused"](box), flow_paused(box)),
        }
        for name, arrange in cases.items():
            with self.subTest(name):
                box = Box(("box_slot_jog",))
                arrange(box)
                box.run("VO_BOX_SLOT_JOG SLOT=1 DISTANCE=-20")
                self.assertAlmostEqual(box.slot(1).stepper.filament, -20.)
                self.assertEqual(box.motor(1), QIDIS_MOTOR_1)

    def test_jog_moves_only_its_slot_by_the_distance_asked_and_hands_the_motor_back(self):
        box = Box(("box_slot_jog",))
        box.run("VO_BOX_SLOT_JOG SLOT=1 DISTANCE=30")
        box.run("VO_BOX_SLOT_JOG SLOT=1 DISTANCE=-12.5 SPEED=50")
        self.assertAlmostEqual(box.slot(1).stepper.filament, 17.5)
        self.assertEqual([box.slot(number).stepper.filament for number in (0, 2, 3)], [0., 0., 0.])
        # Filament backing out of the toolhead sensor would otherwise run QIDI's runout pause.
        self.assertFalse(any(move.runout_sensor_on for move in box.moves))
        self.assertTrue(box.runout.sensor_enabled)
        self.assertEqual(box.motor(1), QIDIS_MOTOR_1)

    def test_move_in_pieces_stops_at_the_first_piece_that_meets_its_condition(self):
        # box_rfid turns a spool this way until its tag comes past the reader.
        box = Box(("box_slot_jog",))
        moved, stopped = box.jog.move(1, 100., until=lambda: box.extras.b_endstop_state == 0, piece=10.)
        self.assertTrue(stopped)
        self.assertGreaterEqual(moved, HUB_AT)
        self.assertLess(moved, HUB_AT + 10.)
        self.assertAlmostEqual(box.slot(1).stepper.filament, moved)

        moved, stopped = box.jog.move(2, 25., until=lambda: False, piece=10.)
        self.assertFalse(stopped)
        self.assertAlmostEqual(moved, 25.)
        self.assertAlmostEqual(box.slot(2).stepper.filament, 25.)

    def test_motor_qidi_takes_mid_jog_is_left_with_qidi_and_the_jog_fails(self):
        box = Box(("box_slot_jog",))
        box.during_move[2] = lambda: box.slot(1).stepper.set_trapq("qidi-flow-tq")
        with self.assertRaises(CommandError):
            box.jog.move(1, 60., piece=10.)
        self.assertEqual(box.queued, 2)
        self.assertEqual(box.slot(1).stepper.get_trapq(), "qidi-flow-tq")
        self.assertTrue(box.enable(1).enabled)
        self.assertTrue(box.runout.sensor_enabled)

    def test_klipper_error_mid_jog_still_hands_the_motor_and_runout_sensor_back(self):
        box = Box(("box_slot_jog",))
        box.during_move[1] = lambda: setattr(box.toolhead, "fault", CommandError("Klipper is shutting down"))
        with self.assertRaises(CommandError):
            box.run("VO_BOX_SLOT_JOG SLOT=1 DISTANCE=30")
        self.assertEqual(box.motor(1), QIDIS_MOTOR_1)
        self.assertTrue(box.runout.sensor_enabled)


class BoxLoadedSlotTests(QuietTestCase):
    def hub_slot(self, box):
        return box.variables.allVariables.get("vo_box_hub_slot")

    def test_slot_whose_motion_brings_filament_to_the_hub_is_recorded(self):
        def qidi_load(box):
            self.assertEqual(box.slot(2).do_home_three_steps(HUB_AT + 5.), "fed")

        def qidi_load_that_fails(box):
            # A QIDI load that raises after its filament reached the hub leaves the filament there.
            box.slot(2).fault = CommandError("load failed")
            self.assertRaises(CommandError, box.slot(2).do_home_three_steps, HUB_AT + 5.)

        def jog(box):
            box.run("VO_BOX_SLOT_JOG SLOT=2 DISTANCE=%.1f" % (HUB_AT + 5.))

        for move in (qidi_load, qidi_load_that_fails, jog):
            with self.subTest(move.__name__):
                box = Box(("box_slot_jog", "box_loaded_slot"))
                move(box)
                self.assertEqual(self.hub_slot(box), "slot2")

    def test_record_stays_with_that_slot_until_the_hub_is_clear(self):
        box = Box(("box_loaded_slot",))
        box.slot(2).do_home_three_steps(HUB_AT + 5.)
        # Another slot moving while the hub is occupied isn't what put filament there.
        box.slot(0).do_home_three_steps(10.)
        box.slot(0).do_home_double_steps(10.)
        self.assertEqual(self.hub_slot(box), "slot2")
        box.slot(2).do_home_double_steps(HUB_AT + 5.)
        self.assertEqual(self.hub_slot(box), "")

    def test_failed_save_never_stops_qidis_slot_motion_or_unload_step(self):
        box = Box(("box_loaded_slot",), variables={"vo_box_hub_slot": "slot2"})
        box.variables.fault = CommandError("Unable to save variable")
        self.assertEqual(box.slot(2).do_home_double_steps(0.), "retracted")
        box.slot(2).stepper.feed(HUB_AT + 5.)
        self.assertFalse(box.unload())
        self.assertEqual(box.adapter.unloads, 1)

    def test_unload_names_the_hubs_slot_when_only_one_slot_can_be_it(self):
        cases = {
            "the slot recorded at the hub": dict(variables={"vo_box_hub_slot": "slot2"}, empty=()),
            "recorded, with QIDI's saved slot the external spool": dict(
                variables={"vo_box_hub_slot": "slot2", "last_load_slot": "slot16"}, empty=()),
            # A Box that disconnects partway through a load leaves filament at the hub with no record.
            "the only slot holding filament": dict(variables={}, empty=(0, 1, 3)),
        }
        for name, case in cases.items():
            with self.subTest(name):
                box = Box(("box_loaded_slot",), variables=case["variables"])
                for number in case["empty"]:
                    box.slot(number).r_endstop_state = 1
                box.slot(2).stepper.feed(HUB_AT + 5.)
                self.assertTrue(box.unload())
                self.assertEqual(box.variables.allVariables["last_load_slot"], "slot2")
                self.assertAlmostEqual(box.slot(2).stepper.filament, 0.)

    def test_unload_never_guesses_between_slots_and_turns_off_only_an_idle_nozzle(self):
        cases = {
            "two slots hold filament, none recorded": dict(variables={}, inlets=(1, 0, 0, 1)),
            "the recorded slot's inlet is empty": dict(variables={"vo_box_hub_slot": "slot2"}, inlets=(0, 0, 1, 0)),
            "an inlet sensor has no reading": dict(variables={}, inlets=(1, None, 0, 1)),
        }
        for name, case in cases.items():
            for print_state in ("standby", "printing", "paused"):
                with self.subTest(name, print_state=print_state):
                    box = Box(("box_loaded_slot",), variables=case["variables"])
                    box.print_stats.status["state"] = print_state
                    for slot, inlet in zip(box.slots, case["inlets"]):
                        slot.r_endstop_state = inlet
                    box.slot(1).stepper.feed(HUB_AT + 5.)
                    self.assertFalse(box.unload())
                    self.assertNotIn("last_load_slot", box.variables.allVariables)
                    self.assertAlmostEqual(box.slot(1).stepper.filament, HUB_AT + 5.)
                    self.assertTrue(box.gcode.responses)
                    # An idle nozzle would stay hot until the operator names the slot; a print's heat is its own.
                    self.assertEqual(box.gcode.scripts, ["M104 S0"] if print_state == "standby" else [])

    def test_unload_with_a_slot_qidi_already_knows_or_a_clear_hub_is_left_to_qidi(self):
        known = {"last_load_slot": "slot0", "vo_box_hub_slot": "slot2"}
        cases = {
            "QIDI saved the loaded slot": dict(variables=known, at_hub=0, jammed=False),
            "nothing at the hub": dict(variables={"vo_box_hub_slot": "slot2"}, at_hub=None, jammed=False),
            # QIDI's unload can fail for reasons of its own, which naming a slot wouldn't fix.
            "QIDI's unload fails with its slot saved": dict(variables=known, at_hub=0, jammed=True),
        }
        for name, case in cases.items():
            with self.subTest(name):
                box = Box(("box_loaded_slot",), variables=case["variables"])
                box.adapter.jammed = case["jammed"]
                if case["at_hub"] is not None:
                    box.slot(case["at_hub"]).stepper.feed(HUB_AT + 5.)
                self.assertEqual(box.unload(), not case["jammed"])
                self.assertEqual(box.adapter.unloads, 1)
                self.assertEqual(box.variables.allVariables.get("last_load_slot"),
                                 case["variables"].get("last_load_slot"))
                self.assertEqual((box.gcode.responses, box.gcode.scripts), ([], []))


class Filaments:
    """QIDI's filament-0.4.json lookups. Comparing nozzle temperatures raises on an ID it doesn't list. QFA00 is
    QIDI PLA Basic; the other IDs are made up."""

    listed = {"QFA00": ("QIDI", "PLA Basic", 220), "QFG01": ("QIDI", "PETG Tough", 250),
              "QFN05": ("QIDI", "PA12-CF", 300)}

    def get_filament_info_by_id(self, filament_id):
        return self.listed.get(filament_id)

    def get_fila_id(self, filament, vendor):
        return next((key for key, entry in self.listed.items() if entry[:2] == (vendor, filament)), None)

    def get_max_temp_id(self, first, second):
        return max(self.listed[first][2], self.listed[second][2])


class ExternalSpool:
    """QIDI's external spool adapter, which saves the ID it looked up before finding it has none."""

    def __init__(self, material_db):
        self.material_db = material_db
        self.saved = {}

    def set_filament_information(self, slot, color, filament, vendor):
        filament_id = self.material_db.get_fila_id(filament, vendor)
        self.saved["filament_" + slot] = str(filament_id)
        return self.material_db.listed[filament_id]


class BoxFilamentGuardTests(QuietTestCase):
    def printer(self):
        filaments = Filaments()
        external = ExternalSpool(filaments)
        gcode = GCode()
        printer = Printer({"gcode": gcode, "multi_color_controller": types.SimpleNamespace(
            fila_json=filaments, extre=external)})
        load_extra("box_filament_guard").load_config(Config(printer))
        printer.send_event("klippy:ready")
        return filaments, external, gcode

    def test_saved_ids_qidi_doesnt_list_are_compared_as_pla_basic_instead_of_raising(self):
        cases = {
            "both listed": (("QFG01", "QFN05"), ("QFG01", "QFN05")),
            # What the external spool saved for a filament QIDI doesn't list.
            "the string 'None'": (("None", "QFN05"), ("QFA00", "QFN05")),
            "an ID missing from QIDI's list": (("QFG01", "QFZ99"), ("QFG01", "QFA00")),
            "not strings": ((None, 7), ("QFA00", "QFA00")),
        }
        for name, (saved, compared) in cases.items():
            with self.subTest(name):
                filaments, _external, gcode = self.printer()
                self.assertEqual(filaments.get_max_temp_id(*saved), Filaments().get_max_temp_id(*compared))
                self.assertEqual(bool(gcode.responses), saved != compared)

    def test_external_spool_keeps_its_filament_when_asked_for_one_qidi_doesnt_list(self):
        _filaments, external, gcode = self.printer()
        external.set_filament_information("slot16", "#ffffff", "PETG Tough", "QIDI")
        self.assertEqual(external.saved, {"filament_slot16": "QFG01"})
        external.set_filament_information("slot16", "#ffffff", "Mystery PETG", "Somebody")
        self.assertEqual(external.saved, {"filament_slot16": "QFG01"})
        self.assertTrue(gcode.responses)


class WithoutBoxCodeTests(QuietTestCase):
    def test_klipper_gets_ready_when_qidis_box_code_is_missing_or_not_as_expected(self):
        printers = {
            "no QIDI controller": lambda controller: {},
            "a controller without the parts Valve Oil wraps": lambda controller: {"multi_color_controller": controller},
        }
        for module in ("box_loaded_slot", "box_filament_guard"):
            for name, objects in printers.items():
                with self.subTest(module, printer=name):
                    controller = types.SimpleNamespace()
                    printer = Printer(dict(objects(controller), gcode=GCode()))
                    load_extra(module).load_config(Config(printer))
                    printer.send_event("klippy:ready")
                    self.assertEqual(vars(controller), {})


if __name__ == "__main__":
    unittest.main()
