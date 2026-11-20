"""Valve Oil's screen on the touchscreen while an install changes the printer.

From the install's first board read (cli.py: the boards' admission, a firmware update's start, the configuration
install, and the board firmware), QIDI's touchscreen app is stopped and the panel shows update_screen.INSTALLING, so
nobody uses a printer that is half installed, or one whose Klipper is stopped for a board read. A restart of the app that
an install step asks for waits until the end (touchscreen_held), when the app starts once. Board reads and flashes stop
and restore services themselves; they find the app stopped and leave it so.

The app starts again when the install ends, however it ends, unless an unfinished board or installer operation needs
recovery: then it stays stopped and the panel shows update_screen.INSTALL_STOPPED, as after a board flash that can't be
verified. A watcher started with systemd-run starts the app, with the same exception, when the installer ends without
doing so: its SSH session closed or it was killed.

A firmware update the install starts takes the app and the panel over when it stops the app (hand_over): from then on
the update starts the app when it ends, and neither the install nor the watcher does. When the update ends without
having changed anything and the install carries on for the current firmware, the install takes them again (retake).

Nothing is stopped and no screen is shown for a dry run, while a print runs or is paused or a heater is on, when the app
isn't running, or when sudo or the watcher can't be started. The USB installer's launcher stops the app itself, to show
its starting screen, and leaves a marker saying so: given it (stopped_marker), the hold takes over the stopped app and
starts it at the end as if it had stopped it. The install a firmware update runs (firmware_update.py)
finds the app stopped and the update's screen showing: its restarts of the app wait too, and the update starts the app.
"""
from __future__ import annotations

import os
import subprocess
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from . import safety, update_screen
from .errors import InstallerError
from .fs_atomic import atomic_write_text
from .sudo import SudoError, authenticate_sudo, run_sudo

TOUCHSCREEN = "qidi-client.service"
MARKER = ".tltg_install_screen"  # in printer_data while an install holds the app stopped
WATCHER_UNIT_PREFIX = "tltg-install-screen-"
# Without a description, systemd-run makes one from the command line, and systemd writes it unescaped into the transient
# unit file; a systemctl daemon-reload while the install runs then reads the script's lines as broken settings.
WATCHER_DESCRIPTION = "Valve Oil install screen watcher"
# Run as root by systemd-run: wait for the installer to end, then start the app if the installer didn't release it and
# no recovery record exists. $1 is the installer's process id, $2 the marker, and the rest the recovery records.
WATCHER_SCRIPT = """pid=$1; marker=$2; shift 2
while kill -0 "$pid" 2>/dev/null; do sleep 2; done
[ -e "$marker" ] || exit 0
rm -f "$marker"
for record in "$@"; do [ -e "$record" ] && exit 0; done
exec systemctl start {unit}
""".format(unit=TOUCHSCREEN)

_held = False
_marker: Path | None = None  # the marker of the hold this process took, while it has one


def touchscreen_held() -> bool:
    """An install holds the touchscreen app stopped: a restart of it waits for the install's end."""
    return _held and (_marker is None or _marker.exists())


def hand_over(paths) -> None:
    """A firmware update stops the touchscreen app and shows its own screen: an install holding the app leaves the app
    to the update, which starts it when it ends."""
    (paths.printer_data_root / MARKER).unlink(missing_ok=True)


class Hold:
    """The install's hold of the touchscreen app, for retake; password is sudo's while the hold has the app."""

    def __init__(self, retake, password: str | None = None):
        self.retake = retake
        self.password = password


def recovery_records(paths) -> tuple[Path, ...]:
    """The records of an unfinished board or installer operation, which keep the app stopped until it is recovered."""
    root = paths.printer_data_root
    return (root / safety.MAINBOARD_RECOVERY_MARKER, root / safety.BOX_RECOVERY_MARKER,
            root / safety.THR_RECOVERY_MARKER, paths.recovery_sentinel_path)


@contextmanager
def held(*, paths, reporter, environ, input_stream=None, run=subprocess.run, urlopen=urllib.request.urlopen,
         draw=update_screen.draw, require_idle: bool = True, progress_screen: bool = True,
         stopped_marker: Path | None = None):
    """Hold the touchscreen app stopped, with Valve Oil's install screen showing, for the block, which gets a Hold.

    require_idle False takes the screen while the printer is busy too: the USB installer (usb_install.py) draws its
    own screens from the first one, including the one that says the printer is busy. progress_screen False leaves the
    drawing to the caller. stopped_marker, when it exists, says the app is stopped for this hold to take over."""
    global _held, _marker
    if environ.get(safety.FIRMWARE_UPDATE_RUN_ENV):
        # The firmware update stopped the app and shows its own screen; it starts the app when it ends.
        _held, _marker = True, None
        try:
            yield Hold(lambda: None)
        finally:
            _held = False
        return

    def take():
        return _take(paths=paths, reporter=reporter, environ=environ, input_stream=input_stream, run=run,
                     urlopen=urlopen, draw=draw if progress_screen else _no_draw, require_idle=require_idle,
                     stopped_marker=stopped_marker)

    marker = paths.printer_data_root / MARKER
    password = take()

    def retake():
        nonlocal password
        if password is not None and not marker.exists():
            password = take()  # a firmware update had the app, and ended without changing anything

    _held, _marker = password is not None, marker
    hold = Hold(retake, password)
    screen = _progress_screen(paths, reporter, marker) if progress_screen and password is not None else None
    try:
        yield hold
    finally:
        _held, _marker = False, None
        if screen is not None:
            screen.stop()
            screen.ui.close()
        if password is not None and marker.exists():
            _give_back(paths=paths, reporter=reporter, run=run, password=password, draw=draw)


def _progress_screen(paths, reporter, marker):
    """The live progress screen over the still install image, for an SSH install or an automatic update; None where
    it can't be drawn, which leaves the still image. It draws only while this install has the app: not while a
    firmware update it started has the screen (hand_over)."""
    from .firmware import detect_firmware_version_best_effort
    from .progress_screen import ProgressScreen

    try:
        from . import touch_ui

        ui = touch_ui.open(reporter, touch=False)
    except Exception as exc:  # no drawing toolkit or framebuffer: the still image stays
        reporter.debug(event="install_screen.progress_unavailable", error=f"{type(exc).__name__}: {exc}")
        return None
    if ui is None:
        return None
    screen = ProgressScreen(ui, reporter=reporter, firmware=detect_firmware_version_best_effort(
        paths.firmware_manifest_path) if hasattr(paths, "firmware_manifest_path") else None, active=marker.exists)
    screen.start()
    return screen


def _no_draw(*args, **kwargs) -> bool:
    return True


def _take(*, paths, reporter, environ, input_stream, run, urlopen, draw, require_idle=True,
          stopped_marker: Path | None = None) -> str | None:
    """Stop the app and show the screen; the sudo password, or None when the app was left as it was. An app that
    stopped_marker says is stopped for this hold is taken over as it is."""
    try:
        active = run(["systemctl", "is-active", TOUCHSCREEN], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None  # not a printer
    stopped = (active.stdout or "").strip() != "active"
    if stopped and (stopped_marker is None or not stopped_marker.exists()):
        return None
    try:
        if require_idle:
            safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen)
        password = authenticate_sudo(run=run, environ=environ, reporter=reporter, input_stream=input_stream)
    except (InstallerError, SudoError):
        return None  # the install's own checks say why
    marker = paths.printer_data_root / MARKER
    try:
        atomic_write_text(marker, f"{os.getpid()}\n", mode=0o644)
    except OSError:
        return None
    watcher = run_sudo(["systemd-run", f"--unit={WATCHER_UNIT_PREFIX}{os.getpid()}",
                        f"--description={WATCHER_DESCRIPTION}", "--collect", "--quiet",
                        "/bin/sh", "-c", WATCHER_SCRIPT, "sh", str(os.getpid()), str(marker),
                        *(str(record) for record in recovery_records(paths))],
                       run=run, password=password, timeout=30)
    if watcher.returncode or (not stopped and run_sudo(["systemctl", "stop", TOUCHSCREEN], run=run, password=password,
                                                       timeout=60).returncode):
        marker.unlink(missing_ok=True)  # the watcher, if it started, then leaves the app alone
        run_sudo(["systemctl", "start", TOUCHSCREEN], run=run, password=password, timeout=60)
        if stopped_marker is not None:
            stopped_marker.unlink(missing_ok=True)
        return None
    if stopped_marker is not None:
        stopped_marker.unlink(missing_ok=True)  # the hold has the app now; the launcher's cleanup leaves it alone
    update_screen.light(lambda command: run_sudo(command, run=run, password=password, timeout=60), reporter=reporter)
    draw(update_screen.INSTALLING, reporter=reporter)
    return password


def _give_back(*, paths, reporter, run, password: str, draw) -> None:
    marker = paths.printer_data_root / MARKER
    if any(record.exists() for record in recovery_records(paths)):
        draw(update_screen.INSTALL_STOPPED, reporter=reporter)
        marker.unlink(missing_ok=True)
        reporter.line("The touchscreen stays stopped until the recovery above is done.")
        return
    if run_sudo(["systemctl", "start", TOUCHSCREEN], run=run, password=password, timeout=60).returncode:
        reporter.line(f"The touchscreen app could not be started; start it with: sudo systemctl start {TOUCHSCREEN}")
    marker.unlink(missing_ok=True)
