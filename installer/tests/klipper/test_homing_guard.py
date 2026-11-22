from __future__ import annotations

import math
import types
import unittest

from installer.tests.helpers import REPO_ROOT
from installer.tests.klipper_fakes import NEVER, CommandError, Reactor, load_extra, load_source


homing_guard = load_extra("homing_guard", "vo_homing_guard")
# Both Max 4 variants of Valve Oil's QIDI homing.py carry the same touch loop.
HOMING_SOURCES = ("installer/klipper/qidi/homing.py",
                  "installer/klipper/qidi/homing-sync-reset.py")

ADDRS = {"x": 1, "y": 3}
CYCLE = 1024


def reply(addr, value):
    frame = [0xfb, addr, 0x39] + list(value.to_bytes(4, "big", signed=True))
    return bytes(frame + [sum(frame) & 0xff])


# --------------------------------------------------------------- guard fakes
class ClInterface:
    def __init__(self):
        self.name2addr = dict(ADDRS)
        self.sent = []
        self.upgrading = False

    def send_command(self, addr, msg, expects_response=True, timeout=0.5):
        self.sent.append(bytes(msg))

    def is_any_upgrading(self):
        return self.upgrading


class TriggerCommand:
    def __init__(self):
        self.sent = []

    def send(self, data, minclock=0, reqclock=0):
        self.sent.append(list(data))


class Trsync:
    REASON_HOST_REQUEST = 2

    def __init__(self):
        self._trsync_trigger_cmd = TriggerCommand()

    def get_oid(self):
        return 7


class Endstop:
    """Klipper's MCU_endstop: its TriggerDispatch holds the trsyncs."""

    def __init__(self):
        self.trsync = Trsync()
        self._dispatch = types.SimpleNamespace(_trsyncs=[self.trsync])


class Stepper:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name

    def get_rotation_distance(self):
        return 39., 200


class Status:
    def __init__(self, **status):
        self.status = status

    def get_status(self, eventtime=None):
        return self.status


class GuardPrinter:
    def __init__(self, closed_loop=True, variables=None):
        self.reactor = Reactor()
        self.cl = ClInterface()
        self.handlers = {}
        kinematics = types.SimpleNamespace(get_steppers=lambda: [
            Stepper("stepper_x"), Stepper("stepper_y"), Stepper("stepper_z")])
        self.objects = {
            "toolhead": types.SimpleNamespace(
                get_kinematics=lambda: kinematics),
            "save_variables": Status(variables=dict(variables or {})),
        }
        if closed_loop:
            self.objects["cl_interface"] = self.cl
        self.guard = homing_guard.load_config(self)
        self.send_event("klippy:ready")

    def get_printer(self):
        return self

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def register_event_handler(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def send_event(self, event, *args):
        for handler in self.handlers.get(event, []):
            handler(*args)

    # Test drivers.
    def begin(self, name="x"):
        endstop = Endstop()
        self.hmove = types.SimpleNamespace(endstops=[(endstop, name)])
        self.send_event("homing:homing_move_begin", self.hmove)
        return endstop

    def end(self):
        self.send_event("homing:homing_move_end", self.hmove)

    def silence(self):
        """Run the poll timer when due and leave its query unanswered."""
        timer = self.reactor.timers[0]
        if timer.waketime >= NEVER:
            return False
        self.reactor.now = max(self.reactor.now, timer.waketime)
        timer.waketime = timer.callback(self.reactor.now)
        return True

    def read(self, name, value):
        """Run the poll timer when due and answer its query with value."""
        if not self.reactor.timers:
            return False
        timer = self.reactor.timers[0]
        if timer.waketime >= NEVER:
            return False
        self.reactor.now = max(self.reactor.now, timer.waketime)
        before = len(self.cl.sent)
        timer.waketime = timer.callback(self.reactor.now)
        if len(self.cl.sent) == before:
            return False
        self.reactor.now += 0.008
        self.send_event("cl:" + name, reply(ADDRS[name], value))
        while self.reactor.callbacks:
            self.reactor.callbacks.pop(0)(self.reactor.now)
        return True


class HomingGuardTest(unittest.TestCase):
    def test_blocked_touch_is_stopped_from_the_host(self) -> None:
        printer = GuardPrinter()
        endstop = printer.begin("x")
        # Moving, the error swings and returns; held by the frame it climbs.
        for value in (1024, 1100, 900, 1024, 3000, 6000, 1024 + 8 * CYCLE):
            self.assertTrue(printer.read("y", value))
        self.assertEqual(endstop.trsync._trsync_trigger_cmd.sent,
                         [[7, Trsync.REASON_HOST_REQUEST]])
        # Stopped: no more queries while Klipper winds the move down.
        self.assertFalse(printer.read("y", 1024 + 9 * CYCLE))
        printer.end()
        message, retry = printer.guard.take_stop()
        self.assertTrue(retry)
        self.assertIn("Stopped homing X", message)
        self.assertIn("6.2 mm behind", message)
        self.assertIsNone(printer.guard.take_stop())
        # Asking the homing driver itself makes it end the touch early, so
        # only the other driver is asked.
        self.assertEqual(set(printer.cl.sent), {bytes([0xfa, 3, 0x39, 0x36])})

    def test_touch_ending_normally_changes_nothing(self) -> None:
        printer = GuardPrinter()
        endstop = printer.begin("y")
        # A touch the driver ends leaves at most a few cycles behind.
        for value in (-2, 40, -140, 3 * CYCLE, 4 * CYCLE):
            self.assertTrue(printer.read("x", value))
        printer.end()
        self.assertEqual(endstop.trsync._trsync_trigger_cmd.sent, [])
        self.assertIsNone(printer.guard.take_stop())
        self.assertEqual(set(printer.cl.sent), {bytes([0xfa, 1, 0x39, 0x34])})
        # Between moves the guard is quiet.
        self.assertFalse(printer.read("x", 0))

    def test_reply_to_an_earlier_move_is_not_the_next_ones_start(self) -> None:
        printer = GuardPrinter()
        printer.begin("x")
        timer = printer.reactor.timers[0]
        timer.waketime = timer.callback(printer.reactor.now)
        printer.end()
        # The next move starts before the last request is answered.
        endstop = printer.begin("x")
        timer.waketime = timer.callback(printer.reactor.now)
        self.assertEqual(len(printer.cl.sent), 1)
        printer.send_event("cl:y", reply(ADDRS["y"], 9 * CYCLE))
        while printer.reactor.callbacks:
            printer.reactor.callbacks.pop(0)(printer.reactor.now)
        for value in (0, 40, 4 * CYCLE):
            self.assertTrue(printer.read("y", value))
        printer.end()
        self.assertEqual(endstop.trsync._trsync_trigger_cmd.sent, [])
        self.assertIsNone(printer.guard.take_stop())

    def test_drivers_that_stop_answering_stop_the_touch(self) -> None:
        printer = GuardPrinter()
        endstop = printer.begin("x")
        self.assertTrue(printer.read("y", 0))
        # One unanswered question is asked again.
        printer.silence()
        self.assertTrue(printer.read("y", 40))
        self.assertEqual(endstop.trsync._trsync_trigger_cmd.sent, [])
        # Two in a row stop the move, and homing must not touch again.
        printer.silence()
        printer.silence()
        printer.silence()
        self.assertEqual(endstop.trsync._trsync_trigger_cmd.sent,
                         [[7, Trsync.REASON_HOST_REQUEST]])
        self.assertFalse(printer.silence())
        printer.end()
        message, retry = printer.guard.take_stop()
        self.assertFalse(retry)
        self.assertIn("stopped answering", message)

    def test_no_questions_while_a_motor_firmware_upgrade_runs(self) -> None:
        # QIDI's UPGRADE_MOTOR returns before the transfer ends, and a
        # question sent into the transfer can corrupt it.
        printer = GuardPrinter()
        printer.cl.upgrading = True
        endstop = printer.begin("x")
        self.assertFalse(printer.read("y", 50 * CYCLE))
        printer.end()
        self.assertEqual(printer.cl.sent, [])
        self.assertIsNone(printer.guard.take_stop())

        # An upgrade that starts during a touch: no more questions, and the
        # driver going quiet doesn't stop the move.
        printer.cl.upgrading = False
        endstop = printer.begin("x")
        self.assertTrue(printer.read("y", 0))
        printer.cl.upgrading = True
        self.assertFalse(printer.read("y", 50 * CYCLE))
        for _ in range(4):
            printer.silence()
        printer.end()
        self.assertEqual(len(printer.cl.sent), 1)
        self.assertEqual(endstop.trsync._trsync_trigger_cmd.sent, [])
        self.assertIsNone(printer.guard.take_stop())

        # Once the upgrade is over, touches are watched again.
        printer.cl.upgrading = False
        endstop = printer.begin("x")
        self.assertTrue(printer.read("y", 0))
        self.assertTrue(printer.read("y", 9 * CYCLE))
        self.assertEqual(endstop.trsync._trsync_trigger_cmd.sent,
                         [[7, Trsync.REASON_HOST_REQUEST]])

    def test_guard_off_z_probe_and_printers_without_closed_loop(self) -> None:
        cases = (
            (GuardPrinter(variables={"vo_homing_guard": 0}), "x"),
            (GuardPrinter(), "z"),
            (GuardPrinter(), "probe"),
            (GuardPrinter(closed_loop=False), "x"),
        )
        for printer, name in cases:
            with self.subTest(name=name):
                endstop = printer.begin(name)
                self.assertFalse(printer.read("y", 50 * CYCLE))
                printer.end()
                self.assertEqual(printer.cl.sent, [])
                self.assertEqual(endstop.trsync._trsync_trigger_cmd.sent, [])
                self.assertIsNone(printer.guard.take_stop())


# ------------------------------------------------------------- homing fakes
class Toolhead:
    def __init__(self):
        self.position = [150., 150., 0., 0.]
        self.moves = []
        self.x_homes = []

    def get_position(self):
        return list(self.position)

    def set_position(self, newpos, homing_axes=()):
        self.position = list(newpos)

    def manual_move(self, newpos, speed):
        axis = HomingMove.axis
        self.moves.append((self.position[axis], newpos[axis]))
        self.position = [p if n is None else n
                         for p, n in zip(self.position, newpos)]

    def get_kinematics(self):
        return self

    # Kinematics: homing X leaves the toolhead at the X endstop.
    def home(self, homing_state):
        self.x_homes.append((homing_state.get_axes(), list(self.position)))
        self.position[0] = 392.


ARM = "SET_HOMING_STATE STEPPER=x VALUE=1"
RECOVER = "SET_HOMING_STATE STEPPER=x VALUE=2"


class Gcode:
    def __init__(self, fail_recover=False):
        self.scripts = []
        self.console = []
        self.fail_recover = fail_recover

    def run_script_from_command(self, script):
        self.scripts.append(script)
        if self.fail_recover and RECOVER in script:
            raise CommandError("Unable to set the drivers' homing state")

    def homing_scripts(self):
        """The arm and recover scripts run, in order."""
        return ["arm" if ARM in script else "recover"
                for script in self.scripts
                if ARM in script or RECOVER in script]

    def respond_raw(self, message):
        self.console.append(message)


class Guard:
    def __init__(self):
        self.last_stop = None

    def take_stop(self):
        stop, self.last_stop = self.last_stop, None
        return stop


class HomingPrinter:
    command_error = CommandError

    def __init__(self, guard=True, fail_recover=False):
        self.toolhead = Toolhead()
        self.gcode = Gcode(fail_recover)
        self.guard = Guard()
        self.objects = {"toolhead": self.toolhead, "gcode": self.gcode}
        if guard:
            self.objects["vo_homing_guard"] = self.guard

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def send_event(self, event, *args):
        pass

    def is_shutdown(self):
        return False


class HomingMove:
    """Each touch: start, end, then how it ended. An int is the distance the
    driver ended it after; "guard" and "error" end it without a trigger."""

    outcomes = []
    touches = []
    axis = 0

    def __init__(self, printer, endstops, toolhead=None):
        self.printer = printer

    def homing_move(self, movepos, speed, probe_pos=False):
        toolhead = self.printer.toolhead
        axis = HomingMove.axis
        start, end = toolhead.position[axis], movepos[axis]
        outcome = HomingMove.outcomes.pop(0)
        HomingMove.touches.append((start, end))
        if outcome in ("guard", "silent"):
            self.printer.guard.last_stop = (
                "Stopped homing %s because %s." % ("XY"[axis], outcome),
                outcome == "guard")
            raise CommandError("No trigger on %s after full movement"
                               % "xy"[axis])
        if outcome == "error":
            raise CommandError("Communication timeout during homing")
        # A touch whose move runs out before the frame: the driver sees the
        # motor slow down at the end of the move and ends it there.
        outcome = min(outcome, abs(end - start) - 2.)
        epos = list(toolhead.position)
        epos[axis] = start + math.copysign(outcome, end - start)
        return epos


class Endstopish:
    def home_zero(self):
        pass


# Max 4 axes: X homes to the right end at 392, Y to the front at -1.
AXES = {
    "x": dict(index=0, rail="stepper_x", range=(-2., 392.), endstop=392.,
              positive_dir=True),
    "y": dict(index=1, rail="stepper_y", range=(-5., 410.), endstop=-1.,
              positive_dir=False),
}


def home(source, outcomes, axis="x", guard=True, fail_recover=False):
    a = AXES[axis]
    module = load_source("qidi_homing", REPO_ROOT / source)
    module.HomingMove = HomingMove
    HomingMove.outcomes = list(outcomes)
    HomingMove.touches = []
    HomingMove.axis = a["index"]
    printer = HomingMove.printer = HomingPrinter(guard=guard,
                                                fail_recover=fail_recover)
    homing = module.Homing(printer)
    rail = types.SimpleNamespace(get_name=lambda: a["rail"],
                                 get_range=lambda: a["range"])
    hi = types.SimpleNamespace(positive_dir=a["positive_dir"],
                               retract_dist=20., retract_speed=500., speed=65.,
                               second_homing_speed=55., tolerance=1.,
                               retries=10)
    # QIDI's force position: 1.5 times the axis before the endstop.
    low, high = a["range"]
    forcepos = [None, None, None, None]
    movepos = [None, None, None, None]
    forcepos[a["index"]] = (a["endstop"] - 1.5 * (a["endstop"] - low)
                            if a["positive_dir"]
                            else a["endstop"] + 1.5 * (high - a["endstop"]))
    movepos[a["index"]] = a["endstop"]
    endstops = [(Endstopish(), axis)]
    try:
        homing.xy_home_second_genera([rail], forcepos, movepos, hi, endstops,
                                     endstops[0][0])
    finally:
        printer.touches = list(HomingMove.touches)
    return printer


class GuardedHomingTest(unittest.TestCase):
    def test_touches_drive_no_further_than_the_axis(self) -> None:
        for source in HOMING_SOURCES:
            with self.subTest(source=source):
                printer = home(source, [150, 20])
                lengths = [end - start for start, end in printer.touches]
                self.assertEqual(lengths, [400., 400.])

    def test_touch_ended_early_by_the_driver_is_homed_again(self) -> None:
        # Seen on a Max 4 with QIDI's homing: the driver ends the first touch
        # in the middle of the bed, and the next touch has to reach the frame
        # for the check to send it round again.
        for source in HOMING_SOURCES:
            with self.subTest(source=source):
                printer = home(source, [25, 369, 20, 20])
                self.assertEqual(len(printer.touches), 4)

    def test_stopped_touch_backs_off_and_homes_again(self) -> None:
        for source in HOMING_SOURCES:
            with self.subTest(source=source):
                printer = home(source, ["guard", 20, 20])
                self.assertEqual(len(printer.touches), 3)
                # Backed off 20 mm from the end of the axis, then touched again.
                self.assertEqual(printer.toolhead.moves[0], (392., 372.))
                self.assertEqual(
                    sum("SET_HOMING_MODE STEPPER=x VALUE=1" in script
                        for script in printer.gcode.scripts), 3)
                self.assertTrue(any("Backing off and homing X again" in line
                                    for line in printer.gcode.console))

    def test_silent_drivers_fail_homing_without_another_touch(self) -> None:
        for source in HOMING_SOURCES:
            with self.subTest(source=source):
                with self.assertRaisesRegex(CommandError, "because silent"):
                    home(source, [150, "silent"])
                self.assertEqual(len(HomingMove.touches), 2)

    def test_second_stop_fails_homing(self) -> None:
        for source in HOMING_SOURCES:
            with self.subTest(source=source):
                with self.assertRaisesRegex(CommandError, "happened twice"):
                    home(source, [150, "guard", "guard"])

    def test_y_stopped_twice_homes_x_then_y_again(self) -> None:
        # Seen on a Max 4: Y homing from the front left runs into the
        # filament cutter, and moving the toolhead to the right clears it.
        for source in HOMING_SOURCES:
            with self.subTest(source=source):
                printer = home(source, ["guard", "guard", 20, 20], axis="y")
                self.assertEqual(len(printer.touches), 4)
                # X homed once, after Y backed off from what stopped it.
                [(axes, at)] = printer.toolhead.x_homes
                self.assertEqual(axes, [0])
                self.assertEqual(at[1], 19.)
                # Y touched from the full axis length again, and X kept the
                # position homing X gave it.
                start, end = printer.touches[2]
                self.assertEqual(start - end, 421.)
                self.assertEqual(printer.toolhead.position[:2], [392., -1.])
                self.assertTrue(any("homing X" in line
                                    for line in printer.gcode.console))

    def test_homing_x_clears_y_only_once(self) -> None:
        cases = (
            (["guard", "guard", "guard"], "again after homing X"),
            (["guard", "guard", 20, "guard", "guard"], "happened twice"),
        )
        for source in HOMING_SOURCES:
            for outcomes, error in cases:
                with self.subTest(source=source, outcomes=outcomes):
                    with self.assertRaisesRegex(CommandError, error):
                        home(source, outcomes, axis="y")
                    self.assertEqual(len(HomingMove.touches), len(outcomes))

    def test_every_touch_leaves_the_drivers_in_working_mode(self) -> None:
        # QIDI's homing arms the drivers before each touch and sets them back
        # to working mode after it; a touch that raised skipped that, leaving
        # both on their homing current and stall detection armed.
        cases = (
            ("x", [150, 20]),
            ("x", ["error"]),
            ("x", [150, "error"]),
            ("x", ["guard", "error"]),
            ("x", [150, "silent"]),
            ("x", [150, "guard", "guard"]),
            ("y", ["guard", "guard", "guard"]),
        )
        for source in HOMING_SOURCES:
            for axis, outcomes in cases:
                with self.subTest(source=source, axis=axis, outcomes=outcomes):
                    try:
                        home(source, outcomes, axis=axis)
                    except CommandError:
                        pass
                    self.assertEqual(len(HomingMove.touches), len(outcomes))
                    # Each arm is followed by exactly one recover, whether the
                    # touch succeeded or raised.
                    scripts = HomingMove.printer.gcode.homing_scripts()
                    self.assertEqual(scripts,
                                     ["arm", "recover"] * len(outcomes))

    def test_recover_failure_keeps_the_touch_error(self) -> None:
        for source in HOMING_SOURCES:
            with self.subTest(source=source):
                with self.assertRaisesRegex(CommandError,
                                            "Communication timeout"), \
                        self.assertLogs(level="ERROR"):
                    home(source, ["error"], fail_recover=True)

    def test_other_homing_errors_are_unchanged(self) -> None:
        for source in HOMING_SOURCES:
            for guard in (True, False):
                with self.subTest(source=source, guard=guard):
                    with self.assertRaisesRegex(CommandError,
                                                "Communication timeout"):
                        home(source, ["error"], guard=guard)


if __name__ == "__main__":
    unittest.main()
