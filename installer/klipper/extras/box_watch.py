# QIDI Box fault watcher for Valve Oil.
#
# QIDI's Box code changes a slot motor's state behind Klipper's back, and
# nothing noticed until a print failed. This module watches for the faults
# known to come of it and writes one `vo_box_watch:` line to klippy.log the
# moment one happens, with the numbers needed to judge it. Valve Oil UI's
# Diagnostics check (installer/runtime/diagnostics.py) reads these lines; see
# docs/troubleshooting.md. It changes nothing on the printer and raises no
# printer warning: every fault it watches for is one Valve Oil repairs before
# it fails the next operation (a printer warning is for those it doesn't).
#
# What it watches, on each USB Box MCU:
#
# - The slot step clocks. QIDI's auto-feed steps the bound slot on the Box
#   itself: when a feed starts, the Box saves the slot's step clock and sends
#   `last_step_clk`, and when it ends it puts the clock back and sends
#   `STEP-set_last_step`. No host module handles either reply. Unless Klipper
#   has stepped or reset the slot since, every clock the Box reports for it
#   should be the one it reported before; one that differs is a clock that
#   changed behind Klipper, and Klipper's next steps for that slot run that
#   far off. The line names the slot and the offset in seconds.
# - Auto-feed re-armed during a feed. QIDI's `mcb_config_stepper`, sent by
#   print start, MCB_AUTO_START, and MCB_CONFIG, drops a running feed without
#   putting the slot's clock back. The Box answers it with an
#   `MCB_CONFIG_STEPPER` reply, which no host module handles; one that comes
#   while a feed runs (from the `MCB_STATE` 2 that starts it to the
#   `STEP-set_last_step` that ends it) is logged with the slot.
# - The Box feeding a slot while Valve Oil moves it: a feed of that slot
#   during Valve Oil's slot moves (tltg_box_slot_jog: jogs, spool rolls, RFID
#   reads, and stranded-filament retracts) or during a Box load or unload
#   move run with Valve Oil's feed motion.
# - Box replies no host module handles, counted by name and logged at each
#   print end, for the audit of what QIDI's Box firmware sends.
#
# The replies are read where Klipper's serial reader hands an unhandled reply
# to its default handler, which still logs it as `got {...}`, so this module
# takes no message from QIDI's code: if QIDI's code ever handles one of them,
# it simply stops arriving here. QIDI's own `MCB_STATE` handler is wrapped
# and still runs. The serial reader runs handlers in its own thread, so they
# only hand each reply to Klipper's reactor.

import logging

# QIDI box_autofeed's feed states in `MCB_STATE`; 2 is a feed running.
FEEDING = 2
WATCHED_REPLIES = ("last_step_clk", "STEP-set_last_step", "MCB_CONFIG_STEPPER")
# The USB Box MCU's clock when it doesn't report one.
BOX_CLOCK_FREQ = 84000000.
# How often Klipper's own steps of each slot are looked for, in seconds.
STEP_CHECK = 5.
PRINT_CHECK = 5.
PRINTING = ("printing", "paused")
# Box moves run with Valve Oil's feed motion (tltg_box_feed_motion).
FEED_MOTION = (("do_home_three_steps", "Box load move"),
               ("do_home_double_steps", "Box unload move"))
# Clock differences under this many ticks are rounding in the Box's
# arithmetic, not a changed clock.
CLOCK_SLACK = 2


def slot_label(slot):
    """Name a slot the way the QIDI Box card does: slot5 is 2B."""
    if isinstance(slot, str) and slot.startswith("slot") \
            and slot[4:].isdigit() and int(slot[4:]) < 16:
        number = int(slot[4:])
        return "%d%s" % (number // 4 + 1, "ABCD"[number % 4])
    return str(slot)


def signed32(value):
    return ((value + 2 ** 31) % 2 ** 32) - 2 ** 31


class _Slot:
    def __init__(self, name, stepper):
        self.name = name
        self.label = slot_label(name)
        self.stepper = stepper
        # The clock the Box last reported for this slot while Klipper hadn't
        # stepped or reset it, and Klipper's position of it and newest step
        # then.
        self.expected = None
        self.position = None
        self.stepped = None
        # The last changed clock logged, so a clock is logged once.
        self.logged = None
        # The Valve Oil move running on this slot, if any.
        self.moving = None

    def mcu_position(self):
        try:
            return self.stepper.get_mcu_position()
        except Exception:
            return None

    def newest_step(self):
        """The clock of Klipper's newest step of this slot still in its step
        history (about 30 s), or None."""
        dump = getattr(self.stepper, "dump_steps", None)
        if not callable(dump):
            return None
        try:
            data, count = dump(1, 0, 0xffffffffffffffff)
            return data[0].last_clock if count > 0 else None
        except Exception:
            return None

    def moved_since(self):
        """Whether Klipper has stepped this slot since the expected clock."""
        if self.mcu_position() != self.position:
            return True
        newest = self.newest_step()
        return newest is not None and newest != self.stepped

    def expect(self, clock):
        self.expected, self.logged = clock, None
        self.position, self.stepped = self.mcu_position(), self.newest_step()

    def forget(self):
        self.expected = self.position = self.stepped = self.logged = None


class _Box:
    def __init__(self, name, mcu, serial):
        self.name = name
        self.number = name[len("mcu_box"):] if name.startswith("mcu_box") \
            else name
        self.mcu = mcu
        self.serial = serial
        self.slots = {}
        self.feeding = False
        # The slot the Box last saved a clock for: the one auto-feed drives.
        self.active = None
        self.unhandled = {}
        try:
            self.freq = float(mcu.get_constants().get("CLOCK_FREQ",
                                                      BOX_CLOCK_FREQ))
        except Exception:
            self.freq = BOX_CLOCK_FREQ


class BoxWatch:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.boxes = {}
        self._print_state = None
        self._step_timer = None
        self.printer.register_event_handler("klippy:ready", self._handle_ready)

    # ----------------------------------------------------------------- setup
    def _handle_ready(self):
        try:
            self._setup()
        except Exception:
            logging.exception("vo_box_watch: setup failed")

    def _setup(self):
        lookup_objects = getattr(self.printer, "lookup_objects", None)
        if not callable(lookup_objects):
            return
        for name, box_stepper in lookup_objects("box_stepper"):
            stepper = self._mcu_stepper(box_stepper)
            if stepper is None:
                continue
            mcu = stepper.get_mcu()
            serial = getattr(mcu, "_serial", None)
            if serial is None or not hasattr(serial, "handle_default"):
                continue
            box_name = mcu.get_name()
            box = self.boxes.get(box_name)
            if box is None:
                box = self.boxes[box_name] = _Box(box_name, mcu, serial)
            slot = _Slot(name.split()[-1], stepper)
            box.slots[stepper.get_oid()] = slot
            self._watch_resets(slot, box_stepper)
            self._watch_feed_motion(slot, box_stepper)
        if not self.boxes:
            logging.info("vo_box_watch: inactive: no QIDI Box slots found")
            return
        for box in self.boxes.values():
            self._watch_replies(box)
        self._watch_slot_jog()
        self._step_timer = self.reactor.register_timer(
            self._check_steps, self.reactor.monotonic() + STEP_CHECK)
        self.reactor.register_timer(
            self._check_print, self.reactor.monotonic() + PRINT_CHECK)
        logging.info("vo_box_watch: watching %s", "; ".join(
            "Box %s slots %s" % (box.number, ", ".join(
                slot.label for slot in sorted(box.slots.values(),
                                              key=lambda s: s.name)))
            for box in sorted(self.boxes.values(), key=lambda b: b.name)))

    def _mcu_stepper(self, box_stepper):
        for stepper in (getattr(box_stepper, "stepper", None),
                        getattr(getattr(box_stepper, "extruder_stepper", None),
                                "stepper", None)):
            if all(callable(getattr(stepper, name, None)) for name in
                   ("get_oid", "get_mcu", "get_mcu_position")):
                return stepper
        return None

    def _watch_replies(self, box):
        serial = box.serial
        original = serial.handle_default

        def handle_default(params):
            try:
                name = params.get("#name")
                box.unhandled[name] = box.unhandled.get(name, 0) + 1
                if name in WATCHED_REPLIES:
                    reply = dict(params)
                    self.reactor.register_async_callback(
                        lambda eventtime: self._reply(box, reply))
            except Exception:
                logging.exception("vo_box_watch: reading a Box reply failed")
            return original(params)
        serial.handle_default = handle_default
        lock = getattr(serial, "lock", None)
        with lock if lock is not None else _NoLock():
            for key, callback in list(getattr(serial, "handlers", {}).items()):
                if key[0] == "MCB_STATE":
                    serial.handlers[key] = self._wrap_state(box, callback)

    def _wrap_state(self, box, original):
        def on_state(params):
            try:
                state = params.get("state")
                self.reactor.register_async_callback(
                    lambda eventtime: self._state(box, state))
            except Exception:
                logging.exception("vo_box_watch: reading a Box state failed")
            return original(params)
        return on_state

    def _watch_resets(self, slot, box_stepper):
        """Klipper resets a slot's step clock at a homing end, as a load
        does and as Valve Oil does before an unload's pull-back, through
        either name QIDI's box_stepper gives the slot's stepper."""
        steppers = []
        for stepper in (slot.stepper, getattr(getattr(
                box_stepper, "extruder_stepper", None), "stepper", None)):
            if callable(getattr(stepper, "note_homing_end", None)) \
                    and all(stepper is not seen for seen in steppers):
                steppers.append(stepper)
        for stepper in steppers:
            original = stepper.note_homing_end

            def note_homing_end(*args, _original=original, **kwargs):
                slot.forget()
                return _original(*args, **kwargs)
            stepper.note_homing_end = note_homing_end

    def _watch_feed_motion(self, slot, box_stepper):
        for method, move in FEED_MOTION:
            original = getattr(box_stepper, method, None)
            if callable(original):
                setattr(box_stepper, method,
                        self._during(slot, move, original))

    def _watch_slot_jog(self):
        jog = self.printer.lookup_object("tltg_box_slot_jog", None)
        original = getattr(jog, "_move", None)
        if not callable(original):
            return

        def move(stepper, *args, **kwargs):
            slot = self._slot_of(stepper)
            if slot is None:
                return original(stepper, *args, **kwargs)
            return self._during(slot, "slot move", original)(
                stepper, *args, **kwargs)
        jog._move = move

    def _slot_of(self, stepper):
        try:
            box = self.boxes.get(stepper.get_mcu().get_name())
            return None if box is None else box.slots.get(stepper.get_oid())
        except Exception:
            return None

    def _during(self, slot, move, original):
        def run(*args, **kwargs):
            if slot.moving is not None:
                return original(*args, **kwargs)
            slot.moving = move
            try:
                return original(*args, **kwargs)
            finally:
                slot.moving = None
        return run

    # ------------------------------------------------------------- the Box
    def _state(self, box, state):
        if state == FEEDING:
            box.feeding = True
            self._feed_during_move(box, "started a feed")

    def _reply(self, box, params):
        name = params.get("#name")
        if name == "last_step_clk":
            box.active = params.get("oid")
            self._clock(box, box.slots.get(box.active), params.get("clk"),
                        "saved")
        elif name == "STEP-set_last_step":
            box.feeding = False
            self._feed_during_move(box, "ended a feed and switched the slot "
                                        "motor off")
            self._clock(box, box.slots.get(box.active), params.get("next_s"),
                        "put back")
        elif name == "MCB_CONFIG_STEPPER":
            if box.feeding:
                feeding = box.slots.get(box.active)
                target = box.slots.get(params.get("stepper_oid"))
                logging.info(
                    "vo_box_watch: auto-feed re-armed during a feed: slot %s "
                    "(Box %s) was feeding when auto-feed was set up again for "
                    "slot %s; the Box drops that feed without putting the "
                    "slot's step clock back",
                    feeding.label if feeding else "unknown", box.number,
                    target.label if target else "unknown")
            box.feeding = False

    def _feed_during_move(self, box, what):
        slot = box.slots.get(box.active)
        if slot is None or slot.moving is None:
            return
        logging.info("vo_box_watch: Box feed during Valve Oil's %s: slot %s "
                     "(Box %s): the Box %s while Valve Oil was moving the "
                     "slot", slot.moving, slot.label, box.number, what)

    def _clock(self, box, slot, clock, how):
        if slot is None or not isinstance(clock, int):
            return
        if slot.expected is None or slot.moved_since():
            slot.expect(clock)
            return
        offset = signed32(clock - slot.expected)
        if abs(offset) <= CLOCK_SLACK or clock == slot.logged:
            return
        slot.logged = clock
        logging.info(
            "vo_box_watch: step clock: slot %s (Box %s) is %.3f s %s Klipper's"
            ": the Box %s clock %d where Klipper's is %d; Valve Oil resets it "
            "before the slot's next unload", slot.label, box.number,
            abs(offset) / box.freq, "behind" if offset < 0 else "ahead of",
            how, clock, slot.expected)

    # ----------------------------------------------------------- Klipper
    def _check_steps(self, eventtime):
        """A slot Klipper has stepped reports a new clock legitimately."""
        for box in self.boxes.values():
            for slot in box.slots.values():
                if slot.expected is not None and slot.moved_since():
                    slot.forget()
        return eventtime + STEP_CHECK

    def _check_print(self, eventtime):
        print_stats = self.printer.lookup_object("print_stats", None)
        try:
            state = print_stats.get_status(eventtime).get("state")
        except Exception:
            state = None
        if self._print_state in PRINTING and state not in PRINTING:
            self._log_unhandled()
        self._print_state = state
        return eventtime + PRINT_CHECK

    def _log_unhandled(self):
        for box in sorted(self.boxes.values(), key=lambda b: b.name):
            counts, box.unhandled = box.unhandled, {}
            if counts:
                logging.info(
                    "vo_box_watch: Box %s replies no host module handled "
                    "since the last print end: %s", box.number,
                    ", ".join("%s %d" % (name, counts[name])
                              for name in sorted(counts)))

    def get_status(self, eventtime=None):
        return {}


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def load_config(config):
    return BoxWatch(config)
