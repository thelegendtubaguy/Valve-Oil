from __future__ import annotations

import getpass
import subprocess
import sys
from typing import Callable

from . import messages
from .errors import InstallerError

PUBLIC_DEFAULT_SUDO_PASSWORD = "qiditech"
SUDO_PASSWORD_ENV = "TLTG_OPTIMIZED_SUDO_PASSWORD"
RunFn = Callable[..., subprocess.CompletedProcess]


class SudoError(InstallerError):
    pass


# The password the operator typed this run, so steps after the questions run without asking again (install_flow.py).
# Kept in this process only: never in the environment, which child processes inherit.
_entered: str | None = None


def authenticate_sudo(*, run: RunFn, environ: dict[str, str], reporter, input_stream) -> str:
    global _entered
    password = environ.get(SUDO_PASSWORD_ENV, PUBLIC_DEFAULT_SUDO_PASSWORD)
    if run_sudo(["-v"], run=run, password=password).returncode == 0:
        return password
    if _entered is not None and run_sudo(["-v"], run=run, password=_entered).returncode == 0:
        return _entered
    fallback = _prompt_sudo_password(reporter=reporter, input_stream=input_stream)
    if fallback is None:
        raise SudoError(messages.AUTO_UPDATE_SUDO_FAILED)
    if run_sudo(["-v"], run=run, password=fallback).returncode != 0:
        raise SudoError(messages.AUTO_UPDATE_SUDO_FAILED)
    _entered = fallback
    return fallback


def run_sudo_or_raise(
    command: list[str],
    message: str,
    *,
    run: RunFn,
    password: str,
) -> None:
    result = run_sudo(command, run=run, password=password)
    if result.returncode != 0:
        raise SudoError(message)


def run_sudo(
    command: list[str],
    *,
    run: RunFn,
    password: str,
    **kwargs,
) -> subprocess.CompletedProcess:
    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    return run(sudo_command(command), input=password + "\n", text=True, **kwargs)


def run_sudo_ignore_failure(
    command: list[str],
    *,
    run: RunFn,
    password: str,
) -> None:
    run_sudo(
        command,
        run=run,
        password=password,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def popen_sudo(command: list[str], *, password: str) -> subprocess.Popen:
    """Start `command` as root with its output piped, for a reader that stays running (touch_ui.Touch)."""
    process = subprocess.Popen(sudo_command(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    try:
        process.stdin.write((password + "\n").encode("utf-8"))
        process.stdin.close()
    except OSError:
        pass
    return process


def sudo_command(command: list[str]) -> list[str]:
    return ["sudo", "-S", "-p", "", *command]


def _prompt_sudo_password(*, reporter, input_stream) -> str | None:
    if input_stream is None:
        return None
    reporter.prepare_for_prompt()
    use_getpass = input_stream is sys.stdin and getattr(input_stream, "isatty", lambda: False)()
    if hasattr(reporter, "emit_prompt") and not use_getpass:
        reporter.emit_prompt(
            question=messages.AUTO_UPDATE_SUDO_INITIAL_FAILED,
            instruction=messages.AUTO_UPDATE_SUDO_PASSWORD_PROMPT,
        )
    else:
        reporter.line(messages.AUTO_UPDATE_SUDO_INITIAL_FAILED)
    if use_getpass:
        try:
            return getpass.getpass(
                messages.AUTO_UPDATE_SUDO_PASSWORD_PROMPT + " ",
                stream=reporter.stream,
            )
        except EOFError:
            return None
    if not hasattr(reporter, "emit_prompt"):
        reporter.line(messages.AUTO_UPDATE_SUDO_PASSWORD_PROMPT)
    response = input_stream.readline()
    if response == "":
        return None
    return response.rstrip("\n")
