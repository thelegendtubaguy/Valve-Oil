"""The live progress screen on the touchscreen: the current step by name, its place among the run's steps, the elapsed
time, and how the run was started.

The USB installer (usb_install.py), SSH installs, and automatic updates (install_screen.py) show it while they hold
the touchscreen. It follows the run's steps (progress.py) and ticks the elapsed time once a second, redrawing only the
header. Drawing never stops a run: a screen that fails is logged, and the screen stops following the run.
"""
from __future__ import annotations

import threading
import time

from . import progress

INSTALL_WARNING = "Don't turn the printer off. It restarts by itself at the end."
AUTOMATIC_WARNING = "Automatic updates are on. Don't turn the printer off."


def clock_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def footer_for(snapshot, *, firmware: str | None) -> str:
    """The footer says how the run was started."""
    version = snapshot.to_version or ""
    if snapshot.origin == progress.ORIGIN_AUTOMATIC and snapshot.from_version:
        return f"Valve Oil {snapshot.from_version} → {version} · automatic update"
    how = {progress.ORIGIN_USB: "USB installer", progress.ORIGIN_SSH: "installing over SSH",
           progress.ORIGIN_AUTOMATIC: "automatic update",
           progress.ORIGIN_FIRMWARE_UPDATE: "QIDI firmware update"}.get(snapshot.origin, "installing")
    return f"Valve Oil {version} · {how}" + (f" · QIDI firmware {firmware}" if firmware else "")


class ProgressScreen:
    """Follows the run's steps on `ui` (touch_ui) from start() to stop()."""

    def __init__(self, ui, *, reporter=None, where: str = "", firmware: str | None = None, updating: bool = False,
                 footer: str | None = None, clock=time.monotonic, tick_seconds: float = 1.0, active=None):
        self.ui = ui
        self.reporter = reporter
        self.where = where
        self.firmware = firmware
        self.updating = updating
        self.footer = footer
        self.clock = clock
        self.tick_seconds = tick_seconds
        self.active = active or (lambda: True)  # whether this run has the screen now
        self._stopped = threading.Event()
        self.started_at: float | None = None
        self.snapshot = None
        self._lock = threading.Lock()
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.started_at = self.clock()
        self._stopped.clear()
        self._running.set()
        progress.add_listener(self.draw)
        self._thread = threading.Thread(target=self._tick, name="valve-oil-progress-clock", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        self._stopped.set()
        progress.remove_listener(self.draw)
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def elapsed(self) -> float:
        return self.clock() - (self.started_at if self.started_at is not None else self.clock())

    def header(self, snapshot) -> str:
        return f"Step {snapshot.index} of {snapshot.total} · {clock_text(self.elapsed())}"

    def draw(self, snapshot) -> None:
        self.snapshot = snapshot
        if snapshot.step is None or not self._running.is_set() or not self.active():
            return
        automatic = snapshot.origin == progress.ORIGIN_AUTOMATIC
        if snapshot.step.startswith("QIDI Box"):
            warning = "Keep the printer and " + snapshot.step[len("QIDI "):-len(" firmware")] + " powered on."
        else:
            warning = AUTOMATIC_WARNING if automatic else INSTALL_WARNING
        detail = snapshot.detail or ""
        if automatic and snapshot.from_version and snapshot.to_version:
            detail = " · ".join(part for part in (detail, f"{snapshot.from_version} to {snapshot.to_version}") if part)
        with self._lock:
            self._show("installing", title="Updating Valve Oil" if (self.updating or automatic) else "Installing",
                       step=snapshot.step, detail=detail, steps=list(snapshot.steps), index=snapshot.index,
                       warning=warning, header_step=self.header(snapshot), where=self.where,
                       footer=self.footer or footer_for(snapshot, firmware=self.firmware))

    def _show(self, screen, **fields) -> None:
        try:
            self.ui.show(screen, **fields)
        except Exception as exc:  # a screen that can't be drawn never stops the run
            self._running.clear()
            if self.reporter is not None:
                self.reporter.debug(event="progress_screen.failed", error=f"{type(exc).__name__}: {exc}")

    def _tick(self) -> None:
        while not self._stopped.wait(self.tick_seconds):
            snapshot = self.snapshot
            if snapshot is None or snapshot.step is None or not self._running.is_set() or not self.active():
                continue
            with self._lock:
                try:
                    self.ui.update(header_step=self.header(snapshot))
                except Exception:
                    self._running.clear()
