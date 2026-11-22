"""What the Klipper module tests share: loading a module as Klipper would, and the Klipper stand-ins that are the
same in every test. Printers, toolheads, and G-code commands differ by what each module reads, so they stay with
their tests."""
from __future__ import annotations

from installer.tests.helpers import REPO_ROOT, load_source

EXTRAS_ROOT = REPO_ROOT / "installer/klipper/extras"
NEVER = 9999999999999999.


def load_extra(name: str, module_name: str | None = None, *, stand_ins: dict | None = None):
    """Import Valve Oil's Klipper module installer/klipper/extras/<name>.py, as vo_<name> unless named."""
    return load_source(module_name or f"vo_{name}", EXTRAS_ROOT / f"{name}.py", stand_ins=stand_ins)


class CommandError(Exception):
    """Klipper's gcode.CommandError."""


class Timer:
    def __init__(self, callback, waketime):
        self.callback, self.waketime = callback, waketime


class Reactor:
    """Klipper's reactor with a clock the test moves: timers and callbacks are kept for the test to run."""

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

    def register_async_callback(self, callback):
        self.callbacks.append(callback)

    def pause(self, waketime):
        self.on_pause(waketime)
        return self.now


# A Box auto-feed's feed, from its first step to its last (18 mm at 40 mm/s), and the wait after its last step, or
# after an abort, before the Box puts the slot's step clock back, in seconds.
FEED_TIME = .5
FEED_RESTORE = .01


class _Command:
    """A command box_autofeed keeps for a Box MCU: send() runs it on the Box."""

    def __init__(self, run):
        self.run = run

    def send(self, data=(), minclock=0, reqclock=0):
        self.run(list(data))


class _FeedDevice:
    """One USB Box's auto-feed as Box firmware 02.03.01.21 runs it (states 0 idle, 1 armed, 2 feeding, 3 the 10 ms
    after a feed, 5 aborted), stepping whichever slot it is set up on, on the Box's own clock."""

    def __init__(self, autofeed, oid):
        self.autofeed, self.oid = autofeed, oid
        self.state, self.slot, self.limit = 0, None, 0
        self.saved, self.due = None, None
        self.record = {"oid": oid, "cmd_mcb_abort": _Command(lambda data: self.abort()),
                       "cmd_set_limit_a": _Command(lambda data: self.set_limit(data[1]))}

    def advance(self):
        now = self.autofeed.reactor.monotonic()
        while self.due is not None and self.due <= now:
            when, self.due = self.due, None
            if self.state == 2:
                self.state, self.due = 3, when + FEED_RESTORE
            elif self.state in (3, 5):
                self.autofeed.offsets[self.slot] = self.saved
                self.state = 1 if self.state == 3 else 0
                self._feed(when)

    def _feed(self, when):
        # Armed with the buffer signal set, the Box saves the slot's step clock and steps it on its own.
        if self.state == 1 and self.limit and self.slot is not None:
            self.saved = self.autofeed.offsets.get(self.slot, 0.)
            self.autofeed.offsets[self.slot] = self.saved + FEED_TIME
            self.state, self.due = 2, when + FEED_TIME
            self.autofeed.feeds += 1

    def set_limit(self, value):
        self.advance()
        self.limit = value
        self._feed(self.autofeed.reactor.monotonic())

    def configure(self, slot):
        # mcb_config_stepper: idle on the new slot, with nothing put back.
        self.advance()
        self.state, self.slot, self.due = 0, slot, None

    def arm(self):
        self.advance()
        self.state, self.due = 1, None
        self._feed(self.autofeed.reactor.monotonic())

    def abort(self):
        self.advance()
        self.autofeed.aborts += 1
        if self.state == 2:
            self.state, self.due = 5, self.autofeed.reactor.monotonic() + FEED_RESTORE
        elif self.state == 1:
            self.state, self.due = 0, None


class BoxAutoFeed:
    """QIDI's box_autofeed with one Box auto-feed per Box MCU, as the harness ran box_autofeed.so on the Max 4
    01.01.07.01 and the Plus 5 and Smart 5 01.01.02.01: auto_start sets a slot up and arms it with the buffer signal
    QIDI last read, auto_abort aborts only while it names an armed slot, and the buffer switch is relayed to the Box
    last set up. offsets has how far each slot's step clock on its Box is ahead of Klipper's, in seconds."""

    def __init__(self, reactor, box_steppers):
        self.reactor, self.box_steppers = reactor, dict(box_steppers)
        self.offsets, self.feeds, self.aborts, self.arms = {}, 0, 0, []
        self.box_shut_down = False
        self.bind_stepper, self.active_slot, self.stepper_dev, self.limit_a_state = "slot-1", None, None, 0
        self._dev_by_mcu, self._devices = {}, {}
        for box_stepper in self.box_steppers.values():
            mcu = box_stepper.stepper.get_mcu()
            if mcu not in self._devices:
                self._devices[mcu] = _FeedDevice(self, 22 + len(self._devices))
                self._dev_by_mcu[mcu] = self._devices[mcu].record

    def device(self, slot):
        device = self._devices[self.box_steppers[slot].stepper.get_mcu()]
        device.advance()
        return device

    def offset(self, slot):
        """How far SLOT's step clock on its Box is now ahead of Klipper's, in seconds."""
        self.device(slot)
        return self.offsets.get(slot, 0.)

    def auto_start(self, v, a, lmax, direction, sync_stepper):
        slot = sync_stepper.stepper_name
        device = self.device(slot)
        self.arms.append((slot, v, a, lmax, direction))
        device.configure(slot)
        device.set_limit(self.limit_a_state)
        device.arm()
        self.bind_stepper = self.active_slot = slot
        self.stepper_dev = device

    def auto_abort(self):
        if self.bind_stepper in self.box_steppers:
            self.stepper_dev.abort()
            disable = getattr(self.box_steppers[self.bind_stepper], "disable_stepper", None)
            if disable is not None:
                disable()
        self.bind_stepper = "slot-1"

    def buffer(self, wants_feed):
        """The Box buffer switch changes; QIDI keeps what it read and relays it to the Box last set up."""
        self.limit_a_state = int(wants_feed)
        if self.stepper_dev is not None:
            self.stepper_dev.set_limit(self.limit_a_state)

    def cmd_SET_LIMIT_A(self, gcmd):
        """SET_LIMIT_A STATE=: overwrites what QIDI read from the buffer switch, and relays it the same way."""
        self.buffer(int(gcmd.get("STATE")))

    def reset_step_clock(self, slot):
        """Klipper's note_homing_end for a slot's stepper; the Box shuts down if the slot is stepping."""
        device = self.device(slot)
        if device.slot == slot and device.state == 2:
            self.box_shut_down = True
        else:
            self.offsets[slot] = 0.
