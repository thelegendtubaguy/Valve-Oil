"""Valve Oil QIDI Box firmware (shutdown fix and RFID commands): status, install, restore, and rescue.

box-firmware.sh runs every action. Every install, including --yes and the
automatic update's child install, also brings each USB Box to the bundled firmware.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .auto_update import LOCK_HELD_ENV
from .board_guard import critical_window, detached
from .board_images import BOARD_MANIFESTS, runs_target_firmware
from .errors import InstallerError
from .fs_atomic import atomic_delete, atomic_write_text, fsync_directory
from .naming import REPOSITORY_URL
from .path_safety import ensure_runtime_path_has_no_symlink_components
from .safety import BOX_RECOVERY_MARKER, ensure_no_pending_firmware
from .sudo import authenticate_sudo, run_sudo
from .thr_firmware import ThrFirmwareError, wait_for_thr_printer_ready

SUBJECT = "Box firmware"
SERVICES = ("klipper.service", "qidi-client.service", "tltg-optimized-auto-update.timer")
BACKUP_DIRECTORY = ".tltg_box_firmware_backups"
MANIFEST = BOARD_MANIFESTS["box"]
CONFIRMATION = "FLASH"
APP_RETURN_SECONDS = 30
BOX_SECTION = re.compile(r"^\[mcu (mcu_box(\d+))\]\s*$")
SERIAL_LINE = re.compile(r"^serial\s*[:=]\s*(\S+)")
WARNING = "Box firmware: keep the printer and every Box powered on until this finishes."
SUPPORT_ISSUE_URL = REPOSITORY_URL + "/issues/new?template=printer-support.yml"
# Klipper's printer warnings show a Fluidd banner for each, and Fluidd's Logs offers it for download.
LOG_REPORT = re.compile(r"^tltg_box_firmware_box(\d+)\.json$")
# A Box on QIDI's image of an earlier version: the flash changes its version, which the installed Klipper may not
# support, so it waits for the target firmware.
_WAITING = "this Box will be updated after the QIDI firmware update"


class BoxFirmwareError(InstallerError):
    pass


@dataclass(frozen=True)
class Box:
    number: int
    name: str
    serial: str | None
    usb: str | None

    @property
    def label(self) -> str:
        return f"Box {self.number}"


def usb_path_for_serial(serial: str | None) -> str | None:
    if not serial:
        return None
    device = Path(os.path.realpath(serial))
    if not device.name.startswith("tty") or not device.exists():
        return None
    interface = Path(os.path.realpath(Path("/sys/class/tty") / device.name / "device")).name
    usb = interface.split(":", 1)[0]
    return usb if re.match(r"^\d+-\d+(\.\d+)*$", usb) else None


def discover_boxes(paths, *, resolve_usb=usb_path_for_serial) -> list[Box]:
    box_cfg = paths.config_root / "box.cfg"
    if not box_cfg.is_file():
        return []
    boxes, current = [], None
    for line in box_cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        match = BOX_SECTION.match(stripped)
        if match:
            current = [int(match.group(2)), match.group(1), None]
            boxes.append(current)
            continue
        if stripped.startswith("["):
            current = None
        elif current is not None and current[2] is None:
            serial = SERIAL_LINE.match(stripped)
            if serial:
                current[2] = serial.group(1)
    return [Box(number, name, serial, resolve_usb(serial)) for number, name, serial in sorted(boxes)]


def _versions(paths, boxes, urlopen) -> dict[str, str | None]:
    names = [f"mcu {box.name}" for box in boxes]
    parts = urllib.parse.urlsplit(paths.moonraker_url)
    url = urllib.parse.urlunsplit(parts._replace(
        query="&".join(urllib.parse.quote(name, safe="") + "=mcu_version" for name in names)))
    try:
        with urlopen(url, timeout=5) as response:
            status = json.load(response)["result"]["status"]
    except (OSError, ValueError, KeyError, TypeError):
        return {box.name: None for box in boxes}
    return {box.name: (status.get(f"mcu {box.name}") or {}).get("mcu_version") for box in boxes}


def _superseded_versions(manifest: dict) -> set[str]:
    return {entry["version"] for entry in manifest["box"].get("superseded", ())}


def _predecessor_versions(manifest: dict) -> set[str]:
    from scripts.build_box_firmware import predecessor_versions
    return predecessor_versions(manifest["box"])


def _describe(version: str | None, manifest: dict) -> str:
    box = manifest["box"]
    if version == box["patched_version"]:
        return "Valve Oil Box firmware installed (" + version + ")"
    if version in _superseded_versions(manifest):
        return "earlier Valve Oil Box firmware (" + version + "); install updates it"
    if version in _predecessor_versions(manifest):
        return "QIDI firmware of an earlier package (" + version + "); replaced once the printer runs its target QIDI firmware"
    if version == box["stock_version"]:
        return "QIDI stock firmware (" + version + ")"
    if version is None:
        return "not connected or Klipper not ready"
    return "unsupported firmware (" + version + ")"


def unsupported_board_note(report: Path) -> str:
    return ("its bootloader or firmware is not supported yet. To get it supported, open an issue at "
            + SUPPORT_ISSUE_URL + " and attach only " + str(report) + ". Keep the .bin files beside it private.")


def _log_report(paths, number: int) -> Path:
    return paths.printer_data_root / "logs" / f"tltg_box_firmware_box{number}.json"


def _publish_unsupported(paths, box: Box, report: Path) -> None:
    target = _log_report(paths, box.number)
    try:
        ensure_runtime_path_has_no_symlink_components(printer_data_root=paths.printer_data_root, target=target)
        atomic_write_text(target, report.read_text(encoding="utf-8"), mode=0o644)
    except (OSError, InstallerError):
        pass


def _clear_unsupported(paths, numbers) -> None:
    for number in numbers:
        try:
            _log_report(paths, number).unlink()
        except OSError:
            pass


def prune_unsupported(paths, configured) -> None:
    """Drop reports of Boxes no longer in box.cfg."""
    try:
        names = os.listdir(paths.printer_data_root / "logs")
    except OSError:
        return
    configured_numbers = {box.number for box in configured}
    _clear_unsupported(paths, {int(match.group(1)) for match in map(LOG_REPORT.match, names)
                               if match and int(match.group(1)) not in configured_numbers})


# A Box the operator put back on QIDI's firmware with `restore` stays there through installs and update checks of the
# same release; installing the next release, or `box-firmware.sh install` on that Box, ends the hold.
HOLD_FILE = ".tltg_box_firmware_holds.json"
HELD = "held: restored to QIDI's firmware with ./box-firmware.sh restore; kept until the next release"


def current_release(paths) -> str | None:
    from .manifest import ManifestValidationError, load_manifest
    try:
        return load_manifest(paths.installer_root / "package.yaml").package.version
    except (OSError, ManifestValidationError):
        return None


def _read_holds(paths) -> dict:
    try:
        record = json.loads((paths.printer_data_root / HOLD_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) and isinstance(record.get("boxes"), list) else {}


def held_boxes(paths, release: str | None) -> set[tuple[str, str | None]]:
    """(name, serial) of each Box restored to QIDI's firmware while `release` was installed."""
    record = _read_holds(paths)
    if release is None or record.get("release") != release:
        return set()
    return {(entry.get("box"), entry.get("serial")) for entry in record["boxes"] if isinstance(entry, dict)}


def _write_holds(paths, release: str | None, held) -> None:
    if release is None:
        return
    target = paths.printer_data_root / HOLD_FILE
    ensure_runtime_path_has_no_symlink_components(printer_data_root=paths.printer_data_root, target=target)
    if not held:
        atomic_delete(target)
        return
    boxes = [{"box": name, "serial": serial} for name, serial in sorted(held, key=lambda item: (item[0], item[1] or ""))]
    atomic_write_text(target, json.dumps({"release": release, "boxes": boxes}, indent=2) + "\n", mode=0o600)


def clear_stale_holds(paths, release: str) -> None:
    """Installing another release ends every hold."""
    record = _read_holds(paths)
    if record and record.get("release") != release:
        _write_holds(paths, release, set())


def _unsupported_capture(backup_root: Path, box: Box, manifest: dict) -> Path | None:
    """The report of this Box's newest capture, if the bundled manifest still can't flash what it found."""
    from scripts.build_box_firmware import known_images
    from scripts.flash_box_firmware import image_spec
    if not box.serial:
        return None
    captures = []
    for record in backup_root.glob(f"*/{box.name}/box.json"):
        report_path = record.parent / "before/report.json"
        try:
            if json.loads(record.read_text()).get("serial") != box.serial:
                continue
            captures.append((record.stat().st_mtime, report_path, json.loads(report_path.read_text())))
        except (OSError, ValueError, AttributeError):
            continue
    if not captures:
        return None
    _, report_path, report = max(captures, key=lambda capture: capture[0])
    applications = set(known_images(image_spec(manifest)))
    bootloaders = {entry["sha256"] for entry in manifest["bootloaders"]}
    if report.get("bootloader_sha256") in bootloaders and report.get("application_sha256") in applications:
        return None
    return report_path


def _select(boxes: list[Box], requested: list[int] | None) -> list[Box]:
    if not boxes:
        raise BoxFirmwareError("No USB QIDI Box is configured in box.cfg. RS-485 Boxes have no Klipper MCU to flash.")
    if not requested:
        return boxes
    known = {box.number: box for box in boxes}
    missing = sorted(set(requested) - set(known))
    if missing:
        raise BoxFirmwareError("Not a configured USB Box: " + ", ".join(map(str, missing)))
    return [known[number] for number in sorted(set(requested))]


def _stock_image(paths, manifest) -> bytes | None:
    from scripts.flash_box_firmware import image_identity
    candidates = sorted((paths.printer_data_root / BACKUP_DIRECTORY).glob("*/*/before/application.bin"))
    # The Box is one accessory for every printer; QIDI names its image after the printer's package (QD_MAX4_, QD_PLUS5_).
    candidates += sorted((paths.printer_data_root.parent / "update").glob("QD_*_BOX_" + manifest["box"]["stock_version"] + "_*.bin"))
    for candidate in candidates:
        try:
            data = candidate.read_bytes()
        except OSError:
            continue
        if image_identity(data, manifest) == "stock":
            return data
    return None


def admitted_backup(candidates, manifest) -> Path | None:
    """The newest backup the transfer tool accepts: a validated bootloader and firmware."""
    from scripts.flash_box_firmware import load_backup
    admitted = []
    for directory in candidates:
        try:
            load_backup(directory, manifest)
            admitted.append((directory.stat().st_mtime, directory))
        except (OSError, ValueError):
            continue
    return max(admitted)[1] if admitted else None


def pending_record(marker: Path) -> dict:
    try:
        record = json.loads(marker.read_text())
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def start_recorded_services(states, *, run, password, paths, urlopen, sleep, clock, marker, subject, error) -> None:
    """Start every recorded service, also when one of them fails; the record stays until all of them run."""
    try:
        failed = [service for service in SERVICES if states.get(service) == "active"
                  and run_sudo(["systemctl", "start", service], run=run, password=password, timeout=60).returncode]
        if failed:
            raise error("Could not start " + ", ".join(failed))
        if states.get("klipper.service") == "active":
            wait_for_thr_printer_ready(paths, urlopen=urlopen, sleep=sleep, clock=clock, subject=subject)
        atomic_delete(marker)
    except BaseException as exc:
        raise error(subject + " service restoration is incomplete; recovery record retained: " + str(marker)) from exc


def run_box_firmware(
    *, paths, reporter, action, boxes=None, input_stream=None, environ=None,
    run=None, urlopen=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic,
    resolve_usb=usb_path_for_serial, usb_state=None, confirmed=False,
    services_stopped=False, firmware=None, outcomes=None,
) -> int:
    """confirmed: the operator already chose this by running the installer, so no FLASH prompt, and Boxes the
    operator restored to QIDI's firmware are left there until the next release.

    services_stopped: the caller already checked the printer and stopped Klipper, the touchscreen, and the update timer,
    and starts them again itself, as a firmware update does; each Box is then read to see whether it needs flashing.
    firmware: the printer's target QIDI firmware; transfers then program only the image pinned for it or QIDI's stock
    image. outcomes, when given, receives each Box's name mapped to (outcome, description), with outcome one of
    "current", "flashed", "restored", "held", "skipped", "not_admitted", or "waiting" (_WAITING).
    """
    from scripts import build_box_firmware
    from scripts import flash_box_firmware as protocol
    from scripts.build_box_firmware import PREDECESSOR

    run = subprocess.run if run is None else run
    usb_state = protocol.usb_state if usb_state is None else usb_state
    env = {} if environ is None else environ
    manifest = build_box_firmware.load_manifest(paths.bundle_root / MANIFEST)
    configured = discover_boxes(paths, resolve_usb=resolve_usb)
    marker = paths.printer_data_root / BOX_RECOVERY_MARKER

    if action == "status":
        versions = _versions(paths, configured, urlopen)
        if not configured:
            reporter.line("No USB QIDI Box is configured in box.cfg.")
        for box in configured:
            reporter.line(f"{box.label} ({box.name}): {_describe(versions[box.name], manifest)}")
        if marker.exists():
            reporter.line("A Box firmware operation did not finish; run ./box-firmware.sh rescue --box N. Record: " + str(marker))
        return 0

    if env.get(LOCK_HELD_ENV) == "1" and action != "install":
        raise BoxFirmwareError("Box firmware " + action + " is not allowed in automatic-update child installs.")
    if services_stopped and action != "install":
        raise BoxFirmwareError("Only Box firmware install runs with services stopped by the caller.")
    if paths.restart_marker_path.exists():
        raise BoxFirmwareError("Complete pending Klipper source activation before Box firmware programming.")
    ensure_no_pending_firmware(paths.recovery_sentinel_path, allow_box=action == "rescue")
    selected = _select(configured, boxes)
    if action == "rescue" and len(selected) != 1:
        raise BoxFirmwareError("rescue needs exactly one --box.")
    python = paths.printer_data_root.parent / "klippy-env/bin/python"
    tool = paths.bundle_root / "scripts/flash_box_firmware.py"
    if not tool.is_file() or tool.is_symlink() or not python.is_file():
        raise BoxFirmwareError("Box firmware requires the packaged tool and the existing klippy-env Python.")
    backup_root = paths.printer_data_root / BACKUP_DIRECTORY
    for target in (marker, backup_root):
        ensure_runtime_path_has_no_symlink_components(printer_data_root=paths.printer_data_root, target=target)

    target = "patched" if action == "install" else "stock"
    automatic = env.get(LOCK_HELD_ENV) == "1"
    release = current_release(paths) if action in ("install", "restore") else None
    held = held_boxes(paths, release) if action == "install" and confirmed else set()
    if action == "install":
        prune_unsupported(paths, configured)
    if action == "rescue":
        box = selected[0]
        rescue_backup = admitted_backup(backup_root.glob(f"*/{box.name}/before"), manifest)
        if rescue_backup is None:
            # Every transfer needs a backup of a validated bootloader, so an operation
            # recorded without one stopped before it reached a Box that still runs.
            unfinished = pending_record(marker)
            usb = box.usb or unfinished.get("usb")
            if (not unfinished or unfinished.get("box", box.name) != box.name or usb is None
                    or usb_state(usb)[0] != protocol.APP_IDS):
                raise BoxFirmwareError(f"No backup of a validated {box.label} bootloader; rescue needs one from an "
                                       "earlier box-firmware run. No Box firmware was changed.")
            reporter.line(f"{box.label}: not changed. The unfinished operation stopped before it reached {box.label}, "
                          "which still runs its firmware. Starting Klipper and the touchscreen again.")
            password = authenticate_sudo(run=run, environ=env, reporter=reporter, input_stream=input_stream)
            start_recorded_services(unfinished.get("services") or {}, run=run, password=password, paths=paths, urlopen=urlopen,
                                    sleep=sleep, clock=clock, marker=marker, subject=SUBJECT, error=BoxFirmwareError)
            return 0
    stock_image = None
    if action in ("restore", "rescue"):
        stock_image = _stock_image(paths, manifest)
        if stock_image is None:
            raise BoxFirmwareError("No verified copy of QIDI's Box firmware " + manifest["box"]["stock_version"]
                                   + " was found in the Box firmware backups or ~/update.")
    previous = {}
    kinds = {}
    if action == "rescue":
        usb = box.usb
        if usb is None:
            record = rescue_backup.parent / "box.json"
            usb = json.loads(record.read_text()).get("usb") if record.is_file() else None
        if usb is None:
            raise BoxFirmwareError(f"Cannot find {box.label}'s USB position.")
        plan = [(box, usb)]
    else:
        # Moonraker stops with Klipper; with services already stopped each Box is read instead.
        versions = {} if services_stopped else _versions(paths, selected, urlopen)
        plan = []
        for box in selected:
            version = versions.get(box.name)
            if not services_stopped:
                reporter.line(f"{box.label} ({box.name}): {_describe(version, manifest)}")
            wanted = manifest["box"]["patched_version" if target == "patched" else "stock_version"]
            if (box.name, box.serial) in held:
                previous[box.name], kinds[box.name] = HELD, "held"
            elif version == wanted:
                previous[box.name], kinds[box.name] = "already " + ("installed" if target == "patched" else "stock"), "current"
                if target == "patched":
                    _clear_unsupported(paths, [box.number])
            elif box.usb is None or (version is None and not services_stopped):
                previous[box.name], kinds[box.name] = "skipped: not connected", "skipped"
            elif version is not None and version not in {manifest["box"]["stock_version"], manifest["box"]["patched_version"],
                                                         *_superseded_versions(manifest), *_predecessor_versions(manifest)}:
                previous[box.name], kinds[box.name] = "skipped: unsupported firmware", "not_admitted"
            elif version in _predecessor_versions(manifest) and not runs_target_firmware(paths, "box", firmware):
                previous[box.name], kinds[box.name] = "not changed: " + _WAITING, "waiting"
            elif automatic and (capture := _unsupported_capture(backup_root, box, manifest)) is not None:
                # Reading it again would only stop Klipper and save the same capture.
                previous[box.name] = "skipped by automatic updates, as an earlier capture found: " + unsupported_board_note(capture)
                kinds[box.name] = "not_admitted"
                _publish_unsupported(paths, box, capture)
            else:
                plan.append((box, box.usb))
                if version in _superseded_versions(manifest) | _predecessor_versions(manifest) and stock_image is None:
                    # The fix is built from QIDI's stock image, which the Box doesn't hold.
                    stock_image = _stock_image(paths, manifest)
                    if stock_image is None:
                        raise BoxFirmwareError("Updating earlier Valve Oil Box firmware needs QIDI's Box firmware "
                                               + manifest["box"]["stock_version"] + " from the Box firmware backups "
                                               "or ~/update; none was found. No Box firmware was changed.")
        if not plan:
            for box in selected:
                reporter.line(f"{box.label}: {previous[box.name]}")
            reporter.line("Nothing to flash.")
            if outcomes is not None:
                outcomes.update({name: (kinds[name], text) for name, text in previous.items()})
            _settle_holds(paths, action, confirmed, release, selected, kinds)
            return 0

    verb = {"install": "Install the Valve Oil Box firmware on", "restore": "Restore QIDI's stock firmware on",
            "rescue": "Rescue and restore QIDI's stock firmware on"}[action]
    reporter.line(WARNING)
    reporter.line(f"{verb}: " + ", ".join(box.label for box, _ in plan)
                  + ". Klipper and the touchscreen stop for about four minutes per Box.")
    if not confirmed:
        if input_stream is None:
            raise BoxFirmwareError("box-firmware needs an interactive terminal to confirm.")
        reporter.emit_prompt(question=f"Type {CONFIRMATION} to continue", instruction="Anything else cancels.")
        if input_stream.readline().strip() != CONFIRMATION:
            reporter.line("Cancelled. No Box firmware was changed.")
            return 1

    try:
        if action != "rescue" and not services_stopped:
            wait_for_thr_printer_ready(paths, urlopen=urlopen, sleep=sleep, clock=clock, reporter=reporter, subject=SUBJECT)
    except ThrFirmwareError as exc:
        raise BoxFirmwareError(str(exc)) from exc
    states = {}
    for service in () if services_stopped else SERVICES:
        result = run(["systemctl", "show", service, "--property=LoadState,ActiveState"], capture_output=True, text=True, timeout=5)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if service.endswith(".timer") and values.get("LoadState") == "not-found":
            continue
        if result.returncode or values.get("LoadState") != "loaded" or values.get("ActiveState") not in {"active", "inactive"}:
            raise BoxFirmwareError("Cannot establish serial service ownership: " + service)
        states[service] = values["ActiveState"]
    if action == "rescue" and marker.is_file():
        try:
            states = json.loads(marker.read_text()).get("services") or states
        except ValueError:
            pass
    password = None if services_stopped else authenticate_sudo(run=run, environ=env, reporter=reporter,
                                                                  input_stream=input_stream)
    backup_root.mkdir(mode=0o700, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="box-", dir=backup_root))
    fsync_directory(backup_root)
    record = {"backup_directory": str(directory), "services": states, "action": action, "phase": "stopping_services"}
    atomic_write_text(marker, json.dumps(record, indent=2) + "\n", mode=0o600)
    reporter.line("Box firmware recovery files: " + str(directory))
    results = dict(previous)
    pending = False
    pin = ("--firmware", firmware) if firmware else ()
    # From here to the services' restart the terminal's hangup and Ctrl-C can't stop the work (board_guard).
    session = detached(run)

    def command(box_dir, operation, *arguments, allow=()):
        result = session([str(python), str(tool), operation, *map(str, arguments), "--printer-idle-confirmed"],
                         capture_output=True, text=True, timeout=960)
        atomic_write_text(box_dir / (operation + ".stdout"), result.stdout or "", mode=0o600)
        atomic_write_text(box_dir / (operation + ".stderr"), result.stderr or "", mode=0o600)
        if result.returncode and result.returncode not in allow:
            raise BoxFirmwareError("Box " + operation + " failed; see " + str(box_dir))
        return result

    def service_action(verb_name, service):
        result = run_sudo(["systemctl", verb_name, service], run=session, password=password, timeout=60)
        if result.returncode:
            raise BoxFirmwareError("Could not " + verb_name + " " + service)

    with critical_window(reporter) as reporter:
        try:
            for service in reversed(SERVICES):
                if states.get(service) == "active":
                    service_action("stop", service)
            for box, usb in plan:
                box_dir = directory / box.name
                box_dir.mkdir(mode=0o700)
                atomic_write_text(box_dir / "box.json", json.dumps({"box": box.name, "usb": usb, "serial": box.serial}) + "\n",
                                  mode=0o600)
                if action == "rescue":
                    before = rescue_backup
                    image = stock_image
                    reporter.line(f"{box.label}: waiting for its bootloader. Turn {box.label} off and on again now.")
                    record.update(phase="programming", box=box.name, usb=usb)
                    atomic_write_text(marker, json.dumps(record, indent=2) + "\n", mode=0o600)
                    pending = True
                    command(box_dir, "transfer", "--usb", usb, "--image", _write_image(box_dir, image),
                            "--backup-dir", before, "--confirm-write", "--rescue", "--wait", 300, *pin)
                else:
                    before = box_dir / "before"
                    reporter.line(f"{box.label}: reading and admitting its bootloader and firmware...")
                    snapshot = command(box_dir, "snapshot", "--usb", usb, "--output-dir", before, allow=(protocol.UNSUPPORTED,))
                    if snapshot.returncode == protocol.UNSUPPORTED:
                        results[box.name] = "not changed: " + unsupported_board_note(before / "report.json")
                        kinds[box.name] = "not_admitted"
                        if target == "patched":
                            _publish_unsupported(paths, box, before / "report.json")
                        continue
                    boot, app = protocol.load_backup(before, manifest)
                    current = protocol.image_identity(app, manifest)
                    if current == PREDECESSOR and not runs_target_firmware(paths, "box", firmware):
                        results[box.name], kinds[box.name] = "not changed: " + _WAITING, "waiting"
                        continue
                    if current == target:
                        results[box.name] = "already " + ("installed" if target == "patched" else "stock") + " (verified by readback)"
                        kinds[box.name] = "current"
                        if target == "patched":
                            _clear_unsupported(paths, [box.number])
                        continue
                    if target == "patched":
                        if current != "stock" and stock_image is None:
                            # Read only now, with the services the caller stopped: QIDI's image the update is built from.
                            stock_image = _stock_image(paths, manifest)
                            if stock_image is None:
                                raise BoxFirmwareError("Updating earlier Valve Oil Box firmware needs QIDI's Box firmware "
                                                       + manifest["box"]["stock_version"] + " from the Box firmware "
                                                       "backups or ~/update; none was found. No Box firmware was changed.")
                        image = build_box_firmware.patch_box(app if current == "stock" else stock_image, manifest)
                    else:
                        image = stock_image
                    record.update(phase="programming", box=box.name, usb=usb)
                    atomic_write_text(marker, json.dumps(record, indent=2) + "\n", mode=0o600)
                    pending = True
                    reporter.line(f"{box.label}: flashing; keep it powered on...")
                    command(box_dir, "transfer", "--usb", usb, "--image", _write_image(box_dir, image),
                            "--backup-dir", before, "--confirm-write", *pin)
                reporter.line(f"{box.label}: reading back its firmware...")
                after = box_dir / "after"
                command(box_dir, "snapshot", "--usb", usb, "--output-dir", after, "--wait", APP_RETURN_SECONDS)
                evidence = protocol.verify_readback(image, after, manifest,
                                                    expected_bootloader=(before / "bootloader.bin").read_bytes())
                atomic_write_text(box_dir / "verification.json", json.dumps(evidence, indent=2) + "\n", mode=0o600)
                results[box.name] = ("Valve Oil Box firmware installed" if target == "patched" else "QIDI stock firmware restored") + " and verified"
                kinds[box.name] = "flashed" if target == "patched" else "restored"
                if target == "patched":
                    _clear_unsupported(paths, [box.number])
                pending = False
                record.update(phase="verified", box=box.name)
                atomic_write_text(marker, json.dumps(record, indent=2) + "\n", mode=0o600)
        except Exception as exc:
            failure = exc
        else:
            failure = None
        if pending:
            box = record.get("box")
            number = next((b.number for b, _ in plan if b.name == box), "N")
            reporter.line(f"{box} firmware is unverified. Klipper and the touchscreen stay stopped. "
                          f"Run ./box-firmware.sh rescue --box {number} and follow its prompt.")
            raise BoxFirmwareError("Box firmware operation failed: " + str(failure) + ". Recovery files: " + str(directory)) from failure
        start_recorded_services(states, run=session, password=password, paths=paths, urlopen=urlopen, sleep=sleep, clock=clock,
                                marker=marker, subject=SUBJECT, error=BoxFirmwareError)
    labels = {box.name: box.label for box in configured}
    for name, outcome in results.items():
        reporter.line(f"{labels.get(name, name)}: {outcome}")
    if outcomes is not None:
        outcomes.update({name: (kinds.get(name, "skipped"), text) for name, text in results.items()})
    _settle_holds(paths, action, confirmed, release, selected, kinds)
    if failure is not None:
        raise BoxFirmwareError("Box firmware operation stopped before any write: " + str(failure))
    return 0


def _settle_holds(paths, action, confirmed, release, selected, kinds) -> None:
    if action == "restore":
        restored = {(box.name, box.serial) for box in selected if kinds.get(box.name) in ("restored", "current")}
        _write_holds(paths, release, held_boxes(paths, release) | restored)
    elif action == "install" and not confirmed:
        # The operator asked for these Boxes again.
        _write_holds(paths, release, held_boxes(paths, release) - {(box.name, box.serial) for box in selected})


def flash_box_firmware_after_install(*, paths, reporter, dry_run=False, environ=None, input_stream=None, **seams) -> None:
    """Bring every USB Box to the bundled firmware at the end of an install.

    The installer was run or automatic updates were turned on, so no FLASH prompt.
    Anything that stops before a write leaves the install successful and says so.
    An unverified flash still stops here with the rescue instructions.
    """
    env = {} if environ is None else environ
    resolve_usb = seams.get("resolve_usb", usb_path_for_serial)
    if not discover_boxes(paths, resolve_usb=resolve_usb):
        return
    if dry_run:
        reporter.line("Box firmware dry-run: would flash the Valve Oil Box firmware to each USB Box that needs it.")
        return
    try:
        if not seams.get("services_stopped"):
            wait_for_thr_printer_ready(paths, urlopen=seams.get("urlopen", urllib.request.urlopen),
                                       sleep=seams.get("sleep", time.sleep), clock=seams.get("clock", time.monotonic),
                                       reporter=reporter, subject=SUBJECT)
        run_box_firmware(paths=paths, reporter=reporter, action="install", input_stream=input_stream,
                         environ=env, confirmed=True, **seams)
    except InstallerError as exc:
        if (paths.printer_data_root / BOX_RECOVERY_MARKER).exists():
            raise
        reporter.line("Box firmware not updated: " + str(exc))
        reporter.line("Run the installer again, or ./box-firmware.sh install, once this is resolved.")


def _write_image(directory: Path, image: bytes) -> Path:
    path = directory / "image.bin"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
        handle.write(image)
        handle.flush()
        os.fsync(handle.fileno())
    return path
