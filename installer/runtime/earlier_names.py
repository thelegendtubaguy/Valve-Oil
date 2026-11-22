"""Moves what releases before 26.11.22.1 named `tltg` to Valve Oil's `vo` names.

Those releases named Valve Oil's files in printer_data, its services and their files, the folders it serves, and
its Klipper modules `tltg`. Every run moves printer_data to the new names first (migrate_printer_data), before it
reads any of it, and installs and uninstalls remove the earlier services and files (retire_earlier_host_files) and
Klipper modules (remove_earlier_klipper_modules) once their replacements are in place.

An update from such a release runs this installer from that release's update check or Update now, which writes its
run's result under the earlier names after this installer ends. That run's status and its update check state stay
behind for it (EARLIER_PARENT), and the next run takes what it wrote.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Callable

from .errors import InstallerError
from .fs_atomic import atomic_write_text, fsync_directory
from .models import RuntimePaths
from .naming import BUNDLE_ROOT_NAME, LEGACY_BUNDLE_ROOT_NAME, has_marker
from .sudo import run_sudo

RunFn = Callable[..., subprocess.CompletedProcess]

# ---- the environment -------------------------------------------------------------------------------------------------
# What one run passes to another, under the names releases before 26.11.22.1 used.
EARLIER_ENVIRONMENT = {
    "VO_INSTALLER_LOCK_HELD": "TLTG_OPTIMIZED_INSTALLER_LOCK_HELD",
    "VO_FIRMWARE_UPDATE_KIND": "TLTG_FIRMWARE_UPDATE_KIND",
    "VO_FIRMWARE_UPDATE_RUN": "TLTG_FIRMWARE_UPDATE_RUN",
    "VO_STATUS_JOB": "TLTG_STATUS_JOB",
    "VO_SUDO_PASSWORD": "TLTG_OPTIMIZED_SUDO_PASSWORD",
}
# Set while this process was started by an earlier release's update check or Update now, which holds only the
# earlier installer lock and finishes its run under the earlier names after this one ends.
EARLIER_PARENT = False


def adopt_earlier_environment(environ: dict[str, str]) -> dict[str, str]:
    """The environment with each earlier name's value under its current name, where only the earlier one is set."""
    global EARLIER_PARENT
    env = dict(environ)
    EARLIER_PARENT = env.get("TLTG_OPTIMIZED_INSTALLER_LOCK_HELD") == "1" and "VO_INSTALLER_LOCK_HELD" not in env
    for name, earlier in EARLIER_ENVIRONMENT.items():
        if name not in env and earlier in env:
            env[name] = env[earlier]
    return env


# ---- printer_data ----------------------------------------------------------------------------------------------------
# What to do when a file is under both names: the earlier name was written last, by an earlier release's run still
# going (LATER); this release keeps its own (OURS), as for the status that this release finishes for that run; or
# merge the two folders (MERGE). Lock files are never moved (LOCK).
LATER, OURS, MERGE, LOCK = "later", "ours", "merge", "lock"

# printer_data paths, relative to printer_data, as (earlier, current, policy). Files that name other paths in this
# table are rewritten as they move (CONTENT_FILES).
PRINTER_DATA_MOVES: tuple[tuple[str, str, str], ...] = (
    ("config/tltg_optimized_state.yaml", "config/vo_state.yaml", OURS),
    ("config/tltg_optimized_auto_update_state.json", "config/vo_auto_update_state.json", LATER),
    ("config/tltg_optimized_runtime_state.json", "config/vo_runtime_state.json", OURS),
    ("config/tltg_bed_soak", "config/vo_bed_soak", MERGE),
    ("tltg_optimized_system_backups", "vo_system_backups", MERGE),
    (".tltg_thr_firmware_backups", ".vo_thr_firmware_backups", MERGE),
    (".tltg_mainboard_firmware_backups", ".vo_mainboard_firmware_backups", MERGE),
    (".tltg_box_firmware_backups", ".vo_box_firmware_backups", MERGE),
    (".tltg_optimized_installer.lock", ".vo_installer.lock", LOCK),
    (".tltg_valve_oil_status.lock", ".vo_status.lock", LOCK),
    (".tltg_firmware_update.lock", ".vo_firmware_update.lock", LOCK),
    (".tltg_optimized_recovery_required", ".vo_recovery_required", LATER),
    (".tltg_optimized_klipper_restart_required", ".vo_klipper_restart_required", LATER),
    (".tltg_optimized_host_reboot_required", ".vo_host_reboot_required", LATER),
    (".tltg_optimized_reboot_recommended", ".vo_reboot_recommended", LATER),
    (".tltg_optimized_system_journal.json", ".vo_system_journal.json", LATER),
    (".tltg_optimized_system_ledger_migration.json", ".vo_system_ledger_migration.json", LATER),
    (".tltg_optimized_saved_variables_verify_pending", ".vo_saved_variables_verify_pending", LATER),
    (".tltg_optimized_auto_update_enrolled", ".vo_auto_update_enrolled", LATER),
    (".tltg_optimized_auto_update_settings.json", ".vo_auto_update_settings.json", LATER),
    (".tltg_valve_oil_status.json", ".vo_status.json", OURS),
    (".tltg_valve_oil_progress.json", ".vo_progress.json", OURS),
    (".tltg_valve_oil_install_started", ".vo_install_started", LATER),
    (".tltg_carried_system_ledger.json", ".vo_carried_system_ledger.json", LATER),
    (".tltg_firmware_update_result.json", ".vo_firmware_update_result.json", LATER),
    (".tltg_install_answers.json", ".vo_install_answers.json", LATER),
    (".tltg_install_screen", ".vo_install_screen", LATER),
    (".tltg_update_check.json", ".vo_update_check.json", LATER),
    (".tltg_board_admission.json", ".vo_board_admission.json", LATER),
    (".tltg_update_lights_hold.json", ".vo_update_lights_hold.json", LATER),
    (".tltg_boot_logo.json", ".vo_boot_logo.json", LATER),
    (".tltg_box_numbers.json", ".vo_box_numbers.json", LATER),
    (".tltg_box_monitor.json", ".vo_box_monitor.json", LATER),
    (".tltg_box_cfg.previous", ".vo_box_cfg.previous", LATER),
    (".tltg_box485_salt", ".vo_box485_salt", LATER),
    (".tltg_thr_firmware_pending.json", ".vo_thr_firmware_pending.json", LATER),
    (".tltg_mainboard_firmware_pending.json", ".vo_mainboard_firmware_pending.json", LATER),
    (".tltg_box_firmware_pending.json", ".vo_box_firmware_pending.json", LATER),
    (".tltg_box_firmware_holds.json", ".vo_box_firmware_holds.json", LATER),
    ("logs/tltg_thr_firmware.json", "logs/vo_thr_firmware.json", LATER),
    ("logs/tltg_mainboard_firmware.json", "logs/vo_mainboard_firmware.json", LATER),
    ("logs/tltg_input_shaper_thr_flash.json", "logs/vo_input_shaper_thr_flash.json", LATER),
    ("logs/tltg_box485_report.json", "logs/vo_box485_report.json", LATER),
)
# Files under earlier names that are numbered or dated: (folder, earlier prefix, current prefix).
PRINTER_DATA_PREFIX_MOVES: tuple[tuple[str, str, str], ...] = (
    ("logs", "tltg_box_firmware_box", "vo_box_firmware_box"),
    ("", ".tltg_optimized_system_journal.json.set-aside-", ".vo_system_journal.json.set-aside-"),
)
# Files that record paths, which are rewritten to the current names.
CONTENT_FILES = (
    "config/vo_state.yaml",
    ".vo_system_journal.json",
    ".vo_system_ledger_migration.json",
    ".vo_carried_system_ledger.json",
    ".vo_thr_firmware_pending.json",
    ".vo_mainboard_firmware_pending.json",
    ".vo_box_firmware_pending.json",
)
# A QIDI firmware update an earlier release started; its own service finishes it with that release.
EARLIER_FIRMWARE_UPDATE_RECORD = ".tltg_firmware_update_pending.json"
FIRMWARE_UPDATE_RECORD = ".vo_firmware_update_pending.json"
# Kept for an earlier release's run that started this one, which writes its result there after this run ends.
EARLIER_PARENT_FILES = frozenset({".tltg_valve_oil_status.json", "config/tltg_optimized_auto_update_state.json"})
# The macros folder printer.cfg includes.
EARLIER_INCLUDE = "[include tltg-optimized-macros/*.cfg]"
INCLUDE = "[include vo-macros/*.cfg]"
EARLIER_MACROS = "config/tltg-optimized-macros"
MACROS = "config/vo-macros"
PACKAGE_ID = ("qidi-max4-optimized", "valve-oil")

# Host paths in recorded state, as (earlier, current) text, most specific first.
HOST_PATH_REWRITES: tuple[tuple[str, str], ...] = (
    ("/etc/systemd/system/tltg-optimized-auto-update.", "/etc/systemd/system/vo-auto-update."),
    ("/etc/systemd/system/tltg-", "/etc/systemd/system/vo-"),
    (".service.d/tltg-", ".service.d/vo-"),
    ("/etc/udev/rules.d/99-tltg-", "/etc/udev/rules.d/99-vo-"),
    ("/usr/local/lib/tltg_", "/usr/local/lib/vo_"),
    ("/usr/local/lib/tltg-", "/usr/local/lib/vo-"),
    ("site-packages/tltg-", "site-packages/vo-"),
    ("site-packages/tltg_", "site-packages/vo_"),
    ("/home/qidi/tltg-fluidd", "/home/qidi/vo-fluidd"),
    ("/home/qidi/tltg-valve-oil-ui", "/home/qidi/vo-valve-oil-ui"),
    ("/home/qidi/tltg-moonraker-speedups", "/home/qidi/vo-moonraker-speedups"),
    (".tltg-previous", ".vo-previous"),
    (".tltg-staging", ".vo-staging"),
    (".tltg-removing", ".vo-removing"),
    ("tltg-zram-swap", "vo-zram-swap"),
    ("__TLTG_SECTION_DELETED__", "__VO_SECTION_DELETED__"),
)


class EarlierNamesError(InstallerError):
    pass


def migrate_printer_data(paths: RuntimePaths) -> bool:
    """Move printer_data files from their earlier names, rewriting the paths they record; returns whether anything
    moved. Safe to repeat, and an interrupted run is finished by the next one. Call it holding the installer lock,
    before anything reads printer_data."""
    root = paths.printer_data_root
    if (root / EARLIER_FIRMWARE_UPDATE_RECORD).exists() and not (root / FIRMWARE_UPDATE_RECORD).exists():
        raise EarlierNamesError(
            "A QIDI firmware update an earlier Valve Oil release started hasn't finished. Turn the printer off and on "
            "so that release finishes it, then run this release again."
        )
    moved = False
    rewrites = _content_rewrites(root)
    for earlier, current, policy in PRINTER_DATA_MOVES:
        if earlier == EARLIER_MACROS:
            continue
        keep = EARLIER_PARENT and earlier in EARLIER_PARENT_FILES
        moved |= _move(root / earlier, root / current, policy, keep=keep)
    for folder, earlier_prefix, prefix in PRINTER_DATA_PREFIX_MOVES:
        directory = root / folder if folder else root
        with suppress(OSError):
            for path in sorted(directory.iterdir()):
                if path.name.startswith(earlier_prefix):
                    moved |= _move(path, directory / (prefix + path.name[len(earlier_prefix):]), LATER)
    moved |= _move_macros(paths)
    for relative in CONTENT_FILES:
        _rewrite(root / relative, rewrites)
    _remove_earlier_bundle_link(root.parent)
    return moved


def _move(source: Path, target: Path, policy: str, *, keep: bool = False) -> bool:
    if not source.exists() and not source.is_symlink():
        return False
    if policy == LOCK:
        # The earlier lock is the earlier release's; holding it or not, removing its name changes nothing for it.
        with suppress(OSError):
            source.unlink()
        return False
    if source.is_symlink():
        raise EarlierNamesError(f"Refusing to move a symbolic link: {source}")
    if keep:
        # An earlier release's run that started this one still writes here; this run works on a copy.
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(source, target)
            return True
        return False
    if not target.exists() and not target.is_symlink():
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        fsync_directory(target.parent)
        return True
    if policy == MERGE and source.is_dir() and target.is_dir() and not target.is_symlink():
        for child in sorted(source.iterdir()):
            if not (target / child.name).exists():
                os.replace(child, target / child.name)
        with suppress(OSError):
            source.rmdir()
        fsync_directory(target)
        return True
    if policy == LATER and source.is_file() and target.is_file() and not target.is_symlink():
        os.replace(source, target)
        fsync_directory(target.parent)
        return True
    if source.is_file():
        source.unlink()
    return False


def _copy_file(source: Path, target: Path) -> None:
    staged = target.with_name(f".{target.name}.vo-copy")
    shutil.copy2(source, staged)
    os.replace(staged, target)
    fsync_directory(target.parent)


def _move_macros(paths: RuntimePaths) -> bool:
    """Move the macros folder and point printer.cfg's include at it. Between the two steps printer.cfg includes a
    folder that isn't there, which Klipper takes as no files; the next run finishes the second step."""
    root = paths.printer_data_root
    moved = False
    if (root / EARLIER_MACROS).is_dir() and not (root / EARLIER_MACROS).is_symlink():
        if (root / MACROS).exists():
            raise EarlierNamesError(f"Both {root / EARLIER_MACROS} and {root / MACROS} exist; remove the one you don't use.")
        os.replace(root / EARLIER_MACROS, root / MACROS)
        fsync_directory(root / "config")
        moved = True
    printer_cfg = root / "config/printer.cfg"
    try:
        text = printer_cfg.read_text(encoding="utf-8")
    except OSError:
        return moved
    lines = text.splitlines(keepends=True)
    if not any(line.strip() == EARLIER_INCLUDE for line in lines):
        return moved
    has_current = any(line.strip() == INCLUDE for line in lines)
    result = []
    for line in lines:
        if line.strip() == EARLIER_INCLUDE:
            if has_current:
                continue
            line = line.replace(EARLIER_INCLUDE, INCLUDE)
            has_current = True
        result.append(line)
    atomic_write_text(printer_cfg, "".join(result), mode=printer_cfg.stat().st_mode & 0o777)
    return True


def _content_rewrites(root: Path) -> tuple[tuple[str, str], ...]:
    """Earlier text in recorded state, with its current text, most specific first."""
    pairs = [
        (f"{root}/{earlier}", f"{root}/{current}")
        for earlier, current, _ in sorted(PRINTER_DATA_MOVES, key=lambda item: -len(item[0]))
    ]
    pairs.append((EARLIER_MACROS, MACROS))
    pairs.extend(HOST_PATH_REWRITES)
    return tuple(pairs)


def _rewrite(path: Path, rewrites: tuple[tuple[str, str], ...]) -> None:
    try:
        if path.is_symlink() or not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    result = text
    for earlier, current in rewrites:
        result = result.replace(earlier, current)
    if path.name == "vo_state.yaml":
        result = result.replace(f"id: {PACKAGE_ID[0]}\n", f"id: {PACKAGE_ID[1]}\n")
    if result != text:
        atomic_write_text(path, result, mode=path.stat().st_mode & 0o777)


def _remove_earlier_bundle_link(home: Path) -> None:
    """The link releases 26.11.15.1 to 26.11.21 left where releases up to 26.11.12.2 installed the bundle."""
    link = home / LEGACY_BUNDLE_ROOT_NAME
    with suppress(OSError):
        if link.is_symlink() and os.readlink(link).rstrip("/") in (BUNDLE_ROOT_NAME, str(home / BUNDLE_ROOT_NAME)):
            link.unlink()


# ---- host files ------------------------------------------------------------------------------------------------------
# Earlier units, as (unit, stop): units serving a port or watching devices stop before their replacements start; the
# update check's service may be the run that started this one, a firmware update's service is running one, and zram
# swap stays on for the new unit, whose start keeps swap that is already on.
EARLIER_UNITS: tuple[tuple[str, bool], ...] = (
    ("tltg-status.service", True),
    ("tltg-addons.service", True),
    ("tltg-remote-screen.service", True),
    ("tltg-box-monitor.service", True),
    ("tltg-boot-animation.service", True),
    ("tltg-optimized-auto-update.timer", True),
    ("tltg-optimized-auto-update.service", False),
    ("tltg-firmware-update.service", False),
    ("tltg-zram-swap.service", False),
)
SYSTEMD_DIR = "/etc/systemd/system"
# Other package-created files under earlier names. Each carries a package marker, and nothing else is removed.
EARLIER_FILES: tuple[str, ...] = (
    "/usr/local/lib/tltg_status.py",
    "/usr/local/lib/tltg_addons.py",
    "/usr/local/lib/tltg_remote_screen.py",
    "/usr/local/lib/tltg_box_monitor.py",
    "/usr/local/lib/tltg_boot_animation.py",
    "/etc/udev/rules.d/99-tltg-box-monitor.rules",
    "/etc/systemd/system/klipper.service.d/tltg-priority.conf",
    "/etc/systemd/system/moonraker.service.d/tltg-priority.conf",
    "/etc/systemd/system/nginx.service.d/tltg-priority.conf",
    "/etc/systemd/system/crowsnest.service.d/tltg-priority.conf",
    "/etc/systemd/system/algo_app.service.d/tltg-priority.conf",
    "/etc/systemd/system/qidi-client.service.d/tltg-priority.conf",
    "/etc/systemd/system/qidi-tuning.service.d/tltg-tuning.conf",
    "/etc/systemd/system/irqbalance.service.d/tltg-tuning.conf",
    "/etc/systemd/system/moonraker.service.d/tltg-restart-on-exit.conf",
    "/etc/systemd/system/klipper.service.d/tltg-restart-on-exit.conf",
    "/etc/systemd/system/makerbase-webcam.service.d/tltg-wait-for-camera.conf",
    "/etc/systemd/system/makerbase-wlan0.service.d/tltg-wait-for-wlan0.conf",
    "/home/qidi/moonraker-env/lib/python3.9/site-packages/tltg-moonraker-speedups.pth",
    "/home/qidi/moonraker-env/lib/python3.9/site-packages/tltg-moonraker-fixes.pth",
    "/home/qidi/moonraker-env/lib/python3.9/site-packages/tltg_moonraker_fixes.py",
    "/home/qidi/klippy-env/lib/python3.9/site-packages/tltg-klipper-speedups.pth",
)
EARLIER_UNIT_MARKER_PREFIX = "# tltg-optimized:"
# Package folders under earlier names, with the marker file each holds: removed once their replacements are served.
EARLIER_FOLDERS: tuple[tuple[str, str], ...] = (
    ("/home/qidi/tltg-fluidd", ".tltg-optimized-fluidd"),
    ("/home/qidi/tltg-valve-oil-ui", ".tltg-optimized-valve-oil-ui"),
    ("/home/qidi/tltg-moonraker-speedups", ".tltg-moonraker-speedups"),
    ("/usr/local/lib/tltg-zram-swap", ".tltg-zram-swap"),
)
FOLDER_SUFFIXES = ("", ".tltg-staging", ".tltg-previous", ".tltg-removing")


def earlier_host_files(root: Path) -> list[str]:
    """The earlier units, files, and folders present on this host."""
    found = [f"{SYSTEMD_DIR}/{unit}" for unit, _ in EARLIER_UNITS if _owned(_map(root, f"{SYSTEMD_DIR}/{unit}"))]
    found += [path for path in EARLIER_FILES if _owned(_map(root, path))]
    found += [
        folder + suffix
        for folder, marker in EARLIER_FOLDERS
        for suffix in FOLDER_SUFFIXES
        if _owned_folder(_map(root, folder + suffix), marker)
    ]
    return found


def retire_earlier_host_files(
    *, root: Path = Path("/"), sudo_password: str | None, run: RunFn = subprocess.run, folders: bool,
    keep: Callable[[str], bool] = lambda path: False,
) -> list[str]:
    """Stop, disable, and remove the earlier units, and remove the earlier files and, with `folders`, folders, each only
    when it carries the package's marker. Returns what was removed."""
    removed: list[str] = []
    units = [(unit, stop) for unit, stop in EARLIER_UNITS if _owned(_map(root, f"{SYSTEMD_DIR}/{unit}"))]
    for unit, stop in units:
        if stop:
            _sudo(["systemctl", "stop", unit], root=root, run=run, password=sudo_password)
        if unit == "tltg-firmware-update.service" and _active(unit, root=root, run=run):
            continue
        _sudo(["systemctl", "disable", unit], root=root, run=run, password=sudo_password)
        _remove(f"{SYSTEMD_DIR}/{unit}", root=root, run=run, password=sudo_password)
        removed.append(f"{SYSTEMD_DIR}/{unit}")
    rules = False
    for path in EARLIER_FILES:
        if _owned(_map(root, path)):
            _remove(path, root=root, run=run, password=sudo_password)
            removed.append(path)
            rules |= path.startswith("/etc/udev/")
    if folders:
        for folder, marker in EARLIER_FOLDERS:
            for suffix in FOLDER_SUFFIXES:
                if _owned_folder(_map(root, folder + suffix), marker) and not keep(folder + suffix):
                    _remove(folder + suffix, root=root, run=run, password=sudo_password, tree=True)
                    removed.append(folder + suffix)
    if units or any(path.startswith(SYSTEMD_DIR) for path in removed):
        _sudo(["systemctl", "daemon-reload"], root=root, run=run, password=sudo_password)
    if rules:
        _sudo(["udevadm", "control", "--reload"], root=root, run=run, password=sudo_password)
    return removed


def _owned(path: Path) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(256)
    except OSError:
        return False
    # A .pth file and a module carry the marker on their first line too; a udev rule or drop-in as well.
    return head.startswith(EARLIER_UNIT_MARKER_PREFIX) or has_marker(head, "# vo:")


def _owned_folder(path: Path, marker: str) -> bool:
    return path.is_dir() and not path.is_symlink() and (path / marker).exists()


def _active(unit: str, *, root: Path, run: RunFn) -> bool:
    if _fake(root):
        return False
    result = run(["systemctl", "is-active", unit], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
    return (result.stdout or "").strip() in {"active", "activating", "reloading", "deactivating"}


def _remove(path: str, *, root: Path, run: RunFn, password: str | None, tree: bool = False) -> None:
    if _fake(root):
        target = _map(root, path)
        if tree:
            shutil.rmtree(target)
        else:
            target.unlink()
        return
    if run_sudo(["rm", "-rf" if tree else "-f", "--", path], run=run, password=password or "").returncode != 0:
        raise EarlierNamesError(f"Could not remove {path}.")


def _sudo(command: list[str], *, root: Path, run: RunFn, password: str | None) -> None:
    if not _fake(root):
        run_sudo(command, run=run, password=password or "")


def _fake(root: Path) -> bool:
    return root != Path("/")


def _map(root: Path, absolute: str) -> Path:
    return Path(absolute) if not _fake(root) else root / absolute.lstrip("/")


# ---- Klipper modules -------------------------------------------------------------------------------------------------
def remove_earlier_klipper_modules(paths: RuntimePaths, module_destinations: list[str], *, journal) -> list[Path]:
    """Remove klippy/extras/tltg_<name>.py for each klippy/extras/vo_<name>.py module the package installs. Klipper
    loads a module only for its config section, so the earlier ones load nothing once their sections are renamed."""
    removed = []
    for destination in module_destinations:
        directory, _, name = destination.rpartition("/")
        if directory != "klippy/extras" or not name.startswith("vo_"):
            continue
        for candidate in (f"tltg_{name[len('vo_'):]}", f"tltg_{name[len('vo_'):]}c"):
            target = paths.managed_klipper_root / directory / candidate
            if target.is_file() and not target.is_symlink():
                journal.track_file(target)
                journal.note_write()
                target.unlink()
                removed.append(target)
    return removed


def earlier_names_present(paths: RuntimePaths) -> bool:
    """Whether anything in printer_data, or printer.cfg's include, still has an earlier name."""
    root = paths.printer_data_root
    if any((root / earlier).exists() or (root / earlier).is_symlink() for earlier, _, _ in PRINTER_DATA_MOVES):
        return True
    if (root / EARLIER_MACROS).exists() or (root.parent / LEGACY_BUNDLE_ROOT_NAME).is_symlink():
        return True
    for folder, earlier_prefix, _ in PRINTER_DATA_PREFIX_MOVES:
        with suppress(OSError):
            if any(path.name.startswith(earlier_prefix) for path in (root / folder if folder else root).iterdir()):
                return True
    try:
        return EARLIER_INCLUDE in (root / "config/printer.cfg").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


def migrate_when_free(paths: RuntimePaths) -> bool:
    """migrate_printer_data under the installer lock, unless another run holds it; that run moved the files when it
    started. Returns whether anything moved."""
    from .errors import LockAcquisitionError
    from .locking import acquire

    if not paths.printer_data_root.is_dir() or not earlier_names_present(paths):
        return False
    try:
        with acquire(paths.lock_path):
            return migrate_printer_data(paths)
    except LockAcquisitionError:
        return False


def finish_earlier_parent_run(paths: RuntimePaths, *, succeeded: bool) -> None:
    """Record, for the earlier release's update check or Update now that started this install, the result that run
    writes under the earlier names once this one ends: this release's status service reads only the current ones."""
    if not EARLIER_PARENT:
        return
    from . import messages, valve_oil_status

    try:
        status = valve_oil_status.load_status(paths)
        run = status.run
        if run is None or run.kind not in ("automatic", "update_now") or not valve_oil_status.run_active(run):
            return
        if succeeded:
            message = messages.AUTO_UPDATE_COMPLETE if run.kind == "automatic" else "The latest Valve Oil release is installed."
            valve_oil_status.clear_pending(paths, kind=valve_oil_status.RELEASE)
            valve_oil_status.finish_run(paths, run.id, state=valve_oil_status.SUCCEEDED, message=message)
        else:
            valve_oil_status.finish_run(paths, run.id, state=valve_oil_status.FAILED, message=messages.AUTO_UPDATE_INSTALLER_FAILED)
    except (OSError, ValueError, InstallerError):
        return


# ---- text the package put in other files -----------------------------------------------------------------------------
# Earlier names in what the package wrote into QIDI's and Moonraker's files, and their current names, applied in order.
# Vendor sources never contain "tltg", so each match is the package's own.
_EARLIER_TEXT = (
    (re.compile(r"\[tltg\]"), "[Valve Oil]"),
    (re.compile(r"tltg-optimized:"), "vo:"),
    (re.compile(r"TLTG_"), "VO_"),
    (re.compile(r"Tltg"), "Vo"),
    (re.compile(r"tltg-optimized-"), "vo-"),
    (re.compile(r"tltg-"), "vo-"),
    (re.compile(r"tltg_"), "vo_"),
    (re.compile(r"tltg(?=[A-Z])"), "vo"),
)


def current_text(text: str) -> str:
    """`text` with the earlier names the package wrote into it replaced by their current ones, so a patch made by a
    release before 26.11.22.1 reads as this release's."""
    if "tltg" not in text and "Tltg" not in text and "TLTG" not in text:
        return text
    for pattern, replacement in _EARLIER_TEXT:
        text = pattern.sub(replacement, text)
    return text
