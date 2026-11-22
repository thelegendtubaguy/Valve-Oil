from __future__ import annotations

import os
import pwd
import subprocess
from pathlib import Path
from typing import Callable

from . import messages
from .errors import InstallerError
from .models import RuntimePaths
from .naming import BUNDLE_ROOT_NAME
from .sudo import SudoError, authenticate_sudo, run_sudo

RunFn = Callable[..., subprocess.CompletedProcess]
# Valve Oil's names, and the names releases before 26.11.22.1 used, which an install moves to Valve Oil's.
INSTALLER_PREFIXES = ("vo_", "vo-", ".vo_", ".vo-", "tltg", ".tltg")
CHOWN_BATCH = 200


class OwnershipRepairError(InstallerError):
    pass


def repair_installer_ownership(
    paths: RuntimePaths,
    *,
    reporter,
    environ: dict[str, str],
    input_stream=None,
    run: RunFn = subprocess.run,
) -> int:
    """Give installer files back to the user who owns printer_data.

    Runs started as root before the installer switched to that user left the
    files they created owned by root, so a later run could not open its lock,
    read its state, or replace its own bundle. Everything the project names
    `vo_`, `vo-`, or earlier `tltg` under printer_data, its config folder, the home folder, and
    Klipper's extras, and the installed bundle in the home folder, belongs to that user, as an unprivileged run creates it;
    QIDI's own files are left alone. Returns the number of entries changed.
    """
    try:
        printer_data = paths.printer_data_root.stat()
    except OSError:
        return 0
    if printer_data.st_uid == 0:
        return 0
    uid = printer_data.st_uid
    try:
        user = pwd.getpwuid(uid)
        name, gid = user.pw_name, user.pw_gid
    except KeyError:
        name, gid = str(uid), printer_data.st_gid
    wrong = [path for path in installer_entries(paths) if _owner(path) not in (None, uid)]
    if not wrong:
        return 0
    try:
        password = authenticate_sudo(run=run, environ=environ, reporter=reporter, input_stream=input_stream)
    except SudoError as exc:
        raise OwnershipRepairError(messages.OWNERSHIP_REPAIR_FAILED.format(user=name)) from exc
    # A directory root created may not be listable by the user, so it is
    # changed with everything below it.
    directories = [path for path in wrong if path.is_dir() and not path.is_symlink()]
    others = [path for path in wrong if path not in directories]
    for flags, group in ((["-R", "-h"], directories), (["-h"], others)):
        for start in range(0, len(group), CHOWN_BATCH):
            batch = [str(path) for path in group[start:start + CHOWN_BATCH]]
            command = ["chown", *flags, f"{uid}:{gid}", "--", *batch]
            if run_sudo(command, run=run, password=password).returncode != 0:
                raise OwnershipRepairError(messages.OWNERSHIP_REPAIR_FAILED.format(user=name))
    reporter.line(messages.OWNERSHIP_REPAIRED.format(count=len(wrong), user=name))
    return len(wrong)


def installer_entries(paths: RuntimePaths) -> list[Path]:
    """Every installer-created path, without following symbolic links."""
    roots: list[Path] = []
    for directory in (paths.printer_data_root, paths.config_root):
        roots.extend(_named_children(directory))
    roots.extend(_named_children(paths.printer_data_root.parent, extra_prefixes=(f".{BUNDLE_ROOT_NAME}.",),
                                 extra_names=(BUNDLE_ROOT_NAME,)))
    extras = paths.managed_klipper_root / "klippy" / "extras"
    roots.extend(path for path in _named_children(extras) if path.name.endswith(".py"))
    entries: list[Path] = []
    for root in dict.fromkeys(roots):
        entries.append(root)
        if root.is_symlink() or not root.is_dir():
            continue
        for directory, dirnames, filenames in os.walk(root):
            entries.extend(Path(directory) / name for name in dirnames + filenames)
    return entries


def _owner(path: Path) -> int | None:
    try:
        return path.lstat().st_uid
    except OSError:
        return None


def _named_children(directory: Path, *, extra_prefixes: tuple[str, ...] = (), extra_names: tuple[str, ...] = ()) -> list[Path]:
    """The installer's entries in `directory`: named with INSTALLER_PREFIXES, and in the home folder also the installed
    bundle and its copies in progress (bootstrap.copy_bundle)."""
    prefixes = INSTALLER_PREFIXES + extra_prefixes
    try:
        return sorted(path for path in directory.iterdir() if path.name.startswith(prefixes) or path.name in extra_names)
    except OSError:
        return []
