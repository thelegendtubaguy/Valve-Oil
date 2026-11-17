"""A log of each install, update, and uninstall run, in printer_data/logs where Fluidd downloads it.

Every run writes what it detected, decided, and did, at debug level whatever the terminal shows: each reporter
event, line, status, step, prompt and answer, and the error a run stops on. The newest KEEP logs are kept. A USB run
also copies its log to the drive (usb_install.py). Writing the log is best-effort: a log that can't be written never
stops a run.
"""
from __future__ import annotations

import os
import re
import time
import traceback
from pathlib import Path

from .reporter import format_debug_line

PREFIX = "valve-oil-"
SUFFIX = ".log"
KEEP = 10
# valve-oil-<mode>-<YYYYmmdd-HHMMSS>[-<n>].log; other valve-oil-*.log files, such as the USB hand-off's, aren't these.
_RUN_LOG = re.compile(r"^valve-oil-[a-z-]+-\d{8}-\d{6}(-\d+)?\.log$")


def log_dir(paths) -> Path:
    return paths.printer_data_root / "logs"


def run_logs(paths) -> list[Path]:
    """This package's run logs, oldest first."""
    try:
        return sorted((path for path in log_dir(paths).iterdir()
                       if _RUN_LOG.match(path.name) and path.is_file() and not path.is_symlink()),
                      key=lambda path: path.stat().st_mtime)
    except OSError:
        return []


class RunLog:
    """An open run log; `path` is None once it can't be written."""

    def __init__(self, path: Path | None, clock=time.time):
        self.path = path
        self._clock = clock
        self._file = None
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._file = open(path, "a", encoding="utf-8")
            except OSError:
                self.path = None

    def write(self, text: str) -> None:
        if self._file is None:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._clock()))
        try:
            for line in str(text).splitlines() or [""]:
                self._file.write(f"{stamp} {line}\n")
            self._file.flush()
        except (OSError, ValueError):
            self._close_quietly()

    def exception(self, exc: BaseException) -> None:
        self.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip())

    def close(self) -> None:
        if self._file is None:
            return
        try:
            self._file.flush()
            os.fsync(self._file.fileno())
        except (OSError, ValueError):
            pass
        self._close_quietly()

    def _close_quietly(self) -> None:
        try:
            self._file.close()
        except (OSError, ValueError):
            pass
        self._file = None


def open_run_log(paths, mode: str, *, clock=time.time) -> RunLog:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(clock()))
    directory = log_dir(paths)
    path = directory / f"{PREFIX}{mode}-{stamp}{SUFFIX}"
    counter = 1
    while path.exists():
        counter += 1
        path = directory / f"{PREFIX}{mode}-{stamp}-{counter}{SUFFIX}"
    log = RunLog(path, clock=clock)
    if log.path is not None:
        for old in run_logs(paths)[:-KEEP]:
            try:
                old.unlink()
            except OSError:
                pass
    return log


class LoggingReporter:
    """A reporter that also writes everything to a run log, and passes every call on to the reporter it wraps."""

    def __init__(self, reporter, log: RunLog):
        self._reporter = reporter
        self.log = log

    def __getattr__(self, name):
        attribute = getattr(self._reporter, name)
        if not callable(attribute) or name.startswith("_"):
            return attribute

        def call(*args, **kwargs):
            self.log.write(_describe(name, args, kwargs))
            return attribute(*args, **kwargs)

        return call

    def debug(self, *, event: str, **fields) -> None:
        self.log.write(format_debug_line(event, fields))
        self._reporter.debug(event=event, **fields)

    def line(self, message: str = "") -> None:
        self.log.write(message)
        self._reporter.line(message)

    def status(self, message: str) -> None:
        self.log.write(f"status | {message}")
        self._reporter.status(message)

    def emit_error(self, error: Exception) -> None:
        self.log.write(f"error | {type(error).__name__}: {getattr(error, 'message', str(error))}")
        self.log.exception(error)
        self._reporter.emit_error(error)


def _describe(name: str, args, kwargs) -> str:
    parts = [f"{name}"]
    parts.extend(repr(arg) for arg in args)
    parts.extend(f"{key}={value!r}" for key, value in sorted(kwargs.items()))
    text = " | ".join(parts)
    return text if len(text) <= 2000 else text[:2000] + "..."
