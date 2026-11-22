# QIDI Box spool rolling for Valve Oil.
#
# Each Box slot's motor also drives the roller under that slot's spool. QIDI's
# drying turns every slot's motor back 50 mm every 300 s so the spools turn in
# the heat, but it does so with G-code (RUN_STEPPER, then G4 P5000 per slot)
# that would pause a print, and only while QIDI's drying runs.
#
# This module turns the spools of empty slots whenever their Box heater has a
# target, including during prints: every ROLL_INTERVAL seconds each empty slot
# of a heated Box is turned DISTANCE mm, one slot at a time. A slot counts as
# empty only when QIDI's saved state for it is 0 (empty) and its inlet sensor
# sees no filament; a slot with filament anywhere is never moved. Boxes whose
# QIDI drying is running are left to QIDI's own rolling.
#
# The roll never touches the toolhead's queue. The slot motor is moved on this
# module's own motion queue, starting after every step Klipper has already
# generated, so nothing the print does waits for it and nothing it does is
# reordered. Klipper's toolhead generates the slot motor's steps as it does
# for any other motor. The motor is handed back once the roll has run; if
# QIDI's Box code takes it first, the module lets go without switching it off.
#
# It is off unless the saved variable vo_box_spool_roll is 1. Changes apply
# at the next roll.

import logging

import chelper
from . import force_move

try:
    # Klipper 0.14 and later
    from . import motion_queuing as _motion_queuing_module
except ImportError:
    _motion_queuing_module = None

ENABLE_VARIABLE = "vo_box_spool_roll"
ROLL_INTERVAL = 300.
# Half QIDI's 50 mm drying roll, in the same direction, at a gentler speed.
DISTANCE = -25.
SPEED = 20.
ACCEL = 50.
CHECK_TIME = 1.0
# Room ahead of the Box MCU's clock for the motor enable, which QIDI's
# stepper_enable sends 0.3 s before the time it's given, and for host delays.
MIN_LEAD = 0.8
QUEUE_MARGIN = 0.1
ENABLE_SETTLE = 0.1
STEPCOMPRESS_FLUSH_TIME = 0.050
RELEASE_MARGIN = 0.25
EMPTY_STATE = 0
SENSOR_EMPTY = 1


def slot_label(slot):
    """Name a slot the way the QIDI Box card does: slot 5 is 2B."""
    return "%d%s" % (slot // 4 + 1, "ABCD"[slot % 4])


class BoxSpoolRoll:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        ffi_main, ffi_lib = chelper.get_ffi()
        # Klipper 0.14 and later queue and expire every motion queue through
        # motion_queuing; older Klipper leaves that to whoever owns the queue.
        self.motion_queuing = None
        if _motion_queuing_module is not None:
            self.motion_queuing = self.printer.load_object(config,
                                                           "motion_queuing")
            self.trapq = self.motion_queuing.allocate_trapq()
        else:
            self.trapq = ffi_main.gc(ffi_lib.trapq_alloc(),
                                     ffi_lib.trapq_free)
        self.sk = ffi_main.gc(ffi_lib.cartesian_stepper_alloc(b"x"),
                              ffi_lib.free)
        self.trapq_append = ffi_lib.trapq_append
        self.trapq_finalize_moves = ffi_lib.trapq_finalize_moves
        self.slots = {}
        self.toolhead = None
        self.held = None
        self.pending = []
        self.next_roll = {}
        self.timer = None
        self.printer.register_event_handler("klippy:ready", self._ready)

    # ----------------------------------------------------------------- setup
    def _ready(self):
        slots = {}
        for name, obj in self.printer.lookup_objects("box_stepper"):
            slot = name.split()[-1]
            stepper = getattr(obj, "stepper", None)
            if not slot.startswith("slot") or not slot[4:].isdigit() \
                    or not hasattr(stepper, "set_stepper_kinematics"):
                continue
            slots[int(slot[4:])] = obj
        toolhead = self.printer.lookup_object("toolhead")
        if not slots or not all(hasattr(toolhead, attr) for attr in (
                "print_time", "last_flush_time", "min_restart_time",
                "kin_flush_delay", "note_mcu_movequeue_activity")):
            logging.info("tltg_box_spool_roll: inactive: QIDI Box slots or "
                         "toolhead timing not found")
            return
        self.slots = slots
        self.toolhead = toolhead
        self.timer = self.reactor.register_timer(self._tick, self.reactor.NOW)
        logging.info("tltg_box_spool_roll: active, %d slots", len(slots))

    # ----------------------------------------------------------------- state
    def enabled(self):
        variables = getattr(self.printer.lookup_object("save_variables", None),
                            "allVariables", None) or {}
        value = variables.get(ENABLE_VARIABLE, 0)
        try:
            return float(value) != 0.
        except (TypeError, ValueError):
            return bool(value)

    def _status(self, name, eventtime):
        obj = self.printer.lookup_object(name, None)
        if obj is None or not hasattr(obj, "get_status"):
            return {}
        status = obj.get_status(eventtime)
        return status if hasattr(status, "get") else {}

    def _box_busy(self, eventtime):
        """Whether QIDI's Box code or the operator may be using the Box."""
        if self._status("print_stats", eventtime).get("state") == "paused":
            return True
        task = self._status("multi_color_controller", eventtime).get("task")
        if hasattr(task, "get") and (task.get("is_paused")
                                     or task.get("flow_id") not in (None, 0)
                                     or task.get("current_queue")
                                     or task.get("current_step")):
            return True
        return bool(self._status("box_extras", eventtime).get(
            "is_tool_change"))

    def _heated_boxes(self, eventtime):
        """Box numbers (0-based) whose heater has a target."""
        return set(box for box in set(slot // 4 for slot in self.slots)
                   if self._status("heater_generic heater_box%d" % (box + 1),
                                   eventtime).get("target"))

    def _drying(self, box, eventtime):
        """Whether QIDI's own drying, which rolls the spools itself, is
        running on a Box."""
        boxes = self._status("box_extras", eventtime).get("boxes")
        return isinstance(boxes, list) and box < len(boxes) \
            and hasattr(boxes[box], "get") and bool(boxes[box].get("dry_state"))

    def _slot_empty(self, slot):
        variables = getattr(self.printer.lookup_object("save_variables", None),
                            "allVariables", None) or {}
        name = "slot%d" % slot
        if variables.get(name) != EMPTY_STATE \
                or name in (variables.get("slot_sync"),
                            variables.get("last_load_slot")):
            return False
        return getattr(self.slots[slot], "r_endstop_state", None) \
            == SENSOR_EMPTY

    # ----------------------------------------------------------------- timer
    def _tick(self, eventtime):
        if self.printer.is_shutdown():
            return self.reactor.NEVER
        try:
            if self.held is not None:
                self._release(eventtime)
            if self.held is None:
                self._roll_next(eventtime)
        except Exception:
            logging.exception("tltg_box_spool_roll: roll failed")
            self.pending = []
        return eventtime + CHECK_TIME

    def _roll_next(self, eventtime):
        if not self.enabled():
            self.pending = []
            self.next_roll = {}
            return
        heated = self._heated_boxes(eventtime)
        for box in list(self.next_roll):
            if box not in heated:
                del self.next_roll[box]
        for box in sorted(heated):
            # The first roll comes one interval after the heater comes on.
            due = self.next_roll.setdefault(box, eventtime + ROLL_INTERVAL)
            if eventtime >= due:
                self.next_roll[box] = eventtime + ROLL_INTERVAL
                if self._drying(box, eventtime):
                    continue
                self.pending.extend(
                    slot for slot in sorted(self.slots) if slot // 4 == box
                    and slot not in self.pending)
        while self.pending:
            if self._box_busy(eventtime):
                return
            slot = self.pending.pop(0)
            if slot // 4 in heated and self._slot_empty(slot):
                self._start(slot, eventtime)
                return

    # ------------------------------------------------------------------ move
    def _start(self, slot, eventtime):
        obj = self.slots[slot]
        stepper = obj.stepper
        extruder = self.printer.lookup_object("extruder", None)
        if extruder is not None and hasattr(extruder, "get_trapq") \
                and stepper.get_trapq() == extruder.get_trapq():
            return
        toolhead = self.toolhead
        est = stepper.get_mcu().estimated_print_time(eventtime)
        vendor_time = getattr(obj, "next_cmd_time", 0.)
        if not isinstance(vendor_time, (int, float)):
            vendor_time = 0.
        # No step of any motor has been generated past these times, so a move
        # that starts after them can't land among steps already sent.
        start = max(toolhead.print_time, toolhead.min_restart_time,
                    toolhead.last_flush_time + STEPCOMPRESS_FLUSH_TIME,
                    vendor_time) + QUEUE_MARGIN
        start = max(start, est + MIN_LEAD)
        if self.motion_queuing is not None:
            start = max(start, self.motion_queuing.calc_step_gen_restart(est))
        enable = self.printer.lookup_object("stepper_enable").lookup_enable(
            stepper.get_name())
        was_enabled = enable.is_motor_enabled()
        old_tq = stepper.set_trapq(None)
        old_sk = stepper.set_stepper_kinematics(self.sk)
        stepper.set_trapq(self.trapq)
        stepper.set_position([0., 0., 0.])
        enable.motor_enable(start)
        start += ENABLE_SETTLE
        axis_r, accel_t, cruise_t, cruise_v = force_move.calc_move_time(
            DISTANCE, SPEED, ACCEL)
        self.trapq_append(self.trapq, start, accel_t, cruise_t, accel_t,
                          0., 0., 0., axis_r, 0., 0., 0., cruise_v, ACCEL)
        end = start + accel_t + cruise_t + accel_t
        # Keeps Klipper generating steps through the end of the roll when
        # nothing else moves. It doesn't hold back any later toolhead move.
        if self.motion_queuing is not None:
            self.motion_queuing.note_mcu_movequeue_activity(end)
        else:
            toolhead.note_mcu_movequeue_activity(
                end + toolhead.kin_flush_delay)
        self.held = {"slot": slot, "stepper": stepper, "old_tq": old_tq,
                     "old_sk": old_sk, "end": end, "enable": enable,
                     "was_enabled": was_enabled}
        logging.info("tltg_box_spool_roll: turning %s's spool %.0f mm",
                     slot_label(slot), abs(DISTANCE))

    def _release(self, eventtime):
        held = self.held
        stepper = held["stepper"]
        est = stepper.get_mcu().estimated_print_time(eventtime)
        if est < held["end"] + RELEASE_MARGIN:
            return
        owns_tq = stepper.get_trapq() == self.trapq
        owns_sk = stepper.get_stepper_kinematics() == self.sk
        if owns_sk and not owns_tq:
            # QIDI's Box code took the motor on its own queue, which it drives
            # through whatever kinematics the motor has. Give its own back once
            # its moves have run.
            vendor_time = getattr(self.slots[held["slot"]], "next_cmd_time",
                                  0.)
            if self._box_busy(eventtime) or (
                    isinstance(vendor_time, (int, float))
                    and est < vendor_time + RELEASE_MARGIN):
                return
            logging.info("tltg_box_spool_roll: QIDI's Box code moved %s "
                         "during its roll", slot_label(held["slot"]))
        if owns_tq:
            stepper.set_trapq(None)
        if owns_sk:
            stepper.set_stepper_kinematics(held["old_sk"])
        if owns_tq:
            stepper.set_trapq(held["old_tq"])
            if not held["was_enabled"]:
                held["enable"].motor_disable(est + RELEASE_MARGIN)
        if self.motion_queuing is not None:
            self.motion_queuing.wipe_trapq(self.trapq)
        else:
            self.trapq_finalize_moves(self.trapq, held["end"] + 99999.9,
                                      held["end"] + 99999.9)
        self.held = None

    def get_status(self, eventtime):
        held = self.held
        return {"enabled": self.enabled(),
                "rolling": None if held is None else slot_label(held["slot"])}


def load_config(config):
    return BoxSpoolRoll(config)
