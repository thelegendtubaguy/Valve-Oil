"""Valve Oil's Klipper: upstream Klipper v0.14 with QIDI's changes, installed beside QIDI's ~/klipper.

The source is klipper/ in the repository. Its c_helper.so is built from klipper/klippy/chelper with the printers'
toolchain by scripts/build_klipper_chelper.py and shipped prebuilt, because QIDI's printers have no compiler; Klipper
rebuilds the library only when a source file is newer than it, so the installer keeps the library the newest file.

The installer builds the tree in /home/qidi/vo-klipper for the detected printer and firmware: klipper/klippy, the
printer's files from klipper/printers/<printer>/klippy over it, Valve Oil's own Klipper modules where QIDI's tree has
them, QIDI's compiled modules copied from the printer's ~/klipper/klippy, the prebuilt c_helper.so, and klippy/.version.
A marker written last records what went in, so the launcher (installer/system/vo_klipper_launch.py) can tell at every
Klipper start that the tree is whole and still matches the printer's firmware and QIDI's modules. QIDI's ~/klipper is
never changed. Klipper runs Valve Oil's tree only when the operator chose it (the choice file); otherwise, and on any
doubt, QIDI's.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Iterable

from .errors import InstallerError
from .models import ImagePin


TREE_SOURCE = Path(__file__).resolve().parents[2] / "klipper"
TREE_ROOT = "/home/qidi/vo-klipper"
CHELPER_PAYLOAD_SUBDIR = "system/klipper_tree"
CHELPER_PAYLOAD = Path(__file__).resolve().parents[1] / CHELPER_PAYLOAD_SUBDIR / "c_helper.so"
# klipper/klippy/chelper's sources and the library scripts/build_klipper_chelper.py builds from them.
CHELPER_SOURCES_SHA256 = "aad2932c4a653038a22506cec17c0c3bf15fb20fdebed79881353b202be32d3e"
CHELPER_SHA256 = "0a6a093e38e7891efad52828b08c76d366795eca9ba8242b9d88e9b1ce9d4ffe"
CHELPER_SIZE = 394840
# The upstream release the tree is built on (klipper/README.md); klippy/.version adds the package version.
KLIPPER_VERSION = "v0.14.0"

OPERATION = "vo_klipper_tree"
# Updates stage the new tree beside the destination and keep the replaced tree until the host transaction ends.
STAGING = f"{TREE_ROOT}.vo-staging"
PREVIOUS = f"{TREE_ROOT}.vo-previous"
REMOVING = f"{TREE_ROOT}.vo-removing"
# The tree is only ever installed where nothing existed or over an earlier package tree, so restoration is removal.
PREIMAGE = {"path": TREE_ROOT, "exists": False}
UPDATE_PREIMAGE = {"path": TREE_ROOT, "exists": True, "previous": PREVIOUS}
MARKER = ".vo-klipper.json"
MARKER_SCHEMA = 1
QIDI_KLIPPY = "/home/qidi/klipper/klippy"
QIDI_CHELPER = "chelper/c_helper.so"
CHELPER = "klippy/chelper/c_helper.so"
VERSION_FILE = "klippy/.version"

# The launcher klipper.service runs in place of QIDI's command (see vo_klipper_launch.py).
LAUNCHER_OPERATION = "vo_klipper_launcher"
LAUNCHER_FILE = "/usr/local/lib/vo_klipper_launch.py"
LAUNCHER_PAYLOAD = Path(__file__).resolve().parents[1] / "system/vo_klipper_launch.py"
# Package-created files start with a marker line so later package versions replace earlier ones.
LAUNCHER_MARKER = "# vo:klipper-launcher\n"
DROPIN_OPERATION = "vo_klipper_dropin"
UNIT_FILE = "/etc/systemd/system/klipper.service"
DROPIN_FILE = "/etc/systemd/system/klipper.service.d/vo-klipper.conf"
DROPIN_MARKER = "# vo:klipper-launcher-dropin\n"
KLIPPY_PYTHON = "/home/qidi/klippy-env/bin/python"
# QIDI's unit, as on the Max 4, Plus 5, and Smart 5: the one ExecStart the drop-in replaces.
QIDI_EXEC_START = f"{KLIPPY_PYTHON} $KLIPPER_ARGS"
DROPIN_CONTENT = DROPIN_MARKER + f"""\
# Starts Klipper through Valve Oil's launcher, which runs QIDI's Klipper, or Valve Oil's when the operator chose it and it
# can run, with QIDI's arguments. Installed by Valve Oil.
[Service]
ExecStart=
ExecStart={KLIPPY_PYTHON} -I -S {LAUNCHER_FILE} $KLIPPER_ARGS
"""
# What an earlier drop-in becomes when QIDI's unit changed: it changes nothing, and Klipper starts as QIDI's unit says.
DROPIN_INACTIVE = DROPIN_MARKER + """\
# QIDI's klipper.service is not the unit Valve Oil's launcher was made for, so Klipper starts as QIDI's unit says.
# Installed by Valve Oil.
"""
UNIT_UNRECOGNIZED = ("QIDI's klipper.service is not the unit Valve Oil's Klipper launcher was made for; "
                     "Klipper stays QIDI's.")

# printer_data files: the operator's choice (written by the status service), what the launcher started last, and
# the acknowledgement Valve Oil's Klipper writes once it is ready (VO_KLIPPER_LAUNCH_ID).
CHOICE_FILE = ".vo_klipper.json"
STATUS_FILE = ".vo_klipper_status.json"
READY_FILE = ".vo_klipper_ready.json"
STATE_FILES = (CHOICE_FILE, STATUS_FILE, READY_FILE)

ABSENT = "absent"
CURRENT = "current"
OUTDATED = "outdated"
FOREIGN = "foreign"
CHUNK = 1 << 20


class KlipperTreeError(InstallerError):
    pass


def chelper_sources_sha256(source: Path | None = None) -> str:
    """One digest over every file in klippy/chelper except the library, by name."""
    source = TREE_SOURCE if source is None else source
    digest = hashlib.sha256()
    chelper = source / "klippy" / "chelper"
    for path in sorted(chelper.iterdir()):
        if path.name == "c_helper.so" or path.name == "__pycache__" or not path.is_file():
            continue
        digest.update(path.name.encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Plan:
    """What the tree holds for one printer and firmware."""
    printer: str
    firmware: str
    package_version: str
    # Package files by their path in the tree.
    files: tuple[tuple[str, Path], ...]
    # QIDI's compiled modules, by their path under klippy/.
    qidi_modules: tuple[tuple[str, ImagePin], ...]

    @property
    def version(self) -> str:
        return f"{KLIPPER_VERSION}-valve-oil-{self.package_version}"

    def expected(self) -> dict[str, str]:
        """Every file the tree holds, with its sha256."""
        hashes = {relative: sha256_file(path) for relative, path in self.files}
        hashes.update((f"klippy/{path}", pin.sha256) for path, pin in self.qidi_modules)
        hashes[VERSION_FILE] = hashlib.sha256(_version_bytes(self.version)).hexdigest()
        hashes[CHELPER] = CHELPER_SHA256
        return hashes


def plan(*, source: Path, printer: str, firmware: str, package_version: str,
         extra_modules: Iterable[tuple[Path, str]], qidi_modules: dict[str, ImagePin]) -> Plan:
    """The tree for `printer` on `firmware`: source's klippy/ with the printer's overlay over it, Valve Oil's modules
    (source file, destination under the tree), and QIDI's modules. Raises KlipperTreeError on a package that can't
    make a whole tree."""
    klippy = source / "klippy"
    if not (klippy / "klippy.py").is_file():
        raise KlipperTreeError("Valve Oil's Klipper is missing from the package.")
    files: dict[str, Path] = {}
    # The printer's files replace or add to the common ones.
    for base in (source, source / "printers" / printer):
        tree = base / "klippy"
        if not tree.is_dir():
            continue
        for path in sorted(tree.rglob("*")):
            relative = path.relative_to(base).as_posix()
            if "__pycache__" in path.parts or path.suffix == ".pyc" or path.is_dir():
                continue
            if path.is_symlink() or not path.is_file():
                raise KlipperTreeError(f"Valve Oil's Klipper has an unexpected entry: {relative}")
            files[relative] = path
    if (source / "COPYING").is_file():
        files["COPYING"] = source / "COPYING"
    for module_source, destination in extra_modules:
        if destination in files or not module_source.is_file():
            raise KlipperTreeError(f"Valve Oil's Klipper can't take the module {destination}.")
        files[destination] = module_source
    reserved = {CHELPER, VERSION_FILE, *(f"klippy/{path}" for path in qidi_modules)}
    if reserved & set(files):
        raise KlipperTreeError(f"Valve Oil's Klipper already has {sorted(reserved & set(files))[0]}.")
    if chelper_sources_sha256(source) != CHELPER_SOURCES_SHA256:
        raise KlipperTreeError("The package's Klipper C helper library wasn't built from its sources.")
    return Plan(printer=printer, firmware=firmware, package_version=package_version,
                files=tuple(sorted(files.items())), qidi_modules=tuple(sorted(qidi_modules.items())))


def _version_bytes(version: str) -> bytes:
    return f"{version}\n".encode("utf-8")


def qidi_modules_match(qidi_klippy: Path, pins: dict[str, ImagePin]) -> bool:
    """Whether QIDI's klippy/ has exactly these compiled modules (its c_helper.so aside)."""
    return _live_modules(qidi_klippy) == set(pins) and all(
        _matches_pin(qidi_klippy / path, pin) for path, pin in pins.items())


def _live_modules(qidi_klippy: Path) -> set[str]:
    found = set()
    for directory, dirnames, filenames in os.walk(qidi_klippy):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for name in filenames:
            if name.endswith(".so"):
                relative = Path(directory, name).relative_to(qidi_klippy).as_posix()
                if relative != QIDI_CHELPER:
                    found.add(relative)
    return found


def _matches_pin(path: Path, pin: ImagePin) -> bool:
    try:
        return not path.is_symlink() and path.is_file() and path.stat().st_size == pin.size and sha256_file(path) == pin.sha256
    except OSError:
        return False


def read_marker(destination: Path) -> dict | None:
    """The marker of a package-owned tree, or None when the directory isn't one."""
    marker = destination / MARKER
    if destination.is_symlink() or not destination.is_dir() or marker.is_symlink() or not marker.is_file():
        return None
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("schema") != MARKER_SCHEMA or not isinstance(value.get("files"), dict):
        return None
    return value


def state(destination: Path, tree: Plan) -> str:
    if not destination.exists() and not destination.is_symlink():
        return ABSENT
    marker = read_marker(destination)
    if marker is None:
        return FOREIGN
    expected = tree.expected()
    recorded = marker["files"]
    if (marker.get("printer"), marker.get("firmware")) != (tree.printer, tree.firmware) or set(recorded) != set(expected):
        return OUTDATED
    for relative, sha256 in expected.items():
        target = destination / relative
        if (not isinstance(recorded[relative], dict) or recorded[relative].get("sha256") != sha256
                or target.is_symlink() or not target.is_file() or sha256_file(target) != sha256):
            return OUTDATED
    return CURRENT if chelper_newest(destination) else OUTDATED


def chelper_newest(destination: Path) -> bool:
    """Whether c_helper.so is at least as new as everything beside it, so Klipper never tries to rebuild it."""
    library = destination / CHELPER
    try:
        built = library.stat().st_mtime_ns
        return all(item.stat().st_mtime_ns <= built for item in library.parent.iterdir()
                   if item.name != library.name and item.is_file())
    except OSError:
        return False


def stage(tree: Plan, target: Path, *, qidi_klippy: Path, soc: dict, chelper: Path | None = None) -> None:
    """Build the tree in a new directory, copying QIDI's modules as they are now and checking each against its pin,
    with the C helper library newer than its sources and the marker written last. `soc` is the firmware manifest's
    SOC version and file, which the launcher compares at every start."""
    chelper = CHELPER_PAYLOAD if chelper is None else chelper
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    for relative, source in tree.files:
        _copy(source, target / relative)
    qidi: dict[str, dict] = {}
    for relative, pin in tree.qidi_modules:
        source = qidi_klippy / relative
        before = os.stat(source, follow_symlinks=False)
        sha256 = _copy(source, target / "klippy" / relative)
        if sha256 != pin.sha256 or before.st_size != pin.size:
            raise KlipperTreeError(f"QIDI's {relative} changed while Valve Oil's Klipper was being installed.")
        qidi[relative] = {"sha256": sha256, "size": before.st_size, "mtime_ns": before.st_mtime_ns}
    version = target / VERSION_FILE
    version.write_bytes(_version_bytes(tree.version))
    version.chmod(0o644)
    library = target / CHELPER
    if _copy(chelper, library) != CHELPER_SHA256:
        raise KlipperTreeError("The package's Klipper C helper library doesn't match its checksum.")
    # Klipper rebuilds the library when any file beside it is newer, and the printers have no compiler.
    newest = max(item.stat().st_mtime_ns for item in library.parent.iterdir() if item.is_file())
    os.utime(library, ns=(newest + 1_000_000_000, newest + 1_000_000_000))
    for directory in (item for item in target.rglob("*") if item.is_dir()):
        directory.chmod(0o755)
    files = {}
    for relative in tree.expected():
        path = target / relative
        status = path.stat()
        files[relative] = {"sha256": sha256_file(path), "size": status.st_size, "mtime_ns": status.st_mtime_ns}
    marker = {
        "schema": MARKER_SCHEMA,
        "package_version": tree.package_version,
        "printer": tree.printer,
        "firmware": tree.firmware,
        "soc": {"version": soc.get("version"), "file": soc.get("file")},
        "klipper_version": tree.version,
        "qidi_modules": qidi,
        "files": files,
    }
    marker_path = target / MARKER
    marker_path.write_text(json.dumps(marker, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    marker_path.chmod(0o644)


def _copy(source: Path, destination: Path) -> str:
    """Copy a file to a new path, returning the sha256 of what was written."""
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with source.open("rb") as reader, destination.open("xb") as writer:
        while chunk := reader.read(CHUNK):
            digest.update(chunk)
            writer.write(chunk)
    destination.chmod(0o644)
    return digest.hexdigest()


def remove(directory: Path, removing: Path) -> None:
    """Remove a package-owned tree, first moving it aside so Klipper never starts from a partial tree."""
    if read_marker(directory) is None:
        raise KlipperTreeError(f"{directory} is not Valve Oil's Klipper; preserving it.")
    discard(removing)
    directory.rename(removing)
    discard(removing)


def discard(directory: Path) -> None:
    """Delete a leftover staging, previous, or removal directory."""
    if directory.is_symlink():
        directory.unlink()
    elif directory.is_dir():
        shutil.rmtree(directory)
    elif directory.exists():
        directory.unlink()


def choice(printer_data_root: Path) -> str:
    """The operator's choice: "valve_oil", or "qidi" when the file is missing, unreadable, or says anything else."""
    try:
        value = json.loads((printer_data_root / CHOICE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "qidi"
    return "valve_oil" if isinstance(value, dict) and value.get("use") == "valve_oil" else "qidi"


def remove_state_files(printer_data_root: Path) -> None:
    """Uninstall: drop the choice, the launcher's last status, and Klipper's acknowledgement."""
    for name in STATE_FILES:
        try:
            (printer_data_root / name).unlink()
        except FileNotFoundError:
            pass


# ---- launcher and drop-in -------------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def launcher_source() -> str:
    try:
        source = LAUNCHER_PAYLOAD.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallerError(f"Valve Oil's Klipper launcher is missing from the package: {exc}") from exc
    if not source.startswith(LAUNCHER_MARKER):
        raise InstallerError("Valve Oil's Klipper launcher payload lacks its package marker.")
    compile(source, "vo_klipper_launch.py", "exec")
    return source


def patch_launcher(current: str) -> str:
    if current == "" or current.startswith(LAUNCHER_MARKER):
        return launcher_source()
    raise InstallerError(f"Existing {LAUNCHER_FILE} is unrecognized and was preserved.")


def patch_dropin(current: str, *, unit_recognized: bool = True) -> str:
    """The drop-in, while QIDI's unit is the one it was made for. An earlier drop-in on a changed unit is made inactive;
    none is installed on one."""
    if current != "" and not current.startswith(DROPIN_MARKER):
        raise InstallerError(f"Existing {DROPIN_FILE} is unrecognized and was preserved.")
    if unit_recognized:
        return DROPIN_CONTENT
    if current == "":
        raise InstallerError(UNIT_UNRECOGNIZED)
    return DROPIN_INACTIVE


def unit_recognized(unit: Path, dropins: Path) -> bool:
    """Whether klipper.service is QIDI's: one ExecStart, the klippy-env Python with $KLIPPER_ARGS, and no other drop-in
    that sets ExecStart."""
    try:
        if unit.is_symlink() or not unit.is_file():
            return False
        commands = _exec_starts(unit.read_text(encoding="utf-8"))
        others = [path for path in sorted(dropins.glob("*.conf")) if path.name != PurePosixPath(DROPIN_FILE).name]
        if any(_exec_starts(path.read_text(encoding="utf-8")) for path in others):
            return False
    except (OSError, UnicodeDecodeError):
        return False
    return len(commands) == 1 and " ".join(commands[0].split()) == QIDI_EXEC_START


def _exec_starts(text: str) -> list[str]:
    commands = []
    for line in text.splitlines():
        name, _, value = line.strip().partition("=")
        if name.strip() == "ExecStart":
            commands.append(value.strip())
    return commands
