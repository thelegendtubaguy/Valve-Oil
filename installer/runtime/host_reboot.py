from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from . import boot_path, messages, safety
from .errors import ActivePrintError, InstallerError, KlipperBusyError, KlipperNotReadyError, PrinterStateError
from .fs_atomic import atomic_delete, atomic_write_text
from .interaction import prompt_yes
from .models import RuntimePaths
from .sudo import SudoError, authenticate_sudo, run_sudo, run_sudo_or_raise

HOST_REBOOT_SCHEMA_VERSION = 1
HOST_REBOOT_REASON = "rockchip_root_sync"
HOST_REBOOT_OPERATION = "rockchip_root_sync"
HOST_REBOOT_UNIT = "vo-host-reboot"
HOST_REBOOT_FOLLOWUP_UNIT = "vo-host-reboot-followup"
# Automatic updates reboot only after Klipper has been this long without motion, heat, or a running command.
KLIPPER_IDLE_SECONDS = 600
# A scheduled reboot waits this long for a printer whose state can't be read yet, such as one whose Klipper is starting
# again and again while the touchscreen app finds the boards after a QIDI firmware update.
STATE_WAIT_SECONDS = 300
STATE_POLL_SECONDS = 10
# A QIDI firmware update reboots this long after it has finished.
FIRMWARE_UPDATE_REBOOT_DELAY = "5s"


class HostRebootError(InstallerError):
    pass


@dataclass(frozen=True)
class HostRebootMarker:
    schema_version: int
    reason: str
    operation_id: str
    package_version: str
    created_at: str
    boot_id: str
    source: str
    auto_update_checksum_before: str | None = None


def current_boot_id(paths: RuntimePaths) -> str:
    try:
        value = paths.boot_id_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise HostRebootError(messages.HOST_REBOOT_BOOT_ID_FAILED) from exc
    if not value or len(value) > 128 or any(char.isspace() for char in value):
        raise HostRebootError(messages.HOST_REBOOT_BOOT_ID_FAILED)
    return value


def read_host_reboot_marker(paths: RuntimePaths) -> HostRebootMarker | None:
    path = paths.host_reboot_marker_path
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    if path.stat().st_mode & 0o777 != 0o600:
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID) from exc
    if not isinstance(payload, dict):
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    required = {
        "schema_version",
        "reason",
        "operation_id",
        "package_version",
        "created_at",
        "boot_id",
        "source",
    }
    allowed = required | {"auto_update_checksum_before"}
    if set(payload) - allowed or not required.issubset(payload):
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    if payload.get("schema_version") != HOST_REBOOT_SCHEMA_VERSION:
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    if payload.get("reason") != HOST_REBOOT_REASON or payload.get("operation_id") != HOST_REBOOT_OPERATION:
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    for key in ("package_version", "created_at", "boot_id", "source"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    if payload["source"] not in {
        "interactive_install",
        "yes_install",
        "auto_update_child",
        "auto_update_reconcile",
    }:
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    checksum = payload.get("auto_update_checksum_before")
    if checksum is not None and (
        not isinstance(checksum, str)
        or len(checksum) != 64
        or any(char not in "0123456789abcdef" for char in checksum)
    ):
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID)
    try:
        datetime.strptime(payload["created_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise HostRebootError(messages.HOST_REBOOT_MARKER_INVALID) from exc
    return HostRebootMarker(
        schema_version=payload["schema_version"],
        reason=payload["reason"],
        operation_id=payload["operation_id"],
        package_version=payload["package_version"],
        created_at=payload["created_at"],
        boot_id=payload["boot_id"],
        source=payload["source"],
        auto_update_checksum_before=checksum,
    )


def write_host_reboot_marker(
    paths: RuntimePaths,
    *,
    package_version: str,
    source: str,
    auto_update_checksum_before: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> HostRebootMarker:
    moment = (now or (lambda: datetime.now(timezone.utc)))()
    marker = HostRebootMarker(
        schema_version=HOST_REBOOT_SCHEMA_VERSION,
        reason=HOST_REBOOT_REASON,
        operation_id=HOST_REBOOT_OPERATION,
        package_version=package_version,
        created_at=moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        boot_id=current_boot_id(paths),
        source=source,
        auto_update_checksum_before=auto_update_checksum_before,
    )
    payload = {
        "schema_version": marker.schema_version,
        "reason": marker.reason,
        "operation_id": marker.operation_id,
        "package_version": marker.package_version,
        "created_at": marker.created_at,
        "boot_id": marker.boot_id,
        "source": marker.source,
    }
    if marker.auto_update_checksum_before is not None:
        payload["auto_update_checksum_before"] = marker.auto_update_checksum_before
    atomic_write_text(
        paths.host_reboot_marker_path,
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        mode=0o600,
        force_mode=True,
    )
    return marker


def clear_host_reboot_marker(paths: RuntimePaths) -> None:
    atomic_delete(paths.host_reboot_marker_path)


def verify_completed_host_reboot(
    paths: RuntimePaths,
    *,
    verify_operation: Callable[[], object],
    reporter,
) -> bool:
    marker = read_host_reboot_marker(paths)
    if marker is None or marker.boot_id == current_boot_id(paths):
        return False
    try:
        verify_operation()
    except Exception as exc:
        reporter.line(messages.HOST_REBOOT_POSTFLIGHT_FAILED)
        raise HostRebootError(messages.HOST_REBOOT_POSTFLIGHT_FAILED) from exc
    clear_host_reboot_marker(paths)
    reporter.line(messages.HOST_REBOOT_VERIFIED)
    return True


def arm_auto_update_reboot_followup(
    paths: RuntimePaths,
    *,
    reporter,
    environ: dict[str, str],
    input_stream=None,
    run=subprocess.run,
) -> bool:
    marker = read_host_reboot_marker(paths)
    if marker is None or marker.source != "auto_update_child":
        return False
    script = paths.bundle_root / "auto-update.sh"
    if not script.is_file():
        reporter.line(messages.HOST_REBOOT_FOLLOWUP_FAILED)
        return False
    try:
        password = authenticate_sudo(
            run=run,
            environ=environ,
            reporter=reporter,
            input_stream=input_stream,
        )
        run_sudo_or_raise(
            [
                "systemd-run",
                f"--unit={HOST_REBOOT_FOLLOWUP_UNIT}",
                "--on-active=30s",
                "--collect",
                f"--uid={os.getuid()}",
                "/bin/sh",
                str(script),
                "--run",
            ],
            messages.HOST_REBOOT_FOLLOWUP_FAILED,
            run=run,
            password=password,
        )
    except SudoError:
        reporter.line(messages.HOST_REBOOT_FOLLOWUP_FAILED)
        return False
    reporter.line(messages.HOST_REBOOT_FOLLOWUP_ARMED)
    return True


def recommend_reboot(paths: RuntimePaths) -> None:
    """Recommend a reboot until the host next boots."""
    atomic_write_text(paths.reboot_recommended_path, current_boot_id(paths) + "\n", mode=0o600, force_mode=True)


def reboot_recommended(paths: RuntimePaths) -> bool:
    """Whether a reboot is still recommended; a recommendation from an earlier boot is dropped."""
    path = paths.reboot_recommended_path
    if not path.exists():
        return False
    try:
        current = path.read_text(encoding="utf-8").strip() == current_boot_id(paths)
    except (OSError, HostRebootError):
        return False
    if not current:
        atomic_delete(path)
    return current


def maybe_schedule_recommended_reboot(
    paths: RuntimePaths,
    *,
    reporter,
    input_stream,
    environ: dict[str, str],
    require_klipper_idle: bool = False,
    urlopen=urllib.request.urlopen,
    run=subprocess.run,
) -> bool:
    """Reboot for the host tuning changes without asking, once the printer allows it."""
    if not reboot_recommended(paths):
        return False
    if not _schedule_reboot(
        paths,
        reporter=reporter,
        input_stream=input_stream,
        environ=environ,
        require_klipper_idle=require_klipper_idle,
        urlopen=urlopen,
        run=run,
    ):
        reporter.line(messages.REBOOT_RECOMMENDED)
        return False
    reporter.line(messages.REBOOT_SCHEDULED)
    return True


def _printer_allows_reboot(paths: RuntimePaths, *, reporter, require_klipper_idle: bool, urlopen,
                           wait_seconds: float = 0, sleep=time.sleep, clock=time.monotonic,
                           require_ready: bool = True) -> bool:
    """Whether the printer is idle, and, with require_ready, whether Klipper is ready: a reboot is never the next step
    after a run that left Klipper halted or shut down. A state that can't be read, or a Klipper that isn't ready, is
    read again for up to `wait_seconds`. Only a reboot the operator approved (uninstall) passes require_ready False."""
    deadline = clock() + wait_seconds
    while True:
        try:
            if require_klipper_idle:
                safety.ensure_klipper_idle(paths.moonraker_url, idle_seconds=KLIPPER_IDLE_SECONDS, urlopen=urlopen)
            else:
                safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen, allow_halted=not require_ready)
            if require_ready:
                safety.ensure_klipper_ready(paths.moonraker_url, urlopen=urlopen)
        except KlipperNotReadyError as exc:
            if clock() >= deadline:
                reporter.line(messages.HOST_REBOOT_HELD_UNFINISHED.format(reason=exc.message))
                return False
            sleep(STATE_POLL_SECONDS)
            continue
        except ActivePrintError:
            reporter.line(messages.HOST_REBOOT_DEFERRED_ACTIVE_PRINT)
            return False
        except KlipperBusyError:
            reporter.line(messages.HOST_REBOOT_DEFERRED_KLIPPER_BUSY)
            return False
        except PrinterStateError:
            if clock() >= deadline:
                reporter.line(messages.HOST_REBOOT_DEFERRED_UNKNOWN_STATE)
                return False
            sleep(STATE_POLL_SECONDS)
            continue
        return True


def _reboot_unit_command(paths: RuntimePaths, *, delay: str, require_klipper_idle: bool,
                         require_ready: bool = True) -> list[str]:
    """The transient unit that runs complete-host-reboot, which checks the printer again before rebooting."""
    return [
        "systemd-run",
        f"--unit={HOST_REBOOT_UNIT}",
        f"--on-active={delay}",
        "--collect",
        f"--uid={os.getuid()}",
        sys.executable,
        "-I",
        "-S",
        str(paths.bundle_root / "installer/runtime/bootstrap.py"),
        "complete-host-reboot",
        "--plain",
        *(["--require-klipper-idle"] if require_klipper_idle else []),
        *([] if require_ready else ["--operator-approved"]),
    ]


def reboot_pending(paths: RuntimePaths) -> bool:
    """A reboot this boot still needs: the Rockchip repair's, or the one recommended for the host tuning."""
    try:
        marker = read_host_reboot_marker(paths)
        if marker is not None:
            return marker.boot_id == current_boot_id(paths)
        return reboot_recommended(paths)
    except HostRebootError:
        return False


def schedule_reboot_after_firmware_update(paths: RuntimePaths, *, password: str, run=subprocess.run, reporter=None,
                                          environ: dict[str, str] | None = None) -> bool:
    """At the end of a QIDI firmware update, reboot for its install's host changes once the update has ended and the
    printer is idle, whether a session follows the update or not. Returns whether the reboot was scheduled."""
    if not reboot_pending(paths):
        return False
    if reporter is not None and not boot_path.boot_files_allow_reboot(
            paths, reporter=reporter, environ=os.environ if environ is None else environ):
        return False
    command = _reboot_unit_command(paths, delay=FIRMWARE_UPDATE_REBOOT_DELAY, require_klipper_idle=False)
    try:
        return run_sudo(command, run=run, password=password, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _schedule_reboot(
    paths: RuntimePaths,
    *,
    reporter,
    input_stream,
    environ: dict[str, str],
    require_klipper_idle: bool,
    urlopen,
    run,
    require_ready: bool = True,
) -> bool:
    """Schedule complete-host-reboot, which checks the printer again before rebooting."""
    if not boot_path.boot_files_allow_reboot(paths, reporter=reporter, environ=environ):
        return False
    if not _printer_allows_reboot(paths, reporter=reporter, require_klipper_idle=require_klipper_idle, urlopen=urlopen,
                                  require_ready=require_ready):
        return False
    try:
        password = authenticate_sudo(
            run=run,
            environ=environ,
            reporter=reporter,
            input_stream=input_stream,
        )
        run_sudo_or_raise(
            _reboot_unit_command(paths, delay="5s", require_klipper_idle=require_klipper_idle,
                                 require_ready=require_ready),
            messages.HOST_REBOOT_SCHEDULE_FAILED,
            run=run,
            password=password,
        )
    except SudoError:
        reporter.line(messages.HOST_REBOOT_SCHEDULE_FAILED)
        return False
    return True


def maybe_schedule_host_reboot(
    paths: RuntimePaths,
    *,
    reporter,
    input_stream,
    environ: dict[str, str],
    explicit: bool = False,
    automatic: bool = False,
    require_klipper_idle: bool = False,
    urlopen=urllib.request.urlopen,
    run=subprocess.run,
) -> bool:
    marker = read_host_reboot_marker(paths)
    if marker is None or marker.boot_id != current_boot_id(paths):
        return False
    if not explicit and not automatic:
        if input_stream is None:
            reporter.line(messages.HOST_REBOOT_PENDING)
            return False
        if not prompt_yes(
            reporter=reporter,
            input_stream=input_stream,
            question=messages.HOST_REBOOT_PROMPT,
            instruction=messages.HOST_REBOOT_PROMPT_INSTRUCTION,
        ):
            reporter.line(messages.HOST_REBOOT_PENDING)
            return False
    if not _schedule_reboot(
        paths,
        reporter=reporter,
        input_stream=input_stream,
        environ=environ,
        require_klipper_idle=require_klipper_idle,
        urlopen=urlopen,
        run=run,
        require_ready=not explicit,
    ):
        return False
    reporter.line(messages.HOST_REBOOT_SCHEDULED)
    return True


def perform_scheduled_host_reboot(
    paths: RuntimePaths,
    *,
    reporter,
    environ: dict[str, str],
    recommended: bool = False,
    require_klipper_idle: bool = False,
    require_ready: bool = True,
    urlopen=urllib.request.urlopen,
    run=subprocess.run,
    sleep=time.sleep,
    clock=time.monotonic,
) -> bool:
    """Reboot for the pending Rockchip marker, or for a recommendation when recommended is set. A printer whose state
    can't be read yet is read again for a few minutes first."""
    if recommended:
        if not reboot_recommended(paths):
            return False
    else:
        marker = read_host_reboot_marker(paths)
        if marker is None or marker.boot_id != current_boot_id(paths):
            return False
    if not _printer_allows_reboot(paths, reporter=reporter, require_klipper_idle=require_klipper_idle, urlopen=urlopen,
                                  wait_seconds=STATE_WAIT_SECONDS, sleep=sleep, clock=clock,
                                  require_ready=require_ready):
        return False
    # Checked last, right before the reboot: never restart a printer that would stop at the boot logo.
    if not boot_path.boot_files_allow_reboot(paths, reporter=reporter, environ=environ):
        return False
    try:
        password = authenticate_sudo(
            run=run,
            environ=environ,
            reporter=reporter,
            input_stream=None,
        )
        run_sudo_or_raise(
            ["systemctl", "reboot"],
            messages.HOST_REBOOT_SCHEDULE_FAILED,
            run=run,
            password=password,
        )
    except SudoError:
        reporter.line(messages.HOST_REBOOT_SCHEDULE_FAILED)
        return False
    return True
