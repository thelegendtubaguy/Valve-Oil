from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import messages
from .board_guard import critical_window, detached
from .board_images import board_manifest_path, installed_printer, pinned_for_installed_printer, runs_target_firmware
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
                # Every heater Klipper lists is checked below; the Smart 5 has no chamber heater.
                or not {"extruder", "heater_bed"}.issubset(heaters)):
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
BACKUP_DIRECTORY = ".vo_thr_firmware_backups"

# Finishes, at boot, a flash that the installer started and didn't finish: the record (THR_RECOVERY_MARKER) is in place
# from the first erase until the THR is verified. Installed and enabled before the first erase; it runs nothing while
# no record exists.
RESCUE_UNIT = "vo-thr-firmware.service"
RESCUE_UNIT_MARKER = "# valve-oil:thr-firmware-rescue\n"
SYSTEMD_DIR = Path("/etc/systemd/system")
# The unit's first step, root-owned: it reaches the THR before the THR tool can (installer/system/vo_thr_catch.py).
CATCH_SCRIPT = Path("/usr/local/lib/vo_thr_catch.py")
# The same catch compiled on the printer (installer/system/vo_thr_catch.c), which starts about 0.4 s sooner; the generator
# starts it when it is in place, and vo_thr_catch.py --early otherwise.
CATCH_BINARY = Path("/usr/local/lib/vo_thr_catch")
# Starts that catch about a second after the kernel, before any unit, while a record exists.
GENERATOR = Path("/etc/systemd/system-generators/vo-thr-catch")
GENERATOR_MARKER = "# valve-oil:thr-catch-generator\n"
RESCUE_WAIT_SECONDS = 30 * 60
POWER_CYCLE = ("Turn the printer off with its power switch, wait 10 seconds, and turn it on again: Valve Oil finishes the "
               "THR firmware as the printer starts.")


def _stock_thr_image(paths, backup_root, manifest) -> bytes | None:
    from scripts.flash_thr_firmware import digest
    thr = manifest["thr"]
    candidates = sorted(backup_root.glob("thr-*/before/application.bin"))
    # QIDI names the image after the printer's package (QD_MAX4_, QD_PLUS5_, S5_); the hash says which it is.
    candidates += sorted((paths.printer_data_root.parent / "update").glob("*_THR_" + thr["stock_version"] + "_*.bin"))
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

    _, printer, _ = installed_printer(paths)
    manifest_path = board_manifest_path(paths.bundle_root, printer, "thr")
    manifest = json.loads((paths.bundle_root / manifest_path).read_text())
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
        if service.endswith(".timer") and values.get("LoadState") == "not-found":
            continue
        if result.returncode or values.get("LoadState") != "loaded" or values.get("ActiveState") not in {"active", "inactive"}:
            raise ThrFirmwareError("Cannot establish serial service ownership: " + service)
        states[service] = values["ActiveState"]
    password = authenticate_sudo(run=run, environ=env, reporter=reporter, input_stream=input_stream)
    if not services_stopped:
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
    command = _tool_command(session, python, tool, Path(manifest_path).name, directory)

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
                if not protocol.identify_bootloader(boot).recoverable:
                    raise ThrFirmwareError("This THR bootloader has no validated recovery; THR firmware was not changed.")
                image_path = directory / "patched-thr.bin"
                atomic_write_bytes(image_path, image, mode=0o600)
                progress = directory / "progress.json"
                # Before the first erase: what the boot unit, or a later run, needs to finish the flash.
                install_rescue_unit(paths, run=session, password=password)
                record.update(phase="programming", rescue={
                    "image": str(image_path), "backup": str(stock_backup), "progress": str(progress),
                    "manifest": Path(manifest_path).name, "firmware": pinned.firmware})
                atomic_write_text(marker, json.dumps(record, indent=2) + "\n", mode=0o600)
                firmware_may_have_changed = True
                reporter.line("Flashing Valve Oil's THR firmware; keep printer power on...")
                try:
                    transfer = command("transfer", "--image", image_path, "--backup-dir", stock_backup, "--reset",
                                       "--confirm-write", "--firmware", pinned.firmware, "--recover-to-stock",
                                       "--progress", progress, allow=(protocol.FELL_BACK, protocol.END_UNCONFIRMED))
                    fell_back = transfer.returncode == protocol.FELL_BACK
                    sleep(12)
                    after = directory / "after"
                    reporter.line("Reading back the complete THR application and bootloader...")
                    command("snapshot", "--output-dir", after)
                    evidence = protocol.verify_readback(app if fell_back else image, after, manifest, expected_bootloader=boot)
                except Exception as exc:
                    reporter.line("The THR flash didn't finish (" + str(exc) + "). Finishing it...")
                    outcome, evidence = rescue(paths, record, command=command, manifest=manifest)
                    fell_back = outcome == FELL_BACK
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
                reporter.line(POWER_CYCLE)
    if fell_back:
        reporter.line("Valve Oil's THR firmware was not applied: the transfer failed and the THR runs a verified firmware it held before. Previous service states restored. Recovery files: " + str(directory))
        return FELL_BACK
    reporter.line("THR firmware verified; bootloader unchanged and previous service states restored.")
    if not firmware_may_have_changed:
        return CURRENT
    reporter.line("Run input shaper calibration now, before bed leveling or printing: your saved calibration was measured with the old THR firmware. The installer has not run calibration.")
    return FLASHED


def _tool_command(session, python, tool, manifest_name, directory):
    """The THR tool, with its output kept in the run's backup directory."""
    def command(operation, *arguments, allow=()):
        result = session(
            [str(python), str(tool), operation, *map(str, arguments), "--printer-idle-confirmed",
             "--manifest", manifest_name],
            capture_output=True, text=True, timeout=None if operation == "rescue" else 960,
        )
        atomic_write_text(directory / (operation + ".stdout"), result.stdout or "", mode=0o600)
        atomic_write_text(directory / (operation + ".stderr"), result.stderr or "", mode=0o600)
        if result.returncode and result.returncode not in allow:
            raise ThrFirmwareError("THR " + operation + " failed; see recovery files: " + str(directory))
        return result
    return command


def rescue(paths, record, *, command, manifest):
    """Finish the record's flash with the tool's rescue and settle it; returns (FLASHED or FELL_BACK, evidence)."""
    from scripts import flash_thr_firmware as protocol
    directory = Path(record["backup_directory"])
    (directory / protocol.RESCUE_RESULT).unlink(missing_ok=True)
    command("rescue", "--record", paths.printer_data_root / THR_RECOVERY_MARKER, allow=(protocol.FELL_BACK,))
    return settle(record, manifest)


def settle(record, manifest):
    """The verified outcome of the record's last rescue: (FLASHED, evidence) when the THR holds the pinned image,
    (FELL_BACK, evidence) when it holds another admitted image. Raises ThrFirmwareError otherwise."""
    from scripts import flash_thr_firmware as protocol
    job = record["rescue"]
    directory = Path(record["backup_directory"])
    try:
        result = json.loads((directory / protocol.RESCUE_RESULT).read_text())
        readback = Path(result["readback"])
        image = Path(job["image"]).read_bytes()
        boot = (Path(job["backup"]) / "bootloader.bin").read_bytes()
        if result["exit"] not in (0, protocol.FELL_BACK):
            raise ThrFirmwareError("The THR rescue did not finish the flash; THR state is unknown.")
        held = image if result["exit"] == 0 else (readback / "application.bin").read_bytes()
        evidence = protocol.verify_readback(held, readback, manifest, expected_bootloader=boot)
    except ThrFirmwareError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ThrFirmwareError("The THR rescue's readback could not be verified: " + str(exc)) from exc
    atomic_write_text(directory / "verification.json", json.dumps(evidence, indent=2) + "\n", mode=0o600)
    return (FLASHED if result["exit"] == 0 else FELL_BACK), evidence


def _record(paths):
    marker = paths.printer_data_root / THR_RECOVERY_MARKER
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and isinstance(record.get("rescue"), dict) else None


def _manifest(paths, record):
    return json.loads((paths.bundle_root / "firmware" / Path(record["rescue"]["manifest"]).name).read_text())


def _unit_active(run) -> bool:
    try:
        result = run(["systemctl", "is-active", RESCUE_UNIT], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return (result.stdout or "").strip() in ("active", "activating", "deactivating", "reloading")


def finish_pending(*, paths, reporter, environ=None, input_stream=None, run=None, urlopen=urllib.request.urlopen,
                   sleep=time.sleep, clock=time.monotonic) -> str | None:
    """A THR flash an earlier run left unfinished, finished before this run does anything else, as the boot unit would.
    Returns FLASHED or FELL_BACK, or None when there is nothing to finish here. A record written before this release
    has no rescue in it and stays for ensure_no_pending_firmware to stop at. Raises ThrFirmwareError when the flash
    can't be finished; the record stays."""
    from scripts import flash_thr_firmware as protocol
    from .board_firmware import mark_thr_flashed

    run = subprocess.run if run is None else run
    env = {} if environ is None else environ
    marker = paths.printer_data_root / THR_RECOVERY_MARKER
    if _record(paths) is None:
        return None
    deadline = clock() + RESCUE_WAIT_SECONDS
    while _unit_active(run):
        if clock() >= deadline:
            raise ThrFirmwareError(f"{RESCUE_UNIT} is still finishing the THR firmware; try again when it has stopped.")
        sleep(5)
    record = _record(paths)
    if record is None:
        return None  # the boot unit finished it
    reporter.line("A THR firmware flash didn't finish; finishing it now. Keep the printer powered on.")
    try:
        check_thr_printer_ready(paths, urlopen=urlopen)
    except ThrPrinterNotReady:
        pass  # Klipper can't be ready without a working THR, and with it neither prints nor heats.
    password = authenticate_sudo(run=run, environ=env, reporter=reporter, input_stream=input_stream)
    states = {}
    for service in protocol.SERVICES:
        result = run(["systemctl", "is-active", service], capture_output=True, text=True, timeout=5)
        states[service] = "active" if (result.stdout or "").strip() == "active" else "inactive"
    session = detached(run)
    directory = Path(record["backup_directory"])
    python = paths.printer_data_root.parent / "klippy-env/bin/python"
    command = _tool_command(session, python, paths.bundle_root / "scripts/flash_thr_firmware.py",
                            record["rescue"]["manifest"], directory)
    with critical_window(reporter) as reporter:
        # The boot unit this bundle writes, should this rescue not finish either.
        install_rescue_unit(paths, run=session, password=password)
        for service in reversed(protocol.SERVICES):
            if states[service] == "active":
                run_sudo(["systemctl", "stop", service], run=session, password=password, timeout=60)
        try:
            outcome, _ = rescue(paths, record, command=command, manifest=_manifest(paths, record))
        except ThrFirmwareError as exc:
            reporter.line("The THR firmware could not be finished: " + exc.message + " " + POWER_CYCLE)
            raise
        if outcome == FLASHED:
            mark_thr_flashed(paths)
        for service in protocol.SERVICES:
            if states[service] == "active" or service == "klipper.service":
                run_sudo(["systemctl", "start", service], run=session, password=password, timeout=60)
        wait_for_thr_printer_ready(paths, urlopen=urlopen, sleep=sleep, clock=clock)
        atomic_delete(marker)
    reporter.line("The THR firmware is finished and verified." if outcome == FLASHED else
                  "The THR runs a verified firmware it held before; Valve Oil's THR firmware is flashed again on the "
                  "next install or update.")
    if outcome == FLASHED:
        reporter.line("Run input shaper calibration now, before bed leveling or printing.")
    return outcome


def boot_main(argv, *, bundle_root) -> int:
    """The boot unit's second step, after the tool's rescue: settle the record. Klipper starts after the unit, so no
    service is touched here."""
    from .board_firmware import mark_thr_flashed
    from .cli import resolve_runtime_paths

    paths = resolve_runtime_paths(bundle_root=bundle_root, environ=dict(os.environ))
    record = _record(paths)
    if record is None:
        return 0
    try:
        outcome, _ = settle(record, _manifest(paths, record))
    except ThrFirmwareError as exc:
        sys.stderr.write(exc.message + "\n")
        return 1
    if outcome == FLASHED:
        mark_thr_flashed(paths)
    atomic_delete(paths.printer_data_root / THR_RECOVERY_MARKER)
    return 0


# ---- the boot unit --------------------------------------------------------------------------------------------------


def rescue_unit_text(paths, bundle: Path) -> str:
    from .auto_update import current_user
    record = paths.printer_data_root / THR_RECOVERY_MARKER
    klippy = paths.printer_data_root.parent / "klippy-env/bin/python"
    python = sys.executable or "/usr/bin/python3"
    return (RESCUE_UNIT_MARKER
            + "# Finishes, at boot, a THR firmware flash the installer didn't finish, before Klipper starts. Runs only "
            "while its record exists. Installed by Valve Oil.\n"
            "[Unit]\n"
            "Description=Valve Oil THR firmware rescue\n"
            "DefaultDependencies=no\n"
            "After=local-fs.target\n"
            "Before=klipper.service qidi-client.service vo-auto-update.service\n"
            f"ConditionPathExists={record}\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"User={current_user()}\n"
            f"WorkingDirectory={bundle}\n"
            f"Environment=VO_PRINTER_DATA_ROOT={paths.printer_data_root}\n"
            # Reach the THR inside its startup window, and give its port, still root's alone, the group udev gives it
            # later in boot.
            f"ExecStartPre=-+/usr/bin/python3 -I -S {CATCH_SCRIPT}\n"
            # The tool next, without the installer's imports.
            f"ExecStart=-{klippy} {bundle / 'scripts/flash_thr_firmware.py'} rescue --record {record} "
            "--printer-idle-confirmed --at-boot\n"
            f"ExecStart={python} -I -S {bundle / 'installer/runtime/bootstrap.py'} thr-rescue --plain\n"
            f"TimeoutStartSec={RESCUE_WAIT_SECONDS}\n\n"
            "[Install]\n"
            "WantedBy=sysinit.target\n")


def generator_text(paths) -> str:
    record = paths.printer_data_root / THR_RECOVERY_MARKER
    return ("#!/bin/sh\n" + GENERATOR_MARKER
            + "# While a THR firmware flash is unfinished, reaches the THR's bootloader inside its startup window, before\n"
            "# any unit starts: the catch, compiled where it could be, holds it until vo-thr-firmware.service joins it.\n"
            "# Once per boot and only in its first 30 seconds, since systemd also runs generators at every daemon-reload,\n"
            "# when Klipper may own the THR's port. Installed by Valve Oil.\n"
            f"[ -e '{record}' ] || exit 0\n"
            "[ -e /run/vo-thr-catch.started ] && exit 0\n"
            "read -r up _ < /proc/uptime\n"
            '[ "${up%%.*}" -lt 30 ] || exit 0\n'
            ": > /run/vo-thr-catch.started\n"
            f"if [ -x {CATCH_BINARY} ]; then\n"
            f"  setsid {CATCH_BINARY} --early < /dev/null > /dev/null 2>&1 &\n"
            "else\n"
            f"  setsid /usr/bin/python3 -I -S {CATCH_SCRIPT} --early < /dev/null > /dev/null 2>&1 &\n"
            "fi\n"
            "exit 0\n")


def _catch_build(run, binary: Path) -> str | None:
    """What `binary --check` reports: the sha256 of the source it was built from; None when it doesn't run."""
    try:
        result = run([str(binary), "--check"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def install_catch_binary(paths, bundle, *, run, password) -> bool:
    """Compile the catch with the printer's gcc and install it at CATCH_BINARY, unless the one there was built from
    this source. True when it is in place; otherwise none is, and the generator starts vo_thr_catch.py --early."""
    source = bundle / "installer/system/vo_thr_catch.c"
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    if _catch_build(run, CATCH_BINARY) == source_sha256:
        return True
    staged = paths.printer_data_root / BACKUP_DIRECTORY / CATCH_BINARY.name
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.unlink(missing_ok=True)
    try:
        try:
            built = run(["gcc", "-O2", f'-DSOURCE_SHA256="{source_sha256}"', "-o", str(staged), str(source)],
                        capture_output=True, text=True, timeout=120).returncode == 0
        except (OSError, subprocess.SubprocessError):
            built = False
        if built and _catch_build(run, staged) == source_sha256 and not run_sudo(
                ["install", "-m", "0755", str(staged), str(CATCH_BINARY)], run=run, password=password,
                timeout=60).returncode:
            return True
    finally:
        staged.unlink(missing_ok=True)
    # Never leave a catch built from other source for the generator to start.
    run_sudo(["rm", "-f", str(CATCH_BINARY)], run=run, password=password, timeout=60)
    return False


def install_rescue_unit(paths, *, run, password) -> None:
    from .firmware_update import _settled_bundle
    target = SYSTEMD_DIR / RESCUE_UNIT
    bundle = _settled_bundle(paths)
    catch = bundle / "installer/system/vo_thr_catch.py"
    try:
        current_catch = CATCH_SCRIPT.read_bytes()
    except OSError:
        current_catch = None
    if current_catch != catch.read_bytes():
        if run_sudo(["install", "-m", "0644", str(catch), str(CATCH_SCRIPT)], run=run, password=password,
                    timeout=60).returncode:
            raise ThrFirmwareError(f"Could not install {CATCH_SCRIPT}; THR firmware was not changed.")
    install_catch_binary(paths, bundle, run=run, password=password)
    try:
        current_generator = GENERATOR.read_text(encoding="utf-8")
    except OSError:
        current_generator = None
    if current_generator is not None and not current_generator.startswith("#!/bin/sh\n" + GENERATOR_MARKER):
        raise ThrFirmwareError(f"Existing {GENERATOR} is unrecognized and was preserved; THR firmware was not changed.")
    if current_generator != generator_text(paths):
        staged = paths.printer_data_root / BACKUP_DIRECTORY / GENERATOR.name
        atomic_write_text(staged, generator_text(paths), mode=0o644)
        try:
            for step in (["mkdir", "-p", str(GENERATOR.parent)], ["install", "-m", "0755", str(staged), str(GENERATOR)]):
                if run_sudo(step, run=run, password=password, timeout=60).returncode:
                    raise ThrFirmwareError(f"Could not install {GENERATOR}; THR firmware was not changed.")
        finally:
            staged.unlink(missing_ok=True)
    text = rescue_unit_text(paths, bundle)
    try:
        current = target.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current is not None and not current.startswith(RESCUE_UNIT_MARKER):
        raise ThrFirmwareError(f"Existing {target} is unrecognized and was preserved; THR firmware was not changed.")
    if current != text:
        staged = paths.printer_data_root / BACKUP_DIRECTORY / RESCUE_UNIT
        atomic_write_text(staged, text, mode=0o644)
        try:
            for step in (["install", "-m", "0644", str(staged), str(target)], ["systemctl", "daemon-reload"]):
                if run_sudo(step, run=run, password=password, timeout=60).returncode:
                    raise ThrFirmwareError(f"Could not install {target}; THR firmware was not changed.")
        finally:
            staged.unlink(missing_ok=True)
    if run_sudo(["systemctl", "enable", RESCUE_UNIT], run=run, password=password, timeout=60).returncode:
        raise ThrFirmwareError(f"Could not enable {RESCUE_UNIT}; THR firmware was not changed.")
    # Neither install nor systemctl writes through to storage; a power cut after the first erase must find the unit.
    os.sync()


def refresh_rescue_unit(paths, *, run, password) -> bool:
    """Rewrite an installed rescue unit, its generator, and the catch where they still name what releases before
    26.11.22.2 called tltg. Nothing is installed where there is no rescue unit, or while a flash is unfinished."""
    try:
        current = (SYSTEMD_DIR / RESCUE_UNIT).read_text(encoding="utf-8")
    except OSError:
        return False
    if not current.startswith(RESCUE_UNIT_MARKER) or (paths.printer_data_root / THR_RECOVERY_MARKER).exists():
        return False
    texts = [current]
    for path in (GENERATOR, CATCH_SCRIPT):
        try:
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    if not any("tltg" in text for text in texts):
        return False
    (paths.printer_data_root / BACKUP_DIRECTORY).mkdir(mode=0o700, exist_ok=True)
    install_rescue_unit(paths, run=run, password=password)
    return True


def remove_rescue_unit(*, run, password) -> None:
    """Uninstall: disable and remove the unit, which an uninstall leaves only when no THR record exists."""
    target = SYSTEMD_DIR / RESCUE_UNIT
    try:
        if not target.read_text(encoding="utf-8").startswith(RESCUE_UNIT_MARKER):
            return
    except OSError:
        return
    for step in (["systemctl", "disable", RESCUE_UNIT],
                 ["rm", "-f", str(target), str(CATCH_SCRIPT), str(CATCH_BINARY), str(GENERATOR)],
                 ["systemctl", "daemon-reload"]):
        run_sudo(step, run=run, password=password, timeout=60)
