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
