"""The named steps of an install, update, or uninstall run, for the terminal, the run log, and the touchscreen.

A run begins with the steps it expects (begin) and moves through them by name (step). A step the run didn't expect,
such as a board that turns out to need its firmware, is added where it starts, so the count stays honest. Listeners,
such as the touchscreen's progress screen (install_screen.py), get a Snapshot on every change; the elapsed time is
theirs to tick. Nothing here fails a run: a listener that raises is dropped.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

# How a run was started, which the progress screen says.
ORIGIN_SSH = "ssh"
ORIGIN_USB = "usb"
ORIGIN_AUTOMATIC = "automatic"
ORIGIN_FIRMWARE_UPDATE = "firmware_update"

# Step names shared by the runs, so the touchscreen, the terminal, and the log name them the same way.
CHECK = "Check the printer"
QIDI_FIRMWARE = "QIDI firmware {target}"
BACKUP = "Back up your configuration"
CONFIGURATION = "Macros and Klipper fixes"
SYSTEM_CHANGES = "System changes"
RESTART_KLIPPER = "Restart Klipper and verify"
BOARDS = "Toolhead and mainboard firmware"
FINISH = "Finish"
UNINSTALL_CONFIGURATION = "Remove Valve Oil's configuration"
RESTORE_SYSTEM = "Restore system changes"


def box_firmware(numbers) -> str:
    numbers = sorted(numbers)
    if len(numbers) == 1:
        return f"QIDI Box {numbers[0]} firmware"
    return "QIDI Box " + ", ".join(str(number) for number in numbers[:-1]) + f" and {numbers[-1]} firmware"


@dataclass(frozen=True)
class Snapshot:
    step: str | None  # None before the first step
    index: int  # 1-based place of the step; 0 before the first
    total: int
    started_at: float  # time.monotonic() when the run began
    origin: str
    from_version: str | None = None
    to_version: str | None = None
    detail: str | None = None  # what the step is doing now, e.g. "restarting Moonraker"
    steps: tuple[str, ...] = ()  # every step of the run, in order

    def elapsed(self, clock=time.monotonic) -> float:
        return max(0.0, clock() - self.started_at)


class _Run:
    def __init__(self, steps, *, origin, reporter, clock, from_version, to_version):
        self.steps = list(dict.fromkeys(steps))
        self.index = 0
        self.origin = origin
        self.reporter = reporter
        self.started_at = clock()
        self.from_version = from_version
        self.to_version = to_version
        self.detail = None

    def snapshot(self) -> Snapshot:
        step = self.steps[self.index - 1] if self.index else None
        return Snapshot(step, self.index, len(self.steps), self.started_at, self.origin, self.from_version,
                        self.to_version, self.detail, tuple(self.steps))


_run: _Run | None = None
_listeners: list = []


def begin(steps, *, origin: str, reporter=None, clock=time.monotonic, from_version: str | None = None,
          to_version: str | None = None) -> None:
    """Start counting the run's steps; `steps` are the names the run expects, in order."""
    global _run
    _run = _Run(steps, origin=origin, reporter=reporter, clock=clock, from_version=from_version,
                to_version=to_version)
    if reporter is not None:
        reporter.debug(event="progress.begin", origin=origin, steps=" / ".join(_run.steps))
    _notify()


def step(name: str, reporter=None) -> None:
    """The run is now on step `name`."""
    run = _run
    if run is None:
        return
    if name in run.steps[run.index:]:
        run.index = run.steps.index(name, run.index) + 1
    elif run.index and run.steps[run.index - 1] == name:
        pass  # already on it
    else:
        run.steps.insert(run.index, name)
        run.index += 1
    run.detail = None
    reporter = reporter or run.reporter
    if reporter is not None:
        snapshot = run.snapshot()
        reporter.debug(event="progress.step", step=name, index=snapshot.index, total=snapshot.total)
        emit = getattr(reporter, "emit_step", None)
        if emit is not None:
            emit(step=name, index=snapshot.index, total=snapshot.total)
    _notify()


def detail(text: str | None) -> None:
    """What the current step is doing now, shown beside its name."""
    run = _run
    if run is None or run.detail == text:
        return
    run.detail = text
    _notify()


def skip(name: str) -> None:
    """Step `name`, still ahead, won't run: it no longer counts."""
    run = _run
    if run is None:
        return
    if name in run.steps[run.index:]:
        run.steps.remove(name)
        _notify()


def current() -> Snapshot | None:
    return _run.snapshot() if _run is not None else None


def end() -> None:
    global _run
    _run = None


def add_listener(listener) -> None:
    _listeners.append(listener)
    snapshot = current()
    if snapshot is not None:
        _call(listener, snapshot)


def remove_listener(listener) -> None:
    if listener in _listeners:
        _listeners.remove(listener)


def _notify() -> None:
    snapshot = current()
    if snapshot is None:
        return
    for listener in list(_listeners):
        _call(listener, snapshot)


def _call(listener, snapshot) -> None:
    try:
        listener(snapshot)
    except Exception:  # a broken screen never stops a run
        remove_listener(listener)
