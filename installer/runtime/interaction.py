from __future__ import annotations

import urllib.request
from typing import Callable, TextIO

from . import messages, safety
from .errors import ActivePrintError, PrinterStateError

UrlOpenFn = Callable[..., object]

_YES_RESPONSES = {"Y", "YES"}
_NO_RESPONSES = {"N", "NO"}


def prompt_yes(
    *,
    reporter,
    input_stream: TextIO,
    question: str,
    instruction: str,
) -> bool:
    while True:
        if hasattr(reporter, "emit_prompt"):
            reporter.emit_prompt(question=question, instruction=instruction)
        else:
            reporter.prepare_for_prompt()
            reporter.line(question)
            reporter.line(instruction)
        raw_response = input_stream.readline()
        if raw_response == "":
            return False
        response = raw_response.strip().upper()
        if response in _YES_RESPONSES:
            return True
        if response in _NO_RESPONSES:
            return False


def confirm_yes(
    *,
    reporter,
    input_stream: TextIO | None,
    question: str,
    instruction: str,
    cancel_message: str,
) -> bool:
    if input_stream is None:
        return True
    if prompt_yes(
        reporter=reporter,
        input_stream=input_stream,
        question=question,
        instruction=instruction,
    ):
        return True
    reporter.line(cancel_message)
    return False



def maybe_restart_pending_service(
    *,
    paths,
    allowed_entries,
    reporter,
    input_stream: TextIO | None,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    """Restart the Klipper service so changed sources run, without asking: a run that changed them restarts it.

    A restart that fails raises in a run with no terminal (input_stream None), where nobody reads the line."""
    from .process_restart import ProcessRestartError, restart_pending

    reporter.line(messages.KLIPPER_SERVICE_RESTARTING)
    try:
        restart_pending(paths, allowed_entries=allowed_entries, urlopen=urlopen)
    except ProcessRestartError as exc:
        if hasattr(reporter, "debug"):
            reporter.debug(event="klipper.process_restart.failed", message=exc.message)
        reporter.line(messages.KLIPPER_SERVICE_RESTART_FAILED)
        if input_stream is None:
            raise
        return False
    if hasattr(reporter, "debug"):
        reporter.debug(event="klipper.process_restart.verified")
    reporter.line(messages.KLIPPER_SERVICE_RESTARTED)
    return True


def maybe_restart_pending_service_if_idle(
    *,
    paths,
    allowed_entries,
    reporter,
    input_stream: TextIO | None,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    try:
        # Install and uninstall may have just restarted Moonraker; give it time to report printer state again.
        safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen, wait_seconds=90.0)
    except ActivePrintError:
        reporter.debug(event="klipper.process_restart.deferred", reason="active_print")
        reporter.line(messages.KLIPPER_SERVICE_RESTART_DEFERRED_ACTIVE_PRINT)
        return False
    except PrinterStateError:
        reporter.debug(event="klipper.process_restart.deferred", reason="unknown_state")
        reporter.line(messages.KLIPPER_SERVICE_RESTART_DEFERRED_UNKNOWN_STATE)
        return False
    return maybe_restart_pending_service(
        paths=paths,
        allowed_entries=allowed_entries,
        reporter=reporter,
        input_stream=input_stream,
        urlopen=urlopen,
    )


def restart_klipper_unattended(
    *,
    paths,
    reporter,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    """End a `--yes` run or an automatic update with Klipper running what was installed.

    Nobody is there to restart Klipper afterwards, so the service restarts once the printer
    is idle, and the run waits for Klipper to be ready under a new process.
    """
    from .process_restart import ProcessRestartError, restart_klipper_service

    try:
        # The run may have just restarted Moonraker; give it time to report printer state again.
        safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen, wait_seconds=90.0)
    except ActivePrintError:
        reporter.debug(event="klipper.restart.skipped", reason="active_print")
        reporter.line(messages.RESTART_KLIPPER_SKIPPED_BUSY)
        return False
    except PrinterStateError:
        reporter.debug(event="klipper.restart.skipped", reason="unknown_state")
        reporter.line(messages.COULD_NOT_RESTART_KLIPPER)
        return False
    reporter.line(messages.KLIPPER_SERVICE_RESTARTING)
    try:
        restart_klipper_service(paths, urlopen=urlopen)
    except ProcessRestartError as exc:
        reporter.debug(event="klipper.restart.failed", message=exc.message)
        reporter.line(messages.COULD_NOT_RESTART_KLIPPER)
        return False
    reporter.line(messages.KLIPPER_SERVICE_RESTARTED)
    return True


def maybe_restart_klipper(
    *,
    paths,
    reporter,
    input_stream: TextIO | None = None,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    """End a run that changed the configuration with Klipper running it: restarted without asking, once idle."""
    return restart_klipper_unattended(paths=paths, reporter=reporter, urlopen=urlopen)
