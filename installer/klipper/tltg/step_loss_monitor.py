# X/Y step-loss monitor for Valve Oil.
#
# The Max 4's X and Y closed-loop drivers report their position error: the
# commanded angle minus the encoder angle, 51200 counts per motor turn. A
# motor that slips under load falls behind by whole electrical cycles of 1024
# counts (4 full steps, 0.78 mm of belt at rotation_distance 39) and the
# driver keeps that offset; it reports it but never moves to remove it, and
# QIDI's software never reads it. Homing slips the same way against the frame,
# so each home leaves its own whole-cycle offset.
#
# This module asks each driver for its position error (function 0x39, the only
# query it sends) through QIDI's cl_interface queue, one request at a time,
# while X and Y are homed and enabled. It records a baseline per motor at rest
# after each home, and reports a slip when two reads in a row sit at least
# three quarters of a cycle from it. The slip shows in the console and in
# Fluidd's warning banner until X and Y are homed again or the position is
# corrected.
#
# A slip during print start cancels the print: the start sequence runs as one
# command, so a pause would wait for all of it, leveling and meshing included.
# A slip later in a print pauses it, or, with `tltg_step_loss_recovery` on,
# runs _TLTG_STEP_LOSS_RECOVER once per print: it lifts the nozzle, and
# TLTG_STEP_LOSS_CORRECT moves Klipper's X/Y position by the slip the motors
# report, so the print carries on where it was. A second slip, or a failed
# correction, pauses.
#
# The module is inert until the saved variable `tltg_step_loss_monitor` exists
# and is truthy. On printers without QIDI's closed-loop drivers it does
# nothing.

import logging

ENABLE_VARIABLE = "tltg_step_loss_monitor"
RECOVERY_VARIABLE = "tltg_step_loss_recovery"
RECOVER_MACRO = "_TLTG_STEP_LOSS_RECOVER"
RECOVER_STATE = "_tltg_step_loss_recover"
RECOVERIES_PER_PRINT = 1
STEPPERS = ("x", "y")
READ_POSITION_ERROR = 0x39
CYCLE = 1024
COUNTS_PER_TURN = 51200
SLIP_THRESHOLD = 768
CONFIRM_READS = 2
POLL_INTERVAL = 0.25
REPLY_TIMEOUT = 0.5
IDLE_INTERVAL = 1.0
# How long TLTG_STEP_LOSS_CORRECT waits, once the toolhead stops, for
# CONFIRM_READS fresh reads of each motor.
SETTLE_TIMEOUT = 5.0
SETTLE_POLL = 0.05
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


class StepLossMonitor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.cl = self.toolhead = self.motion_report = self.mcu = None
        self.print_stats = self.stepper_enable = self.variables = None
        self.homing_override = self.status_manager = self.sdcard = None
        self.corexy = False
        self.addrs = {}
        self.rotation_distance = {}
        self.homing = False
        self.next_stepper = 0
        # (stepper, send time) of the request in flight.
        self.waiting = None
        self.baseline = {}
        self.over = {}
        # Cycles each motor slipped since X and Y were last homed, and the
        # axes homed since the last slip.
        self.slipped = {}
        self.rehomed = set()
        self.message = None
        self.timer = None
        # The pause, cancel, or recovery queued for a slip, until it has run.
        self.action = None
        self.recoveries = 0
        # Reads of each motor sent since fresh_since, while a correction waits.
        self.fresh_since = None
        self.fresh = {}
        for name in STEPPERS:
            self.printer.register_event_handler(
                "cl:" + name, self._make_rx_handler(name))
        self.printer.register_event_handler(
            "homing:home_rails_begin", self._homing_begin)
        self.printer.register_event_handler(
            "homing:home_rails_end", self._homing_end)
        self.printer.register_event_handler("klippy:ready", self._ready)

    # ----------------------------------------------------------------- setup
    def _ready(self):
        cl = self.printer.lookup_object("cl_interface", None)
        addrs = getattr(cl, "name2addr", None)
        if not callable(getattr(cl, "send_command", None)) \
                or not isinstance(addrs, dict) \
                or not all(name in addrs for name in STEPPERS):
            logging.info("tltg_step_loss_monitor: no QIDI closed-loop X/Y"
                         " drivers; monitor off")
            return
        self.cl = cl
        self.addrs = {name: addrs[name] for name in STEPPERS}
        lookup = self.printer.lookup_object
        self.toolhead = lookup("toolhead")
        self.motion_report = lookup("motion_report", None)
        self.mcu = lookup("mcu")
        self.print_stats = lookup("print_stats", None)
        self.stepper_enable = lookup("stepper_enable", None)
        self.variables = lookup("save_variables", None)
        self.homing_override = lookup("homing_override", None)
        self.status_manager = lookup("print_stats_manager", None)
        self.sdcard = lookup("virtual_sdcard", None)
        # The correction assumes CoreXY: stepper_x drives X+Y, stepper_y X-Y.
        self.corexy = type(self.toolhead.get_kinematics()).__name__ \
            == "CoreXYKinematics"
        for name in STEPPERS:
            stepper = self._stepper(name)
            if stepper is not None:
                self.rotation_distance[name] = \
                    stepper.get_rotation_distance()[0]
        reset = self.gcode.register_command("RESET_MOTOR_POSITION_ERROR",
                                            None)
        if reset is not None:
            self.gcode.register_command(
                "RESET_MOTOR_POSITION_ERROR",
                lambda gcmd: self._reset_position_error(reset, gcmd),
                desc=self.gcode.gcode_help.get("RESET_MOTOR_POSITION_ERROR"))
        self.gcode.register_command(
            "TLTG_STEP_LOSS_CORRECT", self.cmd_TLTG_STEP_LOSS_CORRECT,
            desc="Moves Klipper's X/Y position by the slip the X and Y motors"
                 " report")
        self.timer = self.reactor.register_timer(self._tick, self.reactor.NOW)

    def _stepper(self, name):
        kin = self.toolhead.get_kinematics()
        for stepper in kin.get_steppers():
            if stepper.get_name() == "stepper_" + name:
                return stepper
        return None

    # ----------------------------------------------------------------- state
    def _variable(self, name):
        if self.variables is None:
            return False
        try:
            variables = self.variables.get_status(0.).get("variables")
        except Exception:
            variables = None
        if not isinstance(variables, dict):
            variables = getattr(self.variables, "allVariables", {}) or {}
        return _is_true(variables.get(name, False))

    def _enabled(self):
        return self._variable(ENABLE_VARIABLE)

    def _recovery_enabled(self):
        return self.corexy and self._variable(RECOVERY_VARIABLE)

    def _xy_ready(self, eventtime):
        homed = self.toolhead.get_status(eventtime).get("homed_axes", "")
        if "x" not in homed or "y" not in homed:
            return False
        if self.stepper_enable is not None:
            for name in STEPPERS:
                try:
                    enable = self.stepper_enable.lookup_enable(
                        "stepper_" + name)
                except Exception:
                    continue
                if not enable.is_motor_enabled():
                    return False
        return True

    def _busy(self):
        if self.homing or getattr(self.homing_override, "in_script", False):
            return True
        upgrading = getattr(self.cl, "is_any_upgrading", None)
        return callable(upgrading) and bool(upgrading())

    def _at_rest(self, start, end):
        if self.motion_report is None:
            return False
        trapq = self.motion_report.trapqs.get("toolhead")
        if trapq is None:
            return False
        for eventtime in (start, end):
            print_time = self.mcu.estimated_print_time(eventtime)
            pos, velocity = trapq.get_trapq_position(print_time)
            if pos is not None and velocity:
                return False
        return True

    def _print_state(self, eventtime):
        if self.print_stats is None:
            return None
        return self.print_stats.get_status(eventtime).get("state")

    def _printing(self, eventtime):
        return self._print_state(eventtime) == "printing"

    def _print_starting(self, eventtime):
        if self.status_manager is None:
            return False
        status = self.status_manager.get_status(eventtime)
        return status.get("main_status") == "print_start"

    def _clear_baseline(self):
        # A reply to a read sent before a home or reset would carry the old
        # offset.
        self.waiting = None
        self.baseline = {}
        self.over = {}

    def _clear_slips(self):
        self.slipped = {}
        self.message = None

    # --------------------------------------------------------------- events
    def _homing_begin(self, homing_state, rails):
        self.homing = True
        self._clear_baseline()

    def _homing_end(self, homing_state, rails):
        self.homing = False
        self._clear_baseline()
        self.rehomed.update("xyz"[axis] for axis in homing_state.get_axes()
                            if axis < 3)
        if self.slipped and {"x", "y"} <= self.rehomed:
            self._clear_slips()

    def _reset_position_error(self, original, gcmd):
        try:
            return original(gcmd)
        finally:
            self._clear_baseline()

    # -------------------------------------------------------------- polling
    def _tick(self, eventtime):
        try:
            return self._poll(eventtime)
        except Exception:
            logging.exception("tltg_step_loss_monitor: poll failed")
            self.waiting = None
            return eventtime + IDLE_INTERVAL

    def _poll(self, eventtime):
        if self.printer.is_shutdown():
            return self.reactor.NEVER
        if self._print_state(eventtime) not in ("printing", "paused"):
            self.recoveries = 0
        if not self._enabled() or not self._xy_ready(eventtime):
            self.waiting = None
            self._clear_baseline()
            return eventtime + IDLE_INTERVAL
        if self._busy():
            self.waiting = None
            return eventtime + POLL_INTERVAL
        if self.waiting is not None \
                and eventtime < self.waiting[1] + REPLY_TIMEOUT:
            return self.waiting[1] + REPLY_TIMEOUT
        name = STEPPERS[self.next_stepper % len(STEPPERS)]
        self.next_stepper += 1
        self.waiting = (name, eventtime)
        self.cl.send_command(self.addrs[name], query_frame(self.addrs[name]))
        return eventtime + REPLY_TIMEOUT

    def _make_rx_handler(self, name):
        def handler(data):
            try:
                self._rx(name, data)
            except Exception:
                logging.exception("tltg_step_loss_monitor: reply failed")
        return handler

    def _rx(self, name, data):
        waiting = self.waiting
        if waiting is None or waiting[0] != name:
            return
        value = parse_reply(data)
        if value is None:
            return
        self.waiting = None
        now = self.reactor.monotonic()
        if self.timer is not None:
            self.reactor.update_timer(self.timer, waiting[1] + POLL_INTERVAL)
        self._check(name, value, waiting[1], now)
        if self.fresh_since is not None and waiting[1] >= self.fresh_since:
            self.fresh[name] = self.fresh.get(name, 0) + 1

    def _check(self, name, value, sent, received):
        baseline = self.baseline.get(name)
        if baseline is None:
            if self._at_rest(sent, received):
                self.baseline[name] = value
            return
        deviation = value - baseline
        if abs(deviation) < SLIP_THRESHOLD:
            self.over[name] = 0
            return
        self.over[name] = self.over.get(name, 0) + 1
        if self.over[name] < CONFIRM_READS:
            return
        self.over[name] = 0
        cycles = int(round(deviation / float(CYCLE)))
        self.baseline[name] = baseline + cycles * CYCLE
        self.slipped[name] = self.slipped.get(name, 0) + cycles
        self.rehomed = set()
        self._report(name, cycles, received)

    def _size(self, name, cycles):
        size = "%d step cycle%s" % (abs(cycles), "" if abs(cycles) == 1
                                    else "s")
        distance = self.rotation_distance.get(name)
        if distance:
            size += " (%.1f mm of belt)" % (
                abs(cycles) * CYCLE * distance / COUNTS_PER_TURN,)
        return size

    def _summary(self):
        """The net slip of each motor since X and Y were last homed."""
        slips = ["stepper_%s %s" % (name, self._size(name, self.slipped[name]))
                 for name in STEPPERS if self.slipped.get(name)]
        if not slips:
            return None
        return ("Since X and Y were last homed, %s slipped: the toolhead is no"
                " longer where Klipper thinks it is. Home X and Y before moving"
                " it again." % (" and ".join(slips),))

    def _report(self, name, cycles, eventtime):
        printing = self._printing(eventtime)
        self.message = self._summary()
        event = ("The stepper_%s motor slipped %s: the toolhead is no longer"
                 " where Klipper thinks it is. Home X and Y before moving it"
                 " again." % (name, self._size(name, cycles)))
        logging.warning("tltg_step_loss_monitor: %s Position error %+d counts"
                        " from its baseline.", event, cycles * CYCLE)
        action = self.action
        if printing and action is None:
            if self._print_starting(eventtime):
                action = "cancel"
            elif self._recovery_enabled() \
                    and self.recoveries < RECOVERIES_PER_PRINT:
                action = "recover"
            else:
                action = "pause"
            self.action = action
            self.reactor.register_callback(
                lambda e, action=action: self._run_action(action))
        if action == "cancel":
            event += " The print is cancelled."
        elif action == "recover":
            event = ("The stepper_%s motor slipped %s. Correcting the"
                     " toolhead position and continuing the print."
                     % (name, self._size(name, cycles)))
        elif action == "pause":
            event += " The print is paused."
        self.gcode.respond_raw("!! " + event)

    def _run_action(self, action):
        try:
            if action == "cancel":
                self._cancel()
            elif action == "recover":
                self._recover()
            else:
                self._pause()
        finally:
            self.action = None

    def _pause(self):
        if not self._printing(self.reactor.monotonic()):
            return
        try:
            self.gcode.run_script("PAUSE")
        except Exception:
            logging.exception("tltg_step_loss_monitor: PAUSE failed")

    def _cancel(self):
        # As Fluidd's and the screen's cancel: QIDI's virtual_sdcard cancel
        # also breaks heater waits and the running G-code, so the cancel
        # doesn't wait for the rest of the start sequence.
        if not self._printing(self.reactor.monotonic()):
            return
        try:
            if self.sdcard is not None and self.sdcard.is_active():
                self.sdcard.do_cancel()
            if self._recovery_enabled():
                self._try_script("TLTG_STEP_LOSS_CORRECT")
            self.gcode.run_script("CANCEL_PRINT")
        except Exception:
            logging.exception("tltg_step_loss_monitor: cancel failed")

    def _recover(self):
        if not self._printing(self.reactor.monotonic()):
            return
        self.recoveries += 1
        if not self._try_script(RECOVER_MACRO):
            self.gcode.respond_raw(
                "!! The toolhead position couldn't be corrected after the"
                " slip. The print is paused.")
            self._try_script("RESTORE_GCODE_STATE NAME=" + RECOVER_STATE)
            self._pause()
        elif self.slipped:
            self.gcode.respond_raw(
                "!! A motor slipped again while the position was being"
                " corrected. The print is paused.")
            self._pause()

    def _try_script(self, script):
        try:
            self.gcode.run_script(script)
        except Exception:
            logging.exception("tltg_step_loss_monitor: %s failed", script)
            return False
        return True

    # ------------------------------------------------------------ correction
    def cmd_TLTG_STEP_LOSS_CORRECT(self, gcmd):
        if not self._enabled():
            raise gcmd.error("The X/Y step-loss monitor is off.")
        if not self.corexy or set(self.rotation_distance) != set(STEPPERS):
            raise gcmd.error("TLTG_STEP_LOSS_CORRECT needs CoreXY X and Y"
                             " steppers.")
        # Any other exception from a command shuts Klipper down.
        try:
            self._correct(gcmd)
        except gcmd.error:
            raise
        except Exception:
            logging.exception("tltg_step_loss_monitor: correction failed")
            raise gcmd.error("The slip couldn't be corrected; see"
                             " klippy.log.")

    def _correct(self, gcmd):
        self.toolhead.wait_moves()
        self._settle(gcmd)
        cycles = {name: self.slipped.get(name, 0) for name in STEPPERS}
        if not any(cycles.values()):
            gcmd.respond_info("No X/Y motor slip to correct.")
            return
        # A motor that slipped is behind where it was told to be, by whole
        # cycles; a positive error is behind in the direction +X turns it.
        a, b = (cycles[name] * CYCLE * self.rotation_distance[name]
                / COUNTS_PER_TURN for name in STEPPERS)
        dx, dy = -(a + b) / 2., -(a - b) / 2.
        pos = self.toolhead.get_position()
        self.toolhead.set_position([pos[0] + dx, pos[1] + dy] + pos[2:])
        self._clear_slips()
        message = ("Moved Klipper's position by X%+.2f Y%+.2f mm for the slip"
                   " (stepper_x %+d, stepper_y %+d step cycles)."
                   % (dx, dy, cycles["x"], cycles["y"]))
        logging.info("tltg_step_loss_monitor: %s", message)
        gcmd.respond_info(message)

    def _settle(self, gcmd):
        """Waits for fresh reads of both motors at rest."""
        self.fresh_since = self.reactor.monotonic()
        self.fresh = {}
        deadline = self.fresh_since + SETTLE_TIMEOUT
        try:
            while not (all(self.fresh.get(name, 0) >= CONFIRM_READS
                           and name in self.baseline for name in STEPPERS)
                       and not any(self.over.values())):
                now = self.reactor.monotonic()
                if now > deadline or self.printer.is_shutdown():
                    raise gcmd.error("The X and Y motors didn't report their"
                                     " position; the slip isn't corrected.")
                self.reactor.pause(now + SETTLE_POLL)
        finally:
            self.fresh_since = None

    def get_status(self, eventtime=None):
        return {
            "monitoring": bool(self.baseline),
            "slipped_cycles": dict(self.slipped),
            "message": self.message,
        }


def load_config(config):
    return StepLossMonitor(config)
