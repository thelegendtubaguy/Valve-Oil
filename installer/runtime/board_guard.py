"""Board firmware work survives the operator's terminal going away.

The window: from the first service stop of a board read or flash until its services run again (or, when a write
can't be verified, until the pending record is kept and the run stops). Inside it:

- SIGHUP (the SSH session closing) and SIGPIPE are ignored;
- Ctrl-C (SIGINT) is refused with a line saying why, and the work goes on;
- output to a terminal that has gone away is dropped instead of raising;
- the board tools and `systemctl` run in their own session, so the terminal's signals never reach them, and the tools
  ignore SIGHUP and SIGINT themselves while they erase and program a board.

Before the window (questions, sudo, waiting for Klipper) and after it, Ctrl-C stops the installer as usual. A board
operation that the caller runs with services already stopped (a firmware update) has the same window.
"""
from __future__ import annotations

import signal
import subprocess
import threading
from contextlib import contextmanager

REFUSED = ("Board firmware work is in progress, so Ctrl-C is ignored until Klipper and the touchscreen run again. "
           "Keep the printer powered on.")
_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGPIPE)
_depth = 0


class _Output:
    """The reporter, with output to a terminal that has gone away dropped instead of raising."""

    def __init__(self, reporter):
        self._reporter = reporter

    def __getattr__(self, name):
        attribute = getattr(self._reporter, name)
        if not callable(attribute):
            return attribute

        def call(*args, **kwargs):
            try:
                return attribute(*args, **kwargs)
            except (OSError, ValueError):
                return None

        return call


@contextmanager
def critical_window(reporter):
    """Hold the terminal's signals off for the window; yields the reporter to use inside it. Nested windows share the
    outermost one."""
    global _depth
    output = reporter if isinstance(reporter, _Output) else _Output(reporter)
    if _depth or threading.current_thread() is not threading.main_thread():
        _depth += 1
        try:
            yield output
        finally:
            _depth -= 1
        return
    previous = {number: signal.getsignal(number) for number in _SIGNALS}

    def refuse(signum, frame):
        output.line(REFUSED)

    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    signal.signal(signal.SIGINT, refuse)
    _depth += 1
    try:
        yield output
    finally:
        _depth -= 1
        for number, handler in previous.items():
            signal.signal(number, signal.SIG_DFL if handler is None else handler)


def detached(run):
    """`run` with each command in its own session, away from the terminal's hangup and Ctrl-C, and without the
    terminal as its input unless the caller gives one."""

    def call(command, **kwargs):
        kwargs.setdefault("start_new_session", True)
        if "input" not in kwargs:
            kwargs.setdefault("stdin", subprocess.DEVNULL)
        return run(command, **kwargs)

    return call
