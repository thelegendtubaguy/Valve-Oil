from __future__ import annotations

import logging
import threading
import types
import unittest

from installer.runtime import diagnostics
from installer.tests.helpers import temp_path
from installer.tests.klipper_fakes import Reactor, load_extra

box_watch = load_extra("box_watch", "vo_box_watch")

FREQ = 84000000
BOX = "mcu_box1"
STATE_OID = 23
# Slot steppers 1A and 1B of Box 1, by their Klipper oids.
SLOT_1A, SLOT_1B = 8, 11


class Serial:
    """Klipper's serial reader: a reply goes to the handler registered for its name and oid, else to handle_default,
    which logs it as `got {...}`."""

    def __init__(self) -> None:
        self.handlers = {}
        self.lock = threading.Lock()
        self.logged = []

    def handle_default(self, params):
        self.logged.append(params)

    def receive(self, **params) -> None:
        with self.lock:
            handler = self.handlers.get((params["#name"], params.get("oid")), self.handle_default)
            handler(params)


class Mcu:
    def __init__(self, serial: Serial) -> None:
        self._serial = serial

    def get_name(self):
        return BOX

    def get_constants(self):
        return {"CLOCK_FREQ": FREQ}


class Stepper:
    def __init__(self, oid: int, mcu: Mcu) -> None:
        self.oid, self.mcu = oid, mcu
        self.position = 0
        self.newest = None  # the clock of Klipper's newest step in its step history
        self.homing_ends = 0

    def get_oid(self):
        return self.oid

    def get_mcu(self):
        return self.mcu

    def get_mcu_position(self):
        return self.position

    def dump_steps(self, count, start_clock, end_clock):
        if self.newest is None:
            return [], 0
        return [types.SimpleNamespace(last_clock=self.newest)], 1

    def note_homing_end(self):
        self.homing_ends += 1


class SlotJog:
    """vo_box_slot_jog: the test's `during` runs while the slot motor moves."""

    def __init__(self) -> None:
        self.during = lambda: None

    def _move(self, stepper, distance, speed, until=None, piece=None):
        self.during()
        return distance, False, True


class Printer:
    def __init__(self) -> None:
        self.reactor = Reactor()
        self.serial = Serial()
        self.qidi_states = []
        self.serial.handlers["MCB_STATE", STATE_OID] = self.qidi_states.append
        mcu = Mcu(self.serial)
        self.steppers = {oid: Stepper(oid, mcu) for oid in (SLOT_1A, SLOT_1B)}
        self.jog = SlotJog()
        self.print_state = "standby"
        self.objects = {
            "box_stepper slot0": types.SimpleNamespace(stepper=self.steppers[SLOT_1A]),
            "box_stepper slot1": types.SimpleNamespace(stepper=self.steppers[SLOT_1B]),
            "vo_box_slot_jog": self.jog,
            "print_stats": types.SimpleNamespace(get_status=lambda eventtime: {"state": self.print_state}),
        }
        self.events = {}

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def lookup_objects(self, module):
        return [(name, obj) for name, obj in self.objects.items() if name.split()[0] == module]

    def register_event_handler(self, event, callback):
        self.events[event] = callback


class BoxWatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.printer = Printer()
        self.watch = box_watch.load_config(types.SimpleNamespace(get_printer=lambda: self.printer))
        self.printer.events["klippy:ready"]()

    def reply(self, **params) -> None:
        """The Box sends a reply, and Klipper's reactor runs what it was handed."""
        self.printer.serial.receive(**params)
        callbacks, self.printer.reactor.callbacks = self.printer.reactor.callbacks, []
        for callback in callbacks:
            callback(self.printer.reactor.monotonic())

    def saved(self, oid: int, clock: int) -> None:
        self.reply(**{"#name": "last_step_clk", "oid": oid, "clk": clock})

    def put_back(self, clock: int) -> None:
        self.reply(**{"#name": "STEP-set_last_step", "now": clock + 100, "next_s": clock, "diff": 4294967196})

    def feed_starts(self) -> None:
        self.reply(**{"#name": "MCB_STATE", "oid": STATE_OID, "state": 2})

    def rearm(self, oid: int) -> None:
        self.reply(**{"#name": "MCB_CONFIG_STEPPER", "oid": STATE_OID, "stepper_oid": oid, "state": 0})

    def watch_lines(self, run) -> list[str]:
        with self.assertLogs(level="INFO") as logs:
            logging.info("start")
            run()
        return [record.getMessage() for record in logs.records if record.getMessage().startswith("vo_box_watch: ")]

    def test_a_clock_changed_behind_klipper_is_logged_once_and_diagnostics_reads_it(self) -> None:
        good = 4035537278
        late = (good + 22 * FREQ) % 2**32

        def print_start():
            self.saved(SLOT_1A, good)
            self.feed_starts()
            self.put_back(good)
            self.saved(SLOT_1A, good)
            self.feed_starts()
            self.rearm(SLOT_1A)  # print start re-arms auto-feed during that feed
            self.saved(SLOT_1A, late)
            self.feed_starts()
            self.put_back(late)
        lines = self.watch_lines(print_start)
        self.assertEqual(len(lines), 2, lines)
        self.assertRegex(lines[0], r"auto-feed re-armed during a feed: slot 1A \(Box 1\)")
        self.assertRegex(lines[1], r"step clock: slot 1A \(Box 1\) is 22\.000 s ahead of Klipper's")
        # Klipper's own `got {...}` lines are still written, and QIDI's state handler still runs.
        self.assertEqual(len(self.printer.serial.logged), 6)
        self.assertEqual([state["state"] for state in self.printer.qidi_states], [2, 2, 2])

        # The Diagnostics check reads the watcher's lines, and not QIDI's raw lines as well, in a log of that session.
        log = temp_path("vo-box-watch-") / "klippy.log"
        stamp = "2026-11-22 10:49:13 - "
        log.write_text("\n".join([
            stamp + "Start printer at Sun Nov 22 10:49:00 2026 (1795347216.0 17.3)",
            'variable_package_version = "26.11.22.2"',
            stamp + "vo_box_watch: watching Box 1 slots 1A, 1B",
            *(stamp + f"mcu '{BOX}': got {params}" for params in self.printer.serial.logged),
            *(stamp + line for line in lines),
        ]) + "\n")
        findings = {f["id"]: f for f in diagnostics.check([log])["findings"]}
        self.assertEqual(sorted(findings), ["autofeed_rearmed_during_feed", "box_step_clock_offset"])
        self.assertEqual((findings["box_step_clock_offset"]["severity"], findings["box_step_clock_offset"]["count"]),
                         ("repaired", 1))
        self.assertEqual(findings["box_step_clock_offset"]["details"], ["Slot 1A: 22.000 s ahead of Klipper's"])

    def test_clocks_klipper_changed_and_idle_re_arms_are_not_logged(self) -> None:
        stepper = self.printer.steppers[SLOT_1A]

        def klipper_moves_the_slot():
            self.saved(SLOT_1A, 1000)
            self.put_back(1000)
            self.rearm(SLOT_1A)  # print start with no feed running
            stepper.position += 400  # a load moves it
            self.saved(SLOT_1A, 5000)
            stepper.note_homing_end()  # a homing end resets its clock
            self.saved(SLOT_1A, 9000)
            stepper.newest = 12000  # an unload's pull-back ends where it began
            self.saved(SLOT_1A, 12000)
            self.put_back(12000)
            self.saved(SLOT_1B, 3)  # another slot's clock
        self.assertEqual(self.watch_lines(klipper_moves_the_slot), [])
        self.assertEqual(stepper.homing_ends, 1)

    def test_a_feed_of_the_slot_valve_oil_is_moving_is_logged(self) -> None:
        def jog(oid):
            def during():
                self.saved(oid, 1000)
                self.feed_starts()
                self.put_back(1000)
            self.printer.jog.during = during
            self.printer.jog._move(self.printer.steppers[SLOT_1A], 10., 20.)
        self.assertEqual(self.watch_lines(lambda: jog(SLOT_1B)), [])
        lines = self.watch_lines(lambda: jog(SLOT_1A))
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(all(line.startswith("vo_box_watch: Box feed during Valve Oil's slot move: slot 1A (Box 1)")
                            for line in lines))

    def test_replies_qidi_handles_never_reach_the_watcher_and_unhandled_ones_are_counted(self) -> None:
        qidi = []
        self.printer.serial.handlers["last_step_clk", SLOT_1A] = qidi.append

        def print_runs():
            self.printer.print_state = "printing"
            self.printer.reactor.timers[-1].callback(100.)
            self.saved(SLOT_1A, 1000)
            self.saved(SLOT_1A, 900000000)
            self.reply(**{"#name": "MCB_QUERY", "oid": STATE_OID})
            self.printer.print_state = "complete"
            self.printer.reactor.timers[-1].callback(105.)
        lines = self.watch_lines(print_runs)
        self.assertEqual(len(qidi), 2)
        self.assertEqual(lines, ["vo_box_watch: Box 1 replies no host module handled since the last print end: "
                                 "MCB_QUERY 1"])


if __name__ == "__main__":
    unittest.main()
