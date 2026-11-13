"""Mainboard firmware: status, install, restore, and rescue.

`install` brings the mainboard to the image pinned for the printer's target QIDI firmware: this project's closed-loop
reply fix on a printer with closed-loop drivers (the Max 4), and QIDI's stock image on any other (the Plus 5).
mainboard-firmware.sh runs every action; installs, --yes, and automatic updates run `install` without the FLASH prompt
through board_firmware.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .auto_update import LOCK_HELD_ENV
from .board_guard import critical_window, detached
from .board_images import BOARD_MANIFESTS, pinned_for_installed_printer, runs_target_firmware
from .box_firmware import (SERVICES, _write_image, admitted_backup, pending_record, start_recorded_services,
                           unsupported_board_note, usb_path_for_serial)
from .errors import InstallerError
from .fs_atomic import atomic_write_text, fsync_directory
from .path_safety import ensure_runtime_path_has_no_symlink_components
from .safety import MAINBOARD_RECOVERY_MARKER, ensure_no_pending_firmware
from .sudo import authenticate_sudo, run_sudo
from .thr_firmware import ThrFirmwareError, wait_for_thr_printer_ready

SUBJECT = "Mainboard firmware"
MANIFEST = BOARD_MANIFESTS["mcu"]
BACKUP_DIRECTORY = ".tltg_mainboard_firmware_backups"
CONFIRMATION = "FLASH"
APP_RETURN_SECONDS = 30
RESCUE_WAIT_SECONDS = 60
MCU_SECTION = re.compile(r"^\[mcu\]\s*$")
SERIAL_LINE = re.compile(r"^serial\s*[:=]\s*(\S+)")
WARNING = "Mainboard firmware: keep the printer powered on until this finishes."


class MainboardFirmwareError(InstallerError):
    pass


def mainboard_serial(paths) -> str | None:
    for name in ("MCU_ID.cfg", "printer.cfg"):
        path = paths.config_root / name
        if not path.is_file():
            continue
        in_mcu = False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_mcu = bool(MCU_SECTION.match(stripped))
            elif in_mcu:
                serial = SERIAL_LINE.match(stripped)
                if serial:
                    return serial.group(1)
    return None


def _version(paths, urlopen) -> str | None:
    parts = urllib.parse.urlsplit(paths.moonraker_url)
    url = urllib.parse.urlunsplit(parts._replace(query="mcu=mcu_version"))
    try:
        with urlopen(url, timeout=5) as response:
            return (json.load(response)["result"]["status"].get("mcu") or {}).get("mcu_version")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _superseded_versions(manifest: dict) -> set[str]:
    return {entry["version"] for entry in manifest["mcu"].get("superseded", ())}


def _predecessor_versions(manifest: dict) -> set[str]:
    from scripts.build_box_firmware import predecessor_versions
    return predecessor_versions(manifest["mcu"])


def _describe(version: str | None, manifest: dict) -> str:
    mcu = manifest["mcu"]
    if version is not None and version == mcu["patched_version"]:
        return "closed-loop reply fix installed (" + version + ")"
    if version in _superseded_versions(manifest):
        return "earlier closed-loop reply fix (" + version + "); install updates it"
    if version in _predecessor_versions(manifest):
        return "QIDI firmware of an earlier package (" + version + "); replaced once the printer runs its target QIDI firmware"
    if version == mcu["stock_version"]:
        return "QIDI stock firmware (" + version + ")"
    if version is None:
        return "not connected or Klipper not ready"
    return "unsupported firmware (" + version + ")"


_WAITING = "the mainboard will be updated after the QIDI firmware update to {}"


def _waiting(reporter, outcomes, pinned) -> int:
    detail = _WAITING.format(pinned.firmware)
    reporter.line("Mainboard: " + detail + ". No mainboard firmware was changed.")
    if outcomes is not None:
        outcomes["mcu"] = ("waiting", detail)
    return 0


def _stock_image(paths, manifest) -> bytes | None:
    from scripts.flash_box_firmware import image_identity
    candidates = sorted((paths.printer_data_root / BACKUP_DIRECTORY).glob("*/before/application.bin"))
    # QIDI names the image after the printer's package (QD_MAX4_, QD_PLUS5_); the Max 4 and Plus 5 images are the same.
    candidates += sorted((paths.printer_data_root.parent / "update").glob("QD_*_MCU_" + manifest["mcu"]["stock_version"] + "_*.bin"))
    for candidate in candidates:
        try:
            data = candidate.read_bytes()
        except OSError:
            continue
        if image_identity(data, manifest) == "stock":
            return data
    return None


def run_mainboard_firmware(
    *, paths, reporter, action, input_stream=None, environ=None,
    run=None, urlopen=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic,
    resolve_usb=usb_path_for_serial, usb_state=None, confirmed=False, services_stopped=False, pinned=None,
    outcomes=None,
) -> int:
    """confirmed: the operator already chose this by running the installer, so no FLASH prompt.

    services_stopped: the caller already checked the printer and stopped Klipper, the touchscreen, and the update timer,
    and starts them again itself, as a firmware update does; the mainboard is then read to see whether it needs flashing.
    pinned: the image pinned for the printer's target firmware, found from the installed firmware when not given.
    outcomes, when given, receives "mcu" mapped to (outcome, description), with outcome one of "current", "flashed",
    "restored", "not_admitted", or "waiting" (a mainboard on QIDI's image of an earlier version, left as it is until the
    printer runs the target firmware: the flash changes the mainboard's version, which the installed Klipper may not
    support).
    """
    from scripts import build_mcu_firmware
    from scripts import flash_box_firmware as protocol
    from scripts.build_box_firmware import PREDECESSOR

    run = subprocess.run if run is None else run
    usb_state = protocol.usb_state if usb_state is None else usb_state
    env = {} if environ is None else environ
    manifest = build_mcu_firmware.load_manifest(paths.bundle_root / MANIFEST)
    marker = paths.printer_data_root / MAINBOARD_RECOVERY_MARKER

    if action == "status":
        reporter.line("Mainboard: " + _describe(_version(paths, urlopen), manifest))
        if marker.exists():
            reporter.line("A mainboard firmware operation did not finish; run ./mainboard-firmware.sh rescue. Record: " + str(marker))
        return 0

    if env.get(LOCK_HELD_ENV) == "1" and action != "install":
        raise MainboardFirmwareError("Mainboard firmware " + action + " is not allowed in automatic-update child installs.")
    if services_stopped and action != "install":
        raise MainboardFirmwareError("Only mainboard firmware install runs with services stopped by the caller.")
    if paths.restart_marker_path.exists():
        raise MainboardFirmwareError("Complete pending Klipper source activation before mainboard firmware programming.")
    ensure_no_pending_firmware(paths.recovery_sentinel_path, allow_mainboard=action == "rescue")
    try:
        pinned = pinned_for_installed_printer(paths, "mcu") if pinned is None else pinned
    except InstallerError as exc:
        raise MainboardFirmwareError("Mainboard firmware needs a supported QIDI firmware: " + exc.message) from exc
    python = paths.printer_data_root.parent / "klippy-env/bin/python"
    tool = paths.bundle_root / "scripts/flash_mcu_firmware.py"
    if not tool.is_file() or tool.is_symlink() or not python.is_file():
        raise MainboardFirmwareError("Mainboard firmware requires the packaged tool and the existing klippy-env Python.")
    backup_root = paths.printer_data_root / BACKUP_DIRECTORY
    for target in (marker, backup_root):
        ensure_runtime_path_has_no_symlink_components(printer_data_root=paths.printer_data_root, target=target)

    # This project's build goes only where it is pinned (the Max 4); any other mainboard is pinned to QIDI's image.
    target = pinned.build if action == "install" else "stock"
    pin = ("--firmware", pinned.firmware)
    if action == "rescue":
        rescue_backup = admitted_backup(backup_root.glob("*/before"), manifest)
        if rescue_backup is None:
            # Every transfer needs a backup of a validated bootloader, so an operation
            # recorded without one stopped before it reached a mainboard that still runs.
            unfinished = pending_record(marker)
            usb = unfinished.get("usb") or resolve_usb(mainboard_serial(paths))
            if not unfinished or usb is None or usb_state(usb)[0] != protocol.APP_IDS:
                raise MainboardFirmwareError("No backup of a validated mainboard bootloader; rescue needs one from an "
                                             "earlier mainboard-firmware run. No mainboard firmware was changed.")
            reporter.line("Mainboard: not changed. The unfinished operation stopped before it reached the mainboard, "
                          "which still runs its firmware. Starting Klipper and the touchscreen again.")
            password = authenticate_sudo(run=run, environ=env, reporter=reporter, input_stream=input_stream)
            start_recorded_services(unfinished.get("services") or {}, run=run, password=password, paths=paths, urlopen=urlopen,
                                    sleep=sleep, clock=clock, marker=marker, subject=SUBJECT, error=MainboardFirmwareError)
            return 0
    stock_image = None
    if action in ("restore", "rescue"):
        stock_image = _stock_image(paths, manifest)
        if stock_image is None:
            raise MainboardFirmwareError("No verified copy of QIDI's mainboard firmware " + manifest["mcu"]["stock_version"]
                                         + " was found in the mainboard firmware backups or ~/update.")
    if action == "rescue":
        record = rescue_backup.parent / "mainboard.json"
        usb = json.loads(record.read_text()).get("usb") if record.is_file() else None
        usb = usb or resolve_usb(mainboard_serial(paths))
        if usb is None:
            raise MainboardFirmwareError("Cannot find the mainboard's USB position.")
    else:
        usb = resolve_usb(mainboard_serial(paths))
        # Moonraker stops with Klipper; with services already stopped the mainboard is read instead.
        version = None if services_stopped else _version(paths, urlopen)
        if not services_stopped:
            reporter.line("Mainboard: " + _describe(version, manifest))
        wanted = manifest["mcu"]["patched_version" if target == "patched" else "stock_version"]
        if version == wanted:
            reporter.line("Nothing to flash.")
            if outcomes is not None:
                outcomes["mcu"] = ("current", "already " + ("installed" if target == "patched" else "stock"))
            return 0
        if usb is None or (version is None and not services_stopped):
            raise MainboardFirmwareError("The mainboard is not connected, or Klipper is not ready.")
        if version is not None and version not in {manifest["mcu"]["stock_version"], manifest["mcu"]["patched_version"],
                                                   *_superseded_versions(manifest), *_predecessor_versions(manifest)}:
            raise MainboardFirmwareError("The mainboard runs firmware this fix does not support: " + version)
        if version in _predecessor_versions(manifest) and not runs_target_firmware(paths, "mcu", pinned.firmware):
            return _waiting(reporter, outcomes, pinned)
        if (target == "patched" and version in _superseded_versions(manifest)) or version in _predecessor_versions(manifest):
            # The pinned image is QIDI's stock image or built from it, and the mainboard doesn't hold it.
            stock_image = _stock_image(paths, manifest)
            if stock_image is None:
                raise MainboardFirmwareError("Updating the mainboard from " + version + " needs QIDI's mainboard firmware "
                                             + manifest["mcu"]["stock_version"] + " from the mainboard firmware backups "
                                             "or ~/update; none was found. No mainboard firmware was changed.")

    verb = {"install": "Install the closed-loop reply fix on" if target == "patched" else "Install QIDI's firmware on",
            "restore": "Restore QIDI's stock firmware on", "rescue": "Rescue and restore QIDI's stock firmware on"}[action]
    reporter.line(WARNING)
    reporter.line(f"{verb} the mainboard. Klipper and the touchscreen stop for about three minutes; "
                  "the printer can't move or heat until they start again.")
    if not confirmed:
        if input_stream is None:
            raise MainboardFirmwareError("mainboard-firmware needs an interactive terminal to confirm.")
        reporter.emit_prompt(question=f"Type {CONFIRMATION} to continue", instruction="Anything else cancels.")
        if input_stream.readline().strip() != CONFIRMATION:
            reporter.line("Cancelled. No mainboard firmware was changed.")
            return 1

    try:
        if action != "rescue" and not services_stopped:
            wait_for_thr_printer_ready(paths, urlopen=urlopen, sleep=sleep, clock=clock, reporter=reporter, subject=SUBJECT)
    except ThrFirmwareError as exc:
        raise MainboardFirmwareError(str(exc)) from exc
    states = {}
    for service in () if services_stopped else SERVICES:
        result = run(["systemctl", "show", service, "--property=LoadState,ActiveState"], capture_output=True, text=True, timeout=5)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if service.endswith(".timer") and values.get("LoadState") == "not-found":
            continue
        if result.returncode or values.get("LoadState") != "loaded" or values.get("ActiveState") not in {"active", "inactive"}:
            raise MainboardFirmwareError("Cannot establish serial service ownership: " + service)
        states[service] = values["ActiveState"]
    if action == "rescue" and marker.is_file():
        try:
            states = json.loads(marker.read_text()).get("services") or states
        except ValueError:
            pass
    password = None if services_stopped else authenticate_sudo(run=run, environ=env, reporter=reporter,
                                                                  input_stream=input_stream)
    backup_root.mkdir(mode=0o700, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="mainboard-", dir=backup_root))
    fsync_directory(backup_root)
    record = {"backup_directory": str(directory), "services": states, "action": action, "phase": "stopping_services", "usb": usb}
    atomic_write_text(marker, json.dumps(record, indent=2) + "\n", mode=0o600)
    atomic_write_text(directory / "mainboard.json", json.dumps({"usb": usb}) + "\n", mode=0o600)
    reporter.line("Mainboard firmware recovery files: " + str(directory))
    outcome = None
    kind = None
    pending = False

    # From here to the services' restart the terminal's hangup and Ctrl-C can't stop the work (board_guard).
    session = detached(run)

    def command(operation, *arguments, allow=()):
        result = session([str(python), str(tool), operation, *map(str, arguments), "--printer-idle-confirmed"],
                     capture_output=True, text=True, timeout=960)
        atomic_write_text(directory / (operation + ".stdout"), result.stdout or "", mode=0o600)
        atomic_write_text(directory / (operation + ".stderr"), result.stderr or "", mode=0o600)
        if result.returncode and result.returncode not in allow:
            raise MainboardFirmwareError("Mainboard " + operation + " failed; see " + str(directory))
        return result

    def service_action(verb_name, service):
        result = run_sudo(["systemctl", verb_name, service], run=session, password=password, timeout=60)
        if result.returncode:
            raise MainboardFirmwareError("Could not " + verb_name + " " + service)

    with critical_window(reporter) as reporter:
        try:
            for service in reversed(SERVICES):
                if states.get(service) == "active":
                    service_action("stop", service)
            if action == "rescue":
                before = rescue_backup
                image = stock_image
                reporter.line("Waiting for the mainboard's bootloader...")
                record.update(phase="programming")
                atomic_write_text(marker, json.dumps(record, indent=2) + "\n", mode=0o600)
                pending = True
                command("transfer", "--usb", usb, "--image", _write_image(directory, image),
                        "--backup-dir", before, "--confirm-write", "--rescue", "--wait", RESCUE_WAIT_SECONDS, *pin)
            else:
                before = directory / "before"
                reporter.line("Mainboard: reading and admitting its bootloader and firmware...")
                snapshot = command("snapshot", "--usb", usb, "--output-dir", before, allow=(protocol.UNSUPPORTED,))
                if snapshot.returncode == protocol.UNSUPPORTED:
                    outcome, kind = "not changed: " + unsupported_board_note(before / "report.json"), "not_admitted"
                else:
                    boot, app = protocol.load_backup(before, manifest)
                    current = protocol.image_identity(app, manifest)
                    waits = current == PREDECESSOR and not runs_target_firmware(paths, "mcu", pinned.firmware)
                    if current != target and current != "stock" and stock_image is None and not waits:
                        stock_image = _stock_image(paths, manifest)
                    if waits:
                        outcome, kind = "not changed: " + _WAITING.format(pinned.firmware), "waiting"
                    elif current == target:
                        outcome = "already " + ("installed" if target == "patched" else "stock") + " (verified by readback)"
                        kind = "current"
                    elif current != "stock" and stock_image is None:
                        raise MainboardFirmwareError("The mainboard doesn't hold QIDI's mainboard firmware "
                                                     + manifest["mcu"]["stock_version"] + ", and no copy of it was found "
                                                     "in the mainboard firmware backups or ~/update to update it from.")
                    else:
                        image = (build_mcu_firmware.patch_mcu(app if current == "stock" else stock_image, manifest)
                                 if target == "patched" else stock_image)
                        record.update(phase="programming")
                        atomic_write_text(marker, json.dumps(record, indent=2) + "\n", mode=0o600)
                        pending = True
                        reporter.line("Mainboard: flashing; keep the printer powered on...")
                        command("transfer", "--usb", usb, "--image", _write_image(directory, image),
                                "--backup-dir", before, "--confirm-write", *pin)
            if outcome is None:
                reporter.line("Mainboard: reading back its firmware...")
                after = directory / "after"
                command("snapshot", "--usb", usb, "--output-dir", after, "--wait", APP_RETURN_SECONDS)
                evidence = protocol.verify_readback(image, after, manifest,
                                                    expected_bootloader=(before / "bootloader.bin").read_bytes())
                atomic_write_text(directory / "verification.json", json.dumps(evidence, indent=2) + "\n", mode=0o600)
                outcome = ("closed-loop reply fix installed" if target == "patched" else
                           "QIDI stock firmware installed" if action == "install" else "QIDI stock firmware restored") + " and verified"
                kind = "flashed" if action == "install" else "restored"
                pending = False
                record.update(phase="verified")
                atomic_write_text(marker, json.dumps(record, indent=2) + "\n", mode=0o600)
        except Exception as exc:
            failure = exc
        else:
            failure = None
        if pending:
            reporter.line("The mainboard firmware is unverified. Klipper and the touchscreen stay stopped. "
                          "Run ./mainboard-firmware.sh rescue and follow its prompt.")
            raise MainboardFirmwareError("Mainboard firmware operation failed: " + str(failure) + ". Recovery files: " + str(directory)) from failure
        start_recorded_services(states, run=session, password=password, paths=paths, urlopen=urlopen, sleep=sleep, clock=clock,
                                marker=marker, subject=SUBJECT, error=MainboardFirmwareError)
    if failure is not None:
        raise MainboardFirmwareError("Mainboard firmware operation stopped before any write: " + str(failure))
    reporter.line("Mainboard: " + outcome)
    if outcomes is not None:
        outcomes["mcu"] = (kind, outcome)
    return 0
