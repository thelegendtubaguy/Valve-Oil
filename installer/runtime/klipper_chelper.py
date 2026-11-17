"""Klipper's C helper library with upstream fixes QIDI's Klipper lacks.

QIDI's klippy/chelper is upstream Klipper ca815f52c (2024-08-09). The package's c_helper.so is that tree with the
patches in chelper/, which free expired pressure advance entries (each SET_PRESSURE_ADVANCE that changes the value
leaked one), stop the trsync timeout comparison from sending an MCU an earlier homing deadline, stop a possible write
past the step queue, and close the two pipe descriptors each freed serial queue leaked. scripts/build_klipper_chelper.py
rebuilds QIDI's library byte for byte and then the patched one.

Only the library is replaced. Klipper rebuilds c_helper.so when a source file is newer than it, and writing or
restoring the library never leaves it older than QIDI's sources, so Klipper loads it as it is.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .errors import InstallerError


OPERATION = "klipper_chelper_fixes"
FILE = "/home/qidi/klipper/klippy/chelper/c_helper.so"
RESTART_SERVICE = "klipper.service"
PAYLOAD_SUBDIR = "system/klipper_chelper"
PAYLOAD_DIR = Path(__file__).resolve().parents[1] / PAYLOAD_SUBDIR
CHUNK = 1 << 20
UNREVIEWED = "Klipper's C helper library is not a reviewed QIDI build; preserving it."
RELEASED = "Klipper's C helper library is not a reviewed QIDI build; it was left as it is, and uninstall no longer restores it."


@dataclass(frozen=True)
class Build:
    size: int
    patched_size: int
    patched_sha256: str
    # Files in PAYLOAD_DIR: QIDI's library and the patched one.
    stock_payload: str
    patched_payload: str

    def forms(self) -> tuple[str, ...]:
        return (self.patched_sha256,)


# Keyed by the SHA-256 of QIDI's library, the same on every supported firmware.
BUILDS = {
    "214d1ab79a78b2aa28ba8cd7ba0d7383afcaaf9d36561112aa7bf73cf6591714": Build(
        size=336304,
        patched_size=336784,
        patched_sha256="f8b7b7bb28dddff1f28a0775d0ace4696cd7ac95094a8e2dbbef16b1a49ac593",
        stock_payload="qidi_c_helper.so",
        patched_payload="c_helper.so",
    ),
}


class KlipperChelperError(InstallerError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def reviewed_sizes() -> frozenset[int]:
    """Sizes of every reviewed form, so other files aren't hashed."""
    return frozenset(size for build in BUILDS.values() for size in (build.size, build.patched_size))


def _lookup(sha256: str) -> tuple[str, Build] | None:
    for stock, build in BUILDS.items():
        if sha256 == stock or sha256 in build.forms():
            return stock, build
    return None


def stock_sha256(sha256: str) -> str | None:
    """QIDI's SHA-256 of the reviewed build that sha256 is a form of."""
    found = _lookup(sha256)
    return found[0] if found is not None else None


def same_build(sha256: str, other_sha256: str) -> bool:
    """Whether both are forms, QIDI's or patched, of one reviewed build."""
    found = _lookup(sha256)
    return found is not None and found == _lookup(other_sha256)


def patched_sha256(source_sha256: str, *, block: bool | None = None) -> str | None:
    found = _lookup(source_sha256)
    return found[1].patched_sha256 if found is not None else None


def needs_patch(path: Path, *, block: bool | None = None) -> bool:
    if path.stat().st_size in reviewed_sizes():
        sha256 = sha256_file(path)
        found = _lookup(sha256)
        if found is not None:
            return sha256 != found[1].patched_sha256
    raise KlipperChelperError(UNREVIEWED)


def write_patched(source: Path, destination: Path, *, block: bool | None = None) -> None:
    found = _lookup(sha256_file(source))
    if found is None:
        raise KlipperChelperError(UNREVIEWED)
    _copy_payload(found[1].patched_payload, destination, found[1].patched_sha256)


def write_original(source: Path, destination: Path) -> str:
    """Write QIDI's library of the build source is a form of, and return its SHA-256."""
    found = _lookup(sha256_file(source))
    if found is None:
        raise KlipperChelperError(UNREVIEWED)
    stock, build = found
    _copy_payload(build.stock_payload, destination, stock)
    return stock


def _copy_payload(name: str, destination: Path, expected: str) -> None:
    with (PAYLOAD_DIR / name).open("rb") as reader, destination.open("wb") as writer:
        shutil.copyfileobj(reader, writer, CHUNK)
        writer.flush()
        os.fsync(writer.fileno())
    if sha256_file(destination) != expected:
        destination.unlink(missing_ok=True)
        raise KlipperChelperError("The package's Klipper C helper library does not match the reviewed build.")
