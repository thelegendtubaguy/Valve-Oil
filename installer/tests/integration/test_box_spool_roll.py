from __future__ import annotations

import importlib.util
import math
import sys
import types
import unittest
from unittest import mock

from installer.tests.helpers import REPO_ROOT

MODULE = REPO_ROOT / "installer/klipper/tltg/box_spool_roll.py"


class Ffi:
    def __init__(self):
        self.appended = []

    def gc(self, obj, free):
        return obj

    def trapq_alloc(self):
        return object()

    def cartesian_stepper_alloc(self, axis):
        return object()

    def free(self, obj):
        pass

    trapq_free = free

    def trapq_append(self, trapq, start, accel_t, cruise_t, decel_t, *rest):
        self.appended.append((start, accel_t + cruise_t + decel_t))

    def trapq_finalize_moves(self, trapq, free_time, clear_history_time):
        pass


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


def load_module(ffi):
    extras = types.ModuleType("extras")
    extras.__path__ = []
    force_move = types.ModuleType("extras.force_move")
    force_move.calc_move_time = calc_move_time
    chelper = types.ModuleType("chelper")
    chelper.get_ffi = lambda: (ffi, ffi)
    with mock.patch.dict(sys.modules, {"extras": extras, "extras.force_move": force_move,
                                       "chelper": chelper}):
        spec = importlib.util.spec_from_file_location("extras.tltg_box_spool_roll", MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class Mcu:
    def __init__(self, printer):
        self.printer = printer

    def estimated_print_time(self, eventtime):
        return eventtime + self.printer.clock_offset


class Stepper:
    def __init__(self, printer, name, vendor_tq):
        self.mcu = Mcu(printer)
        self.name = name
        self.trapq = vendor_tq
        self.sk = "vendor-sk"

    def get_name(self):
        return self.name

    def get_mcu(self):
        return self.mcu

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
        pass


class Slot:
    def __init__(self, printer, number, inlet_empty):
        self.stepper = Stepper(printer, "slot%d" % number, "vendor-tq-%d" % number)
        self.r_endstop_state = 1 if inlet_empty else 0
        self.next_cmd_time = 0.


class Enable:
    def __init__(self):
        self.enabled = False
        self.disabled_at = None

    def is_motor_enabled(self):
        return self.enabled

    def motor_enable(self, print_time):
        self.enabled = True

    def motor_disable(self, print_time):
        self.enabled = False
        self.disabled_at = print_time


class StepperEnable:
    def __init__(self):
        self.lines = {}

    def lookup_enable(self, name):
        return self.lines.setdefault(name, Enable())


class Toolhead:
    def __init__(self):
        self.print_time = 0.
        self.last_flush_time = 0.
        self.min_restart_time = 0.
        self.kin_flush_delay = 0.001
        self.activity = []

    def note_mcu_movequeue_activity(self, mq_time, set_step_gen_time=False):
        self.activity.append((mq_time, set_step_gen_time))


class Status:
    def __init__(self, status):
        self.status = status

    def get_status(self, eventtime):
        return self.status


class Reactor:
    NOW = 0.
    NEVER = 9999999999999999.

    def __init__(self):
        self.callback = None

    def register_timer(self, callback, waketime):
        self.callback = callback

    def monotonic(self):
        return 0.


class Printer:
    def __init__(self, objects):
        self.objects = objects
        self.reactor = Reactor()
        self.handlers = {}
        self.clock_offset = 100.

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def lookup_objects(self, module):
        return [(name, obj) for name, obj in self.objects.items()
                if name.split()[0] == module]

    def is_shutdown(self):
        return False


class Config:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer


class Box:
    """One USB QIDI Box: 1A and 1D empty, 1B loaded to the toolhead, 1C parked."""

    def __init__(self, *, target=40., variables=None, print_state="printing", task=None,
                 dry_state=0, inlets=(True, False, False, True), saved=(0, 2, 1, 0)):
        self.ffi = Ffi()
        self.module = load_module(self.ffi)
        self.variables = {"slot%d" % i: state for i, state in enumerate(saved)}
        self.variables.update({"slot_sync": "slot1", "last_load_slot": "slot1",
                               "vo_box_spool_roll": 1})
        self.variables.update(variables or {})
        for name in [name for name, value in self.variables.items() if value is None]:
            del self.variables[name]
        self.toolhead = Toolhead()
        self.stepper_enable = StepperEnable()
        self.heater = Status({"target": target})
        objects = {
            "toolhead": self.toolhead,
            "stepper_enable": self.stepper_enable,
            "save_variables": types.SimpleNamespace(allVariables=self.variables),
            "heater_generic heater_box1": self.heater,
            "print_stats": Status({"state": print_state}),
            "multi_color_controller": Status({"task": task or {
                "flow_id": None, "current_queue": [], "current_step": "", "is_paused": False}}),
            "box_extras": Status({"is_tool_change": 0, "boxes": [{"dry_state": dry_state}]}),
            "extruder": types.SimpleNamespace(get_trapq=lambda: "extruder-tq"),
        }
        self.printer = Printer(objects)
        for number, inlet_empty in enumerate(inlets):
            objects["box_stepper slot%d" % number] = Slot(self.printer, number, inlet_empty)
        self.roll = self.module.load_config(Config(self.printer))
        for callback in self.printer.handlers["klippy:ready"]:
            callback()

    def slot(self, number):
        return self.printer.objects["box_stepper slot%d" % number]

    def run(self, until):
        """Ticks the module's timer once a second up to UNTIL seconds; returns
        each roll as (time, slot)."""
        rolls = []
        for second in range(int(until) + 1):
            before = self.roll.held
            self.printer.reactor.callback(float(second))
            if self.roll.held is not None and self.roll.held is not before:
                rolls.append((second, self.roll.held["slot"]))
        return rolls


class BoxSpoolRollTests(unittest.TestCase):
    def test_heated_box_rolls_only_its_empty_slots_each_interval(self):
        box = Box()
        rolls = box.run(2 * box.module.ROLL_INTERVAL + 30)
        self.assertEqual([slot for _, slot in rolls], [0, 3, 0, 3])
        self.assertGreaterEqual(rolls[0][0], box.module.ROLL_INTERVAL)
        self.assertGreaterEqual(rolls[2][0] - rolls[0][0], box.module.ROLL_INTERVAL)
        for number in (1, 2):
            self.assertEqual(box.slot(number).stepper.get_trapq(), "vendor-tq-%d" % number)

    def test_slots_with_filament_are_never_rolled(self):
        cases = {
            "inlet sees filament": dict(inlets=(False, False, False, False)),
            "saved state not empty": dict(saved=(1, 2, 3, -2)),
            "saved as the loaded slot": dict(variables={"slot_sync": "slot0", "last_load_slot": "slot3"},
                                             saved=(0, 0, 1, 0), inlets=(True, False, False, True)),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                box = Box(**kwargs)
                self.assertEqual(box.run(box.module.ROLL_INTERVAL + 30), [])

    def test_nothing_rolls_while_off_unheated_paused_busy_or_drying(self):
        busy = {"flow_id": 13, "current_queue": ["BOX_LOAD"], "current_step": "BOX_LOAD",
                "is_paused": False}
        cases = {
            "setting off": dict(variables={"vo_box_spool_roll": 0}),
            "setting missing": dict(variables={"vo_box_spool_roll": None}),
            "heater off": dict(target=0.),
            "print paused": dict(print_state="paused"),
            "Box flow running": dict(task=busy),
            "QIDI drying": dict(dry_state=1),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                box = Box(**kwargs)
                self.assertEqual(box.run(box.module.ROLL_INTERVAL + 30), [])

    def test_roll_starts_after_every_queued_step_without_holding_the_toolhead(self):
        box = Box()
        box.toolhead.print_time = 103.
        box.toolhead.last_flush_time = 102.
        box.toolhead.min_restart_time = 102.5
        box.run(box.module.ROLL_INTERVAL)
        start, duration = box.ffi.appended[0]
        est = box.module.ROLL_INTERVAL + box.printer.clock_offset
        self.assertGreater(start, max(103., est + 0.3))
        self.assertGreater(duration, 0.)
        self.assertTrue(box.toolhead.activity)
        self.assertFalse(any(step_gen for _, step_gen in box.toolhead.activity))

    def test_motor_is_handed_back_after_the_roll(self):
        box = Box()
        rolls = box.run(box.module.ROLL_INTERVAL + 20)
        self.assertEqual(rolls[0][1], 0)
        stepper = box.slot(0).stepper
        self.assertEqual((stepper.get_trapq(), stepper.get_stepper_kinematics()),
                         ("vendor-tq-0", "vendor-sk"))
        self.assertFalse(box.stepper_enable.lookup_enable("slot0").enabled)

    def test_motor_qidi_takes_mid_roll_is_left_running(self):
        box = Box()
        box.run(box.module.ROLL_INTERVAL)
        slot = box.slot(0)
        slot.stepper.set_trapq("vendor-tq-0")
        slot.next_cmd_time = box.module.ROLL_INTERVAL + box.printer.clock_offset + 60.
        box.printer.objects["box_extras"].status["is_tool_change"] = 1
        for second in range(int(box.module.ROLL_INTERVAL) + 1, int(box.module.ROLL_INTERVAL) + 90):
            box.printer.reactor.callback(float(second))
            if second == int(box.module.ROLL_INTERVAL) + 30:
                self.assertEqual(slot.stepper.get_stepper_kinematics(), box.roll.sk)
                box.printer.objects["box_extras"].status["is_tool_change"] = 0
        self.assertEqual(slot.stepper.get_trapq(), "vendor-tq-0")
        self.assertEqual(slot.stepper.get_stepper_kinematics(), "vendor-sk")
        self.assertIsNone(box.stepper_enable.lookup_enable("slot0").disabled_at)


if __name__ == "__main__":
    unittest.main()
