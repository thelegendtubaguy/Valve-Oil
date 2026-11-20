"""The USB drive image: one zip an owner extracts to the root of a FAT32 drive, for every supported printer.

    QD_Update/<prefix>ValveOil_<label>.zip   one per printer; each printer's touchscreen lists only its own prefix
    valve-oil/valve-oil.tar.gz               the Valve Oil bundle
    valve-oil/<archive>                      each printer's target QIDI package, as installer/package.yaml names it

Each printer's zip holds a firmware_manifest.json whose only entry is SOC version 99.99.99.99, naming the hand-off
package, which QIDI's offline update then installs with dpkg; the hand-off package, whose postinst is
installer/usb/postinst; and valve-oil-restore/, which the postinst reads and then removes:

    printer                  the printer's id in installer/package.yaml
    zip                      this zip's name, by which the postinst finds the drive in the mount table
    valve-oil.tar.gz.sha256  the bundle's SHA-256
    launch.sh                installer/usb/launch.sh, which the postinst starts as its own systemd unit
    update_screen.py         installer/runtime/update_screen.py, with which launch.sh draws starting.png
    starting.png             the screen launch.sh shows from the app's restart until the installer draws its own
                             (touch_ui.starting_screen, for the printer and package version)
    <set>/                   QIDI's firmware_manifest.json and board images for each package of each supported
                             firmware (package.yaml restore_sets), with SHA256SUMS

QIDI's files come from this project's mirror releases and are checked against their pins; restore files are read from
the archives with HTTP range requests. Both are cached by SHA-256 in the build's download cache.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import sys
import tarfile
import urllib.request
import zipfile
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.runtime.naming import ARCHIVE_NAME

USB_SOURCE = REPO_ROOT / "installer/usb"
UPDATE_SCREEN = REPO_ROOT / "installer/runtime/update_screen.py"
HANDOFF_PACKAGE = "valve-oil-usb-handoff"
# Higher than any QIDI version, so QIDI's app always counts the package as an update; it compares only after it has
# emptied its update folder and unzipped the package there.
SOC_VERSION = "99.99.99.99"
DRIVE_FOLDER = "valve-oil"
QD_UPDATE = "QD_Update"
HANDOFF_FOLDER = "valve-oil-restore"
BUNDLE_ON_DRIVE = f"{DRIVE_FOLDER}/{ARCHIVE_NAME}"
SUMS_NAME = "SHA256SUMS"
# Fixed timestamps keep the image identical for identical inputs.
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
DEB_MTIME = 315532800  # 1980-01-01
# The stand-in for a QIDI package in a test image (build_installer_bundle.py --usb-stub-qidi).
STUB_PREFIX = b"Valve Oil USB image test stand-in for "


def image_name(channel: str, version: str) -> str:
    """valve-oil-usb-<version>.zip for a release; dev builds replace one prerelease asset, valve-oil-usb-dev.zip."""
    return f"valve-oil-usb-{version}.zip" if channel == "release" else "valve-oil-usb-dev.zip"


def zip_prefix(printer) -> str:
    """The name prefix QIDI's app lists on the printer: QD_MAX4_, QD_PLUS5_, S5_."""
    return printer.soc_file_prefix.removesuffix("SOC_")


def printer_zip_name(printer, label: str) -> str:
    return f"{zip_prefix(printer)}ValveOil_{label}.zip"


def deb_name(version: str) -> str:
    return f"{HANDOFF_PACKAGE}_{version}_all.deb"


def debian_version(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]", ".", label)


def soc_manifest(deb: str) -> str:
    return json.dumps({"SOC": {"file": deb, "version": SOC_VERSION, "region": "NA"}}, indent=2) + "\n"


# ---- hand-off package ------------------------------------------------------------------------------------------------


def _tar_gz(members: list[tuple[str, bytes, int]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.GNU_FORMAT, compresslevel=9) as tar:
        for name, data, mode in members:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), mode, DEB_MTIME
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
    # gzip's header carries a timestamp; zero it so the package is reproducible.
    data = bytearray(buffer.getvalue())
    data[4:8] = b"\0\0\0\0"
    return bytes(data)


def _ar(members: list[tuple[str, bytes]]) -> bytes:
    out = b"!<arch>\n"
    for name, data in members:
        out += f"{name:<16}{DEB_MTIME:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode() + data
        if len(data) % 2:
            out += b"\n"
    return out


def build_deb(version: str) -> bytes:
    """The hand-off package: no files, and installer/usb/postinst as its postinst."""
    control = (
        f"Package: {HANDOFF_PACKAGE}\n"
        f"Version: {version}\n"
        "Architecture: all\n"
        "Maintainer: Valve Oil\n"
        "Description: Valve Oil USB installer hand-off\n"
        " Restores QIDI's update folder and starts the Valve Oil installer from the USB drive. Installs no files.\n"
    )
    postinst = (USB_SOURCE / "postinst").read_bytes()
    return _ar([
        ("debian-binary", b"2.0\n"),
        ("control.tar.gz", _tar_gz([("./control", control.encode(), 0o644), ("./postinst", postinst, 0o755)])),
        ("data.tar.gz", _tar_gz([])),
    ])


def read_deb(data: bytes) -> dict[str, bytes]:
    """The members of an ar archive, by name, for checks."""
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("not an ar archive")
    members, pos = {}, 8
    while pos < len(data):
        header = data[pos:pos + 60]
        if len(header) != 60 or header[58:60] != b"`\n":
            raise ValueError("bad ar member header")
        name, size = header[:16].decode().strip(), int(header[48:58].decode().strip())
        members[name] = data[pos + 60:pos + 60 + size]
        pos += 60 + size + (size % 2)
    return members


# ---- QIDI's files ---------------------------------------------------------------------------------------------------


def _get(url: str, start: int, end: int) -> tuple[bytes, int]:
    request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent": "valve-oil-build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status != 206:
            raise ValueError(f"{url}: no range support (HTTP {response.status})")
        total = int(response.headers["Content-Range"].rsplit("/", 1)[1])
        return response.read(), total


def _central_directory(url: str) -> dict[str, dict]:
    _, total = _get(url, 0, 0)
    tail, _ = _get(url, max(0, total - 65557), total - 1)
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError(f"{url}: no end of central directory")
    _, _, _, _, count, cd_size, cd_offset, _ = struct.unpack_from("<IHHHHIIH", tail, eocd)
    if cd_offset == 0xFFFFFFFF or count == 0xFFFF:
        raise ValueError(f"{url}: zip64 is not handled")
    directory, _ = _get(url, cd_offset, cd_offset + cd_size - 1)
    members, pos = {}, 0
    for _ in range(count):
        (signature, _, _, _, method, _, _, crc, compressed, size, name_length, extra_length, comment_length, _, _, _,
         offset) = struct.unpack_from("<IHHHHHHIIIHHHHHII", directory, pos)
        if signature != 0x02014B50:
            raise ValueError(f"{url}: bad central directory")
        name = directory[pos + 46:pos + 46 + name_length].decode("utf-8")
        members[name] = dict(method=method, crc=crc, compressed=compressed, size=size, offset=offset)
        pos += 46 + name_length + extra_length + comment_length
    return members


def _read_member(url: str, member: dict) -> bytes:
    header, _ = _get(url, member["offset"], member["offset"] + 29)
    name_length, extra_length = struct.unpack_from("<HH", header, 26)
    start = member["offset"] + 30 + name_length + extra_length
    data, _ = _get(url, start, start + member["compressed"] - 1)
    if member["method"] == 8:
        data = zlib.decompress(data, -15)
    elif member["method"] != 0:
        raise ValueError(f"compression method {member['method']} is not handled")
    if len(data) != member["size"] or zlib.crc32(data) & 0xFFFFFFFF != member["crc"]:
        raise ValueError("size or CRC mismatch")
    return data


def _pinned(data: bytes, pin) -> bool:
    return len(data) == pin.size and hashlib.sha256(data).hexdigest() == pin.sha256


def fetch_restore_sets(manifest, cache: Path) -> dict[tuple[str, str], dict[str, bytes]]:
    """Every printer's restore sets, {(printer id, set name): {file: bytes}}, each file matching its pin."""
    store = cache / "qidi-restore"
    sets = {}
    for printer in manifest.printers:
        for restore_set in printer.restore_sets:
            files, members = {}, None
            for name, pin in restore_set.files:
                cached = store / pin.sha256
                data = cached.read_bytes() if cached.is_file() else None
                if data is None or not _pinned(data, pin):
                    if members is None:
                        print(f"Reading QIDI's update folder from {restore_set.url}", file=sys.stderr)
                        members = _central_directory(restore_set.url)
                    if name not in members:
                        raise ValueError(f"{restore_set.archive} has no {name}")
                    data = _read_member(restore_set.url, members[name])
                    if not _pinned(data, pin):
                        raise ValueError(f"{name} in {restore_set.archive} does not match its pin in package.yaml")
                    store.mkdir(parents=True, exist_ok=True)
                    partial = cached.with_name(cached.name + ".partial")
                    partial.write_bytes(data)
                    partial.replace(cached)
                files[name] = data
            sets[(printer.id, restore_set.name)] = files
    return sets


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_target_archive(target, cache: Path) -> Path:
    """The printer's target QIDI package from the mirror, matching its pinned size and SHA-256."""
    cached = cache / "qidi" / f"{target.sha256}-{target.archive}"
    if cached.is_file() and cached.stat().st_size == target.size and _file_sha256(cached) == target.sha256:
        return cached
    cached.parent.mkdir(parents=True, exist_ok=True)
    partial = cached.with_name(cached.name + ".partial")
    print(f"Downloading {target.archive} ({target.size // 2 ** 20} MiB) from {target.url}", file=sys.stderr)
    request = urllib.request.Request(target.url, headers={"User-Agent": "valve-oil-build"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
        for chunk in iter(lambda: response.read(1 << 20), b""):
            output.write(chunk)
    if partial.stat().st_size != target.size or _file_sha256(partial) != target.sha256:
        partial.unlink()
        raise ValueError(f"{target.url} does not match the pinned size and sha256.")
    partial.replace(cached)
    return cached


# ---- the image ------------------------------------------------------------------------------------------------------


def _write(archive: zipfile.ZipFile, name: str, data: bytes, *, mode: int = 0o644,
           compress: int = zipfile.ZIP_DEFLATED) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
    info.external_attr = (0o100000 | mode) << 16
    info.compress_type = compress
    archive.writestr(info, data)


def starting_screen(printer, version: str) -> bytes:
    """The starting screen launch.sh shows on `printer` for Valve Oil `version`, with the installer's first footer."""
    from installer.runtime import touch_ui

    return touch_ui.starting_screen(where=printer.display_name, footer=f"Valve Oil {version} · USB installer")


def printer_zip(*, printer_id: str, zip_name: str, deb: bytes, deb_file: str, bundle_sha256: str,
                restore_sets: list[tuple[str, dict[str, bytes]]], starting_png: bytes | None = None) -> bytes:
    """One printer's zip: the 99.99.99.99 manifest, the hand-off package, and valve-oil-restore/ with each set and,
    given starting_png, the starting screen."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        _write(archive, "firmware_manifest.json", soc_manifest(deb_file).encode())
        _write(archive, deb_file, deb, compress=zipfile.ZIP_STORED)
        _write(archive, f"{HANDOFF_FOLDER}/printer", f"{printer_id}\n".encode())
        _write(archive, f"{HANDOFF_FOLDER}/zip", f"{zip_name}\n".encode())
        _write(archive, f"{HANDOFF_FOLDER}/{ARCHIVE_NAME}.sha256", f"{bundle_sha256}  {ARCHIVE_NAME}\n".encode())
        _write(archive, f"{HANDOFF_FOLDER}/launch.sh", (USB_SOURCE / "launch.sh").read_bytes(), mode=0o755)
        if starting_png is not None:
            _write(archive, f"{HANDOFF_FOLDER}/update_screen.py", UPDATE_SCREEN.read_bytes())
            _write(archive, f"{HANDOFF_FOLDER}/starting.png", starting_png, compress=zipfile.ZIP_STORED)
        for set_name, files in restore_sets:
            sums = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in files.items())
            for name, data in files.items():
                _write(archive, f"{HANDOFF_FOLDER}/{set_name}/{name}", data)
            _write(archive, f"{HANDOFF_FOLDER}/{set_name}/{SUMS_NAME}", sums.encode())
    return buffer.getvalue()


def build_image(manifest, *, bundle_archive: Path, image: Path, label: str, cache: Path,
                stub_qidi: bool = False) -> Path:
    """Write the drive image and its .sha256; the image's path. `label` names the printers' zips and versions the
    hand-off package: the package version, with the build for dev builds.

    With stub_qidi, each target QIDI package is a small stand-in under the real name, so a test build needs no
    600 MB download; the restore sets are always QIDI's real files.
    """
    restore_sets = fetch_restore_sets(manifest, cache)
    targets = {printer.id: (None if stub_qidi else fetch_target_archive(printer.target_firmware, cache))
               for printer in manifest.printers}
    bundle_sha256 = _file_sha256(bundle_archive)
    version = debian_version(label)
    deb_file = deb_name(version)
    deb = build_deb(version)
    partial = image.with_name(image.name + ".partial")
    with zipfile.ZipFile(partial, "w", allowZip64=True) as archive:
        for printer in manifest.printers:
            zip_name = printer_zip_name(printer, label)
            sets = [(restore_set.name, restore_sets[(printer.id, restore_set.name)])
                    for restore_set in printer.restore_sets]
            _write(archive, f"{QD_UPDATE}/{zip_name}",
                   printer_zip(printer_id=printer.id, zip_name=zip_name, deb=deb, deb_file=deb_file,
                               bundle_sha256=bundle_sha256, restore_sets=sets,
                               starting_png=starting_screen(printer, manifest.package.version)),
                   compress=zipfile.ZIP_STORED)
        info = zipfile.ZipInfo(BUNDLE_ON_DRIVE, date_time=ZIP_TIMESTAMP)
        info.external_attr = 0o100644 << 16
        with bundle_archive.open("rb") as source, archive.open(info, "w", force_zip64=True) as destination:
            for chunk in iter(lambda: source.read(1 << 20), b""):
                destination.write(chunk)
        for printer in manifest.printers:
            target = printer.target_firmware
            name = f"{DRIVE_FOLDER}/{target.archive}"
            if targets[printer.id] is None:
                _write(archive, name, STUB_PREFIX + target.archive.encode() + b"\n")
                continue
            info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
            info.external_attr = 0o100644 << 16
            with targets[printer.id].open("rb") as source, archive.open(info, "w", force_zip64=True) as destination:
                for chunk in iter(lambda: source.read(1 << 20), b""):
                    destination.write(chunk)
    partial.replace(image)
    image.with_name(image.name + ".sha256").write_text(f"{_file_sha256(image)}  {image.name}\n", encoding="utf-8")
    return image
