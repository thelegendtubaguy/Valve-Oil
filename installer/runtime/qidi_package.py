"""QIDI's firmware package for the target firmware: copied from the USB drive an install was started from or downloaded
only from this project's mirror, verified, and unpacked.

The archive goes to a cache folder in the printer user's home, never /tmp, which the package's preinst empties. A
partial download is resumed. Nothing on the printer changes until the archive has the pinned size and SHA-256, and
even then the archive is untrusted: it is unpacked only as a flat set of regular files with plain names, next to
QIDI's update folder, and nothing in it runs except the SOC Debian package handed to dpkg, as QIDI's updater does.
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import naming
from .errors import FirmwareDetectionError, InstallerError, PathSafetyError
from .firmware import detect_firmware, supported_printer
from .fs_atomic import atomic_write_text, fsync_directory
from .path_safety import ensure_external_path_has_no_symlink_components

CACHE_DIRECTORY = f"{naming.CACHE_DIRECTORY}/firmware-update"  # under the printer user's home
STAGING_DIRECTORY = ".vo-update-staging"  # beside QIDI's update folder, so the two can be swapped by rename
PREVIOUS_DIRECTORY = ".vo-update-previous"  # QIDI's update folder while it is replaced without an atomic exchange
STAGED_MARKER = ".vo-staged.json"
MANIFEST_NAME = "firmware_manifest.json"
CHUNK = 1 << 20
# The SOC package's files unpack to about 4.2 times the archive (Max 4 01.01.07.01: 252 MiB of xz, 1,096 MiB of files),
# and dpkg writes each new file beside the old one before replacing it.
DPKG_FACTOR = 5
MARGIN_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 32
MAX_MANIFEST_BYTES = 65536
# GitHub serves release assets from github.com, which redirects to its asset hosts.
ALLOWED_HOSTS = ("github.com",)
ALLOWED_HOST_SUFFIXES = (".githubusercontent.com",)

# Why a package can't be used, as status-file blocker codes (valve_oil_status.BLOCKERS).
UNREACHABLE, UNVERIFIED, FREE_SPACE = "mirror_unreachable", "package_unverified", "free_space"

# The USB installer names the drive it was started from by its mount point, normally printer_data/gcodes/USB. The
# drive's valve-oil/ folder holds each printer's target package under the archive name package.yaml pins.
USB_DRIVE_ENV = "VO_USB_DRIVE_ROOT"
DRIVE_FOLDER = "valve-oil"


class PackageError(InstallerError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StagedPackage:
    root: Path
    soc: Path  # the SOC Debian package

    def component(self, name: str) -> tuple[Path, str] | None:
        """(file, version) of a firmware manifest component such as "CLOSED_LOOP_MOTOR", or None when the package has
        none. stage() checked every entry names a plain file in the package."""
        entry = json.loads((self.root / MANIFEST_NAME).read_text(encoding="utf-8")).get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            return None
        return self.root / entry["file"], entry.get("version")


def home(paths) -> Path:
    return paths.printer_data_root.parent


def cache_dir(paths) -> Path:
    return home(paths) / CACHE_DIRECTORY


def archive_path(paths, target) -> Path:
    return cache_dir(paths) / target.archive


def staging_dir(paths) -> Path:
    return update_dir(paths).parent / STAGING_DIRECTORY


def update_dir(paths) -> Path:
    """QIDI's update folder, whose firmware_manifest.json names the installed firmware."""
    return paths.firmware_manifest_path.parent


# ---- download -------------------------------------------------------------------------------------------------------


class _MirrorRedirects(urllib.request.HTTPRedirectHandler):
    """Follow GitHub's redirects to its asset hosts over https, and nothing else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise urllib.error.HTTPError(newurl, code, "redirect outside the mirror", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def allowed_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host in ALLOWED_HOSTS or host.endswith(ALLOWED_HOST_SUFFIXES))


def mirror_urlopen(request, timeout=60):
    return urllib.request.build_opener(_MirrorRedirects()).open(request, timeout=timeout)


def downloaded_bytes(paths, target) -> int:
    for path in (archive_path(paths, target), _part(paths, target)):
        try:
            return min(path.stat().st_size, target.size)
        except OSError:
            continue
    return 0


def cached(paths, target) -> bool:
    """The archive is in the cache at its pinned size (its hash is checked again before use)."""
    try:
        return archive_path(paths, target).stat().st_size == target.size
    except OSError:
        return False


def required_free_bytes(target, *, downloaded: int = 0, unpacked: bool = False) -> int:
    """Free space a firmware update needs before it starts: the rest of the download, the unpacked package, and dpkg's
    room for the SOC package's files, with a margin. `unpacked`: only dpkg's room, the package being unpacked
    already."""
    subtotal = DPKG_FACTOR * target.size + (0 if unpacked else (target.size - downloaded) + target.size)
    return subtotal + max(MARGIN_BYTES, subtotal // 5)


def check_free_space(paths, target, *, disk_usage=shutil.disk_usage, unpacked: bool = False) -> None:
    needed = required_free_bytes(target, downloaded=downloaded_bytes(paths, target), unpacked=unpacked)
    if _free(home(paths), disk_usage) < needed:
        raise PackageError(f"The firmware update needs {needed // 2 ** 20} MiB free in {home(paths)}.", code=FREE_SPACE)


# What a firmware update short of space removes: files that aren't the operator's and that nothing on the printer
# reads again. The system journal beyond JOURNAL_KEEP, apt's download cache, the archive the touchscreen's online update
# leaves in ~/download, and rotated logs in printer_data/logs (klippy.log.2026-11-10, vo_box_link.log.1) older than
# ROTATED_LOG_DAYS, so the logs a support issue asks for stay. Prints, timelapses, and QIDI's update folder, whose THR
# image the board firmware reads, are left alone.
JOURNAL_KEEP = "50M"
ONLINE_UPDATE_ARCHIVE = "download/online_update.zip"
ROTATED_LOG_DAYS = 2
_ROTATED_LOG = re.compile(r"\.log\.(\d{4}-\d{2}-\d{2}|\d+)$")


def free_up_space(paths, *, sudo, reporter, disk_usage=shutil.disk_usage, clock=time.time) -> int:
    """Remove the files above; the bytes this freed. sudo(command) runs a command as root."""
    before = _free(home(paths), disk_usage)
    cutoff = clock() - ROTATED_LOG_DAYS * 24 * 3600
    try:
        logs = list((paths.printer_data_root / "logs").iterdir())
    except OSError:
        logs = []
    for path in logs:
        try:
            if _ROTATED_LOG.search(path.name) and not path.is_symlink() and path.is_file() \
                    and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass
    for command in (["journalctl", f"--vacuum-size={JOURNAL_KEEP}"], ["apt-get", "clean"],
                    ["rm", "-f", str(home(paths) / ONLINE_UPDATE_ARCHIVE)]):
        try:
            sudo(command)
        except (OSError, subprocess.SubprocessError, InstallerError):
            pass
    freed = max(0, _free(home(paths), disk_usage) - before)
    reporter.line(f"Freed {freed // 2 ** 20} MiB for the firmware update by removing old logs and a leftover QIDI "
                  "download.")
    return freed


def drive_archive(target, environ) -> Path | None:
    """Where the USB drive the installer was started from keeps the target firmware's archive; None when no drive was
    named."""
    root = (environ or {}).get(USB_DRIVE_ENV)
    return Path(root) / DRIVE_FOLDER / target.archive if root else None


def drive_copy_present(target, environ) -> bool:
    """The drive holds a regular file of the archive's pinned size under its name (its hash is checked when copied)."""
    path = drive_archive(target, environ)
    try:
        info = path.lstat() if path is not None else None
    except OSError:
        return False
    return info is not None and stat.S_ISREG(info.st_mode) and info.st_size == target.size


def fetch(paths, target, *, reporter, environ=None, urlopen=mirror_urlopen) -> Path:
    """The target firmware's verified archive in the cache: the one cached already, else the USB drive's copy, else a
    download from the mirror. Raises PackageError as download() does."""
    final = _verified_cache(paths, target)
    if final is not None:
        return final
    final = copy_from_drive(paths, target, environ=environ, reporter=reporter)
    if final is not None:
        return final
    reporter.line(f"Downloading QIDI firmware {target.version} from {target.url}")
    return download(paths, target, reporter=reporter, urlopen=urlopen)


def copy_from_drive(paths, target, *, environ, reporter) -> Path | None:
    """Copy the target firmware's archive from the USB drive into the cache, checked against its pinned size and
    SHA-256 as a download is; the cached archive, or None when no drive was named, or its copy is missing or doesn't
    match, which is reported for the caller to download instead.

    The copy is written to the partial download's name, replacing any partial download, and renamed into place once
    what reached the cache matches the pin, so a cut copy leaves no archive and the next attempt copies it again. A
    copy that doesn't match is removed, so the download starts afresh rather than resuming from the drive's bytes.
    """
    source = drive_archive(target, environ)
    if source is None:
        return None
    cache = _cache(paths)
    _prune(cache, keep={target.archive, target.archive + ".part"})
    part = _part(paths, target)
    where = f"{DRIVE_FOLDER}/{target.archive}"
    try:
        problem = _copy(source, part, target, reporter=reporter)
        if problem is None:
            verify(part, target)  # what reached the cache
    except (OSError, PackageError) as exc:
        part.unlink(missing_ok=True)  # a download must not resume from the drive's bytes
        problem = _MISMATCH if isinstance(exc, PackageError) else \
            f"couldn't be copied ({exc.strerror or type(exc).__name__})"
    if problem is not None:
        if problem == _MISSING:
            reporter.line(f"QIDI firmware {target.version}'s package isn't on the USB drive ({where}); downloading it "
                          "from this project's mirror instead.")
        else:
            reporter.line(f"The USB drive's copy of QIDI firmware {target.version} ({where}) {problem}; downloading it "
                          "from this project's mirror instead.")
        _debug(reporter, "firmware_package_drive_copy", result="fallback", source=str(source), reason=problem)
        return None
    final = cache / target.archive
    os.replace(part, final)
    fsync_directory(cache)
    _debug(reporter, "firmware_package_drive_copy", result="copied", source=str(source))
    return final


_MISSING, _MISMATCH = "isn't on the drive", "doesn't match its pinned size and SHA-256"


def _copy(source: Path, part: Path, target, *, reporter) -> str | None:
    """Copy and hash the drive's file in one read; what's wrong with it, or None."""
    try:
        fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except (FileNotFoundError, NotADirectoryError):
        return _MISSING
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != target.size:
            return _MISMATCH
        reporter.line(f"Copying QIDI firmware {target.version} ({target.size // 2 ** 20} MiB) from the USB drive")
        digest = hashlib.sha256()
        copied = 0
        with part.open("wb") as out:
            while copied < target.size and (chunk := handle.read(min(CHUNK, target.size - copied))):
                digest.update(chunk)
                out.write(chunk)
                copied += len(chunk)
            out.flush()
            os.fsync(out.fileno())
        if copied != target.size or handle.read(1) or digest.hexdigest() != target.sha256:
            part.unlink()
            return _MISMATCH
    return None


def _verified_cache(paths, target) -> Path | None:
    """The cached archive when it matches its pin; a cached archive that doesn't is removed."""
    final = archive_path(paths, target)
    if not cached(paths, target):
        return None
    try:
        verify(final, target)
        return final
    except PackageError:
        final.unlink()
        return None


def _debug(reporter, event: str, **fields) -> None:
    debug = getattr(reporter, "debug", None)
    if debug is not None:
        debug(event=event, **fields)


def download(paths, target, *, reporter, urlopen=mirror_urlopen, attempts: int = 3) -> Path:
    """Download the target firmware's archive from the mirror, resuming a partial download, and verify it.

    Only target.url is fetched. Raises PackageError: UNREACHABLE when the mirror can't be reached or the download
    stops short, UNVERIFIED when the archive doesn't match its pinned size and SHA-256.
    """
    if not allowed_url(target.url):
        raise PackageError("The firmware package URL is not on this project's mirror.", code=UNVERIFIED)
    cache = _cache(paths)
    _prune(cache, keep={target.archive, target.archive + ".part"})
    final = _verified_cache(paths, target)
    if final is not None:
        return final
    final = cache / target.archive
    part = _part(paths, target)
    if part.is_file() and part.stat().st_size > target.size:
        part.unlink()  # not the pinned archive: start again
    problem = None
    for _ in range(attempts):
        have = part.stat().st_size if part.is_file() else 0
        if have >= target.size:
            break
        try:
            have = _fetch(part, target, have=have, reporter=reporter, urlopen=urlopen)
        except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError) as exc:
            problem = exc
            continue
        if have >= target.size:
            break
    if not part.is_file() or part.stat().st_size != target.size:
        detail = f" ({type(problem).__name__})" if problem is not None else ""
        raise PackageError("The firmware package could not be downloaded from this project's mirror" + detail + ".",
                           code=UNREACHABLE)
    try:
        verify(part, target)
    except PackageError:
        part.unlink()
        raise
    os.replace(part, final)
    fsync_directory(cache)
    return final


def verify(path: Path, target) -> None:
    """Raise PackageError(UNVERIFIED) unless the file has the archive's pinned size and SHA-256."""
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise PackageError(f"The firmware package could not be read: {exc}", code=UNVERIFIED) from exc
    if size != target.size or digest.hexdigest() != target.sha256:
        raise PackageError("The downloaded firmware package does not match its pinned size and SHA-256.",
                           code=UNVERIFIED)


def remove_cache(paths) -> None:
    """Drop the cached package after a successful update; the update's log stays."""
    _prune(_cache(paths), keep=set())


def _cache(paths) -> Path:
    cache = cache_dir(paths)
    ensure_external_path_has_no_symlink_components(root=home(paths), target=cache)
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    return cache


def _part(paths, target) -> Path:
    return cache_dir(paths) / (target.archive + ".part")


def _prune(cache: Path, *, keep: set[str]) -> None:
    """At most one package stays cached: drop other archives and partial downloads."""
    for path in cache.iterdir():
        if path.is_file() and path.name not in keep and path.name.endswith((".zip", ".part")):
            path.unlink()


def _fetch(part: Path, target, *, have: int, reporter, urlopen) -> int:
    headers = {"User-Agent": "valve-oil-installer"}
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(target.url, headers=headers)
    with urlopen(request, timeout=60) as response:
        status = getattr(response, "status", 200)
        if have and (status != 206 or not _resumes_at(response, have)):
            have = 0  # The server sent the whole file.
        elif status not in (200, 206):
            raise ValueError(f"HTTP {status}")
        mode = "r+b" if have else "wb"
        with part.open(mode) as out:
            out.seek(have)
            out.truncate()
            reported = have * 10 // target.size
            while have < target.size:
                chunk = response.read(min(CHUNK, target.size - have))
                if not chunk:
                    break
                out.write(chunk)
                have += len(chunk)
                if have * 10 // target.size > reported:
                    reported = have * 10 // target.size
                    reporter.line(f"  Downloaded {reported * 10}% of {target.size // 2 ** 20} MiB")
            if have == target.size and response.read(1):
                out.close()
                part.unlink()
                raise PackageError("The mirror sent more than the firmware package's pinned size.", code=UNVERIFIED)
            out.flush()
            os.fsync(out.fileno())
    return have


def _resumes_at(response, offset: int) -> bool:
    value = response.headers.get("Content-Range", "") if getattr(response, "headers", None) else ""
    return value.startswith(f"bytes {offset}-")


def _free(root: Path, disk_usage) -> int:
    usage = disk_usage(root)
    free = getattr(usage, "free", None)
    return int(usage[2] if free is None else free)


# ---- unpacking ------------------------------------------------------------------------------------------------------


def stage(paths, target, printer, *, archive: Path, remove_tree) -> StagedPackage:
    """Unpack a verified archive into the staging folder beside QIDI's update folder.

    The archive must be a flat set of at most MAX_MEMBERS regular files with plain names, including a firmware manifest
    whose SOC entry is the printer's SOC package of the target version and whose every file is in the archive.
    Anything else raises PackageError(UNVERIFIED) with nothing written outside the staging folder.
    remove_tree(path) removes a folder that may hold files the printer user can't delete.
    """
    root = staging_dir(paths)
    ensure_external_path_has_no_symlink_components(root=home(paths), target=root)
    done = staged(paths, target, printer)
    if done is not None:
        return done
    if root.exists() or root.is_symlink():
        remove_tree(root)
    verify(archive, target)
    try:
        with zipfile.ZipFile(archive) as package:
            members = _checked_members(package, target)
            manifest = _checked_manifest(package, members, target, printer)
            root.mkdir(mode=0o755)
            for info in members.values():
                _extract(package, info, root)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, RuntimeError) as exc:
        raise PackageError(f"The firmware package could not be unpacked: {exc}", code=UNVERIFIED) from exc
    fsync_directory(root)
    atomic_write_text(root / STAGED_MARKER, json.dumps({"archive_sha256": target.sha256}) + "\n", mode=0o644)
    return _package(root, manifest)


def staged(paths, target, printer) -> StagedPackage | None:
    """The completely unpacked package in the staging folder, or None."""
    root = staging_dir(paths)
    try:
        if root.is_symlink() or json.loads((root / STAGED_MARKER).read_text(encoding="utf-8")).get(
                "archive_sha256") != target.sha256:
            return None
        manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        return None
    return _package(root, manifest)


def _package(root: Path, manifest: dict) -> StagedPackage:
    return StagedPackage(root=root, soc=root / manifest["SOC"]["file"])


def _plain_name(name: str) -> bool:
    return (bool(name) and len(name) <= 128 and not name.startswith(".") and "/" not in name and "\\" not in name
            and "\x00" not in name and name.isprintable())


def _checked_members(package: zipfile.ZipFile, target) -> dict[str, zipfile.ZipInfo]:
    infos = package.infolist()
    if not infos or len(infos) > MAX_MEMBERS:
        raise PackageError("The firmware package has an unexpected number of files.", code=UNVERIFIED)
    members: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for info in infos:
        kind = stat.S_IFMT(info.external_attr >> 16)
        if (not _plain_name(info.filename) or info.is_dir() or kind not in (0, stat.S_IFREG)
                or info.flag_bits & 0x1 or info.filename in members):
            raise PackageError(f"The firmware package has an entry it may not have: {info.filename!r}",
                               code=UNVERIFIED)
        members[info.filename] = info
        total += info.file_size
    # The SOC package is already compressed; a much larger total is not a QIDI package.
    if total > 2 * target.size + MARGIN_BYTES:
        raise PackageError("The firmware package unpacks to more than expected.", code=UNVERIFIED)
    return members


def _checked_manifest(package: zipfile.ZipFile, members, target, printer) -> dict:
    info = members.get(MANIFEST_NAME)
    if info is None or info.file_size > MAX_MANIFEST_BYTES:
        raise PackageError("The firmware package has no firmware manifest.", code=UNVERIFIED)
    try:
        manifest = json.loads(package.read(info).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PackageError("The firmware package's manifest is not valid JSON.", code=UNVERIFIED) from exc
    if not isinstance(manifest, dict):
        raise PackageError("The firmware package's manifest is not a list of components.", code=UNVERIFIED)
    for name, entry in manifest.items():
        if (not isinstance(entry, dict) or not isinstance(entry.get("file"), str) or entry["file"] not in members
                or entry["file"] == MANIFEST_NAME or not isinstance(entry.get("version"), str)):
            raise PackageError(f"The firmware package's manifest entry {name!r} names no file in the package.",
                               code=UNVERIFIED)
    soc = manifest.get("SOC")
    if (not isinstance(soc, dict) or soc["version"] != target.soc_version
            or not soc["file"].startswith(printer.soc_file_prefix)):
        raise PackageError(f"The firmware package is not {printer.display_name} firmware {target.soc_version}.",
                           code=UNVERIFIED)
    return manifest


def _extract(package: zipfile.ZipFile, info: zipfile.ZipInfo, root: Path) -> None:
    _extract_to(package, info, root / info.filename, algorithms=())


def _extract_to(package: zipfile.ZipFile, info: zipfile.ZipInfo, path: Path, *, algorithms) -> dict[str, str]:
    """Write one member to a new file, its CRC-32 checked by zipfile; the hex digests of `algorithms` over it."""
    hashes = {name: hashlib.new(name) for name in algorithms}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    written = 0
    with os.fdopen(fd, "wb") as out, package.open(info) as source:
        while chunk := source.read(CHUNK):
            written += len(chunk)
            if written > info.file_size:
                raise PackageError(f"The firmware package entry {info.filename!r} is longer than it says.",
                                   code=UNVERIFIED)
            for digest in hashes.values():
                digest.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    return {name: digest.hexdigest() for name, digest in hashes.items()}


# ---- QIDI's update folder -------------------------------------------------------------------------------------------


def remove_installable_soc(paths, *, remove_file) -> str | None:
    """Remove the SOC Debian package QIDI's update folder holds, before another SOC package is installed.

    The touchscreen's per-board update installs the SOC package it finds there whenever the folder's manifest names
    another SOC version than the running app's. Between installing the new package and replacing the folder, the new
    app (not yet patched) would install the old package again. Returns the removed file's name, if any.
    """
    update = update_dir(paths)
    try:
        name = json.loads((update / MANIFEST_NAME).read_text(encoding="utf-8"))["SOC"]["file"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(name, str) or not _plain_name(name):
        return None
    path = update / name
    ensure_external_path_has_no_symlink_components(root=update.parent, target=path)
    if not path.is_file():
        return None
    remove_file(path)
    fsync_directory(update)
    return name


def install_update_folder(paths, target, *, remove_tree) -> None:
    """Make the staged package QIDI's update folder, so a power cut leaves the old or the new manifest readable.

    On Linux the two folders are exchanged in one rename; elsewhere the old folder is moved aside first, and a run
    that finds it moved aside finishes the move. Safe to repeat: a folder already holding the target package is kept.
    """
    update, root, previous = update_dir(paths), staging_dir(paths), update_dir(paths).parent / PREVIOUS_DIRECTORY
    for path in (update, root, previous):
        ensure_external_path_has_no_symlink_components(root=update.parent, target=path)
    if (root / STAGED_MARKER).is_file():
        if update.exists() and not exchange_paths(root, update):
            os.rename(update, previous)
            fsync_directory(update.parent)
        if not update.exists():
            os.rename(root, update)
        fsync_directory(update.parent)
    elif not update.exists() and previous.exists():
        # Interrupted before the staged folder was moved in, which no longer holds the package: put QIDI's back.
        os.rename(previous, update)
        fsync_directory(update.parent)
    marker = update / STAGED_MARKER
    if marker.is_file():
        marker.unlink()
        fsync_directory(update)
    for leftover in (root, previous):
        if leftover.exists() or leftover.is_symlink():
            remove_tree(leftover)
    try:
        version = json.loads((update / MANIFEST_NAME).read_text(encoding="utf-8"))["SOC"]["version"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PathSafetyError(f"QIDI's update folder has no readable firmware manifest: {exc}") from exc
    if version != target.soc_version:
        raise PathSafetyError(f"QIDI's update folder holds firmware {version}, not {target.soc_version}.")


def exchange_paths(first: Path, second: Path) -> bool:
    """Atomically swap two paths with renameat2(RENAME_EXCHANGE); False where that isn't available."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (OSError, AttributeError):
        return False
    at_fdcwd, rename_exchange = -100, 2
    if renameat2(at_fdcwd, os.fsencode(first), at_fdcwd, os.fsencode(second), rename_exchange) == 0:
        return True
    error = ctypes.get_errno()
    if error in (errno.ENOSYS, errno.EINVAL, getattr(errno, "ENOTSUP", errno.EINVAL)):
        return False
    raise OSError(error, os.strerror(error), str(first))


# ---- QIDI's system package file, put back from the USB drive -------------------------------------------------------

# What restore_soc_package_from_drive found or did.
RESTORED = "restored"  # the file is back in QIDI's update folder
ALREADY_PRESENT = "already_present"  # the folder holds it already
NO_DRIVE_COPY = "no_drive_copy"  # no drive was named, or it has no copy of the target package
MISMATCH = "mismatch"  # the drive's package doesn't match its pin, or doesn't hold the file the folder's manifest names
NOT_TARGET = "not_target"  # the printer doesn't run the target firmware, so the drive's package isn't its own
FAILED = "failed"  # the file couldn't be written; the folder is as it was
RESTORE_PART = ".vo-soc-restore"  # the file in QIDI's update folder before its rename to the manifest's name
RESTORE_MODE = "0644"  # what QIDI's own unzip gives it: QIDI's archives record every file as 0644


@dataclass(frozen=True)
class SocRestore:
    result: str
    message: str = ""
    file: str | None = None  # the system package file the folder's manifest names


def restore_soc_package_from_drive(paths, manifest, *, environ, sudo, reporter,
                                   disk_usage=shutil.disk_usage, copy_started=None) -> SocRestore:
    """Put QIDI's system package file back in QIDI's update folder from the target package on the USB drive.

    A USB run's hand-off restores QIDI's manifest and board images but not the system package file the manifest names.
    On the target firmware, this extracts that file, and only it, from the drive's package once the package matches
    its pinned size and SHA-256 and its manifest names the same file, and moves it into the folder owned by root with
    mode 0644, as QIDI's unzip leaves it. sudo(command) runs a command as root and returns its CompletedProcess.
    copy_started(), when given, is called once the drive holds the package, before it is checked and copied.
    Never raises for a missing drive or a write that fails: the result says what happened, and leaves the folder as
    it was unless it is RESTORED.
    """
    try:
        return _restore_soc(paths, manifest, environ=environ, sudo=sudo, reporter=reporter, disk_usage=disk_usage,
                            copy_started=copy_started)
    except (OSError, InstallerError, subprocess.SubprocessError, zipfile.BadZipFile, zipfile.LargeZipFile,
            EOFError, RuntimeError) as exc:
        message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
        reporter.line(f"QIDI's system package file wasn't put back in QIDI's update folder: {message}")
        _debug(reporter, "soc_restore", result=FAILED, error=message)
        return SocRestore(FAILED, message)


def _restore_soc(paths, manifest, *, environ, sudo, reporter, disk_usage, copy_started=None) -> SocRestore:
    try:
        detected = detect_firmware(paths.firmware_manifest_path)
    except FirmwareDetectionError:
        return _restore_result(reporter, NOT_TARGET, "QIDI's update folder has no readable firmware manifest")
    printer = supported_printer(manifest, detected)
    target = printer.target_firmware if printer is not None else None
    if target is None or printer.firmware_key(detected.version) != target.version:
        return _restore_result(reporter, NOT_TARGET, f"the printer runs QIDI firmware {detected.version}, not the "
                                                     "target firmware the drive carries")
    name = detected.soc_file
    if not isinstance(name, str) or not _plain_name(name) or not name.startswith(printer.soc_file_prefix):
        return _restore_result(reporter, MISMATCH, "QIDI's firmware manifest names no plain system package file",
                               shown=True)
    update = update_dir(paths)
    final, part = update / name, update / RESTORE_PART
    for path in (final, part):
        ensure_external_path_has_no_symlink_components(root=update.parent, target=path)
    if final.is_file():
        return _restore_result(reporter, ALREADY_PRESENT, f"QIDI's update folder holds {name}", file=name)
    source = drive_archive(target, environ)
    where = f"{DRIVE_FOLDER}/{target.archive}"
    try:
        info = source.lstat() if source is not None else None
    except OSError:
        info = None
    if info is None:
        return _restore_result(reporter, NO_DRIVE_COPY, f"QIDI's system package file {name} wasn't put back in QIDI's "
                               f"update folder: the USB drive has no {where}", file=name, shown=source is not None)
    if copy_started is not None:
        copy_started()
    try:
        if not stat.S_ISREG(info.st_mode):
            raise PackageError("not a file", code=UNVERIFIED)
        verify(source, target)
    except PackageError:
        return _restore_result(reporter, MISMATCH, f"QIDI's system package file {name} wasn't put back in QIDI's "
                               f"update folder: the USB drive's {where} doesn't match its pinned size and SHA-256",
                               file=name, shown=True)
    scratch = _cache(paths) / RESTORE_PART.lstrip(".")
    _remove_root_file(scratch, sudo)
    _remove_root_file(part, sudo)
    with zipfile.ZipFile(source) as package:
        try:
            entry = _checked_manifest(package, members := _checked_members(package, target), target, printer)["SOC"]
        except PackageError as exc:
            return _restore_result(reporter, MISMATCH, f"QIDI's system package file {name} wasn't put back in QIDI's "
                                   f"update folder: the USB drive's {where} isn't QIDI's package ({exc.message})",
                                   file=name, shown=True)
        if entry["file"] != name or entry["version"] != detected.version:
            return _restore_result(reporter, MISMATCH, f"QIDI's system package file {name} wasn't put back in QIDI's "
                                   f"update folder: the USB drive's {where} carries {entry['file']}", file=name,
                                   shown=True)
        member = members[name]
        if _free(home(paths), disk_usage) < member.file_size + MARGIN_BYTES:
            raise PackageError(f"it needs {(member.file_size + MARGIN_BYTES) // 2 ** 20} MiB free in {home(paths)}",
                               code=FREE_SPACE)
        reporter.line(f"Putting QIDI's system package file {name} back in QIDI's update folder from the USB drive")
        try:
            # QIDI's manifests carry no hash; one that did would be checked too.
            digests = _extract_to(package, member, scratch, algorithms=[key for key in ("sha256", "md5")
                                                                         if isinstance(entry.get(key), str)])
        except BaseException:
            scratch.unlink(missing_ok=True)
            raise
    if any(digests[key] != str(entry[key]).lower() for key in digests):
        scratch.unlink(missing_ok=True)
        return _restore_result(reporter, MISMATCH, f"QIDI's system package file {name} wasn't put back in QIDI's "
                               f"update folder: it doesn't match its hash in the package's manifest", file=name,
                               shown=True)
    # Owned by root and moved, not copied, into the folder: first under a name QIDI's app doesn't read, then renamed in
    # one step, so the folder never holds part of the file under the manifest's name.
    for command in (["chown", "root:root", str(scratch)], ["chmod", RESTORE_MODE, str(scratch)],
                    ["mv", "-f", str(scratch), str(part)], ["mv", "-f", str(part), str(final)]):
        if sudo(command).returncode:
            _remove_root_file(scratch, sudo)
            _remove_root_file(part, sudo)
            raise InstallerError(f"{' '.join(command[:2])} failed")
    sudo(["sync", str(update)])  # the rename, to disk
    written = final.lstat()
    if not stat.S_ISREG(written.st_mode) or written.st_size != member.file_size:
        raise InstallerError(f"{final} is not the file the package holds")
    return _restore_result(reporter, RESTORED, f"Put QIDI's system package file {name} back in QIDI's update folder "
                           "from the USB drive.", file=name, shown=True)


def _restore_result(reporter, result: str, message: str, *, file: str | None = None, shown=False) -> SocRestore:
    if shown:
        reporter.line(message if message.endswith(".") else message + ".")
    _debug(reporter, "soc_restore", result=result, file=file, detail=message)
    return SocRestore(result, message, file)


def _remove_root_file(path: Path, sudo) -> None:
    """Remove a file the restore left, which may be root's already."""
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except PermissionError:
        sudo(["rm", "-f", str(path)])


def deb_fields(deb: Path, *, run=subprocess.run) -> dict[str, str]:
    """Package, Version, and Architecture of a Debian package, read with dpkg-deb without unpacking it."""
    result = run(["dpkg-deb", "-f", str(deb), "Package", "Version", "Architecture"], capture_output=True, text=True,
                 timeout=120)
    if result.returncode:
        raise PackageError("The firmware package's SOC file is not a Debian package.", code=UNVERIFIED)
    fields = {}
    for line in (result.stdout or "").splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields
