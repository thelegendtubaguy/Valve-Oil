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
#   far off. The line names the slot and the offset in seconds. After Klipper
#   resets a slot's clock (a homing end, as every Box home is for every slot,
#   and a Klipper start), the Box's next clock for it should be 0, or the
#   clock of Klipper's last step when Klipper has moved the slot since: a
#   feed running across the reset puts back a clock from before it.
# - Auto-feed re-armed during a feed. QIDI's `mcb_config_stepper`, sent by
#   print start, MCB_AUTO_START, and MCB_CONFIG, drops a running feed without
#   putting the slot's clock back. The Box answers it with an
#   `MCB_CONFIG_STEPPER` reply, which no host module handles; one that comes
#   while a feed runs (from the `MCB_STATE` 2 that starts it to the
#   `STEP-set_last_step` that ends it) is logged with the slot.
# - The Box feeding a slot while Valve Oil moves it: a feed of that slot
#   during Valve Oil's slot moves (vo_box_slot_jog: jogs, spool rolls, RFID
#   reads, and stranded-filament retracts) or during a Box load or unload
#   move run with Valve Oil's feed motion.
# - The Box feeding a slot Klipper still has moves queued for. A feed's steps
#   go into the same step queue as Klipper's, and the feed starts without
#   looking at it: it saves a clock from inside Klipper's moves, and its
#   direction applies to Klipper's moves queued during it. The line gives how
#   many seconds of Klipper's moves were still queued.
# - The Box feeding a slot after QIDI's code aborted auto-feed (an unload
#   does, before its pull-back): an abort that lands in the 10 ms after a
#   feed's steps end is ignored and auto-feed stays armed.
# - Auto-feed aborted during a feed while Klipper still has moves queued for
#   the slot: the Box's abort empties the slot's whole step queue, Klipper's
#   moves with the feed's.
# - A slot's step clock reset during a Box feed. Every slot shares the Box's
#   hub and toolhead endstops, so every Box home resets every slot's clock,
#   and a feed running on another slot then puts back a clock from before
#   the reset when it ends.
# - The Box switching a slot's motor off while Klipper has it on. A feed's
#   end, an abort, and arming auto-feed write the slot's enable pin on the
#   Box itself; Klipper switches the motor on only when it thinks it is off,
#   so its next move of that slot would run without current. The line is
#   written when Klipper still has the motor on a few seconds later without
#   having switched it on again.
# - Box replies no host module handles, counted by name and logged at each
#   print end, for the audit of what QIDI's Box firmware sends.
# - How many feeds each Box starts (`MCB_STATE` 2), which Valve Oil's unload
#   line in klippy.log reports (vo_box_flow_recovery).
#
# The replies are read where Klipper's serial reader hands an unhandled reply
# to its default handler, which still logs it as `got {...}`, so this module
# takes no message from QIDI's code: if QIDI's code ever handles one of them,
# it simply stops arriving here. QIDI's own `MCB_STATE` handler is wrapped
# and still runs. The serial reader runs handlers in its own thread, so they
# only hand each reply to Klipper's reactor.

import logging

# QIDI box_autofeed's feed states in `MCB_STATE`: 1 armed, 2 a feed running.
ARMED = 1
FEEDING = 2
WATCHED_REPLIES = ("last_step_clk", "STEP-set_last_step", "MCB_CONFIG_STEPPER")
# The USB Box MCU's clock when it doesn't report one.
BOX_CLOCK_FREQ = 84000000.
# How often Klipper's own steps of each slot are looked for, in seconds.
STEP_CHECK = 5.
PRINT_CHECK = 5.
PRINTING = ("printing", "paused")
# Box moves run with Valve Oil's feed motion (vo_box_feed_motion).
FEED_MOTION = (("do_home_three_steps", "Box load move"),
               ("do_home_double_steps", "Box unload move"))
# Clock differences under this many ticks are rounding in the Box's
# arithmetic, not a changed clock.
CLOCK_SLACK = 2
# Klipper's moves queued for a slot for less than this many seconds past a
# Box reply are the reply's own latency, not moves the Box ran into.
QUEUED_SLACK = 0.050
# How many of a slot's newest step history entries are searched for a step:
# a reset and its position query each leave one with none.
HISTORY_LOOKBACK = 4
# How long after the Box switches a slot motor off Klipper may still have it
# on before that counts: QIDI's own flows switch it off in Klipper too, a
# second ahead.
MOTOR_OFF_GRACE = 2.


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
        # Klipper reset the slot's clock since the Box last reported it.
        self.reset = False
        # The Valve Oil move running on this slot, if any.
        self.moving = None
        # Klipper's tracking of the slot's enable pin, when Klipper last
        # switched it on, and when the Box last switched it off.
        self.enable = None
        self.enabled_at = None
        self.box_off = None

    def mcu_position(self):
        try:
            return self.stepper.get_mcu_position()
        except Exception:
            return None

    def newest_step(self):
        """The clock of Klipper's newest step of this slot still in its step
        history (about 30 s), or None. A step clock reset leaves a marker
        with no steps, which is skipped."""
        dump = getattr(self.stepper, "dump_steps", None)
        if not callable(dump):
            return None
        try:
            data, count = dump(HISTORY_LOOKBACK, 0, 0xffffffffffffffff)
            for index in range(count):
                if data[index].step_count:
                    return data[index].last_clock
        except Exception:
            pass
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

    def note_reset(self):
        """Klipper reset the slot's step clock to 0 and has stepped it no
        more since."""
        self.expect(0)
        self.reset = True


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
        # When QIDI's code last aborted auto-feed on this Box, if it hasn't
        # armed it since.
        self.aborted = None
        self.unhandled = {}
        # Feeds the Box started since Klipper started.
        self.feeds = 0
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
            # Klipper resets every slot's step clock when it connects to the
            # Box, a Box it reuses after a restart too.
            slot.note_reset()
            box.slots[stepper.get_oid()] = slot
            self._watch_enable(slot)
            self._watch_resets(box, slot, box_stepper)
            self._watch_feed_motion(slot, box_stepper)
        if not self.boxes:
            logging.info("vo_box_watch: inactive: no QIDI Box slots found")
            return
        for box in self.boxes.values():
            self._watch_replies(box)
        self._watch_slot_jog()
        self._watch_autofeed()
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
                received = params.get("#receive_time")
                self.reactor.register_async_callback(
                    lambda eventtime: self._state(box, state, received))
            except Exception:
                logging.exception("vo_box_watch: reading a Box state failed")
            return original(params)
        return on_state

    def _watch_enable(self, slot):
        try:
            enable = self.printer.lookup_object("stepper_enable").lookup_enable(
                slot.stepper.get_name())
        except Exception:
            return
        original = getattr(enable, "motor_enable", None)
        if not callable(original) or not callable(
                getattr(enable, "is_motor_enabled", None)):
            return

        def motor_enable(*args, **kwargs):
            slot.enabled_at = self.reactor.monotonic()
            return original(*args, **kwargs)
        enable.motor_enable = motor_enable
        slot.enable = enable

    def _watch_resets(self, box, slot, box_stepper):
        """Klipper resets a slot's step clock at a homing end, as a load
        does and as Valve Oil does before an unload's pull-back, through
        either name QIDI's box_stepper gives the slot's stepper."""
        steppers = []
        for stepper in (slot.stepper, getattr(getattr(
                box_stepper, "extruder_stepper", None), "stepper", None)):
            if callable(getattr(stepper, "note_homing_end", None)) \
                    and all(stepper is not seen for seen in steppers):
                steppers.append(stepper)
        stepper_oid = slot.stepper.get_oid()
        for stepper in steppers:
            original = stepper.note_homing_end

            def note_homing_end(*args, _original=original, **kwargs):
                if box.feeding and box.active == stepper_oid:
                    logging.info(
                        "vo_box_watch: step clock reset during a feed: slot "
                        "%s (Box %s): Klipper reset the slot's step clock, as "
                        "a Box home does for every slot, while the Box was "
                        "feeding it; the feed's end puts back a clock from "
                        "before the reset", slot.label, box.number)
                result = _original(*args, **kwargs)
                slot.note_reset()
                return result
            stepper.note_homing_end = note_homing_end

    def _watch_feed_motion(self, slot, box_stepper):
        for method, move in FEED_MOTION:
            original = getattr(box_stepper, method, None)
            if callable(original):
                setattr(box_stepper, method,
                        self._during(slot, move, original))

    def _watch_slot_jog(self):
        jog = self.printer.lookup_object("vo_box_slot_jog", None)
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

    def _watch_autofeed(self):
        """QIDI's box_autofeed aborts auto-feed on the Box of its bound slot
        (`bind_stepper`) and arms it with auto_start."""
        autofeed = self.printer.lookup_object("box_autofeed", None)
        abort = getattr(autofeed, "auto_abort", None)
        start = getattr(autofeed, "auto_start", None)
        if not callable(abort) or not callable(start):
            return

        def auto_abort(*args, **kwargs):
            try:
                self._aborting(getattr(autofeed, "bind_stepper", None))
            except Exception:
                logging.exception("vo_box_watch: reading an auto-feed abort "
                                  "failed")
            return abort(*args, **kwargs)

        def auto_start(*args, **kwargs):
            for box in self.boxes.values():
                box.aborted = None
            return start(*args, **kwargs)
        try:
            autofeed.auto_abort = auto_abort
            autofeed.auto_start = auto_start
        except Exception:
            logging.info("vo_box_watch: QIDI's auto-feed calls can't be "
                         "watched")

    def _aborting(self, bound):
        now = self.reactor.monotonic()
        for box in self.boxes.values():
            slot = next((s for s in box.slots.values() if s.name == bound),
                        None)
            if slot is None:
                continue
            box.aborted = now
            queued = self._queued(box, slot, now)
            if box.feeding and queued is not None:
                logging.info(
                    "vo_box_watch: auto-feed aborted while Klipper moves the "
                    "slot: slot %s (Box %s): the abort empties the slot's "
                    "step queue, dropping %.3f s of Klipper's moves with the "
                    "feed", slot.label, box.number, queued)

    def _queued(self, box, slot, eventtime):
        """Seconds of steps Klipper has queued for SLOT past EVENTTIME, or
        None."""
        newest = slot.newest_step()
        if newest is None or eventtime is None:
            return None
        try:
            mcu = slot.stepper.get_mcu()
            clock = mcu.print_time_to_clock(
                mcu.estimated_print_time(eventtime))
        except Exception:
            return None
        queued = (newest - clock) / box.freq
        return queued if queued > QUEUED_SLACK else None

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
    def _state(self, box, state, received=None):
        if state == ARMED:
            # Arming auto-feed switches the bound slot's motor off.
            self._motor_off(box.slots.get(box.active))
        if state != FEEDING:
            return
        box.feeding = True
        box.feeds += 1
        self._feed_during_move(box, "started a feed")
        slot = box.slots.get(box.active)
        if slot is None:
            return
        queued = self._queued(box, slot, received)
        if queued is not None:
            logging.info(
                "vo_box_watch: Box feed while Klipper moves the slot: slot %s "
                "(Box %s): the Box started a feed with %.3f s of Klipper's "
                "moves still queued for the slot; the feed saves its step "
                "clock from inside them, and Klipper's moves queued during "
                "the feed run in the feed's direction", slot.label,
                box.number, queued)
        # A feed whose reply was already on its way when the abort was sent
        # started before it.
        if box.aborted is not None and received is not None \
                and received - box.aborted > QUEUED_SLACK:
            box.aborted = None
            logging.info(
                "vo_box_watch: Box feed after auto-feed was aborted: slot %s "
                "(Box %s): QIDI's code aborted auto-feed and the Box started "
                "another feed; an abort in the 10 ms after a feed's steps end "
                "leaves auto-feed armed", slot.label, box.number)

    def _reply(self, box, params):
        name = params.get("#name")
        if name == "last_step_clk":
            box.active = params.get("oid")
            self._clock(box, box.slots.get(box.active), params.get("clk"),
                        "saved", params.get("#receive_time"))
        elif name == "STEP-set_last_step":
            box.feeding = False
            self._feed_during_move(box, "ended a feed and switched the slot "
                                        "motor off")
            self._motor_off(box.slots.get(box.active))
            self._clock(box, box.slots.get(box.active), params.get("next_s"),
                        "put back", params.get("#receive_time"))
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
            box.aborted = None

    def _motor_off(self, slot):
        if slot is not None and slot.enable is not None:
            slot.box_off = self.reactor.monotonic()

    def _check_motors(self, eventtime):
        for box in self.boxes.values():
            for slot in box.slots.values():
                off = slot.box_off
                if off is None or eventtime - off < MOTOR_OFF_GRACE:
                    continue
                slot.box_off = None
                try:
                    enabled = slot.enable.is_motor_enabled()
                except Exception:
                    continue
                if enabled and (slot.enabled_at is None
                                or slot.enabled_at < off):
                    logging.info(
                        "vo_box_watch: Box switched a slot motor off that "
                        "Klipper has on: slot %s (Box %s): Klipper doesn't "
                        "switch it on again before its next move of the "
                        "slot, which then runs without current", slot.label,
                        box.number)

    def _feed_during_move(self, box, what):
        slot = box.slots.get(box.active)
        if slot is None or slot.moving is None:
            return
        logging.info("vo_box_watch: Box feed during Valve Oil's %s: slot %s "
                     "(Box %s): the Box %s while Valve Oil was moving the "
                     "slot", slot.moving, slot.label, box.number, what)

    def _clock(self, box, slot, clock, how, received=None):
        if slot is None or not isinstance(clock, int):
            return
        if slot.reset:
            # The first clock since Klipper reset the slot's: it should be
            # the reset's 0, or Klipper's last step if Klipper has moved the
            # slot since and is done moving it.
            slot.reset = False
            if slot.moved_since():
                newest = slot.newest_step()
                if newest is None \
                        or self._queued(box, slot, received) is not None:
                    slot.expect(clock)
                    return
                slot.expect(newest & 0xffffffff)
        elif slot.expected is None or slot.moved_since():
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
                if slot.expected is not None and not slot.reset \
                        and slot.moved_since():
                    slot.forget()
        self._check_motors(eventtime)
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
