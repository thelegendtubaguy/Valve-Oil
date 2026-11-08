from __future__ import annotations

import importlib.util
import sys
import types
import unittest

from installer.tests.helpers import REPO_ROOT


class SerialError(Exception):
    pass


class CommandError(Exception):
    pass


def _load():
    """Import tltg_klipper_fixes with stand-ins for the Klipper modules it
    patches; the tests attach the classes they need."""
    saved = {name: sys.modules.get(name)
             for name in ("mcu", "reactor", "serialhdl", "toolhead", "webhooks", "extras",
                          "extras.heaters", "extras.stepper_enable", "extras.tmc")}
    for name in ("mcu", "reactor", "serialhdl", "toolhead", "webhooks"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["serialhdl"].error = SerialError
    sys.modules["webhooks"].DEEP_COMPARISON_MODULES = ["multi_color_controller"]
    sys.modules["webhooks"].SUBSCRIPTION_REFRESH_TIME = 1.
    package = types.ModuleType("extras")
    package.__path__ = []
    sys.modules["extras"] = package
    for name in ("heaters", "stepper_enable", "tmc"):
        module = types.ModuleType("extras." + name)
        sys.modules["extras." + name] = module
        setattr(package, name, module)
    sys.modules["extras.heaters"].MAX_HEAT_TIME = 5.
    sys.modules["extras.stepper_enable"].ENABLE_DELAY_TIME = .3
    try:
        spec = importlib.util.spec_from_file_location(
            "extras.tltg_klipper_fixes", REPO_ROOT / "installer/klipper/tltg/klipper_fixes.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


klipper_fixes = _load()


class Completion:
    def __init__(self):
        self.result = "pending"

    def complete(self, result):
        self.result = result

    def wait(self):
        return None if self.result == "pending" else self.result


class GuardTest(unittest.TestCase):
    def test_only_the_expected_original_is_replaced(self) -> None:
        class Target:
            def method(self):
                return "original"

        def fixed(self):
            return "fixed"

        label = "MCU_trsync.start"
        saved = klipper_fixes.ORIGINALS[label]
        self.addCleanup(klipper_fixes.ORIGINALS.__setitem__, label, saved)

        self.assertFalse(klipper_fixes._replace(Target, "method", label, fixed))
        self.assertEqual(Target().method(), "original")

        klipper_fixes.ORIGINALS[label] = klipper_fixes.source_hash(Target.method)
        self.assertTrue(klipper_fixes._replace(Target, "method", label, fixed))
        self.assertTrue(klipper_fixes._replace(Target, "method", label, fixed))
        self.assertEqual(Target().method(), "fixed")


class LostMcuTest(unittest.TestCase):
    class Serial:
        def __init__(self):
            self.last_notify_id = 0
            self.pending_notifications = {}
            self.serialqueue = object()
            self.reactor = types.SimpleNamespace(completion=Completion)
            self.ffi_lib = types.SimpleNamespace(serialqueue_send=lambda *args: None)

        def _error(self, msg):
            raise SerialError(msg)

    def test_waits_fail_once_communication_is_lost(self) -> None:
        serial = self.Serial()
        waiting = Completion()
        serial.pending_notifications[1] = waiting

        klipper_fixes._abort_wait_acks(serial)

        self.assertIsNone(waiting.result)
        with self.assertRaisesRegex(SerialError, "No communication with MCU"):
            klipper_fixes._raw_send_wait_ack(serial, b"", 0, 0, None)

    def test_wait_failure_is_a_command_error(self) -> None:
        serial = self.Serial()
        klipper_fixes._abort_wait_acks(serial)
        wrapper = types.SimpleNamespace(
            _cmd=types.SimpleNamespace(encode=lambda data: b""),
            _serial=types.SimpleNamespace(raw_send_wait_ack=lambda *args: klipper_fixes._raw_send_wait_ack(serial, *args)),
            _cmd_queue=None)
        saved = klipper_fixes._COMMAND_ERROR[0]
        klipper_fixes._COMMAND_ERROR[0] = CommandError
        self.addCleanup(klipper_fixes._COMMAND_ERROR.__setitem__, 0, saved)

        with self.assertRaisesRegex(CommandError, "No communication with MCU"):
            klipper_fixes._send_wait_ack(wrapper)


class HeaterTest(unittest.TestCase):
    class Heater:
        def __init__(self):
            self.target_temp = 200.
            self.is_shutdown = False
            self.next_pwm_time = 0.
            self.last_pwm_value = 0.
            self.pwm_delay = .1
            self.sent = []
            self.mcu_pwm = types.SimpleNamespace(
                set_pwm=lambda time, value: self.sent.append(value),
                get_mcu=lambda: types.SimpleNamespace(estimated_print_time=lambda eventtime: eventtime))

    def test_output_stops_when_the_main_loop_stops(self) -> None:
        heater = self.Heater()
        heater._tltg_mainthread_time = 105.

        klipper_fixes._heater_set_pwm(heater, 100., 1.)
        klipper_fixes._heater_set_pwm(heater, 106., 1.)

        self.assertEqual(heater.sent, [1., 0.])

    def test_untracked_heater_keeps_qidi_behavior(self) -> None:
        heater = self.Heater()

        klipper_fixes._heater_set_pwm(heater, 1000., 1.)

        self.assertEqual(heater.sent, [1.])

    def test_temperature_reads_zero_only_after_seven_seconds(self) -> None:
        heater = self.Heater()
        class Lock:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False
        heater.lock = Lock()
        heater.smoothed_temp = 210.
        heater.last_temp_time = 100.

        self.assertEqual(klipper_fixes._heater_get_temp(heater, 106.)[0], 210.)
        self.assertEqual(klipper_fixes._heater_get_temp(heater, 107.5)[0], 0.)


class StepperEnableTest(unittest.TestCase):
    """QIDI enables a stepper 0.3 s before its first step, which must still
    be ahead of the MCU when the enable is sent."""

    FREQ = 1000000

    class Pin:
        """Klipper's MCU_digital_out as stepper_enable uses it."""

        def __init__(self, mcu):
            self.mcu = mcu
            self._last_clock = 0
            self.sent = []

        def get_mcu(self):
            return self.mcu

        def set_digital(self, print_time, value):
            self._last_clock = int(print_time * StepperEnableTest.FREQ)
            self.sent.append((print_time, value))

    class EnablePin:
        """QIDI's StepperEnablePin: a pin shared by several steppers, or none."""

        def __init__(self, mcu_enable, enable_count=0):
            self.mcu_enable = mcu_enable
            self.enable_count = enable_count

        def set_enable(self, print_time):
            if not self.enable_count:
                self.mcu_enable.set_digital(print_time, 1)
            self.enable_count += 1

        def set_disable(self, print_time):
            self.enable_count -= 1
            if not self.enable_count:
                self.mcu_enable.set_digital(print_time, 0)

    def setUp(self) -> None:
        now = self.now = [10.]
        reactor = types.SimpleNamespace(monotonic=lambda: now[0])
        self.mcu = types.SimpleNamespace(
            get_printer=lambda: types.SimpleNamespace(get_reactor=lambda: reactor),
            estimated_print_time=lambda eventtime: eventtime,
            clock_to_print_time=lambda clock: clock / self.FREQ)

    def tracking(self, enable):
        stepper = types.SimpleNamespace(get_mcu=lambda: self.mcu, get_name=lambda: "stepper_y")
        callbacks = []
        tracking = types.SimpleNamespace(stepper=stepper, enable=enable, is_enabled=False,
                                         callbacks=[lambda time, on: callbacks.append(time)])
        return tracking, callbacks

    def test_enable_is_never_scheduled_in_the_past(self) -> None:
        # (first step, pin's previous change, expected enable), with the MCU at 10 s.
        cases = (
            (11., None, .7),             # 0.3 s early is still ahead: QIDI's time
            (10.16, None, .02),          # first homing move after a disable: 20 ms ahead
            (10.3, 10.1, .12),           # after the disable still pending on the pin
            (10.01, None, .01),          # never after the step itself
        )
        for print_time, last_change, expected in cases:
            with self.subTest(print_time=print_time, last_change=last_change):
                pin = self.Pin(self.mcu)
                if last_change is not None:
                    pin.set_digital(last_change, 0)
                tracking, callbacks = self.tracking(self.EnablePin(pin))

                with self.assertLogs(level="INFO") as logs:
                    klipper_fixes._motor_enable(tracking, print_time)

                self.assertAlmostEqual(pin.sent[-1][0], 10. + expected)
                self.assertEqual(pin.sent[-1][1], 1)
                self.assertAlmostEqual(callbacks[0], pin.sent[-1][0])
                self.assertTrue(tracking.is_enabled)
                self.assertIn("ENABLE delay: stepper=stepper_y enable=%.6f move=%.6f"
                              % (10. + expected, print_time), logs.output[0])

    def test_steppers_without_their_own_pin_still_enable(self) -> None:
        # No enable pin, and a shared pin another stepper already enabled.
        pin = self.Pin(self.mcu)
        for enable in (self.EnablePin(None, 9999), self.EnablePin(pin, 1)):
            with self.subTest(enable=enable):
                tracking, callbacks = self.tracking(enable)

                with self.assertLogs(level="INFO"):
                    klipper_fixes._motor_enable(tracking, 10.16)

                self.assertAlmostEqual(callbacks[0], 10.02)
                self.assertTrue(tracking.is_enabled)
        self.assertEqual(pin.sent, [])


class TimerTest(unittest.TestCase):
    def test_running_timer_is_not_rescheduled(self) -> None:
        reactor = types.SimpleNamespace(_next_timer=50.)
        timer = types.SimpleNamespace(waketime=10., timer_is_running=True)

        klipper_fixes._update_timer(reactor, timer, 0.)
        self.assertEqual(timer.waketime, 10.)

        timer.timer_is_running = False
        klipper_fixes._update_timer(reactor, timer, 0.)
        self.assertEqual((timer.waketime, reactor._next_timer), (0., 0.))

    def test_scan_runs_due_timers_and_finds_the_next_wake(self) -> None:
        never = 1e99
        reactor = types.SimpleNamespace(NEVER=never, _next_timer=0., _g_dispatch=None)
        later = types.SimpleNamespace(waketime=20.)
        idle = types.SimpleNamespace(waketime=never)

        def run_due(eventtime):
            klipper_fixes._update_timer(reactor, idle, 15.)
            return 30.
        due = types.SimpleNamespace(waketime=5., callback=run_due)
        reactor._timers = [due, later, idle]

        klipper_fixes._check_timers(reactor, 10., False)

        self.assertEqual((due.waketime, idle.waketime, reactor._next_timer), (30., 15., 15.))


class TimeSliceTest(unittest.TestCase):
    """Streamed moves let due timers run every 20 ms; homing doesn't."""

    def setUp(self) -> None:
        class Reactor:
            NOW = 0.

            def __init__(self):
                self.clock = 100.
                self.pauses = []

            def monotonic(self):
                return self.clock

            def pause(self, waketime):
                self.pauses.append(self.clock)
                return self.clock

        class ToolHead:
            def __init__(self, reactor):
                self.reactor = reactor
                self.special_queuing_state = ""
                self.can_pause = True
                self.max_velocity = 565.
                self.kin = types.SimpleNamespace(max_z_velocity=10.)
                self.commanded_pos = [0., 0., 3., 0.]
                self.moves = []

            def _do_actual_move(self, newpos, speed):
                self.moves.append(list(newpos))
                self.commanded_pos[:] = newpos
                self.reactor.clock += .008

        def check_timers(self, eventtime, busy):
            pass
        setattr(check_timers, klipper_fixes.MARKER, True)
        saved = (getattr(klipper_fixes.toolhead, "ToolHead", None),
                 getattr(klipper_fixes.reactor, "SelectReactor", None),
                 klipper_fixes.ORIGINALS["ToolHead._do_actual_move"])
        def restore():
            klipper_fixes.toolhead.ToolHead, klipper_fixes.reactor.SelectReactor = saved[:2]
            klipper_fixes.ORIGINALS["ToolHead._do_actual_move"] = saved[2]
        self.addCleanup(restore)
        klipper_fixes.toolhead.ToolHead = ToolHead
        klipper_fixes.reactor.SelectReactor = type("SelectReactor", (), {"_check_timers": check_timers})
        klipper_fixes.ORIGINALS["ToolHead._do_actual_move"] = klipper_fixes.source_hash(
            ToolHead._do_actual_move)
        self.assertTrue(klipper_fixes._fix_move_slices())
        self.reactor = Reactor()
        saved_clock = klipper_fixes._clock
        self.addCleanup(setattr, klipper_fixes, "_clock", saved_clock)
        klipper_fixes._clock = self.reactor.monotonic
        # The timer scan starts a pass now.
        klipper_fixes._slice_start[0] = self.reactor.clock
        self.toolhead = ToolHead(self.reactor)

    def test_moves_yield_once_the_loop_has_worked_a_slice(self) -> None:
        for i in range(6):
            self.toolhead._do_actual_move([i, 0., 0., 0.], 100.)

        self.assertEqual(len(self.toolhead.moves), 6)
        # 8 ms a move: yields after the 3rd move (24 ms) and the 6th.
        self.assertEqual([round(t - 100., 3) for t in self.reactor.pauses], [.024, .048])

    def test_homing_moves_keep_their_timing(self) -> None:
        self.toolhead.special_queuing_state = "Drip"
        for i in range(6):
            self.toolhead._do_actual_move([i, 0., 0., 0.], 5.)
        self.toolhead._do_actual_move([5., 0., 200., 0.], 10.)

        self.assertEqual(self.reactor.pauses, [])
        self.assertEqual(len(self.toolhead.moves), 7)

    def test_long_travel_is_queued_in_short_pieces_along_the_same_line(self) -> None:
        # The end G-code's bed drop: G1 Z181 F600 from Z3, capped at 10 mm/s.
        self.toolhead._do_actual_move([0., 0., 181., 0.], 100.)

        moves = self.toolhead.moves
        self.assertEqual(len(moves), 36)
        self.assertEqual(moves[-1], [0., 0., 181., 0.])
        steps = [b[2] - a[2] for a, b in zip([[0., 0., 3., 0.]] + moves, moves)]
        self.assertTrue(all(abs(step - 178. / 36) < 1e-9 for step in steps))
        self.assertTrue(all(m[0] == 0. and m[1] == 0. and m[3] == 0. for m in moves))
        self.assertTrue(self.reactor.pauses)

    def test_extruding_and_short_moves_are_queued_whole(self) -> None:
        self.toolhead._do_actual_move([300., 0., 3., 50.], 5.)
        self.toolhead._do_actual_move([300., 200., 3., 50.], 500.)

        self.assertEqual(self.toolhead.moves, [[300., 0., 3., 50.], [300., 200., 3., 50.]])


class StatusEncodingTest(unittest.TestCase):
    """Updates msgspec can't encode still reach Moonraker instead of
    shutting Klipper down."""

    def test_updates_msgspec_rejects_go_out_as_json(self) -> None:
        def reject(data):
            raise TypeError("dict keys must be strings")
        saved = klipper_fixes._msgspec_encode
        self.addCleanup(setattr, klipper_fixes, "_msgspec_encode", saved)
        klipper_fixes._msgspec_encode = reject
        connection = types.SimpleNamespace(send_buffer=b"", is_blocking=True)

        klipper_fixes._client_send(connection, {None: 1, "temperature": 21.5})

        self.assertEqual(connection.send_buffer, b'{"null":1,"temperature":21.5}\x03')


class StatusUpdateTest(unittest.TestCase):
    """Moonraker's once-a-second update carries only what changed."""

    class Helper:
        def __init__(self, objects):
            self.printer = types.SimpleNamespace(lookup_object=lambda name, default=None: objects.get(name, default))
            self.clients = {}
            self.pending_queries = []
            self.last_query = {}

        def _get_diff_recursive(self, now, last):
            # QIDI's nested comparison for multi_color_controller.
            if not isinstance(now, dict) or not isinstance(last, dict):
                return now if now != last else None
            diff = {}
            for k, v in now.items():
                if k not in last:
                    diff[k] = v
                else:
                    res = self._get_diff_recursive(v, last[k])
                    if res is not None:
                        diff[k] = res
            return diff if diff else None

    def test_updates_carry_only_changes(self) -> None:
        macro = {"run": False}
        state = {"sensor": {"temperature": 20.}, "box": {"slot0": "PLA", "temp": 28.7}}
        objects = {
            "gcode_macro M": types.SimpleNamespace(get_status=lambda e: macro),
            "temperature_sensor s": types.SimpleNamespace(get_status=lambda e: dict(state["sensor"])),
            "multi_color_controller": types.SimpleNamespace(
                get_status=lambda e: {"box": dict(state["box"]), "task": {}}),
        }
        helper = self.Helper(objects)
        sent = []
        class Connection:
            def is_closed(self):
                return False
        conn = Connection()
        helper.clients[conn] = (conn, {name: None for name in objects}, sent.append, {})

        klipper_fixes._do_query(helper, 1.)
        klipper_fixes._do_query(helper, 2.)
        state["sensor"]["temperature"] = 21.
        state["box"]["temp"] = 28.8
        klipper_fixes._do_query(helper, 3.)

        updates = [message["params"]["status"] for message in sent]
        self.assertEqual(updates, [
            {"gcode_macro M": {"run": False}, "temperature_sensor s": {"temperature": 20.},
             "multi_color_controller": {"box": {"slot0": "PLA", "temp": 28.7}, "task": {}}},
            {"temperature_sensor s": {"temperature": 21.},
             "multi_color_controller": {"box": {"temp": 28.8}}},
        ])

    def test_box_temperature_wobble_does_not_resend_the_boxes(self) -> None:
        boxes = [{"aht20_temp": 26.33, "slots": ["PLA"]}, {"aht20_temp": 0, "slots": []}]
        objects = {"box_extras": types.SimpleNamespace(
            get_status=lambda e: {"boxes": [dict(box) for box in boxes]})}
        helper = self.Helper(objects)
        sent = []
        class Connection:
            def is_closed(self):
                return False
        conn = Connection()
        helper.clients[conn] = (conn, {"box_extras": None}, sent.append, {})

        def update(temp, slots=None):
            boxes[0]["aht20_temp"] = temp
            if slots is not None:
                boxes[0]["slots"] = slots
            klipper_fixes._do_query(helper, 1.)
            return sent[-1]["params"]["status"].get("box_extras") if sent else None

        first = update(26.33)
        wobbles = [update(t) for t in (26.35, 26.31, 26.40)]
        moved = update(26.43)
        slot_change = update(26.46, slots=["PETG"])

        self.assertEqual(first["boxes"][0]["aht20_temp"], 26.33)
        self.assertEqual(len(sent), 3)
        self.assertEqual(wobbles, [first] * 3)
        self.assertEqual(moved["boxes"][0]["aht20_temp"], 26.43)
        self.assertEqual(slot_change["boxes"][0], {"aht20_temp": 26.43, "slots": ["PETG"]})


if __name__ == "__main__":
    unittest.main()
