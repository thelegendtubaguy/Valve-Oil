#!/usr/bin/env python3
"""Put a QIDI Max 4 back to QIDI's stock state after Valve Oil, as far as software can.

For a printer where an uninstall couldn't undo everything: one that had a QIDI update from a USB drive or a firmware
zip while Valve Oil was installed, after which the reinstall no longer knew what QIDI's files looked like before
Valve Oil, and uninstall doesn't change board firmware at all. Run it on the printer as `qidi`:

    python3 max4_back_to_stock.py           # each step asks first
    python3 max4_back_to_stock.py --check   # only report what is still Valve Oil's; nothing is changed

It downloads the Valve Oil release named below, checks its SHA-256, and runs that release's own tools from
~/vo-back-to-stock, without installing it:

1. Mainboard: QIDI's stock firmware back (mainboard-firmware.sh restore), then whether the X and Y closed-loop
   drivers report a version.
2. Toolhead board (THR): QIDI's stock application back, read back and verified (firmware/README.md, "Restore the
   stock THR firmware from backup").
3. USB Boxes: QIDI's stock firmware back (box-firmware.sh restore).
4. Boot logo: QIDI's boot partition back from the copy Valve Oil saved before it wrote its logo, only when the two
   differ in nothing but the two logo images and the image id.
5. System changes: Valve Oil's own restore code run with the record of changes found in the config backups Valve Oil
   made before QIDI's update deleted it; then Valve Oil's own units, programs, and drop-ins that are still there,
   which carry Valve Oil's marker, are archived and removed.

The closed-loop motor drivers aren't touched: Valve Oil never sent them anything but QIDI's own driver firmware, and
QIDI's firmware update from the touchscreen puts them on QIDI's version once they answer. Everything printed goes to
~/vo-back-to-stock/back-to-stock-<time>.log.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace

RELEASE = "26.11.25.1"
ARCHIVE = f"valve-oil-{RELEASE}.tar.gz"
ARCHIVE_URL = f"https://github.com/thelegendtubaguy/valve-oil/releases/download/v{RELEASE}/{ARCHIVE}"
ARCHIVE_SHA256 = "eb259e2e9248802fe38323bf7a6dc196ceb6a65ed0819af9a099543daaaefddd"
HOME = Path("/home/qidi")
PRINTER_DATA = HOME / "printer_data"
WORK = HOME / "vo-back-to-stock"
BUNDLE = WORK / "valve-oil"
KLIPPY_PYTHON = HOME / "klippy-env/bin/python"
MOONRAKER = "http://127.0.0.1:7125"
LOGGED_ENV = "VO_BACK_TO_STOCK_LOGGED"
NOT_STOCK = ("valve-oil", "tltg", "-vo-")
STATE_MEMBERS = ("config/vo_state.yaml", "config/tltg_optimized_state.yaml")
PRINTER_SERVICES = ("klipper.service", "qidi-client.service", "vo-auto-update.timer",
                    "tltg-optimized-auto-update.timer")


def say(text: str = "") -> None:
    print(text, flush=True)


def heading(text: str) -> None:
    say()
    say("== " + text)


def ask(question: str) -> bool:
    try:
        return input(question + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def query(*objects: str) -> dict:
    """Klipper objects through Moonraker, {} when it doesn't answer."""
    url = MOONRAKER + "/printer/objects/query?" + "&".join(urllib.parse.quote(item) for item in objects)
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.load(response)["result"]["status"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def klipper_state() -> str:
    try:
        with urllib.request.urlopen(MOONRAKER + "/printer/info", timeout=10) as response:
            return json.load(response)["result"]["state"]
    except (OSError, ValueError, KeyError, TypeError):
        return "unreachable"


def not_stock(version: str | None) -> bool:
    return bool(version) and any(mark in version for mark in NOT_STOCK)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---- setup -----------------------------------------------------------------------------------------------------------

def log_to_file() -> None:
    """Run again under `script`, which keeps a full copy of the session, prompts included, in a log file."""
    if os.environ.get(LOGGED_ENV) or not shutil.which("script") or not sys.stdin.isatty():
        return
    WORK.mkdir(parents=True, exist_ok=True)
    log = WORK / time.strftime("back-to-stock-%Y%m%d-%H%M%S.log")
    command = " ".join(_quote(part) for part in [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    os.environ[LOGGED_ENV] = str(log)
    os.execvp("script", ["script", "-q", "-e", "-c", command, str(log)])


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def fetch_bundle() -> None:
    marker = WORK / ".bundle-ok"
    if (BUNDLE / "installer/package.yaml").is_file() and marker.is_file() and marker.read_text().strip() == ARCHIVE_SHA256:
        return
    say(f"Downloading Valve Oil {RELEASE}'s tools (not installing them)...")
    WORK.mkdir(parents=True, exist_ok=True)
    archive = WORK / ARCHIVE
    with urllib.request.urlopen(ARCHIVE_URL, timeout=60) as response, archive.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    if sha256(archive) != ARCHIVE_SHA256:
        archive.unlink()
        raise SystemExit("The download doesn't match its SHA-256; nothing was changed. Try again later.")
    staging = WORK / "extract"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts or name.parts[0] != "valve-oil" or member.islnk() \
                    or (member.issym() and (Path(member.linkname).is_absolute() or ".." in Path(member.linkname).parts)):
                raise SystemExit(f"The download holds an unexpected entry ({member.name}); nothing was changed.")
        bundle.extractall(staging)
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    (staging / "valve-oil").rename(BUNDLE)
    staging.rmdir()
    archive.unlink()
    (WORK / ".bundle-ok").write_text(ARCHIVE_SHA256 + "\n")


def load_bundle():
    sys.path[:0] = [str(BUNDLE / "installer/runtime/vendor"), str(BUNDLE)]
    from installer.runtime import sudo as vo_sudo
    from installer.runtime.cli import resolve_runtime_paths
    from installer.runtime.manifest import load_manifest
    from installer.runtime.reporter import create_reporter

    environ = dict(os.environ)
    reporter = create_reporter(prefer_plain=True)
    return SimpleNamespace(
        environ=environ, reporter=reporter, sudo=vo_sudo,
        paths=resolve_runtime_paths(bundle_root=BUNDLE, environ=environ),
        manifest=load_manifest(BUNDLE / "installer/package.yaml"), password=None,
    )


def sudo_password(vo) -> str:
    if vo.password is None:
        vo.password = vo.sudo.authenticate_sudo(run=subprocess.run, environ=vo.environ, reporter=vo.reporter,
                                                input_stream=sys.stdin)
    return vo.password


def sudo(vo, *command: str, check: bool = True) -> subprocess.CompletedProcess:
    result = vo.sudo.run_sudo(list(command), run=subprocess.run, password=sudo_password(vo),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and result.returncode:
        raise RuntimeError(f"`{' '.join(command)}` failed: {(result.stdout or '').strip()}")
    return result


def bundle_tool(*command: str) -> int:
    return subprocess.run([str(BUNDLE / command[0]), *command[1:]], cwd=BUNDLE).returncode


# ---- report ----------------------------------------------------------------------------------------------------------

def board_report() -> dict:
    status = query("mcu", "mcu THR", "closed_loop x", "closed_loop y", "print_stats")
    found = {
        "klipper": klipper_state(),
        "mainboard": (status.get("mcu") or {}).get("mcu_version"),
        "thr": (status.get("mcu THR") or {}).get("mcu_version"),
        "x": (status.get("closed_loop x") or {}).get("stepper_version"),
        "y": (status.get("closed_loop y") or {}).get("stepper_version"),
        "print": (status.get("print_stats") or {}).get("state"),
    }
    return found


def show_boards(found: dict) -> None:
    def label(version, stock_name="QIDI's"):
        if not version:
            return "no answer"
        return f"{version} ({'Valve Oil' if not_stock(version) else stock_name})"

    say(f"Klipper: {found['klipper']}")
    say(f"Mainboard firmware: {label(found['mainboard'])}")
    say(f"Toolhead board (THR) firmware: {label(found['thr'])}")
    say(f"X closed-loop driver: {found['x'] or 'no answer'}")
    say(f"Y closed-loop driver: {found['y'] or 'no answer'}")


# ---- 1. mainboard ----------------------------------------------------------------------------------------------------

def mainboard(check: bool) -> None:
    heading("1. Mainboard firmware")
    found = board_report()
    show_boards(found)
    if not not_stock(found["mainboard"]):
        say("Nothing to do here." if found["mainboard"] else
            "Klipper didn't report the mainboard's firmware, so it can't be checked or restored now.")
        return
    if check:
        say("Would put QIDI's stock mainboard firmware back.")
        return
    say("The mainboard passes every message between Klipper and the X and Y motor drivers. Putting QIDI's firmware")
    say("back takes about a minute, and the script then checks whether the X driver answers.")
    if not ask("Put QIDI's stock mainboard firmware back?"):
        return
    bundle_tool("mainboard-firmware.sh", "restore")
    for _ in range(24):
        if klipper_state() == "ready":
            break
        time.sleep(5)
    after = board_report()
    say()
    show_boards(after)
    if after["x"]:
        say("The X driver answers again.")
    else:
        say("The X driver still doesn't answer with QIDI's mainboard firmware.")


# ---- 2. THR ----------------------------------------------------------------------------------------------------------

def thr(vo, check: bool) -> None:
    heading("2. Toolhead board (THR) firmware")
    found = board_report()
    say(f"THR reports: {found['thr'] or 'no answer'}")
    manifest = json.loads((BUNDLE / "firmware/max4-thr-valve-oil-v1.json").read_text())["thr"]
    if found["thr"] and not not_stock(found["thr"]) and found["thr"] != manifest["stock_version"]:
        say("This THR firmware isn't one Valve Oil knows; it is left as it is.")
        return
    stock = stock_thr_application(manifest["stock_sha256"])
    if check:
        say("Would read the THR (Klipper stops for about a minute) and put QIDI's application back if it isn't QIDI's."
            if found["thr"] != manifest["stock_version"] or found["thr"] is None else
            "Reports QIDI's version. An early Valve Oil build reported the same version; reading the THR tells them apart.")
        say(f"QIDI's THR application for the restore: {stock or 'not found'}")
        return
    if stock is None:
        say("QIDI's THR application wasn't found in ~/update or in the THR backups, so the THR is left as it is.")
        return
    if found["print"] in ("printing", "paused"):
        say("A print is running or paused; the THR is left as it is.")
        return
    if not ask("Read the THR and put QIDI's application back if it isn't QIDI's? Klipper and the touchscreen stop for "
               "about a minute. Keep the printer powered on."):
        return
    tool = [str(KLIPPY_PYTHON), str(BUNDLE / "scripts/flash_thr_firmware.py")]
    run_dir = WORK / time.strftime("thr-%Y%m%d-%H%M%S")
    run_dir.mkdir()
    active = stop_printer_services(vo)
    try:
        say("Reading the THR...")
        if subprocess.run([*tool, "snapshot", "--output-dir", str(run_dir / "before"), "--printer-idle-confirmed"]).returncode:
            say("The THR couldn't be read; its firmware wasn't changed.")
            return
        if sha256(run_dir / "before/application.bin") == manifest["stock_sha256"]:
            say("The THR already runs QIDI's application.")
            return
        recovery = run_dir / "recovery"
        recovery.mkdir()
        shutil.copy2(run_dir / "before/bootloader.bin", recovery / "bootloader.bin")
        shutil.copy2(stock, recovery / "application.bin")
        say("Writing QIDI's THR application...")
        if subprocess.run([*tool, "transfer", "--image", str(recovery / "application.bin"), "--backup-dir", str(recovery),
                           "--reset", "--printer-idle-confirmed", "--confirm-write"]).returncode:
            say(f"The THR transfer failed. Klipper is left stopped. Keep the printer on and send us this log; "
                f"firmware/FLASHING.md in {BUNDLE} has the recovery steps.")
            active = []
            return
        time.sleep(12)
        verified = subprocess.run([*tool, "snapshot", "--output-dir", str(run_dir / "after"), "--printer-idle-confirmed"]).returncode == 0 \
            and subprocess.run([*tool, "verify", "--image", str(recovery / "application.bin"), "--backup-dir",
                                str(run_dir / "after"), "--original-backup-dir", str(run_dir / "before")]).returncode == 0
        if not verified:
            say("The THR didn't read back as QIDI's application. Klipper is left stopped. Keep the printer on and send "
                "us this log.")
            active = []
            return
        say("The THR runs QIDI's application again. Run input shaper calibration before printing.")
    finally:
        start_printer_services(vo, active)


def stock_thr_application(stock_sha256: str) -> Path | None:
    candidates = sorted((HOME / "update").glob("QD_MAX4_THR_*.bin"))
    for folder in (".vo_thr_firmware_backups", ".tltg_thr_firmware_backups"):
        candidates += sorted((PRINTER_DATA / folder).glob("thr-*/before/application.bin"))
    for candidate in candidates:
        try:
            if candidate.is_file() and not candidate.is_symlink() and sha256(candidate) == stock_sha256:
                return candidate
        except OSError:
            continue
    return None


def stop_printer_services(vo) -> list[str]:
    active = []
    for service in PRINTER_SERVICES:
        state = subprocess.run(["systemctl", "is-active", service], capture_output=True, text=True).stdout.strip()
        if state == "active":
            active.append(service)
    for service in reversed(PRINTER_SERVICES):
        if service in active:
            sudo(vo, "systemctl", "stop", service)
    return active


def start_printer_services(vo, active: list[str]) -> None:
    for service in active:
        sudo(vo, "systemctl", "start", service, check=False)


# ---- 3. Boxes --------------------------------------------------------------------------------------------------------

def boxes(check: bool) -> None:
    heading("3. USB Box firmware")
    status = subprocess.run([str(BUNDLE / "box-firmware.sh"), "status"], cwd=BUNDLE, capture_output=True, text=True)
    say((status.stdout + status.stderr).strip())
    if not not_stock(status.stdout):
        return
    if check:
        say("Would put QIDI's stock Box firmware back.")
        return
    if ask("Put QIDI's stock firmware back on the USB Boxes?"):
        bundle_tool("box-firmware.sh", "restore")


# ---- 4. boot logo ----------------------------------------------------------------------------------------------------

def boot_logo(vo, check: bool) -> None:
    heading("4. Boot logo")
    from installer.runtime import boot_logo as logo

    partition = logo.Partition(Path(logo.DEVICE), password=sudo_password(vo), run=subprocess.run)
    if not partition.exists():
        say("No boot partition was found; nothing to do.")
        return
    current = partition.read()
    try:
        found = logo.layout(current)
    except logo.BootLogoError as exc:
        say(f"The boot partition isn't one Valve Oil changes ({exc.message}); it is left as it is.")
        return
    backups = sorted({*PRINTER_DATA.glob("*system_backups/boot-partition-*.img"),
                      *PRINTER_DATA.glob("*system_backups/*/boot-partition-*.img")})
    original = None
    for backup in backups:
        data = backup.read_bytes()
        if logo._sha256(data) == logo._sha256(current):
            say("The boot partition already holds QIDI's boot logo.")
            return
        if only_logos_differ(logo, data, current, found) and not_valve_oil_logo(logo, data):
            original = backup
            break
    if original is None:
        if not_valve_oil_logo(logo, current):
            say("The boot partition holds a boot logo that isn't Valve Oil's; it is left as it is.")
        else:
            say("The boot partition holds Valve Oil's logo, and no saved copy of QIDI's matches it. Send us this log.")
            say("Saved copies looked at: " + (", ".join(str(path) for path in backups) or "none"))
        return
    say(f"The boot partition holds Valve Oil's logo. QIDI's is saved in {original}.")
    if check:
        say("Would put QIDI's boot logo back.")
        return
    if not ask("Put QIDI's boot logo back?"):
        return
    data = original.read_bytes()
    partition.write(data, logo.changed_blocks(current, data))
    if logo._sha256(partition.read()) != logo._sha256(data):
        say("QIDI's boot logo didn't read back as written. Don't restart the printer; send us this log.")
        return
    logo.record_path(vo.paths).unlink(missing_ok=True)
    say("QIDI's boot logo is back. It shows from the next power-on.")


def only_logos_differ(logo, original: bytes, current: bytes, found) -> bool:
    """Whether two boot images differ in nothing but the logo BMPs and the image id, so writing one over the other
    changes only those."""
    try:
        if len(original) != len(current) or logo.layout(original) != found:
            return False
    except logo.BootLogoError:
        return False
    keep = [(found.id_offset, found.id_offset + 32), *((start, start + size) for start, size in found.logos)]
    position = 0
    for start, end in sorted(keep):
        if original[position:start] != current[position:start]:
            return False
        position = end
    return original[position:] == current[position:]


def not_valve_oil_logo(logo, image: bytes) -> bool:
    try:
        return logo.patched(image, logo._logo_rows()) != image
    except logo.BootLogoError:
        return True


# ---- 5. system changes -----------------------------------------------------------------------------------------------

def system_changes(vo, check: bool) -> None:
    heading("5. System changes")
    restore_from_old_record(vo, check)
    remove_marked_leftovers(vo, check)
    report_other_leftovers()


def old_ledger(vo) -> dict | None:
    """The record of system changes from the config backups, newest record of each change first: the one an
    install kept until QIDI's package deleted it."""
    import yaml
    from installer.runtime import state_file
    from installer.runtime import system_optimizations as so

    states = []
    for archive in sorted(PRINTER_DATA.glob("*.zip"), key=lambda path: path.stat().st_mtime):
        try:
            with zipfile.ZipFile(archive) as backup:
                member = next((name for name in STATE_MEMBERS if name in backup.namelist()), None)
                if member is None:
                    continue
                ledger = state_file.parse_installed_state(yaml.safe_load(backup.read(member))).system_ledger
        except Exception as exc:  # a damaged or older backup is skipped, with why
            say(f"  {archive.name}: skipped ({exc})")
            continue
        if isinstance(ledger, dict) and ledger.get("restore_preimages") and not so.system_ledger_needs_migration(ledger):
            say(f"  {archive.name}: a record of {len(ledger['restore_preimages'])} changes")
            states.append(ledger)
    if not states:
        return None
    preimages, outcomes = {}, {}
    for ledger in states:
        for operation_id, preimage in ledger["restore_preimages"].items():
            outcome = (ledger.get("outcomes") or {}).get(operation_id)
            if preimage is not None and isinstance(outcome, dict):
                preimages[operation_id], outcomes[operation_id] = preimage, outcome
    usable = {}
    for operation_id in preimages:
        single = {"schema_version": 2, "policy": states[-1].get("policy"),
                  "restore_preimages": {operation_id: preimages[operation_id]},
                  "outcomes": {operation_id: outcomes[operation_id]}, "committed_transactions": {}}
        try:
            so.validate_system_ledger(single, manifest=vo.manifest, paths=vo.paths)
            usable[operation_id] = preimages[operation_id]
        except Exception as exc:
            say(f"  {operation_id}: its record can't be used ({exc})")
    return {"schema_version": 2, "policy": states[-1].get("policy"), "restore_preimages": usable,
            "outcomes": {name: outcomes[name] for name in usable}, "committed_transactions": {}}


def restore_from_old_record(vo, check: bool) -> None:
    from installer.runtime import system_optimizations as so

    say("Looking for Valve Oil's record of system changes in the config backups in ~/printer_data:")
    ledger = old_ledger(vo)
    if not ledger or not ledger["restore_preimages"]:
        say("No usable record was found.")
        return
    root = Path("/")
    to_restore = []
    for operation_id, preimage in ledger["restore_preimages"].items():
        try:
            decision, why = so._restore_decision(operation_id, preimage=preimage, outcome=ledger["outcomes"][operation_id],
                                                 manifest=vo.manifest, paths=vo.paths, root=root, run=subprocess.run)
        except Exception as exc:
            decision, why = so._PRESERVE, str(exc)
        if decision == so._RESTORE:
            to_restore.append(operation_id)
            say(f"  still Valve Oil's: {operation_id}")
        elif decision == so._PRESERVE:
            say(f"  left as it is: {operation_id} ({why or 'changed since'})")
    if not to_restore:
        say("Every change in the record is already undone.")
        return
    if check:
        say(f"Would undo {len(to_restore)} changes with Valve Oil's restore code.")
        return
    if not ask(f"Undo these {len(to_restore)} changes with Valve Oil's restore code?"):
        return
    restore = {**ledger, "restore_preimages": {name: ledger["restore_preimages"][name] for name in to_restore},
               "outcomes": {name: ledger["outcomes"][name] for name in to_restore}}
    vo.environ.setdefault(vo.sudo.SUDO_PASSWORD_ENV, sudo_password(vo))
    so.restore_system_optimizations(paths=vo.paths, manifest=vo.manifest, state=SimpleNamespace(system_ledger=restore),
                                    reporter=vo.reporter, input_stream=sys.stdin, environ=vo.environ,
                                    include_optional=True)


def marked_leftovers() -> tuple[list[str], list[str]]:
    """Valve Oil's own units, and its other files, still on the printer: each carries Valve Oil's marker."""
    from installer.runtime.earlier_names import _owned

    system = Path("/etc/systemd/system")
    units = sorted(str(path) for pattern in ("vo-*.service", "vo-*.timer", "tltg-*.service", "tltg-*.timer")
                   for path in system.glob(pattern) if _owned(path))
    files = [*system.glob("*.d/vo-*.conf"), *system.glob("*.d/tltg-*.conf"),
             *Path("/usr/local/lib").glob("vo_*.py"), *Path("/usr/local/lib").glob("tltg_*.py"),
             *Path("/etc/udev/rules.d").glob("*-vo-*.rules"), *Path("/etc/udev/rules.d").glob("*-tltg-*.rules"),
             *(path for env in ("moonraker-env", "klippy-env") for pattern in ("vo-*.pth", "vo_*.py", "tltg-*.pth", "tltg_*.py")
               for path in (HOME / env / "lib/python3.9/site-packages").glob(pattern))]
    return units, sorted(str(path) for path in files if _owned(path))


def remove_marked_leftovers(vo, check: bool) -> None:
    units, files = marked_leftovers()
    if not units and not files:
        say("None of Valve Oil's own units or programs are left.")
        return
    say("Valve Oil's own units and programs still on the printer:")
    for path in units + files:
        say(f"  {path}")
    if check:
        say("Would archive these to ~/vo-back-to-stock, turn the units off, and remove them.")
        return
    if not ask("Archive these to ~/vo-back-to-stock, turn the units off, and remove them?"):
        return
    archive = WORK / time.strftime("removed-%Y%m%d-%H%M%S.tgz")
    sudo(vo, "tar", "czpf", str(archive), *units, *files)
    sudo(vo, "chown", "qidi:qidi", str(archive))
    for unit in units:
        sudo(vo, "systemctl", "disable", "--now", Path(unit).name, check=False)
    for path in units + files:
        sudo(vo, "rm", "-f", "--", path)
    for directory in sorted({str(Path(path).parent) for path in files if Path(path).parent.name.endswith(".d")}):
        sudo(vo, "rmdir", "--ignore-fail-on-non-empty", "--", directory, check=False)
    sudo(vo, "systemctl", "daemon-reload")
    sudo(vo, "udevadm", "control", "--reload", check=False)
    say(f"Removed. A copy of each is in {archive}.")


def report_other_leftovers() -> None:
    patterns = [
        (HOME, ("vo-*", "tltg-*", "valve-oil", "tltg-optimized-macros")),
        (Path("/usr/local/lib"), ("vo-*", "tltg-*")),
        (HOME / "moonraker-env/lib/python3.9/site-packages", ("vo*", "tltg*")),
        (HOME / "klippy-env/lib/python3.9/site-packages", ("vo*", "tltg*")),
    ]
    removable = set(sum(marked_leftovers(), []))
    found = sorted({str(path) for folder, globs in patterns for pattern in globs for path in folder.glob(pattern)
                    if path != WORK and str(path) not in removable})
    site = Path("/etc/nginx/sites-available/fluidd")
    try:
        if any(name in site.read_text(errors="replace") for name in ("vo-fluidd", "valve-oil", "tltg")):
            found.append(f"{site} (still names a Valve Oil folder)")
    except OSError:
        pass
    if found:
        say("Other Valve Oil files still on the printer, left for now (send us this log):")
        for path in found:
            say(f"  {path}")


# ---- main ------------------------------------------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only report; nothing is changed")
    args = parser.parse_args()
    if os.geteuid() == 0 or Path.home() != HOME:
        say("Run this as the qidi user, not as root.")
        return 1
    log_to_file()
    say(f"Back to stock, with the tools of Valve Oil {RELEASE}. " + ("Report only." if args.check else
        "Each step asks before it changes anything."))
    fetch_bundle()
    vo = load_bundle()
    from installer.runtime.firmware import detect_supported_firmware_best_effort

    detected = detect_supported_firmware_best_effort(vo.paths.firmware_manifest_path, vo.manifest)
    if detected is None or detected[0].id != "max4":
        say("This script is for the QIDI Max 4, and this printer's QIDI firmware isn't a Max 4 firmware Valve Oil "
            "knows. Nothing was changed.")
        return 1
    say(f"QIDI Max 4, QIDI firmware {detected[1]}.")
    found = board_report()
    if found["print"] in ("printing", "paused") and not args.check:
        say("A print is running or paused. Run this when the printer is idle.")
        return 1
    steps = (lambda: mainboard(args.check), lambda: thr(vo, args.check), lambda: boxes(args.check),
             lambda: boot_logo(vo, args.check), lambda: system_changes(vo, args.check))
    for step in steps:
        try:
            step()
        except Exception as exc:  # one step failing doesn't stop the others; the log carries what happened
            say(f"This step stopped: {type(exc).__name__}: {getattr(exc, 'message', None) or exc}")
    heading("Done")
    show_boards(board_report())
    if not args.check:
        say("Turn the printer off with its power switch, wait 10 seconds, and turn it on again.")
    say("Once the X driver answers, run QIDI's firmware update from the touchscreen, which also puts both motor")
    say("drivers on QIDI's firmware. Until then QIDI's update stops at the motor step.")
    if os.environ.get(LOGGED_ENV):
        say(f"Log: {os.environ[LOGGED_ENV]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
