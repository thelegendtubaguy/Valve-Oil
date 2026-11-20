# X/Y homing guard for QIDI's closed-loop X and Y drivers.
#
# The Max 4 homes X and Y against the frame, and the motor's closed-loop
# driver, not Klipper, decides when the toolhead has reached it: in homing
# mode its firmware waits until the motor has turned about 3.8 mm and passed
# about 48 mm/s, then pulls the endstop line when the motor slows below about
# 29 mm/s. It never looks at the position error. If it misses the wall, Klipper
# keeps driving the motor into the frame for the whole homing move, about
# 590 mm on X, while the driver reports nothing wrong.
#
# During every X and Y homing move this module asks the other motor's driver
# for its position error (function 0x39, the commanded angle minus the encoder
# angle, 51200 counts per motor turn) as often as QIDI's cl_interface queue
# answers. The Max 4 is CoreXY, so both motors turn for either axis and both
# stop when the toolhead does. The homing motor's own driver can't be asked:
# answering during a touch makes it report the end of the axis early, in the
# middle of the bed. A touch that ends normally leaves the motor at most about
# a cycle of 1024 counts behind. When the motor falls TRIP_CYCLES behind where
# it was at the start of the move, it is held, so the module ends the move the
# way Klipper ends a homing move that runs out of time: it triggers the
# endstop's trsync from the host, and the mainboard stops the X and Y motors at
# once. Klipper then reports "No trigger on x after full movement", and QIDI's
# homing (Valve Oil's homing.py) asks take_stop() whether this module stopped
# the move, backs off, and homes again.
#
# The guard can only watch while the driver answers. When SILENT_LIMIT
# questions in a row go unanswered during a touch, about half a second, it
# stops the move the same way, and homing fails without trying again: the
# toolhead can be anywhere, and a bus that stopped answering won't start again
# between two touches.
#
# Each move logs one line with what the driver reported, so a touch that went
# wrong can be told from one that didn't. On by default; the saved variable
# `vo_homing_guard` set to 0 turns it off. Printers without QIDI's closed-loop
# X/Y drivers ignore it.

import logging

ENABLE_VARIABLE = "vo_homing_guard"
STEPPERS = ("x", "y")
# The driver each homing axis is watched through. Asking the homing motor's own
# driver anything during a touch makes it report the wall early, so the guard
# asks the other one: on CoreXY both motors turn for either axis, and both stop
# when the toolhead does.
WATCHED = {"x": "y", "y": "x"}
READ_POSITION_ERROR = 0x39
CYCLE = 1024
COUNTS_PER_TURN = 51200
# On the Max 4 a touch that ends normally leaves the other motor at most about
# 1.2 cycles behind; a toolhead held by the frame at 65 mm/s passes eight in
# about 0.1 s.
TRIP_CYCLES = 8
TRIP_COUNTS = TRIP_CYCLES * CYCLE
# The shortest gap between two requests, so a touch logs about 50 replies a
# second, and how long to wait for an answer before asking again.
POLL_INTERVAL = 0.02
REPLY_TIMEOUT = 0.25
# Unanswered questions in a row that stop a touch. On the Max 4 every question
# is answered, the first within about 160 ms even when QIDI's mainboard holds
# each answer back for 50 ms. Two in a row also rides out a Klipper stall: the
# poll that runs late asks again, and the reply that was waiting is taken for
# the new question.
SILENT_LIMIT = 2
TRUE_WORDS = ("1", "true", "yes", "on", "enable", "enabled")


def _is_true(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in TRUE_WORDS
    return False


def query_frame(addr):
    """The driver's position-error query: FA addr 39 sum8."""
    frame = [0xfa, addr, READ_POSITION_ERROR]
    return bytes(frame + [sum(frame) & 0xff])


def parse_reply(data):
    """Position error from a `FB addr 39 int32 sum8` reply, else None."""
    data = bytes(data)
    if len(data) != 8 or data[0] != 0xfb or data[2] != READ_POSITION_ERROR \
            or sum(data[:-1]) & 0xff != data[-1]:
        return None
    return int.from_bytes(data[3:7], "big", signed=True)


def _trsyncs(mcu_endstop):
    """The trsyncs that stop this endstop's homing move, or None."""
    # QIDI's homing code allows for an endstop object that wraps the MCU's.
    endstop = getattr(mcu_endstop, "mcu_endstop", mcu_endstop)
    trsyncs = getattr(getattr(endstop, "_dispatch", None), "_trsyncs", None)
    if not trsyncs:
        return None
    for trsync in trsyncs:
        command = getattr(trsync, "_trsync_trigger_cmd", None)
        if not callable(getattr(command, "send", None)) \
                or not callable(getattr(trsync, "get_oid", None)) \
                or not hasattr(trsync, "REASON_HOST_REQUEST"):
            return None
    return list(trsyncs)


class Touch:
    """One X or Y homing move and what its motor's driver reported."""

    def __init__(self, name, trsyncs, started):
        self.name = name
        self.driver = WATCHED[name]
        self.trsyncs = trsyncs
        self.started = started
        self.start_pos = None
        self.travel = None
        self.baseline = None
        self.peak = 0
        self.reads = 0
        self.unanswered = 0
        self.silent = 0
        self.first_reply = None
        # Why the guard stopped the move: "behind" or "silent".
        self.stopped = None


class HomingGuard:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.cl = self.variables = self.toolhead = None
        self.addrs = {}
        self.rotation_distance = {}
        self.timer = None
        self.touch = None
        # (touch, send time) of the request in flight. QIDI's queue answers
        # one request at a time, so a reply to a request from an earlier move
        # can arrive during the next one, and is dropped.
        self.waiting = None
        # (message, retry) for the last move this module stopped, until
        # take_stop() collects it.
        self.last_stop = None
        self.warned_no_trsync = False
        for name in STEPPERS:
            self.printer.register_event_handler(
                "cl:" + name, self._make_rx_handler(name))
        self.printer.register_event_handler(
            "homing:homing_move_begin", self._move_begin)
        self.printer.register_event_handler(
            "homing:homing_move_end", self._move_end)
        self.printer.register_event_handler("klippy:ready", self._ready)

    def _ready(self):
        cl = self.printer.lookup_object("cl_interface", None)
        addrs = getattr(cl, "name2addr", None)
        if not callable(getattr(cl, "send_command", None)) \
                or not isinstance(addrs, dict) \
                or not all(name in addrs for name in STEPPERS):
            logging.info("vo_homing_guard: no QIDI closed-loop X/Y drivers;"
                         " guard off")
            return
        self.cl = cl
        self.addrs = {name: addrs[name] for name in STEPPERS}
        self.variables = self.printer.lookup_object("save_variables", None)
        toolhead = self.toolhead = self.printer.lookup_object("toolhead")
        for stepper in toolhead.get_kinematics().get_steppers():
            name = stepper.get_name()
            if name.startswith("stepper_") and name[8:] in STEPPERS:
                self.rotation_distance[name[8:]] = \
                    stepper.get_rotation_distance()[0]
        self.timer = self.reactor.register_timer(self._poll)

    def _enabled(self):
        if self.variables is None:
            return True
        try:
            variables = self.variables.get_status(0.).get("variables")
        except Exception:
            variables = None
        if not isinstance(variables, dict):
            variables = getattr(self.variables, "allVariables", {}) or {}
        if ENABLE_VARIABLE not in variables:
            return True
        return _is_true(variables[ENABLE_VARIABLE])

    def _axis_pos(self, name):
        try:
            return float(self.toolhead.get_position()[STEPPERS.index(name)])
        except Exception:
            return None

    def _mm(self, name, counts):
        distance = self.rotation_distance.get(name)
        if not distance:
            return None
        return counts * distance / COUNTS_PER_TURN

    # --------------------------------------------------------------- events
    def _move_begin(self, hmove):
        self._finish()
        self.last_stop = None
        if self.cl is None or not self._enabled():
            return
        endstops = getattr(hmove, "endstops", None) or []
        if len(endstops) != 1 or endstops[0][1] not in STEPPERS:
            return
        mcu_endstop, name = endstops[0]
        trsyncs = _trsyncs(mcu_endstop)
        if trsyncs is None:
            if not self.warned_no_trsync:
                self.warned_no_trsync = True
                logging.warning("vo_homing_guard: this Klipper's endstop has no"
                                " trsync the guard can trigger; guard off")
            return
        self.touch = Touch(name, trsyncs, self.reactor.monotonic())
        self.touch.start_pos = self._axis_pos(name)
        self.reactor.update_timer(self.timer, self.reactor.NOW)

    def _move_end(self, hmove):
        touch = self._finish()
        if touch is None:
            return
        # QIDI's closed-loop homing asks for the trigger position, so the
        # toolhead now stands where the touch ended.
        end_pos = self._axis_pos(touch.name)
        if touch.start_pos is not None and end_pos is not None:
            touch.travel = abs(end_pos - touch.start_pos)
        if touch.stopped:
            self.last_stop = (self._stop_message(touch),
                              touch.stopped == "behind")
        logging.info("vo_homing_guard: %s", self._summary(touch))

    def _finish(self):
        touch, self.touch = self.touch, None
        if self.timer is not None:
            self.reactor.update_timer(self.timer, self.reactor.NEVER)
        return touch

    def take_stop(self):
        """(message, retry) for the last homing move this module stopped,
        once, else None. retry is False when homing must not touch again."""
        stop, self.last_stop = self.last_stop, None
        return stop

    # -------------------------------------------------------------- polling
    def _poll(self, eventtime):
        touch = self.touch
        if touch is None or touch.stopped:
            return self.reactor.NEVER
        if self.waiting is not None:
            sent = self.waiting[1]
            if eventtime < sent + REPLY_TIMEOUT:
                return sent + REPLY_TIMEOUT
            if self.waiting[0] is touch:
                touch.unanswered += 1
                touch.silent += 1
                if touch.silent >= SILENT_LIMIT:
                    self.waiting = None
                    self._stop(touch, "silent")
                    return self.reactor.NEVER
        self.waiting = (touch, eventtime)
        addr = self.addrs[touch.driver]
        try:
            self.cl.send_command(addr, query_frame(addr))
        except Exception:
            logging.exception("vo_homing_guard: position error query failed")
        return eventtime + REPLY_TIMEOUT

    def _make_rx_handler(self, name):
        # A reply can arrive on the serial thread; timers and MCU commands
        # belong to the reactor.
        def rx(eventtime, data):
            try:
                self._rx(name, data)
            except Exception:
                logging.exception("vo_homing_guard: reply failed")

        def handler(data):
            data = bytes(data)
            self.reactor.register_async_callback(
                lambda eventtime: rx(eventtime, data))
        return handler

    def _rx(self, name, data):
        waiting = self.waiting
        value = parse_reply(data)
        if waiting is None or value is None:
            return
        touch, sent = waiting
        if name != touch.driver:
            return
        self.waiting = None
        if touch is not self.touch:
            # The move that asked has ended; the current one asks afresh.
            if self.touch is not None and not self.touch.stopped:
                self.reactor.update_timer(self.timer, self.reactor.NOW)
            return
        if touch.stopped:
            return
        now = self.reactor.monotonic()
        touch.reads += 1
        touch.silent = 0
        if touch.baseline is None:
            touch.baseline = value
            touch.first_reply = now - touch.started
        deviation = value - touch.baseline
        if abs(deviation) > abs(touch.peak):
            touch.peak = deviation
        if abs(deviation) >= TRIP_COUNTS:
            self._stop(touch, "behind")
            return
        self.reactor.update_timer(self.timer, max(now, sent + POLL_INTERVAL))

    def _stop(self, touch, reason):
        touch.stopped = reason
        for trsync in touch.trsyncs:
            trsync._trsync_trigger_cmd.send(
                [trsync.get_oid(), trsync.REASON_HOST_REQUEST])
        if reason == "silent":
            logging.warning("vo_homing_guard: stopped the %s homing move, the %s"
                            " driver did not answer %d questions in a row",
                            touch.name.upper(), touch.driver.upper(),
                            touch.silent)
        else:
            logging.warning("vo_homing_guard: stopped the %s homing move, the"
                            " %s motor is %+d counts behind",
                            touch.name.upper(), touch.driver.upper(),
                            touch.peak)

    # ------------------------------------------------------------- messages
    def _behind(self, touch):
        mm = self._mm(touch.driver, abs(touch.peak))
        if mm is None:
            return "%.1f step cycles" % (abs(touch.peak) / float(CYCLE),)
        return "%.1f mm" % (mm,)

    def _stop_message(self, touch):
        axis = touch.name.upper()
        if touch.stopped == "silent":
            return ("Stopped homing %s because the X and Y motor drivers"
                    " stopped answering, so nothing could tell whether the"
                    " toolhead had reached the end of the axis. Turn the"
                    " printer off and on, then home again." % (axis,))
        return ("Stopped homing %s because the motors fell %s behind before"
                " the %s driver reported the end of the axis." % (
                    axis, self._behind(touch), axis))

    def _summary(self, touch):
        first = ("no reply" if touch.first_reply is None
                 else "first reply %.0f ms" % (touch.first_reply * 1000.,))
        baseline = "none" if touch.baseline is None else "%d" % touch.baseline
        travel = ("" if touch.travel is None
                  else " after %.1f mm" % (touch.travel,))
        return ("%s homing move %s%s: %d %s position error reads, %d unanswered,"
                " %s, baseline %s, peak %+d counts (%s)" % (
                    touch.name.upper(),
                    {"behind": "stopped by the guard",
                     "silent": "stopped by the guard, no answer"}.get(
                         touch.stopped, "ended"),
                    travel,
                    touch.reads, touch.driver.upper(), touch.unanswered, first,
                    baseline,
                    touch.peak, self._behind(touch)))


def load_config(config):
    return HomingGuard(config)
