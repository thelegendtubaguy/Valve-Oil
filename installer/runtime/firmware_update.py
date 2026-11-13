"""Valve Oil's QIDI firmware update (design D4): take a printer on an older supported QIDI firmware to its target
firmware, its boards to their pinned images, and Valve Oil to an install for the new firmware, in one run.

Starting it (start_firmware_update) checks the printer, downloads and verifies QIDI's package from this project's
mirror, writes the pending record, and starts tltg-firmware-update.service, which runs the update detached from the
session that asked for it: QIDI's package restarts the network, which ends an SSH session. An install follows the
unit's log until it finishes; the update check starts it and leaves it running. The unit is enabled, so a printer that
lost power with the record in place resumes at its next boot; while the record exists no other installer operation
runs (safety.FIRMWARE_UPDATE_MARKER).

Steps, each safe to repeat; a resumed run starts at the first one the record doesn't mark done:
  check          printer idle, every heater target 0, THR and mainboard admitted and deliverable, no operation
                 awaiting recovery, free space, firmware unchanged
  stage          unpack the verified package beside QIDI's update folder and check its SOC Debian package
  closed_loop    Max 4: stop the touchscreen and Moonraker, then update the closed-loop X/Y drivers with QIDI's
                 UPGRADE_MOTOR through Klipper's API socket, the old Klipper still running (closed_loop_firmware.py)
  stop_services  stop the touchscreen, Klipper, Moonraker, and the update timer; draw the "do not power off" screen
  soc            back up the config folder, then dpkg -i --force-overwrite the SOC package, as QIDI's updater does
  update_folder  make the staged package QIDI's update folder
  boards         flash the boards that need it, with the services still stopped
  install        start Klipper and Moonraker and run this release's installer, the touchscreen still stopped until
                 the installer has patched QIDI's new touchscreen app and starts it again
  finish         start everything, clear the record, drop the cached package
Before stop_services nothing has changed, and any failure ends the run with the record removed. From there, the
touchscreen stays stopped until the installer has patched QIDI's new touchscreen app, except where a failure needs it to
recover. A failure is recorded (state "failed") and continued only by ./install.sh or Update now: the unit started at
boot leaves it, and the update check starts no firmware update while a record exists. A resume that one of those asks
for first checks that nothing prints. Every exit of the unit writes a result.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from . import qidi_package, safety, saved_calibration, update_screen, valve_oil_status
from .auto_update import KIND_ENV, LOCK_HELD_ENV, TIMER_NAME, current_user
from .backup import build_install_backup_label, create_config_backup, utc_now
from .board_firmware import NEEDS_IMAGE, UNREAD, load_board_admission, record_board_admission
from .box_firmware import usb_path_for_serial
from . import closed_loop_firmware
from .closed_loop_firmware import DriverUpdateFailed, KlipperAPI, klipper_api_path
from .errors import ActivePrintError, FirmwareDetectionError, HeaterOnError, InstallerError, PrinterStateError
from .firmware import detect_firmware, detect_firmware_version_best_effort, supported_printer
from .fs_atomic import atomic_delete, atomic_write_text
from .mainboard_firmware import mainboard_serial
from .manifest import load_manifest
from .naming import BUNDLE_ROOT_NAME, REPOSITORY_URL
from .sudo import PUBLIC_DEFAULT_SUDO_PASSWORD, SudoError, authenticate_sudo, run_sudo, sudo_command
from .system_optimizations import clear_carried_system_ledger, save_carried_system_ledger
from .valve_oil_status import Blocker

UNIT = "tltg-firmware-update.service"
SYSTEMD_DIR = Path("/etc/systemd/system")
UNIT_MARKER = "# tltg-optimized:firmware-update-unit\n"
RESULT_FILE = ".tltg_firmware_update_result.json"
RECORD_LOCK_FILE = ".tltg_firmware_update.lock"
LOG_NAME = "update.log"
SCHEMA = 1
QIDI_SERVICES = ("qidi-client.service", "klipper.service", "moonraker.service")  # stopped in this order
TOUCHSCREEN = QIDI_SERVICES[0]
KLIPPER_AND_MOONRAKER = ("moonraker.service", "klipper.service")
# At boot Moonraker and Klipper take a while before they report whether the printer is idle.
READY_WAIT_SECONDS = 180
FOLLOW_GRACE_SECONDS = 30
DPKG_LOCK_WAIT_SECONDS = 600
SERIAL_BY_ID = Path("/dev/serial/by-id")
# How long the boards step waits for the mainboard to show up on USB, as after a boot or a flash that renamed it.
MAINBOARD_WAIT_SECONDS = 60
# Boot resumes in a row before a run is recorded as failed, so a crash doesn't stop the printer at every boot.
MAX_BOOT_ATTEMPTS = 3

STEPS = ("check", "stage", "closed_loop", "stop_services", "soc", "update_folder", "boards", "install", "finish")
FIRST_CHANGE = STEPS.index("stop_services")
LABELS = {
    "check": "Checking that the printer can be updated",
    "stage": "Unpacking QIDI firmware {target}",
    "closed_loop": "Updating the closed-loop motor drivers",
    "stop_services": "Stopping Klipper, Moonraker, and the touchscreen",
    "soc": "Installing QIDI firmware {target}",
    "update_folder": "Updating QIDI's update folder",
    "boards": "Updating the board firmware",
    "install": "Installing Valve Oil for QIDI firmware {target}",
    "finish": "Starting the printer again",
}

# Results.
NOT_NEEDED, NOT_STARTED, DECLINED, DRY_RUN = "not_needed", "not_started", "declined", "dry_run"
RUNNING, SUCCEEDED, FAILED = "running", "succeeded", "failed"
DURATION = "usually 10 to 30 minutes"
LAST_ATTEMPT_FAILED = "the last attempt didn't finish"


@dataclass(frozen=True)
class FirmwareUpdatePlan:
    """What a firmware update would do on this printer now, and what keeps it from starting."""

    printer: object | None  # PrinterSpec
    installed: str | None
    target: str | None
    needed: bool  # the installed firmware is one this release updates from
    blockers: tuple[Blocker, ...] = ()
    boards: tuple[str, ...] = ()  # the boards the run would flash, by label
    closed_loop: bool = False  # QIDI's package changes the closed-loop drivers, which the run updates first
    download_bytes: int = 0
    free_bytes_needed: int = 0
    pending: dict | None = None  # a firmware update already recorded and not finished


@dataclass(frozen=True)
class FirmwareUpdateResult:
    state: str
    message: str = ""
    blockers: tuple[Blocker, ...] = ()

    @property
    def handled(self) -> bool:
        """The update run took over this install: the caller installs nothing more."""
        return self.state in (RUNNING, SUCCEEDED, FAILED, DRY_RUN)


# ---- files ----------------------------------------------------------------------------------------------------------


def record_path(paths) -> Path:
    return paths.printer_data_root / safety.FIRMWARE_UPDATE_MARKER


def result_path(paths) -> Path:
    return paths.printer_data_root / RESULT_FILE


def log_path(paths) -> Path:
    return qidi_package.cache_dir(paths) / LOG_NAME


def load_record(paths) -> dict | None:
    """The pending record, whichever release wrote it; see _foreign for whether this release can carry it on."""
    return _load(record_path(paths), any_schema=True)


def load_result(paths) -> dict | None:
    return _load(result_path(paths))


def _load(path: Path, *, any_schema: bool = False) -> dict | None:
    try:
        if path.is_symlink():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or not (value.get("schema") == SCHEMA or any_schema and "schema" in value):
        return None
    return value


def _save(path: Path, value: dict) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n", mode=0o644)


@contextmanager
def _record_lock(paths):
    """Serializes every write of the pending record: the unit's, and a session's asking it to run."""
    paths.printer_data_root.mkdir(parents=True, exist_ok=True)
    with (paths.printer_data_root / RECORD_LOCK_FILE).open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def remove_files(paths) -> None:
    """Uninstall: drop the last run's result. A pending record blocks uninstall, so there is none."""
    for path in (result_path(paths), paths.printer_data_root / RECORD_LOCK_FILE):
        try:
            atomic_delete(path)
        except OSError:
            pass
    clear_carried_system_ledger(paths)


def _write_result(paths, record: dict, *, state: str, message: str, blockers=(), target=None) -> None:
    _save(result_path(paths), {
        "schema": SCHEMA, "id": record.get("id"), "request": record.get("current_request"),
        "kind": record.get("kind", "install"), "state": state, "message": message,
        "from_firmware": record.get("from_firmware"), "target_firmware": target or record.get("target_firmware"),
        "finished_at": valve_oil_status.now(), "blockers": list(blockers), "boards": record.get("boards", []),
        "reported": False,
    })


# ---- a record this release can't carry on --------------------------------------------------------------------------


def release_command(release: str) -> str:
    """The command that installs Valve Oil `release` (a published release) and runs its installer."""
    base = f"{REPOSITORY_URL}/releases/download/v{release}"
    return (f'TLTG_INSTALLER_ARCHIVE_URL={base}/tltg-optimized-macros.tar.gz /bin/bash -c "$(curl -fsSL '
            f'{base}/install-latest.sh)"')


def _foreign(record: dict, manifest) -> tuple[str, bool] | None:
    """(why this release can't continue the record, whether the record can simply be dropped), or None when it can.

    A record can be dropped when nothing of QIDI's package was installed yet: the printer still runs the firmware the
    update started from, and this release plans its own update. Once dpkg has started, only the release that wrote the
    record knows the package and boards it was installing.
    """
    release = record.get("release") or "an earlier release"
    if record.get("schema") != SCHEMA:
        return (f"it was written by Valve Oil {release} in a newer format; install Valve Oil {release} or later to "
                f"finish it: {release_command(release)}" if record.get("release") else
                "it was written by another release of Valve Oil in a format this release doesn't know"), False
    printer = next((printer for printer in manifest.printers if printer.id == record.get("printer")), None)
    if printer is not None and printer.target_firmware.version == record.get("target_firmware"):
        return None
    if not record.get("soc_attempted"):
        return f"Valve Oil {release} started for QIDI firmware {record.get('target_firmware')}", True
    return (f"Valve Oil {release} was installing QIDI firmware {record.get('target_firmware')}, which this release "
            f"doesn't update to; install Valve Oil {release} again to finish it: {release_command(release)}"), False


def _settle_foreign(paths, reporter, record: dict, reason: str, droppable: bool, *, start) -> FirmwareUpdateResult:
    """Drop a record nothing was installed for, starting what it had stopped; otherwise mark it failed with the way
    forward. `start(units)` starts services; it is called only when the record had stopped them."""
    with _record_lock(paths):
        current = load_record(paths)
        if current is None or current.get("id") != record.get("id"):
            return FirmwareUpdateResult(NOT_NEEDED)
        if droppable:
            atomic_delete(record_path(paths))
        elif current.get("schema") == SCHEMA:
            current.pop("request", None)
            # continuable: the status service reports whether Update now or ./install.sh can carry the record on.
            current.update(state="failed", continuable=False,
                           message="This release can't continue the firmware update: " + reason + ".")
            _save(record_path(paths), current)
    if droppable:
        if "stop_services" in record.get("done", ()) or record.get("services"):
            start(QIDI_SERVICES + ((TIMER_NAME,) if (record.get("services") or {}).get(TIMER_NAME) == "active" else ()))
        message = f"The firmware update {reason} was dropped: nothing of it was installed yet."
        reporter.line(message)
        _write_result(paths, record, state=NOT_STARTED, message=message)
        return FirmwareUpdateResult(NOT_NEEDED, message)
    message = "This release can't continue the firmware update: " + reason + "."
    reporter.line(message)
    _write_result(paths, record, state=FAILED, message=message)
    return FirmwareUpdateResult(FAILED, message)


def _start_units(units, *, run, password, reporter) -> None:
    for unit in ("moonraker.service", "klipper.service", TOUCHSCREEN, TIMER_NAME):
        if unit in units and run_sudo(["systemctl", "start", unit], run=run, password=password, timeout=180).returncode:
            reporter.line(f"  (Could not start {unit}.)")


# ---- the plan: what a firmware update would do, read without changing anything ------------------------------------


def firmware_update_plan(paths, *, manifest=None, admission=None, urlopen=urllib.request.urlopen,
                         disk_usage=shutil.disk_usage) -> FirmwareUpdatePlan:
    """Whether a firmware update is waiting for this printer, what it would do, and what blocks it.

    Changes nothing and fetches nothing: it reads the firmware manifest, the boards' last admission, the cache, free
    space, and Moonraker. A firmware update already recorded is returned as `pending` and blocks a new one.
    """
    manifest = manifest or load_manifest(paths.installer_root / "package.yaml")
    pending = load_record(paths)
    try:
        detected = detect_firmware(paths.firmware_manifest_path)
    except FirmwareDetectionError:
        return FirmwareUpdatePlan(None, None, None, False, pending=pending)
    printer = supported_printer(manifest, detected)
    if printer is None:
        return FirmwareUpdatePlan(None, detected.version, None, False, pending=pending)
    target = printer.target_firmware
    needed = target.can_update_from(detected.version)
    admission = load_board_admission(paths) if admission is None else admission
    blockers: list[Blocker] = []
    if pending is not None:
        # The failed run's message is the status's run; the reason stays short.
        blockers.append(Blocker.of("last_run_failed", LAST_ATTEMPT_FAILED) if pending.get("state") == "failed"
                        else Blocker.of("run_in_progress"))
    recovery = recovery_blocker(paths)
    if recovery is not None:
        blockers.append(recovery)
    if admission is None or not admission.delivery_admitted:
        blockers.append(Blocker.of("board_not_admitted"))
    elif needed:
        blockers.extend(_deliverability_blockers(paths, printer, admission))
    downloaded = qidi_package.downloaded_bytes(paths, target)
    needed_bytes = qidi_package.required_free_bytes(target, downloaded=downloaded)
    try:
        qidi_package.check_free_space(paths, target, disk_usage=disk_usage)
    except qidi_package.PackageError as exc:
        blockers.append(Blocker.of(exc.code))
    if needed:
        blockers.extend(printer_blockers(paths, urlopen=urlopen))
    boards = tuple(status.label for status in (admission.boards if admission else ()) if status.state == NEEDS_IMAGE)
    return FirmwareUpdatePlan(
        printer=printer, installed=detected.version, target=target.version, needed=needed, blockers=tuple(blockers),
        boards=boards, closed_loop=needed and target.changes_board("closed_loop", detected.version),
        download_bytes=target.size - downloaded, free_bytes_needed=needed_bytes, pending=pending)


def printer_blockers(paths, *, urlopen, wait_seconds: float = 0.0) -> tuple[Blocker, ...]:
    try:
        safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen, wait_seconds=wait_seconds)
    except HeaterOnError:
        return (Blocker.of("heaters_on"),)
    except ActivePrintError:
        return (Blocker.of("printing"),)
    except PrinterStateError:
        return (Blocker.of("printer_state_unknown"),)
    return ()


def _deliverability_blockers(paths, printer, admission) -> tuple[Blocker, ...]:
    """A THR or mainboard that needs its pinned image but can't get it on the target firmware, such as a board on a
    QIDI image the release knows only by its reported version: the run would install QIDI's package and then stop."""
    from .board_firmware import deliverable_now

    blockers = []
    for status in admission.boards:
        if status.kind in ("thr", "mcu") and status.state == NEEDS_IMAGE:
            try:
                deliverable = deliverable_now(status, printer, printer.target_firmware.version, paths.bundle_root)
            except (OSError, ValueError, KeyError, InstallerError):
                deliverable = False
            if not deliverable:
                blockers.append(Blocker.of("board_not_deliverable", f"the {status.label} firmware can't be updated "
                                           f"for QIDI firmware {printer.target_firmware.version} by this release"))
    return tuple(blockers)


# What each record of an unfinished board or installer operation asks the operator to run, as a recovery_required
# blocker's message. installer/system/tltg_status.py says the same for update now.
RECOVERY_STEPS = (
    (safety.MAINBOARD_RECOVERY_MARKER, "a mainboard firmware write wasn't verified; run ./mainboard-firmware.sh rescue "
                                       "first"),
    (safety.BOX_RECOVERY_MARKER, "a Box firmware write wasn't verified; run ./box-firmware.sh rescue first"),
    (safety.THR_RECOVERY_MARKER, "a THR firmware operation didn't finish; recover the THR with the recovery files the "
                                 "installer named first"),
)
SENTINEL_STEP = "an earlier install's recovery didn't complete; restore a backup with ./restore.sh first"


# The records a resumed firmware update can't get past: each board tool refuses while its own exists, so continuing
# would stop the printer only to fail at the boards again. An installer recovery sentinel is not among them; it stops
# only the update's installer step, which then ends the update so ./restore.sh can run.
RESUME_RECOVERY_MARKERS = tuple(marker for marker, _ in RECOVERY_STEPS)


def resume_blocker(paths) -> Blocker | None:
    """What has to run before a recorded firmware update can be continued, saying what to run; None when nothing.
    The CLI, Update now, and the unit all ask this before stopping anything; installer/system/tltg_status.py's
    update_now.blocked_by says the same while a firmware update is recorded."""
    root = paths.printer_data_root
    steps = [step for marker, step in RECOVERY_STEPS if marker in RESUME_RECOVERY_MARKERS and (root / marker).exists()]
    return Blocker.of("recovery_required", "; ".join(steps)) if steps else None


def _not_continued(blocker: Blocker) -> str:
    return (f"The firmware update was not continued: {blocker.message}, then run ./install.sh again to finish it. "
            "Nothing was stopped or changed.")


def recovery_blocker(paths) -> Blocker | None:
    """The recovery an earlier board or installer operation needs before an update can run, saying what to run; None
    when there is none."""
    root = paths.printer_data_root
    steps = [step for marker, step in RECOVERY_STEPS if (root / marker).exists()]
    if paths.recovery_sentinel_path.exists():
        steps.append(SENTINEL_STEP)
    return Blocker.of("recovery_required", "; ".join(steps)) if steps else None


def describe_plan(plan: FirmwareUpdatePlan) -> list[str]:
    printer = plan.printer
    lines = [f"QIDI firmware {plan.target} is available for this {printer.display_name}, which runs {plan.installed}."]
    download = (f"downloads QIDI's package ({plan.download_bytes / 1e6:.0f} MB) from this project's mirror, "
                if plan.download_bytes else "uses QIDI's package it already downloaded, ")
    boards = ", ".join(plan.boards) if plan.boards else "none"
    lines.append(f"Valve Oil {download}installs it, updates the board firmware that needs it ({boards}), and installs "
                 f"Valve Oil for firmware {plan.target}.")
    if plan.closed_loop:
        lines.append("It updates the closed-loop X/Y motor drivers first, one at a time, if they run the firmware "
                     "QIDI's older package installed; otherwise they keep their firmware. The X and Y motors turn off, "
                     "so the printer homes again before its next print.")
    lines.append(f"The printer can't be used while it runs, {DURATION}. Don't turn the printer off. It continues on "
                 "its own if this session disconnects, and after a power loss it continues when the printer starts.")
    return lines


# ---- starting and following ----------------------------------------------------------------------------------------


def start_firmware_update(*, paths, reporter, manifest, kind, environ, admission=None, input_stream=None,
                          dry_run=False, follow=True, urlopen=urllib.request.urlopen,
                          mirror_urlopen=qidi_package.mirror_urlopen, run=subprocess.run, sleep=time.sleep,
                          clock=time.monotonic, disk_usage=shutil.disk_usage) -> FirmwareUpdateResult:
    """Start the firmware update this printer is waiting for, or report why it can't start.

    A firmware update already recorded is resumed or followed instead. NOT_NEEDED, NOT_STARTED, and DECLINED leave the
    printer as it was, so the caller installs Valve Oil for the current firmware; NOT_STARTED and DECLINED record the
    update as waiting. kind is the run's status-file kind: "install", "update_now", or "automatic".
    """
    if load_record(paths) is not None:
        result = resume_firmware_update(paths=paths, reporter=reporter, environ=environ, manifest=manifest,
                                        input_stream=input_stream, dry_run=dry_run, follow=follow, run=run,
                                        sleep=sleep, clock=clock, urlopen=urlopen)
        if result.state != NOT_NEEDED:
            return result
        # Another release's record, dropped as nothing of it was installed: plan this release's update.
    plan = firmware_update_plan(paths, manifest=manifest, admission=admission, urlopen=urlopen, disk_usage=disk_usage)
    if not plan.needed:
        return FirmwareUpdateResult(NOT_NEEDED)
    for line in describe_plan(plan):
        reporter.line(line)
    if dry_run:
        # A dry run stops nothing, so it can't read the boards an install reads before it decides.
        unread = admission is not None and any(status.kind != "box" and status.state == UNREAD
                                               for status in admission.boards)
        for blocker in plan.blockers:
            if blocker.code == "board_not_admitted" and unread:
                reporter.line("Firmware update dry-run: the install reads the THR and the mainboard first, which a "
                              "dry run doesn't, and starts the update only when this release supports both.")
            else:
                reporter.line(f"Firmware update dry-run: it wouldn't start now: {blocker.message}.")
        reporter.line("Firmware update dry-run: nothing was downloaded, stopped, or changed.")
        return FirmwareUpdateResult(NOT_STARTED if plan.blockers else DRY_RUN, blockers=plan.blockers)
    if plan.blockers:
        return _not_started(paths, reporter, plan, plan.blockers)
    if input_stream is not None:
        from .interaction import prompt_yes

        if not prompt_yes(reporter=reporter, input_stream=input_stream, question="Update the firmware now?",
                          instruction="Type Y/YES to update it, or N/NO to install Valve Oil for the current "
                                      "firmware without updating it."):
            reporter.line("The firmware update was not started; Valve Oil installs for the current firmware.")
            _publish_waiting(paths, plan, ())
            return FirmwareUpdateResult(DECLINED)
    # The run can't ask for a password, so it needs what automatic updates use. -k ignores credentials sudo cached
    # for this session, which the run won't have.
    password = PUBLIC_DEFAULT_SUDO_PASSWORD
    if run(sudo_command(["-k", "-v"]), input=password + "\n", text=True, stdout=subprocess.DEVNULL,
           stderr=subprocess.DEVNULL).returncode:
        return _not_started(paths, reporter, plan, (Blocker.of("sudo_password"),))
    printer = plan.printer
    try:
        reporter.line(f"Downloading QIDI firmware {plan.target} from {printer.target_firmware.url}")
        qidi_package.download(paths, printer.target_firmware, reporter=reporter, urlopen=mirror_urlopen)
    except (OSError, InstallerError) as exc:
        message = getattr(exc, "message", None) or f"the download failed: {exc}"
        reporter.line(message)
        return _not_started(paths, reporter, plan, (Blocker.of(getattr(exc, "code", "run_failed"), message),))
    if admission is not None:
        # The run reads the boards' admission from its record, as a new install's read is not recorded yet.
        record_board_admission(paths, admission, printer=printer)
    try:
        bundle = _settled_bundle(paths)
        _install_unit(paths, bundle, run=run, password=password)
    except (OSError, InstallerError) as exc:
        message = getattr(exc, "message", str(exc))
        return _not_started(paths, reporter, plan, (Blocker.of("run_failed", "the update could not be started: "
                                                                + message),))
    record = {
        "schema": SCHEMA, "id": uuid.uuid4().hex, "kind": kind if kind in valve_oil_status.RUN_KINDS else "install",
        "state": "queued", "printer": printer.id, "from_firmware": plan.installed, "target_firmware": plan.target,
        "release": manifest.package.version, "created_at": valve_oil_status.now(), "done": [], "attempts": 0,
    }
    if log_path(paths).is_file():
        atomic_write_text(log_path(paths), "", mode=0o600)  # one update's log at a time
    return _launch(paths, reporter, record, run=run, password=password, follow=follow, sleep=sleep, clock=clock,
                   plan=plan, new=True)


def resume_firmware_update(*, paths, reporter, environ, manifest=None, input_stream=None, dry_run=False,
                           follow=True, run=subprocess.run, sleep=time.sleep, clock=time.monotonic,
                           urlopen=urllib.request.urlopen) -> FirmwareUpdateResult:
    """Follow the firmware update that is running, or start its unit again to finish a recorded one.

    A record this release can't carry on is dropped when nothing of it was installed (NOT_NEEDED: plan afresh), and
    otherwise reported as FAILED with the command that finishes it; the installed bundle is left as it is."""
    record = load_record(paths)
    if record is None:
        return FirmwareUpdateResult(NOT_NEEDED)
    active = _unit_active(run)
    foreign = None if active else _foreign(record, manifest or load_manifest(paths.installer_root / "package.yaml"))
    if foreign is not None:
        if dry_run:
            reporter.line("Firmware update dry-run: a recorded update can't be continued by this release: "
                          f"{foreign[0]}. Nothing was changed.")
            return FirmwareUpdateResult(DRY_RUN)

        def start(units):
            password = authenticate_sudo(run=run, environ=environ, reporter=reporter, input_stream=input_stream)
            _start_units(units, run=run, password=password, reporter=reporter)

        return _settle_foreign(paths, reporter, record, *foreign, start=start)
    step = next((step for step in STEPS if step not in record.get("done", ())), "finish")
    reporter.line(f"A QIDI firmware update to {record.get('target_firmware')} is "
                  + ("running" if active else "waiting to finish")
                  + f"; it is at: {LABELS[step].format(target=record.get('target_firmware'))}.")
    if record.get("message"):
        reporter.line("Last attempt: " + record["message"])
    blocker = None if active else resume_blocker(paths)
    if blocker is not None:
        message = _not_continued(blocker)
        reporter.line(message)
        return FirmwareUpdateResult(DRY_RUN if dry_run else FAILED, message, (blocker,))
    if dry_run:
        reporter.line("Firmware update dry-run: it would be continued now. Nothing was changed.")
        return FirmwareUpdateResult(DRY_RUN)
    if not active and _changed(record) and step != "finish":
        # Continuing stops Klipper; the unit checks again, but say so before asking for sudo.
        busy = _busy(paths, urlopen=urlopen)
        if busy:
            message = (f"The firmware update was not continued now: {busy}. Run ./install.sh again once the printer "
                       "is idle.")
            reporter.line(message)
            return FirmwareUpdateResult(FAILED, message)
    if active:
        request = record.get("request") or record.get("current_request")
        return follow_firmware_update(paths, reporter, request=request, run=run, sleep=sleep, clock=clock,
                                      replay=True) if follow else FirmwareUpdateResult(RUNNING)
    try:
        password = authenticate_sudo(run=run, environ=environ, reporter=reporter, input_stream=input_stream)
        _install_unit(paths, _settled_bundle(paths), run=run, password=password)
    except (OSError, InstallerError) as exc:
        message = getattr(exc, "message", str(exc))
        reporter.line("The firmware update could not be continued: " + message)
        return FirmwareUpdateResult(FAILED, message)
    return _launch(paths, reporter, record, run=run, password=password, follow=follow, sleep=sleep, clock=clock)


def _launch(paths, reporter, record, *, run, password, follow, sleep, clock, plan=None,
            new=False) -> FirmwareUpdateResult:
    request = uuid.uuid4().hex
    log = log_path(paths)
    position = log.stat().st_size if log.is_file() else 0
    # The record is read again under its lock: the unit may have finished it since it was loaded, and an old copy must
    # never be written back.
    with _record_lock(paths):
        current = load_record(paths)
        if new and current is None:
            current = record
        elif new or current is None or current.get("id") != record.get("id"):
            result = load_result(paths)
            if not new and result is not None and result.get("id") == record.get("id"):
                return _report_result(paths, reporter, result)
            return FirmwareUpdateResult(FAILED, "Another firmware update was started meanwhile; run ./install.sh to "
                                                "follow it.")
        current["request"] = request
        _save(record_path(paths), current)
    reporter.line(f"Starting the firmware update ({UNIT}). Its log is {log}.")
    if run_sudo(["systemctl", "start", "--no-block", UNIT], run=run, password=password, timeout=60).returncode:
        with _record_lock(paths):
            current = load_record(paths)
            if current is not None and current.get("request") == request:
                if new:
                    atomic_delete(record_path(paths))
                else:
                    # Without its request a failed record isn't continued at the next boot.
                    current.pop("request")
                    _save(record_path(paths), current)
        message = f"{UNIT} could not be started"
        reporter.line(f"The firmware update did not start: {message}.")
        if plan is not None:
            return _not_started(paths, reporter, plan, (Blocker.of("run_failed", message),))
        return FirmwareUpdateResult(FAILED, message)
    if not follow:
        return FirmwareUpdateResult(RUNNING)
    return follow_firmware_update(paths, reporter, request=request, position=position, run=run, sleep=sleep,
                                  clock=clock)


def follow_firmware_update(paths, reporter, *, request, position=None, replay=False, run=subprocess.run,
                           sleep=time.sleep, clock=time.monotonic) -> FirmwareUpdateResult:
    """Print the run's log as it grows until the attempt `request` ends; its result is then marked reported."""
    log = log_path(paths)
    if position is None:
        position = max(0, log.stat().st_size - 4096) if replay and log.is_file() else (
            log.stat().st_size if log.is_file() else 0)
    stopped_since = None
    # The attempt this session started, and any the unit is running (another session may have started it first).
    attempts = {request} - {None}
    while True:
        position = _print_new_lines(log, position, reporter)
        record = load_record(paths)
        if record is not None and record.get("state") == "running" and record.get("current_request"):
            attempts.add(record["current_request"])
        result = load_result(paths)
        if result is not None and result.get("request") in attempts:
            _print_new_lines(log, position, reporter)
            return _report_result(paths, reporter, result)
        if _unit_active(run):
            stopped_since = None
        else:
            stopped_since = clock() if stopped_since is None else stopped_since
            if clock() - stopped_since >= FOLLOW_GRACE_SECONDS:
                record = load_record(paths)
                message = ("The firmware update stopped before it finished"
                           + (f" ({record.get('message')})" if record and record.get("message") else "")
                           + ". Run ./install.sh again to continue it.")
                reporter.line(message)
                return FirmwareUpdateResult(FAILED, message)
        sleep(1.0)


def report_or_attach(*, paths, reporter, environ, input_stream=None, dry_run=False, run=subprocess.run,
                     sleep=time.sleep, clock=time.monotonic, urlopen=urllib.request.urlopen) -> int | None:
    """At the start of a manual install: follow or continue a recorded firmware update, or report the result of one
    no session saw finish. Returns the install's exit code when it did one of those, else None."""
    if load_record(paths) is not None:
        result = resume_firmware_update(paths=paths, reporter=reporter, environ=environ, input_stream=input_stream,
                                        dry_run=dry_run, run=run, sleep=sleep, clock=clock, urlopen=urlopen)
        if result.state == NOT_NEEDED:
            return None  # another release's record, dropped: this install plans afresh
        return 0 if result.state in (SUCCEEDED, DRY_RUN) else 1
    result = load_result(paths)
    if result is None or result.get("kind") != "install" or result.get("reported"):
        return None
    reporter.line("A QIDI firmware update ran after the session that started it ended.")
    outcome = _report_result(paths, reporter, result, mark=not dry_run)
    if outcome.state == NOT_STARTED:
        return None  # nothing changed: install for the current firmware
    return 0 if outcome.state == SUCCEEDED else 1


def _report_result(paths, reporter, result: dict, *, mark: bool = True) -> FirmwareUpdateResult:
    state = result.get("state") if result.get("state") in (SUCCEEDED, FAILED, NOT_STARTED) else FAILED
    message = result.get("message") or ""
    reporter.line(("Firmware update finished: " if state == SUCCEEDED else
                   "Firmware update did not start: " if state == NOT_STARTED else
                   "Firmware update failed: ") + message)
    if mark and not result.get("reported"):
        _save(result_path(paths), {**result, "reported": True})
    blockers = tuple(Blocker.of(code) for code in result.get("blockers", ()) if isinstance(code, str))
    return FirmwareUpdateResult(state, message, blockers)


def _print_new_lines(log: Path, position: int, reporter) -> int:
    try:
        with log.open("rb") as handle:
            handle.seek(position)
            data = handle.read()
    except OSError:
        return position
    end = data.rfind(b"\n") + 1
    for line in data[:end].decode("utf-8", errors="replace").splitlines():
        reporter.line(line)
    return position + end


def _not_started(paths, reporter, plan, blockers) -> FirmwareUpdateResult:
    message = "The firmware update did not start: " + "; ".join(blocker.message for blocker in blockers) + "."
    reporter.line(message + " Nothing was changed; it is tried again at the next install or update check.")
    _publish_waiting(paths, plan, blockers)
    return FirmwareUpdateResult(NOT_STARTED, message, tuple(blockers))


def _publish_waiting(paths, plan, blockers) -> None:
    item = valve_oil_status.PendingItem(kind=valve_oil_status.FIRMWARE, version=plan.target, current=plan.installed,
                                        blocked_by=tuple(blockers))
    valve_oil_status.update_status(paths, lambda status: replace(status, pending=tuple(
        entry for entry in status.pending if entry.kind != valve_oil_status.FIRMWARE) + (item,)))


def _changed(record: dict) -> bool:
    """Whether the printer changed: a step from stop_services on is done, or a driver was sent an image."""
    return (any(step in record.get("done", ()) for step in STEPS[FIRST_CHANGE:])
            or bool((record.get("closed_loop_state") or {}).get("sent")))


def _busy(paths, *, urlopen) -> str | None:
    """Why stopping Klipper now would interrupt the printer: a print, or a heater with a target. Without a reachable or
    running Klipper there is nothing to interrupt, as with a mainboard the new Klipper can't use."""
    try:
        safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen, wait_seconds=0)
    except HeaterOnError:
        return valve_oil_status.BLOCKERS["heaters_on"]
    except ActivePrintError:
        return valve_oil_status.BLOCKERS["printing"]
    except PrinterStateError:
        return None
    return None


def _unit_active(run) -> bool:
    try:
        result = run(["systemctl", "is-active", UNIT], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return (result.stdout or "").strip() in ("active", "activating", "deactivating", "reloading")


# ---- the unit -------------------------------------------------------------------------------------------------------


def unit_text(paths, bundle: Path) -> str:
    python = sys.executable or "/usr/bin/python3"
    return (UNIT_MARKER
            + "# Runs Valve Oil's QIDI firmware update, and continues it at boot after a power loss. Installed by "
            "Valve Oil.\n"
            "[Unit]\n"
            "Description=Valve Oil QIDI firmware update\n"
            "After=local-fs.target systemd-udev-trigger.service\n"
            f"ConditionPathExists={record_path(paths)}\n\n"
            "[Service]\n"
            "Type=exec\n"
            f"User={current_user()}\n"
            f"WorkingDirectory={bundle}\n"
            f"Environment=TLTG_OPTIMIZED_PRINTER_DATA_ROOT={paths.printer_data_root}\n"
            f"ExecStart={python} -I -S {bundle / 'installer/runtime/bootstrap.py'} firmware-update --plain\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n")


def _install_unit(paths, bundle: Path, *, run, password: str) -> None:
    text = unit_text(paths, bundle)
    target = SYSTEMD_DIR / UNIT
    try:
        current = target.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current is not None and not current.startswith(UNIT_MARKER):
        raise InstallerError(f"Existing {target} is unrecognized and was preserved.")
    if current != text:
        staged = qidi_package.cache_dir(paths) / UNIT
        staged.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write_text(staged, text, mode=0o644)
        for command in (["install", "-m", "0644", str(staged), str(target)], ["systemctl", "daemon-reload"]):
            if run_sudo(command, run=run, password=password, timeout=60).returncode:
                raise InstallerError(f"Could not install {target}.")
        staged.unlink()
    if run_sudo(["systemctl", "enable", UNIT], run=run, password=password, timeout=60).returncode:
        raise InstallerError(f"Could not enable {UNIT}.")


def remove_unit(*, run, password: str) -> None:
    """Uninstall: disable and remove the unit; it runs nothing without a pending record."""
    target = SYSTEMD_DIR / UNIT
    if not target.exists():
        return
    try:
        if not target.read_text(encoding="utf-8").startswith(UNIT_MARKER):
            return
    except OSError:
        return
    for command in (["systemctl", "disable", UNIT], ["rm", "-f", str(target)], ["systemctl", "daemon-reload"]):
        run_sudo(command, run=run, password=password, timeout=60)


def _settled_bundle(paths) -> Path:
    """The bundle the unit runs: the installed one in the printer user's home, which QIDI's package leaves alone. A
    bundle run from anywhere else is copied there first."""
    from .bootstrap import copy_bundle
    import pwd

    home = qidi_package.home(paths)
    target = home / BUNDLE_ROOT_NAME
    if paths.bundle_root.resolve() != target.resolve():
        copy_bundle(paths.bundle_root, target, pwd.getpwuid(os.getuid()))
    return target


# ---- the run, in tltg-firmware-update.service -----------------------------------------------------------------------


class _NotStarted(Exception):
    """Nothing changed; the run ends with the record removed."""

    def __init__(self, message: str, code: str = "run_failed"):
        super().__init__(message)
        self.message, self.code = message, code


class _Failed(Exception):
    """The run stops after changing the printer: start `start`, keep the record (to finish later) or remove it."""

    def __init__(self, message: str, *, keep: bool, start: tuple[str, ...], screen: bool):
        super().__init__(message)
        self.message, self.keep, self.start, self.screen = message, keep, start, screen


class _Run:
    def __init__(self, *, paths, reporter, environ, manifest, printer, record, run, urlopen, mirror_urlopen, sleep,
                 clock, deliver, draw, disk_usage, log, resolve_usb, sync, klipper):
        self.paths, self.reporter, self.environ, self.manifest = paths, reporter, environ, manifest
        self.printer, self.target, self.record = printer, printer.target_firmware, record
        self.run, self.urlopen, self.mirror_urlopen, self.sleep, self.clock = run, urlopen, mirror_urlopen, sleep, clock
        self.deliver, self.draw, self.disk_usage, self.log = deliver, draw, disk_usage, log
        self.resolve_usb, self.sync = resolve_usb, sync
        # Klipper's API socket, for the closed-loop step: klipper(paths, run=...) -> KlipperAPI.
        self.klipper = klipper or (lambda paths, run: KlipperAPI(klipper_api_path(paths, run=run)))
        self._password = None

    @property
    def password(self) -> str:
        if self._password is None:
            try:
                self._password = authenticate_sudo(run=self.run, environ=self.environ, reporter=self.reporter,
                                                   input_stream=None)
            except SudoError as exc:
                raise InstallerError("sudo needs a password the update can't ask for: " + exc.message) from exc
        return self._password

    def save(self) -> None:
        # The unit's copy is the record while it runs; a request a session adds meanwhile is not kept, so a run that
        # then ends failed doesn't continue at the next boot (that session follows this attempt instead).
        with _record_lock(self.paths):
            _save(record_path(self.paths), self.record)

    def show(self, lines) -> None:
        """Put a message on the touchscreen, lit: QIDI's app leaves a sleeping panel's backlight off when stopped."""
        self.draw(lines, reporter=self.reporter)
        update_screen.light(self.sudo, reporter=self.reporter)

    def sudo(self, command, *, timeout=120, output=False):
        kwargs = {"stdout": self.log, "stderr": subprocess.STDOUT} if output and self.log is not None else {}
        return run_sudo(command, run=self.run, password=self.password, timeout=timeout, **kwargs)

    def systemctl(self, action: str, units, *, strict=True) -> None:
        for unit in units:
            if self.sudo(["systemctl", action, unit], timeout=180).returncode and strict:
                raise InstallerError(f"Could not {action} {unit}.")

    def start_services(self, units) -> None:
        """Start what a stop or a failure leaves stopped, in dependency order; a unit that won't start is logged."""
        order = ("moonraker.service", "klipper.service", TOUCHSCREEN, TIMER_NAME)
        was = self.record.get("services") or {}
        for unit in order:
            if unit in units and (unit != TIMER_NAME or was.get(unit) == "active"):
                try:
                    self.systemctl("start", (unit,))
                except InstallerError as exc:
                    self.reporter.line(f"  ({exc.message})")

    def remove_tree(self, path: Path) -> None:
        allowed = {qidi_package.staging_dir(self.paths),
                   qidi_package.update_dir(self.paths).parent / qidi_package.PREVIOUS_DIRECTORY}
        if path not in allowed:
            raise InstallerError(f"Refusing to remove {path}.")
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return
        except PermissionError:
            # QIDI's updater unpacks its update folder as root.
            if self.sudo(["rm", "-rf", "--one-file-system", str(path)], timeout=600).returncode:
                raise InstallerError(f"Could not remove {path}.")

    def remove_file(self, path: Path) -> None:
        if path.parent != qidi_package.update_dir(self.paths):
            raise InstallerError(f"Refusing to remove {path}.")
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except PermissionError:
            if self.sudo(["rm", "-f", str(path)]).returncode:
                raise InstallerError(f"Could not remove {path}.")

    def changed(self) -> bool:
        return _changed(self.record)


def run_firmware_update(*, paths, reporter, environ, manifest=None, run=subprocess.run,
                        urlopen=urllib.request.urlopen, mirror_urlopen=qidi_package.mirror_urlopen, sleep=time.sleep,
                        clock=time.monotonic, deliver=None, draw=update_screen.draw, disk_usage=shutil.disk_usage,
                        log=None, resolve_usb=usb_path_for_serial, sync=os.sync, klipper=None) -> int:
    """The unit's run: carry the recorded firmware update to its end from its first undone step. Every way out writes
    a result; an error in the run's own failure handling still brings Moonraker back and records the failure.

    A failure after the printer changed keeps the record with state "failed". Started at boot, the unit leaves such a
    record alone; it is continued when ./install.sh or Update now asks (a session's request in the record), and while
    it exists the update check starts no other firmware update."""
    try:
        return _run_update(paths=paths, reporter=reporter, environ=environ, manifest=manifest, run=run, urlopen=urlopen,
                           mirror_urlopen=mirror_urlopen, sleep=sleep, clock=clock, deliver=deliver, draw=draw,
                           disk_usage=disk_usage, log=log, resolve_usb=resolve_usb, sync=sync, klipper=klipper)
    except Exception as exc:  # noqa: BLE001 - the last resort
        return _last_resort(paths, reporter=reporter, environ=environ, run=run, draw=draw, error=exc)


def _run_update(*, paths, reporter, environ, manifest, run, urlopen, mirror_urlopen, sleep, clock, deliver, draw,
                disk_usage, log, resolve_usb, sync, klipper) -> int:
    with _record_lock(paths):
        record = load_record(paths)
        if record is None:
            return 0
        request = record.pop("request", None)
        if request is not None:
            _save(record_path(paths), record)
    boot = request is None
    if boot and record.get("state") == "failed":
        # Started at boot: a recorded failure waits for ./install.sh or Update now.
        reporter.line("The last firmware update failed; run ./install.sh to continue it.")
        return 0
    manifest = manifest or load_manifest(paths.installer_root / "package.yaml")
    foreign = _foreign(record, manifest)
    if foreign is not None:
        def start(units):
            _start_units(units, run=run, password=authenticate_sudo(run=run, environ=environ, reporter=reporter,
                                                                     input_stream=None), reporter=reporter)

        result = _settle_foreign(paths, reporter, record, *foreign, start=start)
        return 0 if result.state == NOT_NEEDED else 1
    printer = next(printer for printer in manifest.printers if printer.id == record.get("printer"))
    first = next((step for step in STEPS if step not in record["done"]), "finish")
    record["current_request"] = request or record.get("current_request")
    blocker = None if boot else resume_blocker(paths)
    if blocker is not None:
        # Asked to continue while a board still waits for its rescue: nothing is stopped. (A boot after a power loss
        # mid-flash goes on to the boards step, which ends in that board's rescue state with its services stopped.)
        message = _not_continued(blocker)
        reporter.line(message)
        _write_result(paths, record, state=FAILED, message=message, blockers=(blocker.code,))
        return 1
    if not boot and _changed(record) and first != "finish":
        busy = _busy(paths, urlopen=urlopen)
        if busy:
            message = (f"The firmware update was not continued: {busy}. Run ./install.sh again once the printer is "
                       "idle.")
            reporter.line(message)
            _write_result(paths, record, state=FAILED, message=message)
            return 1
    record["boot_attempts"] = int(record.get("boot_attempts", 0)) + 1 if boot else 0
    if record["boot_attempts"] > MAX_BOOT_ATTEMPTS:
        message = (f"The firmware update stopped continuing at boot after {MAX_BOOT_ATTEMPTS} attempts at: "
                   f"{LABELS[first].format(target=record.get('target_firmware'))}. Run ./install.sh to continue it.")
        record.update(state="failed", message=message, failed_step=first)
        with _record_lock(paths):
            _save(record_path(paths), record)
        reporter.line(message)
        _write_result(paths, record, state=FAILED, message=message)
        return 1
    os.environ[safety.FIRMWARE_UPDATE_RUN_ENV] = record["id"]
    record.update(state="running", current_request=record["current_request"] or uuid.uuid4().hex,
                  attempts=int(record.get("attempts", 0)) + 1, message="")
    if deliver is None:
        from .board_firmware import deliver_board_firmware as deliver
    ctx = _Run(paths=paths, reporter=reporter, environ=environ, manifest=manifest, printer=printer, record=record,
               run=run, urlopen=urlopen, mirror_urlopen=mirror_urlopen, sleep=sleep, clock=clock, deliver=deliver,
               draw=draw, disk_usage=disk_usage, log=log, resolve_usb=resolve_usb, sync=sync, klipper=klipper)
    ctx.save()
    previous_run = valve_oil_status.load_status(paths).run
    status_run = valve_oil_status.begin_run(paths, kind=record["kind"], step=first, resumed=record["attempts"] > 1,
                                            step_label=LABELS[first].format(target=ctx.target.version),
                                            firmware_update=record["id"])
    reporter.line(f"Valve Oil firmware update to QIDI firmware {ctx.target.version}"
                  + (f", continuing (attempt {record['attempts']})" if record["attempts"] > 1 else "") + ".")
    step = first
    # Continuing after the printer changed: boot, or a failure's recovery, started the services again. Stop them
    # before anything else, without waiting for Klipper, which may not be able to start (a mainboard that still runs
    # an earlier version than the new Klipper needs).
    restop = ctx.changed() and first not in ("closed_loop", "finish")
    try:
        for step in STEPS:
            if step in record["done"]:
                continue
            label = LABELS[step].format(target=ctx.target.version)
            valve_oil_status.set_run_step(paths, status_run.id, step=step, step_label=label)
            if restop:
                restop = False
                reporter.line(LABELS["stop_services"] + "...")
                _stop_services(ctx, check=False)
            reporter.line(label + "...")
            _STEP_FUNCTIONS[step](ctx)
            record["done"].append(step)
            ctx.save()
    except _NotStarted as stop:
        return _end_not_started(ctx, status_run, stop, previous_run)
    except _Failed as failure:
        return _end_failed(ctx, status_run, step, failure)
    except Exception as exc:  # noqa: BLE001 - every failure ends in a recorded state
        message = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
        if not ctx.changed():
            if step in ("closed_loop", "stop_services"):
                ctx.start_services(QIDI_SERVICES + (TIMER_NAME,))
            return _end_not_started(ctx, status_run, _NotStarted(message), previous_run)
        return _end_failed(ctx, status_run, step, _failure_at(ctx, step, message))
    return _end_succeeded(ctx, status_run)


def _last_resort(paths, *, reporter, environ, run, draw, error) -> int:
    """The run's own failure handling failed: record the failure, bring back Moonraker so Fluidd shows it, and say on
    the screen that the update didn't finish. Each part is tried on its own."""
    message = (f"The firmware update stopped on an unexpected error ({type(error).__name__}: {error}). Moonraker was "
               "started again; run ./install.sh to continue the update.")
    record = None
    for attempt in (
        lambda: reporter.line(message),
        lambda: _mark_failed(paths, message),
        lambda: _write_result(paths, load_record(paths) or {}, state=FAILED, message=message),
        lambda: run_sudo(["systemctl", "start", "moonraker.service"], run=run, timeout=180, password=authenticate_sudo(
            run=run, environ=environ, reporter=reporter, input_stream=None)),
        lambda: valve_oil_status.update_status(paths, lambda status: replace(status, run=replace(
            status.run, state=valve_oil_status.FAILED, message=message, finished_at=valve_oil_status.now()))
            if status.run is not None and status.run.state == valve_oil_status.RUNNING
            and status.run.pid == os.getpid() else status),
    ):
        try:
            attempt()
        except Exception:  # noqa: BLE001
            pass
    try:
        record = load_record(paths)
        if record is not None and "stop_services" in record.get("done", ()) and "finish" not in record.get("done", ()):
            draw(update_screen.STOPPED, reporter=reporter)
            update_screen.light(lambda command: run_sudo(command, run=run, timeout=60, password=authenticate_sudo(
                run=run, environ=environ, reporter=reporter, input_stream=None)), reporter=reporter)
    except Exception:  # noqa: BLE001
        pass
    return 1


def _mark_failed(paths, message: str) -> None:
    with _record_lock(paths):
        record = load_record(paths)
        if record is not None:
            record.pop("request", None)
            record.update(state="failed", message=message)
            _save(record_path(paths), record)


def _failure_at(ctx, step: str, message: str) -> _Failed:
    target = ctx.target.version
    if step == "closed_loop":
        return _closed_loop_failure(ctx, message)
    if step in ("soc", "update_folder"):
        return _Failed(f"Installing QIDI firmware {target} failed ({message}). Klipper and Moonraker were started "
                       "again; run ./install.sh to try again.", keep=True, start=KLIPPER_AND_MOONRAKER, screen=True)
    if step == "boards":
        return _board_failure(ctx, message)
    # install: QIDI's part is done, so the record goes and the installer's own recovery applies.
    return _Failed(f"QIDI firmware {target} and the board firmware are installed, but installing Valve Oil for it "
                   f"failed ({message}). Run ./install.sh again.", keep=False, start=QIDI_SERVICES + (TIMER_NAME,),
                   screen=False)


def _board_failure(ctx, message: str) -> _Failed:
    root = ctx.paths.printer_data_root
    rescue = {safety.THR_RECOVERY_MARKER: "recover and verify the THR as its recovery record describes",
              safety.MAINBOARD_RECOVERY_MARKER: "run ./mainboard-firmware.sh rescue",
              safety.BOX_RECOVERY_MARKER: "run ./box-firmware.sh rescue"}
    pending = [text for marker, text in rescue.items() if (root / marker).exists()]
    if pending:
        # The board may hold a partial image: the services that use it stay stopped (the board scripts' rule);
        # Moonraker serves Fluidd, which shows this.
        return _Failed(f"A board firmware write could not be verified ({message}). Don't turn the printer off; "
                       f"{'; '.join(pending)}, then run ./install.sh to finish the update.", keep=True,
                       start=("moonraker.service",), screen=True)
    stranded = _stranded_mainboard(ctx, message)
    if stranded is not None:
        return stranded
    return _Failed(f"The board firmware could not be updated ({message}); no board was written. Klipper and "
                   "Moonraker were started again; run ./install.sh to try again.", keep=True,
                   start=KLIPPER_AND_MOONRAKER, screen=True)


def _stranded_mainboard(ctx, detail: str, *, outcome: str | None = None) -> _Failed | None:
    """The failure when the mainboard wasn't flashed and Klipper can't use it: it isn't found on USB, or it runs an
    earlier version than the new Klipper needs. In the second case the touchscreen comes back too, since QIDI's own
    board update from its firmware page can still fix the mainboard."""
    from scripts.build_box_firmware import board_spec, predecessor_versions
    from .board_images import load_board_manifest

    if ctx.resolve_usb(mainboard_serial(ctx.paths)) is None:
        return _Failed(f"The mainboard was not found on USB, so its firmware wasn't updated ({detail}). Klipper and "
                       "Moonraker were started again; check that the printer is fully powered, then run ./install.sh "
                       "to finish the firmware update.", keep=True, start=KLIPPER_AND_MOONRAKER, screen=True)
    admission = load_board_admission(ctx.paths)
    status = admission.board("mcu") if admission is not None else None
    version = status.version if status is not None else None
    if outcome != "waiting" and version is not None and version not in predecessor_versions(
            board_spec(load_board_manifest(ctx.paths.bundle_root, "mcu"))):
        return None
    return _Failed(
        f"The mainboard still runs firmware {version or 'of an unknown version'}, which the Klipper in QIDI firmware "
        f"{ctx.target.version} can't use, and it could not be updated ({detail}). Klipper, Moonraker, and the "
        "touchscreen were started again. Update the mainboard from the touchscreen's firmware page, then run "
        "./install.sh to finish the firmware update.", keep=True, start=QIDI_SERVICES + (TIMER_NAME,), screen=False)


# Steps.


def _check(ctx: _Run) -> None:
    if ctx.changed():
        return
    installed = detect_firmware_version_best_effort(ctx.paths.firmware_manifest_path)
    if installed != ctx.record["from_firmware"] or not ctx.target.can_update_from(installed or ""):
        raise _NotStarted(f"the printer's firmware is now {installed}, not {ctx.record['from_firmware']}",
                          "run_failed")
    for blocker in printer_blockers(ctx.paths, urlopen=ctx.urlopen, wait_seconds=READY_WAIT_SECONDS):
        raise _NotStarted(blocker.message, blocker.code)
    admission = load_board_admission(ctx.paths)
    if admission is None or not admission.delivery_admitted:
        raise _NotStarted(valve_oil_status.BLOCKERS["board_not_admitted"], "board_not_admitted")
    for blocker in _deliverability_blockers(ctx.paths, ctx.printer, admission):
        raise _NotStarted(blocker.message, blocker.code)
    recovery = recovery_blocker(ctx.paths)
    if recovery is not None:
        raise _NotStarted(recovery.message, recovery.code)
    try:
        qidi_package.check_free_space(ctx.paths, ctx.target, disk_usage=ctx.disk_usage)
    except qidi_package.PackageError as exc:
        raise _NotStarted(exc.message, exc.code) from exc


def _stage(ctx: _Run):
    archive = qidi_package.archive_path(ctx.paths, ctx.target)
    try:
        staged = qidi_package.staged(ctx.paths, ctx.target, ctx.printer)
        if staged is None:
            if not qidi_package.cached(ctx.paths, ctx.target):
                # Gone since the update started, as after a lost cache: fetch and verify it again.
                ctx.reporter.line(f"Downloading QIDI firmware {ctx.target.version} again...")
                qidi_package.download(ctx.paths, ctx.target, reporter=ctx.reporter, urlopen=ctx.mirror_urlopen)
            staged = qidi_package.stage(ctx.paths, ctx.target, ctx.printer, archive=archive,
                                        remove_tree=ctx.remove_tree)
        fields = qidi_package.deb_fields(staged.soc, run=ctx.run)
    except (OSError, InstallerError) as exc:
        message = getattr(exc, "message", None) or f"the package could not be unpacked: {exc}"
        if ctx.changed():
            raise InstallerError(message) from exc
        raise _NotStarted(message, getattr(exc, "code", qidi_package.UNVERIFIED)) from exc
    expected = {"Package": f"qd-{ctx.printer.id}-system", "Version": ctx.target.version, "Architecture": "arm64"}
    if any(fields.get(key) != value for key, value in expected.items()):
        message = f"QIDI's SOC package is {fields}, not {expected}"
        if ctx.changed():
            raise InstallerError(message)
        raise _NotStarted(message, qidi_package.UNVERIFIED)
    ctx.record["package"] = fields["Package"]
    return staged


def _closed_loop(ctx: _Run) -> None:
    """Max 4: the closed-loop drivers, while the old Klipper runs (closed_loop_firmware.py, design D13). Klipper is
    the only service left running, and the run its only client, through its API socket."""
    target = ctx.target.board_version("closed_loop")
    previous = tuple(ctx.target.boards_for(ctx.record["from_firmware"]).get("closed_loop") or ())
    pin = ctx.target.closed_loop_image
    if not target or not ctx.target.changes_board("closed_loop", ctx.record["from_firmware"]) or pin is None:
        ctx.record["closed_loop"] = {"changes": False, "target": target, "outcome": "unchanged"}
        return
    component = _stage(ctx).component("CLOSED_LOOP_MOTOR")
    if component is None or component[1] != target:
        raise InstallerError(f"QIDI's package has no closed-loop driver image {target}")
    _isolate(ctx)
    api = ctx.klipper(ctx.paths, run=ctx.run)
    state = ctx.record.setdefault("closed_loop_state", {})
    try:
        if not api.wait_ready(sleep=ctx.sleep, clock=ctx.clock, seconds=READY_WAIT_SECONDS):
            if not state.get("sent"):
                raise InstallerError("Klipper didn't become ready")  # nothing sent: everything starts again
            # Continuing after a driver was sent an image: a reset may bring Klipper back; fail either way.
            reset = _reset_bus(ctx, api)
            raise _closed_loop_failure(ctx, "Klipper didn't become ready to continue the motor driver update",
                                       reset=reset)
        blocker = closed_loop_firmware.idle_blocker(api)
        if blocker is not None:
            message = valve_oil_status.BLOCKERS.get(blocker, "Klipper is busy running a command")
            if not ctx.changed():
                # A print or a heater started before Moonraker stopped: put back what was stopped.
                ctx.start_services((TOUCHSCREEN, "moonraker.service", TIMER_NAME))
                ctx.record["isolated"] = False
                raise _NotStarted(message, blocker)
            raise InstallerError(f"the motor driver update was not continued: {message}")
        try:
            # Before any driver was sent an image, a problem here is raised as it is, and the generic handler starts
            # the services again with nothing changed; after one, update_drivers has sent FIRMWARE_RESTART.
            result = closed_loop_firmware.update_drivers(
                api=api, image=component[0], size=pin.size, sha256=pin.sha256, target=target, previous=previous,
                state=state, attempt=ctx.record["current_request"], save=ctx.save, reporter=ctx.reporter,
                sleep=ctx.sleep, clock=ctx.clock)
        except DriverUpdateFailed as exc:
            reset = exc.reset or _reset_bus(ctx, api)
            raise _closed_loop_failure(ctx, exc.message, reset=reset) from exc
    finally:
        api.close()
    behind = [driver["axis"].upper() for driver in result.drivers if driver.get("version") != target]
    if behind:
        # Not admitted, or Klipper refused a driver before sending it anything: say which keep their firmware.
        result.detail = (f"{result.detail}. Valve Oil sent no firmware to the {' and '.join(behind)} closed-loop "
                         f"motor driver{'s' if len(behind) > 1 else ''}, so "
                         f"{'they keep' if len(behind) > 1 else 'it keeps'} the firmware it has; QIDI firmware "
                         f"{ctx.target.version} carries driver firmware {target}. Update the motor drivers from the "
                         "touchscreen's firmware page.")
        ctx.reporter.line("  " + result.detail)
        result_issue = _closed_loop_issue_url(ctx, result.detail)
        ctx.reporter.line("  If they should have been updated, open a support issue: " + result_issue)
        ctx.record["closed_loop_issue_url"] = result_issue
    ctx.record["closed_loop"] = result.to_dict()
    _record_closed_loop(ctx, ctx.record["closed_loop"])


def _isolate(ctx: _Run) -> None:
    """Stop everything that could send Klipper a command: the touchscreen, Moonraker, and the update timer. Klipper
    keeps running for the drivers; the later stop_services step only has Klipper left to stop."""
    _record_services(ctx)
    if ctx.record["services"].get(TIMER_NAME) == "active":
        ctx.systemctl("stop", (TIMER_NAME,), strict=False)
    ctx.systemctl("stop", (TOUCHSCREEN, "moonraker.service"))
    ctx.record["isolated"] = True
    ctx.save()
    ctx.show(update_screen.UPDATING)


def _record_services(ctx: _Run) -> None:
    if "services" in ctx.record:
        return
    states = {}
    for unit in (*QIDI_SERVICES, TIMER_NAME):
        result = ctx.run(["systemctl", "show", unit, "--property=ActiveState"], capture_output=True, text=True,
                         timeout=10)
        states[unit] = (result.stdout or "").strip().partition("=")[2] or "unknown"
    ctx.record["services"] = states
    # Where the mainboard sits on USB, while its configured name still finds it (see _find_mainboard).
    ctx.record["mainboard_usb"] = ctx.resolve_usb(mainboard_serial(ctx.paths))
    ctx.save()


def _reset_bus(ctx: _Run, api: KlipperAPI) -> bool:
    """FIRMWARE_RESTART through Klipper's API socket, reconnecting first; when Klipper doesn't take it, restart
    Klipper's service, which doesn't reset the mainboard by itself, and send it again."""
    if api.firmware_restart(sleep=ctx.sleep, clock=ctx.clock):
        return True
    ctx.reporter.line("  Klipper didn't take FIRMWARE_RESTART; restarting Klipper to send it again.")
    ctx.systemctl("restart", ("klipper.service",), strict=False)
    return api.firmware_restart(sleep=ctx.sleep, clock=ctx.clock, seconds=closed_loop_firmware.READY_SECONDS)


def _closed_loop_failure(ctx: _Run, message: str, *, reset: bool = True) -> _Failed:
    """A driver the run sent an image could not be verified: the bus was reset, the other driver is untouched, and
    QIDI's package is not installed. Everything is started again; the next install or Update now retries."""
    url = _closed_loop_issue_url(ctx, message)
    target = ctx.target.board_version("closed_loop")
    valve_oil_status.record_boards(ctx.paths, (valve_oil_status.BoardStatus(
        id="closed_loop", state=valve_oil_status.NEEDS_IMAGE, pinned=target, issue_url=url,
        detail=message[:1].upper() + message[1:] + ". Run the Valve Oil installer again to retry, or update the "
        "motor drivers from the touchscreen's firmware page.", checked_at=valve_oil_status.now()),))
    ctx.reporter.line("  If it keeps failing, open a support issue: " + url)
    power = "" if reset else (" Valve Oil couldn't reset the motor drivers' connection: turn the printer off and on "
                              "again before using it.")
    return _Failed(f"The closed-loop motor driver update failed: {message}. QIDI firmware {ctx.target.version} was "
                   "not installed, and everything was started again." + power + " Run ./install.sh again to retry, "
                   "or update the motor drivers from the touchscreen's firmware page; the Valve Oil section links a "
                   "support issue.",
                   keep=True, start=QIDI_SERVICES + (TIMER_NAME,), screen=False)


def _closed_loop_issue_url(ctx: _Run, message: str) -> str:
    from .board_firmware import issue_link, printer_details

    try:
        return issue_link(printer_name=ctx.printer.display_name, firmware=ctx.record["from_firmware"], box_count=0,
                          details=printer_details(ctx.paths), output="Closed-loop motor driver update: " + message,
                          report=None, report_path=None)[0]
    except Exception:  # noqa: BLE001 - the link is a convenience
        from .board_firmware import ISSUE_FORM
        return ISSUE_FORM


def _stop_services(ctx: _Run, *, check: bool = True) -> None:
    if check and not ctx.record.get("isolated"):
        # A print may have started while the package was unpacked.
        for blocker in printer_blockers(ctx.paths, urlopen=ctx.urlopen):
            raise _NotStarted(blocker.message, blocker.code)
    elif check and not ctx.changed():
        # Moonraker is stopped already and nothing else can reach Klipper; ask Klipper itself.
        blocker = _klipper_idle_blocker(ctx)
        if blocker is not None:
            ctx.start_services((TOUCHSCREEN, "moonraker.service", TIMER_NAME))
            ctx.record["isolated"] = False
            raise _NotStarted(valve_oil_status.BLOCKERS.get(blocker, "Klipper is busy"), blocker)
    _record_services(ctx)
    if ctx.record["services"].get(TIMER_NAME) == "active":
        ctx.systemctl("stop", (TIMER_NAME,), strict=False)
    ctx.systemctl("stop", QIDI_SERVICES)
    ctx.show(update_screen.UPDATING)


def _klipper_idle_blocker(ctx: _Run) -> str | None:
    api = ctx.klipper(ctx.paths, run=ctx.run)
    try:
        if api.wait_ready(sleep=ctx.sleep, clock=ctx.clock, seconds=10):
            return closed_loop_firmware.idle_blocker(api)
        return "printer_state_unknown"
    finally:
        api.close()


def _dpkg(ctx: _Run, arguments: list[str]):
    """dpkg as root, with no time limit: stopping it partway would leave the printer worse off than waiting. Another
    program holding dpkg's lock is waited for, up to DPKG_LOCK_WAIT_SECONDS."""
    deadline = ctx.clock() + DPKG_LOCK_WAIT_SECONDS
    while True:
        result = run_sudo(["dpkg", *arguments], run=ctx.run, password=ctx.password, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)
        output = result.stdout or ""
        for line in output.splitlines():
            ctx.reporter.line("  " + line)
        if result.returncode and "locked by another process" in output and ctx.clock() < deadline:
            ctx.reporter.line("  Another program is using dpkg; waiting for it to finish...")
            ctx.sleep(10)
            continue
        return result


def _soc(ctx: _Run) -> None:
    staged = _stage(ctx)
    try:
        qidi_package.check_free_space(ctx.paths, ctx.target, disk_usage=ctx.disk_usage, unpacked=True)
    except qidi_package.PackageError as exc:
        raise InstallerError(exc.message) from exc
    # The touchscreen's own board update installs the SOC package in QIDI's update folder when it isn't the app's
    # version. Until the folder holds the new package, it holds no SOC package at all.
    removed = qidi_package.remove_installable_soc(ctx.paths, remove_file=ctx.remove_file)
    if removed:
        ctx.reporter.line("  Removed the installed firmware's SOC package from QIDI's update folder.")
    if not ctx.record.get("soc_attempted") and "calibration" not in ctx.record:
        # Captured once, before dpkg first runs: a later attempt would find QIDI's printer.cfg in its place.
        try:
            ctx.record["calibration"] = saved_calibration.capture(ctx.paths.config_root)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            ctx.record["calibration"] = {"error": f"the saved calibration could not be read ({exc})"}
        # QIDI's package deletes Valve Oil's state file with the config folder. The install after it gets that
        # state's system ledger from here, so uninstall can still undo what the earlier install changed on the host.
        try:
            save_carried_system_ledger(ctx.paths, ctx.paths.printer_data_root / ctx.manifest.state_file, ctx.record["id"])
        except OSError as exc:
            ctx.reporter.line(f"  (The record of Valve Oil's system changes could not be kept across the update: {exc})")
    if ctx.record.get("soc_attempted"):
        # An earlier attempt stopped inside dpkg: let dpkg settle its database before installing the package again.
        _dpkg(ctx, ["--configure", "-a"])
    elif not ctx.record.get("config_backup"):
        # QIDI's package deletes the config folder except six files; keep a restorable copy of what it deletes.
        try:
            label = build_install_backup_label(label_prefix=ctx.manifest.backup.label_prefix,
                                               firmware_version=ctx.record["from_firmware"],
                                               package_version=ctx.manifest.package.version, moment=utc_now())
            ctx.record["config_backup"] = str(create_config_backup(
                printer_data_root=ctx.paths.printer_data_root, source_directory=ctx.manifest.backup.source_directory,
                backup_label=label))
        except (OSError, ValueError, InstallerError) as exc:
            ctx.reporter.line(f"  (The config folder could not be backed up first: {exc})")
    ctx.record["soc_attempted"] = True
    ctx.save()
    ctx.show(update_screen.UPDATING)
    result = _dpkg(ctx, ["-i", "--force-overwrite", str(staged.soc)])
    ctx.sync()
    query = ctx.run(["dpkg-query", "-W", "-f=${Status}\t${Version}", ctx.record["package"]], capture_output=True,
                    text=True, timeout=60)
    installed = (query.stdout or "").strip()
    if result.returncode or installed != f"install ok installed\t{ctx.target.version}":
        raise InstallerError(f"dpkg exited with {result.returncode} and the package is '{installed}'")
    # A Klipper restart the installer was waiting for applied to Klipper files QIDI's package has just replaced, so
    # it can no longer be verified; the installer run below patches the new files and asks for its own restart.
    if ctx.paths.restart_marker_path.exists():
        atomic_delete(ctx.paths.restart_marker_path)
        ctx.reporter.line("  Cleared the pending Klipper restart for the replaced Klipper files.")
    _restore_calibration(ctx)


def _restore_calibration(ctx: _Run) -> None:
    """Put the operator's saved calibration back into the config QIDI's package installed, before the boards step
    (which records the saved input shaper calibration a THR flash invalidates) and the installer run read it. Done
    after every dpkg run, since each one replaces printer.cfg again; never fails the update."""
    captured = ctx.record.get("calibration") or {}
    backup = ctx.record.get("config_backup")
    where = f" The config folder from before the update is in {backup}." if backup else ""
    if captured.get("error"):
        ctx.record["calibration_result"] = {"summary": f"Not restored: {captured['error']}.{where}"}
    else:
        protected = {(patch.section, patch.option.lower()) for patch in ctx.manifest.patches.set_options
                     if patch.file == "config/printer.cfg"}
        protected |= {(patch.section, patch.option) for patch in ctx.manifest.patches.delete_sections
                      if patch.file == "config/printer.cfg"}
        try:
            restored = saved_calibration.restore(ctx.paths.config_root, captured, protected=protected,
                                                 firmware=ctx.target.version)
            summary = restored.summary() + (where if restored.dropped else "")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            summary = f"The saved calibration could not be restored ({type(exc).__name__}: {exc}).{where}"
        ctx.record["calibration_result"] = {"summary": summary}
    if ctx.record["calibration_result"]["summary"]:
        ctx.reporter.line("  " + ctx.record["calibration_result"]["summary"])
    ctx.save()


def _update_folder(ctx: _Run) -> None:
    qidi_package.install_update_folder(ctx.paths, ctx.target, remove_tree=ctx.remove_tree)


def _boards(ctx: _Run) -> None:
    admission = load_board_admission(ctx.paths)
    if admission is None:
        raise InstallerError("the boards' admission record is missing")
    ctx.show(update_screen.UPDATING)
    _find_mainboard(ctx)
    try:
        # Klipper isn't running: each tool reads and resets its board itself (see board_firmware).
        delivery = ctx.deliver(paths=ctx.paths, reporter=ctx.reporter, manifest=ctx.manifest, printer=ctx.printer,
                               admission=admission, environ=ctx.environ, services_stopped=True, run=ctx.run,
                               urlopen=ctx.urlopen, sleep=ctx.sleep, clock=ctx.clock, resolve_usb=ctx.resolve_usb)
    except InstallerError as exc:
        raise _board_failure(ctx, exc.message) from exc
    ctx.record["boards"] = [{"board": result.board, "outcome": result.outcome, "detail": result.detail}
                            for result in delivery.results]
    ctx.save()
    valve_oil_status.record_boards(ctx.paths, valve_oil_status.boards_from_admission(
        delivery.admission, checked_at=valve_oil_status.now()))
    mcu = next((result for result in delivery.results if result.board == "mcu"), None)
    if mcu is not None and mcu.outcome not in ("current", "flashed"):
        stranded = _stranded_mainboard(ctx, f"{mcu.outcome}: {mcu.detail}", outcome=mcu.outcome)
        if stranded is not None:
            raise stranded


def _find_mainboard(ctx: _Run) -> bool:
    """Wait, up to MAINBOARD_WAIT_SECONDS, for the mainboard to be on USB under the name the config gives it: at boot
    USB devices appear late, and a flash from 02.01.01.11 gives it a new name (its USB name carries its firmware
    version, 1.0.11 against 1.0.12), which QIDI's touchscreen app writes into the config when it next starts; it is
    stopped here, so the name is followed from the USB position recorded before the flash."""
    deadline = ctx.clock() + MAINBOARD_WAIT_SECONDS
    while True:
        configured = mainboard_serial(ctx.paths)
        if not configured or os.path.exists(configured) or _follow_mainboard_name(ctx, configured):
            return bool(configured)
        if ctx.clock() >= deadline:
            ctx.reporter.line("  The mainboard was not found on USB under its configured name or at its recorded USB "
                              "position.")
            return False
        ctx.sleep(2)


def _follow_mainboard_name(ctx: _Run, configured: str) -> bool:
    usb = ctx.record.get("mainboard_usb")
    if not usb:
        return False
    try:
        names = [entry for entry in sorted(SERIAL_BY_ID.iterdir()) if ctx.resolve_usb(str(entry)) == usb]
    except OSError:
        names = []
    if len(names) != 1:
        return False
    # The name is in MCU_ID.cfg on QIDI's printers, or in printer.cfg where an operator moved it.
    for name in ("MCU_ID.cfg", "printer.cfg"):
        path = ctx.paths.config_root / name
        if not path.is_file() or path.is_symlink():
            continue
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        changed = [line.replace(configured, str(names[0])) if line.strip().startswith("serial") else line
                   for line in lines]
        if changed != lines:
            atomic_write_text(path, "".join(changed))
            # The name carries the board's serial number, so it is not logged.
            ctx.reporter.line(f"  The mainboard's USB name changed with its new firmware; {name} now names it.")
            return True
    return False


def _install(ctx: _Run) -> None:
    _find_mainboard(ctx)
    ctx.systemctl("start", KLIPPER_AND_MOONRAKER)
    ctx.show(update_screen.UPDATING)
    bootstrap = ctx.paths.bundle_root / "installer/runtime/bootstrap.py"
    env = {**os.environ, **ctx.environ, LOCK_HELD_ENV: "1", KIND_ENV: ctx.record["kind"],
           safety.FIRMWARE_UPDATE_RUN_ENV: ctx.record["id"]}
    kwargs = {"stdout": ctx.log, "stderr": subprocess.STDOUT} if ctx.log is not None else {}
    result = ctx.run([sys.executable, "-I", "-S", str(bootstrap), "install", "--yes", "--plain"],
                     cwd=str(ctx.paths.bundle_root), env=env, text=True, **kwargs)
    if result.returncode:
        raise InstallerError(f"the installer exited with {result.returncode}; see {log_path(ctx.paths)}")


def _finish(ctx: _Run) -> None:
    ctx.start_services(QIDI_SERVICES + (TIMER_NAME,))
    clear_carried_system_ledger(ctx.paths)
    # The update is done; leftovers that won't go are only reported.
    try:
        for leftover in (qidi_package.staging_dir(ctx.paths),
                         qidi_package.update_dir(ctx.paths).parent / qidi_package.PREVIOUS_DIRECTORY):
            if leftover.exists():
                ctx.remove_tree(leftover)
        qidi_package.remove_cache(ctx.paths)
    except (OSError, InstallerError) as exc:
        ctx.reporter.line(f"  (Some of the update's working files could not be removed: {exc})")


_STEP_FUNCTIONS = {"check": _check, "stage": _stage, "closed_loop": _closed_loop, "stop_services": _stop_services,
                   "soc": _soc, "update_folder": _update_folder, "boards": _boards, "install": _install,
                   "finish": _finish}


# Endings.


def _end_succeeded(ctx: _Run, status_run) -> int:
    notes = [f"QIDI firmware {ctx.target.version} is installed, with Valve Oil for it."]
    unfinished = [entry for entry in ctx.record.get("boards", ())
                  if entry["outcome"] not in ("current", "flashed", "held")]
    for entry in unfinished:
        name = {"thr": "THR", "mcu": "Mainboard"}.get(entry["board"]) or "Box " + entry["board"][3:]
        notes.append(f"{name} keeps its firmware ({entry['outcome']}" + (f": {entry['detail']}" if entry["detail"]
                                                                          else "") + ").")
    if any(entry["board"].startswith("box") and entry["outcome"] == "skipped" for entry in unfinished):
        # QIDI's package resets box.cfg, which the touchscreen app fills in again when it finds the Boxes.
        notes.append("USB Boxes get their firmware at the next install or update check, once the touchscreen has "
                     "found them again.")
    closed_loop = ctx.record.get("closed_loop") or {}
    if not _closed_loop_current(closed_loop):
        notes.append(closed_loop.get("detail", ""))
    elif closed_loop.get("outcome") == "flashed":
        notes.append(f"The closed-loop motor drivers run {closed_loop.get('target')}.")
    notes.append((ctx.record.get("calibration_result") or {}).get("summary", ""))
    message = " ".join(note for note in notes if note)
    valve_oil_status.clear_pending(ctx.paths, kind=valve_oil_status.FIRMWARE)
    return _end(ctx, status_run, state=SUCCEEDED, message=message, keep=False)


def _end_not_started(ctx: _Run, status_run, stop: _NotStarted, previous_run) -> int:
    message = f"The firmware update did not start: {stop.message}. Nothing was changed."
    try:
        ctx.remove_tree(qidi_package.staging_dir(ctx.paths))  # the cached package stays for the next attempt
    except (OSError, InstallerError) as exc:
        ctx.reporter.line(f"  (The unpacked package could not be removed: {exc})")
    blocker = Blocker.of(stop.code, stop.message)
    plan = FirmwareUpdatePlan(ctx.printer, ctx.record.get("from_firmware"), ctx.target.version, True)
    _publish_waiting(ctx.paths, plan, (blocker,))
    # No update ran: the status keeps the run before this one, and the waiting update says why it didn't start.
    valve_oil_status.update_status(ctx.paths, lambda status: replace(status, run=previous_run)
                                   if status.run is not None and status.run.id == status_run.id else status)
    return _end(ctx, status_run, state=NOT_STARTED, message=message, keep=False, blockers=(stop.code,),
                finish=False)


def _end_failed(ctx: _Run, status_run, step: str, failure: _Failed) -> int:
    ctx.start_services(failure.start)
    if failure.screen:
        ctx.show(update_screen.STOPPED)
    ctx.record.update(state="failed", message=failure.message, failed_step=step)
    return _end(ctx, status_run, state=FAILED, message=failure.message, keep=failure.keep)


def _end(ctx: _Run, status_run, *, state: str, message: str, keep: bool, blockers=(), finish=True) -> int:
    ctx.reporter.line(message)
    _write_result(ctx.paths, ctx.record, state=state, message=message, blockers=blockers, target=ctx.target.version)
    if finish:
        valve_oil_status.finish_run(ctx.paths, status_run.id, state=valve_oil_status.SUCCEEDED if state == SUCCEEDED
                                    else valve_oil_status.FAILED, message=message)
    with _record_lock(ctx.paths):
        if keep:
            _save(record_path(ctx.paths), ctx.record)
        else:
            atomic_delete(record_path(ctx.paths))
    return 0 if state == SUCCEEDED else 1


def _record_closed_loop(ctx: _Run, closed_loop: dict) -> None:
    """The drivers in the status file: up to date once on the target, or "held" (left on their firmware, to be updated
    from the touchscreen's firmware page) when the run didn't send them an image."""
    drivers = closed_loop.get("drivers") or ()
    versions = sorted({driver.get("version") for driver in drivers if driver.get("version")})
    updated = _closed_loop_current(closed_loop)
    valve_oil_status.record_boards(ctx.paths, (valve_oil_status.BoardStatus(
        id="closed_loop", state=valve_oil_status.CURRENT if updated else valve_oil_status.HELD,
        firmware=", ".join(versions) or None, pinned=closed_loop.get("target"),
        detail=None if updated else closed_loop.get("detail"),
        issue_url=None if updated else ctx.record.get("closed_loop_issue_url"), checked_at=valve_oil_status.now()),))


def _closed_loop_current(closed_loop: dict) -> bool:
    """Both drivers report the target version (or the update doesn't change them)."""
    if closed_loop.get("outcome") in (None, "unchanged"):
        return True
    drivers = closed_loop.get("drivers") or ()
    return bool(drivers) and all(driver.get("version") == closed_loop.get("target") for driver in drivers)


# ---- entry point ----------------------------------------------------------------------------------------------------


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text: str) -> int:
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()

    def isatty(self) -> bool:
        return False


def main(*, argv: list[str], bundle_root: Path) -> int:
    from .cli import resolve_runtime_paths
    from .reporter import create_reporter

    parser = argparse.ArgumentParser(prog="firmware-update",
                                     description="Run Valve Oil's pending QIDI firmware update.")
    parser.add_argument("--plain", action="store_true", help="Use plain text output.")
    parser.parse_args(argv)
    environ = dict(os.environ)
    paths = resolve_runtime_paths(bundle_root=bundle_root, environ=environ)
    log = log_path(paths)
    log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        reporter = create_reporter(_Tee(handle, sys.stdout), prefer_plain=True, bundle_root=bundle_root,
                                   environ=environ)
        return run_firmware_update(paths=paths, reporter=reporter, environ=environ, log=handle)
