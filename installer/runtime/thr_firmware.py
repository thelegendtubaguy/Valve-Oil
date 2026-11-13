from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import messages
from .board_guard import critical_window, detached
from .board_images import BOARD_MANIFESTS, pinned_for_installed_printer, runs_target_firmware
from .errors import InstallerError
from .fs_atomic import atomic_delete, atomic_write_bytes, atomic_write_text, fsync_directory
from .naming import ISSUES_URL
from .path_safety import ensure_runtime_path_has_no_symlink_components
from .safety import THR_RECOVERY_MARKER
from .sudo import authenticate_sudo, run_sudo


class ThrFirmwareError(InstallerError):
    pass


class ThrPrinterNotReady(ThrFirmwareError):
    pass


READY_TIMEOUT_SECONDS = 120


WARNING = (
    "THR firmware: do not power off the printer until this finishes. "
    "Rerun input shaper calibration before printing."
)


def _query(paths, names, urlopen, subject="THR firmware"):
    parts = urllib.parse.urlsplit(paths.moonraker_url)
    url = urllib.parse.urlunsplit(parts._replace(query="&".join(urllib.parse.quote(n, safe="") for n in names)))
    with urlopen(url, timeout=5) as response:
        status = json.load(response)["result"]["status"]
    if status["print_stats"]["state"] not in {"standby", "complete", "cancelled", "error"}:
        raise ThrFirmwareError(subject + " requires an idle printer.")
    if status["webhooks"]["state"] != "ready" or status["idle_timeout"]["state"] not in {"Ready", "Idle"}:
        raise ThrPrinterNotReady(subject + " requires Klipper ready and idle.")
    return status


def check_thr_printer_ready(paths, *, urlopen=urllib.request.urlopen, subject="THR firmware"):
    try:
        names = ("print_stats", "webhooks", "idle_timeout", "heaters")
        status = _query(paths, names, urlopen, subject)
        heaters = status["heaters"]["available_heaters"]
        if (not isinstance(heaters, list) or not 1 <= len(heaters) <= 32
                or not all(isinstance(n, str) and n for n in heaters)
                or len(set(heaters)) != len(heaters)
                or not {"extruder", "heater_bed", "heater_generic chamber"}.issubset(heaters)):
            raise ValueError("Incomplete heater registry")
        status = _query(paths, (*names, *heaters), urlopen, subject)
        if status["heaters"]["available_heaters"] != heaters:
            raise ValueError("Heater registry changed during preflight")
        for name in heaters:
            target = status[name]["target"]
            if (isinstance(target, bool) or not isinstance(target, (int, float))
                    or not math.isfinite(target) or target != 0):
                raise ThrFirmwareError(subject + " requires every heater target at zero: " + name)
    except ThrFirmwareError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ThrPrinterNotReady("Could not verify idle/ready state and all heater targets for " + subject + ".") from exc


def wait_for_thr_printer_ready(paths, *, urlopen, sleep, clock, reporter=None, subject="THR firmware"):
    deadline = clock() + READY_TIMEOUT_SECONDS
    waiting = False
    while True:
        try:
            return check_thr_printer_ready(paths, urlopen=urlopen, subject=subject)
        except ThrPrinterNotReady:
            if clock() >= deadline:
                raise
            if reporter is not None and not waiting:
                reporter.line("Waiting for Klipper to be ready before " + subject + "...")
            waiting = True
            sleep(1)


# Outcomes of a THR run, as board_firmware reports them.
CURRENT, FLASHED, FELL_BACK, NOT_ADMITTED = "current", "flashed", "fell_back", "not_admitted"
BACKUP_DIRECTORY = ".tltg_thr_firmware_backups"


def _stock_thr_image(paths, backup_root, manifest) -> bytes | None:
    from scripts.flash_thr_firmware import digest
    thr = manifest["thr"]
    candidates = sorted(backup_root.glob("thr-*/before/application.bin"))
    # QIDI names the image after the printer's package (QD_MAX4_, QD_PLUS5_); both carry the same THR image.
    candidates += sorted((paths.printer_data_root.parent / "update").glob("QD_*_THR_" + thr["stock_version"] + "_*.bin"))
    for candidate in candidates:
        try:
            data = candidate.read_bytes()
        except OSError:
            continue
        if len(data) == thr["size"] and digest(data) == thr["stock_sha256"]:
            return data
    return None


def flash_thr_firmware(
    *, paths, reporter, dry_run=False, input_stream=None, environ=None,
    run=None, urlopen=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic,
    pinned=None, services_stopped=False,
) -> str | None:
    """Bring the THR to the image pinned for the printer's target firmware; returns CURRENT, FLASHED, FELL_BACK, or
    NOT_ADMITTED, or None for a dry run.

    services_stopped: the caller already checked the printer and stopped Klipper, the touchscreen, and the update timer,
    and starts them again itself, as a firmware update does.
    """
    from scripts import flash_thr_firmware as protocol
    from scripts.build_thr_firmware import patch_thr

    run = subprocess.run if run is None else run
    env = {} if environ is None else environ
    pinned = pinned_for_installed_printer(paths, "thr") if pinned is None else pinned
    # Valve Oil's THR firmware is validated under the Klipper of the firmware it is pinned for, so a printer on an
    # older QIDI firmware gets it only after the QIDI firmware update.
    if not runs_target_firmware(paths, "thr", pinned.firmware):
        raise ThrFirmwareError(f"The THR will be updated after the QIDI firmware update to {pinned.firmware}.")
    if paths.restart_marker_path.exists():
        raise ThrFirmwareError("Complete pending Klipper source activation before THR firmware programming.")
    reporter.line(WARNING)
    if not services_stopped:
        wait_for_thr_printer_ready(paths, urlopen=urlopen, sleep=sleep, clock=clock, reporter=reporter)
    if dry_run:
        reporter.line("THR firmware dry-run: would back up and admit THR, skip a board already on Valve Oil's THR firmware, or flash and verify it. No UART or services accessed.")
        return None

    manifest = json.loads((paths.bundle_root / BOARD_MANIFESTS["thr"]).read_text())
    tool = paths.bundle_root / "scripts/flash_thr_firmware.py"
    python = paths.printer_data_root.parent / "klippy-env/bin/python"
    if not tool.is_file() or tool.is_symlink() or not python.is_file():
        raise ThrFirmwareError("THR firmware requires the packaged tool and existing klippy-env Python.")
    marker = paths.printer_data_root / THR_RECOVERY_MARKER
    backup_root = paths.printer_data_root / BACKUP_DIRECTORY
    for target in (marker, backup_root):
        ensure_runtime_path_has_no_symlink_components(printer_data_root=paths.printer_data_root, target=target)
    if marker.exists():
        raise ThrFirmwareError("Unresolved THR firmware operation: " + str(marker))
    states = {}
    for service in () if services_stopped else protocol.SERVICES:
        result = run(["systemctl", "show", service, "--property=LoadState,ActiveState"], capture_output=True, text=True, timeout=5)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if service == "tltg-optimized-auto-update.timer" and values.get("LoadState") == "not-found":
            continue
        if result.returncode or values.get("LoadState") != "loaded" or values.get("ActiveState") not in {"active", "inactive"}:
            raise ThrFirmwareError("Cannot establish serial service ownership: " + service)
        states[service] = values["ActiveState"]
    password = None
    if not services_stopped:
        password = authenticate_sudo(run=run, environ=env, reporter=reporter, input_stream=input_stream)
        wait_for_thr_printer_ready(paths, urlopen=urlopen, sleep=sleep, clock=clock, reporter=reporter)
    backup_root.mkdir(mode=0o700, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="thr-", dir=backup_root))
    fsync_directory(backup_root)
    record = {"backup_directory": str(directory), "services": states, "phase": "stopping_services"}
    atomic_write_text(marker, json.dumps(record, indent=2) + "\n", mode=0o600)
    reporter.line("THR recovery files: " + str(directory))
    firmware_may_have_changed = False
    verified = False
    fell_back = False
    unsupported_summary = None

    # From here to the services' restart the terminal's hangup and Ctrl-C can't stop the work (board_guard).
    session = detached(run)

    def command(operation, *arguments, allow=()):
        result = session(
            [str(python), str(tool), operation, *map(str, arguments), "--printer-idle-confirmed"],
            capture_output=True, text=True, timeout=960,
        )
        atomic_write_text(directory / (operation + ".stdout"), result.stdout or "", mode=0o600)
        atomic_write_text(directory / (operation + ".stderr"), result.stderr or "", mode=0o600)
        if result.returncode and result.returncode not in allow:
            raise ThrFirmwareError("THR " + operation + " failed; see recovery files: " + str(directory))
        return result

    def service_action(action, service):
        result = run_sudo(["systemctl", action, service], run=session, password=password, timeout=60)
        if result.returncode:
            raise ThrFirmwareError("Could not " + action + " " + service)

    with critical_window(reporter) as reporter:
        try:
            for service in reversed(protocol.SERVICES):
                if service in states:
                    service_action("stop", service)
            before = directory / "before"
            reporter.line("Reading and admitting THR application and bootloader...")
            snapshot = command("snapshot", "--output-dir", before, allow=(protocol.UNSUPPORTED_BOOTLOADER,))
            if snapshot.returncode == protocol.UNSUPPORTED_BOOTLOADER:
                boot = (before / "bootloader.bin").read_bytes()
                region = (before / "application-region.bin").read_bytes()
                summary = json.loads((before / "report.json").read_text())
                if (len(boot) != 0x8000 or len(region) != 0x18000
                        or protocol.digest(boot) in protocol.thr_bootloaders.ADMITTED_BOOTLOADERS
                        or summary.get("bootloader_sha256") != protocol.digest(boot)
                        or summary.get("application_region_sha256") != protocol.digest(region)):
                    raise ThrFirmwareError("Incomplete unsupported-bootloader capture")
                fsync_directory(before)
                fsync_directory(directory)
                unsupported_summary = before / "report.json"
                return NOT_ADMITTED
            boot, app = protocol.load_backup(before, manifest)
            for path in before.iterdir():
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
            fsync_directory(before)
            fsync_directory(directory)
            if protocol.digest(app) == pinned.sha256:
                evidence = protocol.verify_readback(app, before, manifest)
                reporter.line("Valve Oil's THR firmware is already installed; no reset, erase, or transfer required.")
            else:
                stock_backup = before
                if protocol.digest(app) != manifest["thr"]["stock_sha256"]:
                    # An earlier build of the fix: the update is built from, and falls back to,
                    # QIDI's stock image, which the board no longer holds.
                    stock = _stock_thr_image(paths, backup_root, manifest)
                    if stock is None:
                        raise ThrFirmwareError("Updating the earlier THR FIFO fix needs QIDI's THR firmware "
                                               + manifest["thr"]["stock_version"] + " from the THR firmware backups or "
                                               "~/update; none was found. THR firmware was not changed.")
                    stock_backup = directory / "stock"
                    stock_backup.mkdir(mode=0o700)
                    atomic_write_bytes(stock_backup / "application.bin", stock, mode=0o600)
                    atomic_write_bytes(stock_backup / "bootloader.bin", boot, mode=0o600)
                    fsync_directory(directory)
                    app = stock
                    reporter.line("THR has an earlier build of the FIFO fix; updating it from QIDI's stock THR firmware.")
                image = patch_thr(app, manifest) if pinned.is_project_build else app
                if protocol.digest(image) != pinned.sha256:
                    raise ThrFirmwareError("The THR image does not match the image pinned for QIDI firmware " + pinned.firmware)
                image_path = directory / "patched-thr.bin"
                atomic_write_bytes(image_path, image, mode=0o600)
                record["phase"] = "programming"
                atomic_write_text(marker, json.dumps(record, indent=2) + "\n", mode=0o600)
                firmware_may_have_changed = True
                reporter.line("Flashing Valve Oil's THR firmware; keep printer power on...")
                recovery = ("--recover-to-stock",) if protocol.identify_bootloader(boot).recoverable else ()
                transfer = command("transfer", "--image", image_path, "--backup-dir", stock_backup, "--reset", "--confirm-write",
                                   "--firmware", pinned.firmware, *recovery,
                                   allow=(protocol.FELL_BACK, protocol.END_UNCONFIRMED) if recovery else ())
                fell_back = transfer.returncode == protocol.FELL_BACK
                sleep(12)
                after = directory / "after"
                reporter.line("Reading back the complete THR application and bootloader...")
                command("snapshot", "--output-dir", after)
                evidence = protocol.verify_readback(app if fell_back else image, after, manifest, expected_bootloader=boot)
            atomic_write_text(directory / "verification.json", json.dumps(evidence, indent=2) + "\n", mode=0o600)
            verified = True
        except Exception as exc:
            raise ThrFirmwareError("THR firmware operation failed; configuration installation is retained. Recovery files: " + str(directory) + ". " + str(exc)) from exc
        finally:
            if verified or not firmware_may_have_changed:
                try:
                    if states.get("klipper.service") == "active":
                        reporter.line(messages.KLIPPER_SERVICE_RESTARTING)
                    # Every service is started even when one fails; the record stays until all of them run.
                    failed = []
                    for service in protocol.SERVICES:
                        if states.get(service) == "active":
                            try:
                                service_action("start", service)
                            except ThrFirmwareError:
                                failed.append(service)
                    if failed:
                        raise ThrFirmwareError("Could not start " + ", ".join(failed))
                    if states.get("klipper.service") == "active":
                        wait_for_thr_printer_ready(paths, urlopen=urlopen, sleep=sleep, clock=clock)
                    atomic_delete(marker)
                except BaseException as exc:
                    raise ThrFirmwareError("THR service restoration/readiness is incomplete; recovery marker retained: " + str(marker)) from exc
                if unsupported_summary is not None:
                    reporter.line("Valve Oil's THR firmware is unavailable for this bootloader. Your THR firmware was not changed. Please attach this shareable summary to a GitHub issue at " + ISSUES_URL + ": " + str(unsupported_summary))
            else:
                reporter.line("THR firmware is unverified. Serial-owning services remain stopped. Recovery files: " + str(directory))
                reporter.line("Recover and verify THR before removing the firmware blocker: " + str(marker))
    if fell_back:
        reporter.line("Valve Oil's THR firmware was not applied: the transfer failed and the stock THR firmware was reprogrammed and verified. Previous service states restored. Recovery files: " + str(directory))
        return FELL_BACK
    reporter.line("THR firmware verified; bootloader unchanged and previous service states restored.")
    if not firmware_may_have_changed:
        return CURRENT
    reporter.line("Run input shaper calibration now, before bed leveling or printing: your saved calibration was measured with the old THR firmware. The installer has not run calibration.")
    return FLASHED
