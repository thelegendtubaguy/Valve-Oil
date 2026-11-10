from __future__ import annotations

import importlib.util
import unittest

from installer.tests.helpers import REPO_ROOT


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


step_loss_monitor = _load("tltg_step_loss_monitor",
                          "installer/klipper/tltg/step_loss_monitor.py")
printer_warnings = _load("tltg_printer_warnings",
                         "installer/klipper/tltg/printer_warnings.py")

NEVER = 9999999999999999.
ADDRS = {"x": 1, "y": 3}


def reply(addr, value):
    frame = [0xfb, addr, 0x39] + list(value.to_bytes(4, "big", signed=True))
    return bytes(frame + [sum(frame) & 0xff])


class Timer:
    def __init__(self, callback, waketime):
        self.callback, self.waketime = callback, waketime


class Reactor:
    NOW, NEVER = 0., NEVER

    def __init__(self):
        self.now = 100.
        self.timers = []
        self.callbacks = []
        self.on_pause = None

    def monotonic(self):
        return self.now

    def register_timer(self, callback, waketime=NEVER):
        timer = Timer(callback, waketime)
        self.timers.append(timer)
        return timer

    def update_timer(self, timer, waketime):
        timer.waketime = waketime

    def register_callback(self, callback):
        self.callbacks.append(callback)

    def pause(self, waketime):
        self.on_pause(waketime)
        return self.now


class ClInterface:
    """QIDI's cl_interface queue: one request on the bus at a time."""

    def __init__(self):
        self.name2addr = dict(ADDRS)
        self.sent = []

    def send_command(self, addr, msg, expects_response=True, timeout=0.5):
        self.sent.append(bytes(msg))

    def is_any_upgrading(self):
        return False


class Status:
    def __init__(self, **status):
        self.status = status

    def get_status(self, eventtime=None):
        return self.status


class Stepper:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name

    def get_rotation_distance(self):
        return 39., 200


class CoreXYKinematics:
    def get_steppers(self):
        return [Stepper("stepper_x"), Stepper("stepper_y"), Stepper("stepper_z")]


class Toolhead(Status):
    def __init__(self, **status):
        super().__init__(**status)
        self.position = [100., 100., 5., 0.]

    def get_kinematics(self):
        return CoreXYKinematics()

    def wait_moves(self):
        pass

    def get_position(self):
        return list(self.position)

    def set_position(self, newpos, homing_axes=()):
        self.position = list(newpos)


class TrapQ:
    def __init__(self):
        self.velocity = 0.

    def get_trapq_position(self, print_time):
        return (0., 0., 0.), self.velocity


class CommandError(Exception):
    pass


class GCmd:
    error = CommandError

    def __init__(self, gcode):
        self.gcode = gcode

    def respond_info(self, message):
        self.gcode.console.append(message)


class Gcode:
    def __init__(self):
        self.commands = {"RESET_MOTOR_POSITION_ERROR": self._reset}
        self.gcode_help = {}
        self.console = []
        self.scripts = []
        self.resets = 0
        # The macros a test runs, by name; the rest are only recorded.
        self.macros = {}

    def _reset(self, gcmd):
        self.resets += 1

    def register_command(self, name, handler, desc=None):
        old = self.commands.pop(name, None)
        if handler is not None:
            self.commands[name] = handler
        return old

    def respond_raw(self, message):
        self.console.append(message)

    def run_script(self, script):
        self.scripts.append(script)
        if script in self.macros:
            self.macros[script]()

    def run(self, command):
        self.commands[command](GCmd(self))


class HomingState:
    def __init__(self, axes):
        self.axes = axes

    def get_axes(self):
        return self.axes


class SdCard:
    def __init__(self, print_stats):
        self.print_stats = print_stats
        self.cancelled = False

    def is_active(self):
        return not self.cancelled

    def do_cancel(self):
        self.cancelled = True
        self.print_stats.status["state"] = "cancelled"


class Printer:
    def __init__(self, closed_loop=True, enabled=True, recovery=False):
        self.reactor = Reactor()
        self.reactor.on_pause = self._advance
        self.errors = None
        self.answering = True
        self.gcode = Gcode()
        self.cl = ClInterface()
        self.trapq = TrapQ()
        self.toolhead = Toolhead(homed_axes="xyz")
        self.print_stats = Status(state="printing")
        self.status_manager = Status(main_status="printing")
        self.sdcard = SdCard(self.print_stats)
        self.handlers = {}
        self.objects = {
            "gcode": self.gcode,
            "toolhead": self.toolhead,
            "motion_report": Status(trapqs={"toolhead": self.trapq}),
            "mcu": Status(estimated_print_time=lambda t: t),
            "print_stats": self.print_stats,
            "print_stats_manager": self.status_manager,
            "virtual_sdcard": self.sdcard,
            "save_variables": Status(variables={
                "tltg_step_loss_monitor": 1 if enabled else 0,
                "tltg_step_loss_recovery": 1 if recovery else 0}),
        }
        if closed_loop:
            self.objects["cl_interface"] = self.cl
        for obj in self.objects["motion_report"], self.objects["mcu"]:
            obj.__dict__.update(obj.status)
        self.monitor = step_loss_monitor.load_config(self)
        self.objects["tltg_step_loss_monitor"] = self.monitor
        self.warnings = printer_warnings.PrinterWarnings(self)
        self.send_event("klippy:ready")

    # Config and printer interface used by the modules.
    def get_printer(self):
        return self

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return {}

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def register_event_handler(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def send_event(self, event, *args):
        for handler in self.handlers.get(event, []):
            handler(*args)

    def is_shutdown(self):
        return False

    # Test drivers.
    def read(self, errors):
        """Run the poll timer once and answer its query with errors[motor]."""
        self.errors = errors
        timer, = self.reactor.timers
        self.reactor.now = max(self.reactor.now, timer.waketime)
        before = len(self.cl.sent)
        timer.waketime = timer.callback(self.reactor.now)
        if len(self.cl.sent) == before or not self.answering:
            return None
        addr = self.cl.sent[-1][1]
        name = "x" if addr == ADDRS["x"] else "y"
        self.reactor.now += 0.06
        self.send_event("cl:" + name, reply(addr, errors[name]))
        while self.reactor.callbacks:
            self.reactor.callbacks.pop(0)(self.reactor.now)
        return name

    def reads(self, errors, count):
        for _ in range(count):
            self.read(errors)

    def _advance(self, waketime):
        """A command waiting: the motors keep answering with the last errors."""
        timer, = self.reactor.timers
        self.reactor.now = max(self.reactor.now, waketime)
        if timer.waketime <= self.reactor.now:
            self.read(self.errors)

    def home(self, axes=(0, 1)):
        state = HomingState(list(axes))
        self.send_event("homing:home_rails_begin", state, [])
        self.send_event("homing:home_rails_end", state, [])

    def banner(self):
        return [w["id"] for w in self.warnings.get_status(1.)["warnings"]]


class StepLossMonitorTest(unittest.TestCase):
    def assert_only_position_error_queries(self, printer):
        self.assertTrue(printer.cl.sent)
        for frame in printer.cl.sent:
            self.assertIn(frame, (bytes([0xfa, 1, 0x39, 0x34]),
                                  bytes([0xfa, 3, 0x39, 0x36])))

    def test_slip_during_print_pauses_and_warns_until_rehomed(self) -> None:
        printer = Printer()
        rest = {"x": 1024, "y": 3074}
        printer.reads(rest, 4)
        # Motion swings well under a cycle, as at 300 mm/s on the test printer.
        printer.trapq.velocity = 300.
        printer.reads({"x": 1024 + 150, "y": 3074 - 140}, 6)
        self.assertEqual((printer.gcode.scripts, printer.banner()), ([], []))

        # The X motor loses 14 cycles against an obstacle.
        printer.reads({"x": 1024 + 14 * 1024 + 9, "y": 3074}, 4)
        self.assertEqual(printer.gcode.scripts, ["PAUSE"])
        message, = printer.gcode.console
        self.assertIn("stepper_x", message)
        self.assertIn("10.9 mm", message)
        self.assertIn("paused", message)
        self.assertEqual(printer.banner(), ["xy_step_loss"])

        # Further slips each print a line; the banner shows the net slip.
        printer.reads({"x": 1024 + 16 * 1024, "y": 3074}, 4)
        self.assertEqual(len(printer.gcode.console), 2)
        self.assertIn("2 step cycles", printer.gcode.console[1])
        self.assertIn("stepper_x 16 step cycles (12.5 mm of belt)",
                      printer.warnings.get_status(1.)["warnings"][0]["message"])

        # Homing Y alone leaves the warning; homing X and Y clears it.
        printer.home(axes=(1,))
        self.assertEqual(printer.banner(), ["xy_step_loss"])
        printer.home(axes=(0,))
        self.assertEqual(printer.banner(), [])
        self.assert_only_position_error_queries(printer)

    def test_single_spike_and_homing_offsets_are_not_slips(self) -> None:
        printer = Printer()
        printer.reads({"x": 7, "y": 2078}, 4)
        printer.reads({"x": 7 + 900, "y": 2078}, 1)
        printer.reads({"x": 7, "y": 2078}, 4)
        # Each home leaves a new whole-cycle offset; so does QIDI's reset.
        printer.home()
        printer.reads({"x": 2047, "y": 4095}, 6)
        printer.gcode.commands["RESET_MOTOR_POSITION_ERROR"](None)
        self.assertEqual(printer.gcode.resets, 1)
        printer.reads({"x": -2, "y": 11}, 6)
        self.assertEqual((printer.gcode.scripts, printer.gcode.console), ([], []))

    def test_slip_outside_a_print_warns_without_pausing(self) -> None:
        printer = Printer()
        printer.print_stats.status["state"] = "standby"
        printer.reads({"x": 0, "y": 0}, 4)
        printer.reads({"x": 0, "y": -2 * 1024}, 4)
        self.assertEqual(printer.gcode.scripts, [])
        self.assertIn("stepper_y", printer.gcode.console[0])
        self.assertEqual(printer.banner(), ["xy_step_loss"])

    def test_recovery_corrects_the_position_once_per_print(self) -> None:
        printer = Printer(recovery=True)
        printer.gcode.macros["_TLTG_STEP_LOSS_RECOVER"] = \
            lambda: printer.gcode.run("TLTG_STEP_LOSS_CORRECT")
        printer.reads({"x": 1024, "y": 3074}, 4)
        # stepper_x loses 14 cycles, 10.92 mm of belt: on a CoreXY the
        # toolhead is 5.46 mm short in both X and Y.
        printer.reads({"x": 15 * 1024 + 9, "y": 3074}, 4)
        self.assertEqual(printer.gcode.scripts, ["_TLTG_STEP_LOSS_RECOVER"])
        self.assertIn("Correcting", printer.gcode.console[0])
        x, y, z, e = printer.toolhead.position
        self.assertAlmostEqual(x, 100. - 5.46)
        self.assertAlmostEqual(y, 100. - 5.46)
        self.assertEqual((z, e), (5., 0.))
        self.assertEqual(printer.banner(), [])

        # A second slip in the same print pauses it and keeps the warning.
        printer.reads({"x": 15 * 1024 + 9, "y": 3074 - 2 * 1024}, 4)
        self.assertEqual(printer.gcode.scripts[1:], ["PAUSE"])
        self.assertIn("paused", printer.gcode.console[-1])
        self.assertEqual(printer.banner(), ["xy_step_loss"])

        # The next print gets its own recovery.
        printer.print_stats.status["state"] = "complete"
        printer.reads({"x": 0, "y": 0}, 1)
        printer.home()
        printer.print_stats.status["state"] = "printing"
        printer.reads({"x": 0, "y": 0}, 4)
        printer.reads({"x": 0, "y": 3 * 1024}, 4)
        self.assertEqual(printer.gcode.scripts[2:], ["_TLTG_STEP_LOSS_RECOVER"])

    def test_correction_without_fresh_reads_leaves_position_and_pauses(
            self) -> None:
        printer = Printer(recovery=True)

        def recover():
            # The motors stop answering once the toolhead stops.
            printer.answering = False
            printer.gcode.run("TLTG_STEP_LOSS_CORRECT")
        printer.gcode.macros["_TLTG_STEP_LOSS_RECOVER"] = recover
        printer.reads({"x": 0, "y": 0}, 4)
        printer.reads({"x": 7 * 1024, "y": 0}, 4)
        self.assertEqual(printer.gcode.scripts, [
            "_TLTG_STEP_LOSS_RECOVER",
            "RESTORE_GCODE_STATE NAME=_tltg_step_loss_recover", "PAUSE"])
        self.assertEqual(printer.toolhead.position, [100., 100., 5., 0.])
        self.assertEqual(printer.banner(), ["xy_step_loss"])

    def test_slip_during_print_start_cancels_at_once(self) -> None:
        for recovery in (False, True):
            printer = Printer(recovery=recovery)
            printer.status_manager.status["main_status"] = "print_start"
            printer.reads({"x": 0, "y": 0}, 4)
            printer.reads({"x": 7 * 1024, "y": 0}, 4)
            self.assertTrue(printer.sdcard.cancelled)
            self.assertEqual(printer.gcode.scripts[-1], "CANCEL_PRINT")
            self.assertNotIn("_TLTG_STEP_LOSS_RECOVER", printer.gcode.scripts)
            self.assertIn("cancelled", printer.gcode.console[0])

    def test_inert_when_disabled_unhomed_or_without_closed_loop(self) -> None:
        disabled = Printer(enabled=False)
        disabled.reads({"x": 0, "y": 0}, 4)
        unhomed = Printer()
        unhomed.toolhead.status["homed_axes"] = "z"
        unhomed.reads({"x": 0, "y": 0}, 4)
        self.assertEqual((disabled.cl.sent, unhomed.cl.sent), ([], []))
        open_loop = Printer(closed_loop=False)
        self.assertEqual(open_loop.reactor.timers, [])


if __name__ == "__main__":
    unittest.main()
