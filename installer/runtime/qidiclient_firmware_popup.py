from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import InstallerError


OPERATION = "qidiclient_firmware_version_popup"
FILE = "/home/qidi/QIDI_Client/bin/qidiclient"
RESTART_SERVICE = "qidi-client.service"

# QIDI's touchscreen app raises "New MCU or THR firmware available" (QDC_100_005_005_001) unless the version a board
# reports is byte-equal to the one in ~/update/firmware_manifest.json, so this package's `<QIDI version>-valve-oil.N`
# boards raise it although the firmware page, which compares differently, offers nothing. The show and clear checks
# of the firmware-version popups compare the two lengths and branch to memcmp only when they are equal. Branching
# when the manifest version is no longer than the reported one makes memcmp, which already compares the manifest
# version's length, test that the reported version starts with the manifest version.
BRANCH_IF_EQUAL = bytes.fromhex("40020054")
BRANCH_IF_NOT_LONGER = bytes.fromhex("49020054")
# With QIDI's package updates blocked, two more changes apply.
#
# The app announces QIDI's online update from one byte, where its check of /backend/v1/fireware/upgrade-info stores
# the reply's needUpdate. That byte alone makes the "New SOC firmware available" popup (QDC_100_005_005_004) show the
# online version, the firmware page list it as the latest SOC version, and the page's Update button start the online
# update instead of the per-board one. Storing zero leaves the app as if QIDI offered nothing, while the per-board
# notices and update still compare the boards with ~/update/firmware_manifest.json.
STORE_NEED_UPDATE = bytes.fromhex("43c00139")  # strb w3, [x2, #0x70]
STORE_ZERO = bytes.fromhex("5fc00139")  # strb wzr, [x2, #0x70]
# The online, USB, and mandatory-file updates all unpack the package through one function, which deletes
# ~/update, recreates it, and runs unzip. Its two callers stop at "Extract failed" when it returns false, before any
# service, package, or board is touched. Returning false on entry keeps ~/update, which the per-board notices and
# update, this package's firmware detection, and the Box monitor read.
EXTRACT_PROLOGUE = bytes.fromhex("fd7baaa9fd030091")  # stp x29, x30, [sp, #-0x160]!; mov x29, sp
RETURN_FALSE = bytes.fromhex("00008052c0035fd6")  # mov w0, #0; ret
CHUNK = 1 << 20


@dataclass(frozen=True)
class Site:
    offset: int
    original: bytes
    patched: bytes
    # Patched only while QIDI's package updates are blocked.
    blocking: bool = False


@dataclass(frozen=True)
class Build:
    size: int
    # File offsets, each inside one CHUNK, with QIDI's bytes and the patched ones.
    sites: tuple[Site, ...]
    patched_sha256: str
    blocked_sha256: str
    # Other combinations of the sites that package versions have written; updates finish or undo them.
    other_patched_sha256: tuple[str, ...] = ()

    def forms(self) -> tuple[str, ...]:
        return (self.patched_sha256, self.blocked_sha256, *self.other_patched_sha256)

    def target(self, source_sha256: str, *, block: bool | None) -> str:
        """The patched result for a file of this build: blocked or not, or with block None, blocked only when the
        file already is."""
        if block is None:
            block = source_sha256 == self.blocked_sha256
        return self.blocked_sha256 if block else self.patched_sha256


def _sites(show_branch: int, clear_branch: int, need_update_store: int, extract: int) -> tuple[Site, ...]:
    return (
        Site(show_branch, BRANCH_IF_EQUAL, BRANCH_IF_NOT_LONGER),
        Site(clear_branch, BRANCH_IF_EQUAL, BRANCH_IF_NOT_LONGER),
        Site(need_update_store, STORE_NEED_UPDATE, STORE_ZERO, blocking=True),
        Site(extract, EXTRACT_PROLOGUE, RETURN_FALSE, blocking=True),
    )


# Keyed by the SHA-256 of QIDI's executable.
BUILDS = {
    # QIDI Max 4 firmware 01.01.07.01
    "b5285d1a52f1180757360d0893f0f8ed619aaaa31d2d621917c0a963bc3fde4e": Build(
        size=91630352,
        sites=_sites(0x227368, 0x227510, 0x2C7FF0, 0x31BC90),
        patched_sha256="12d9fb74dc73db348cd53460aa3a343f7562890408cc8c32bbe37db2e34b119a",
        blocked_sha256="b0e9a4a6ce399ea4feaa096851ae022d410caaa47eb2f60227c3483f46b5e138",
        other_patched_sha256=("a3870f07e664f7382a22a9ee6167b5c356ed0bc836ed6e99b0f40f00c1a386c2",),
    ),
    # QIDI Plus 5 firmware 01.01.02.01
    "6c09a58a636427d468b77b78149e5305f0b1182e785a0abff67aec53b3aeb855": Build(
        size=91626272,
        sites=_sites(0x226518, 0x2266C0, 0x2C71A0, 0x31AE40),
        patched_sha256="7a95c573ff94455e128fe5f6d9e8b95b55ed693cb6f27d24ca7da72a5ac97802",
        blocked_sha256="58fe6ed91c5f609777d8b4226dae59219563b1c900d0b4ad7edfe1a2fb4a8cdc",
        other_patched_sha256=("6d39c942beca99a53ab1f3107d0e355f573f9450e3d5b66b33c83544e5b22661",),
    ),
}


class QidiClientFirmwarePopupError(InstallerError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _lookup(sha256: str) -> tuple[str, Build] | None:
    """QIDI's SHA-256 and the reviewed build that sha256 is QIDI's executable of, or one of this package's patches of."""
    for stock, build in BUILDS.items():
        if sha256 == stock or sha256 in build.forms():
            return stock, build
    return None


def _build(sha256: str) -> Build | None:
    found = _lookup(sha256)
    return found[1] if found is not None else None


def stock_sha256(sha256: str) -> str | None:
    """QIDI's SHA-256 of the reviewed build that sha256 is a form of."""
    found = _lookup(sha256)
    return found[0] if found is not None else None


def same_build(sha256: str, other_sha256: str) -> bool:
    """Whether both are forms, QIDI's or patched, of one reviewed build."""
    found = _lookup(sha256)
    return found is not None and found == _lookup(other_sha256)


def patched_sha256(source_sha256: str, *, block: bool | None) -> str | None:
    build = _build(source_sha256)
    return build.target(source_sha256, block=block) if build is not None else None


def needs_patch(path: Path, *, block: bool | None) -> bool:
    # The executable is about a fifth of the printer's memory, so it is hashed and copied in pieces.
    if path.stat().st_size in {build.size for build in BUILDS.values()}:
        sha256 = sha256_file(path)
        build = _build(sha256)
        if build is not None:
            return sha256 != build.target(sha256, block=block)
    raise QidiClientFirmwarePopupError("The touchscreen app is not a reviewed QIDI build; preserving it.")


def write_patched(source: Path, destination: Path, *, block: bool | None) -> None:
    sha256 = sha256_file(source)
    build = _build(sha256)
    if build is None:
        raise QidiClientFirmwarePopupError("The touchscreen app is not a reviewed QIDI build; preserving it.")
    target = build.target(sha256, block=block)
    _write_form(source, destination, build, target, lambda site: site.patched if target == build.blocked_sha256 or not site.blocking else site.original)


def write_original(source: Path, destination: Path) -> str:
    """Write QIDI's executable of the build source is a form of, and return its SHA-256."""
    found = _lookup(sha256_file(source))
    if found is None:
        raise QidiClientFirmwarePopupError("The touchscreen app is not a reviewed QIDI build; preserving it.")
    stock, build = found
    _write_form(source, destination, build, stock, lambda site: site.original)
    return stock


def _write_form(source: Path, destination: Path, build: Build, expected: str, wanted) -> None:
    digest = hashlib.sha256()
    with source.open("rb") as reader, destination.open("wb") as writer:
        position = 0
        while chunk := bytearray(reader.read(CHUNK)):
            for site in build.sites:
                if position <= site.offset and site.offset + len(site.original) <= position + len(chunk):
                    start = site.offset - position
                    if chunk[start:start + len(site.original)] in (site.original, site.patched):
                        chunk[start:start + len(site.original)] = wanted(site)
            digest.update(chunk)
            writer.write(chunk)
            position += len(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    if digest.hexdigest() != expected:
        destination.unlink(missing_ok=True)
        raise QidiClientFirmwarePopupError("The patched touchscreen app does not match the reviewed result.")
