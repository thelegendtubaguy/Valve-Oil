from __future__ import annotations

import hashlib
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
CHUNK = 1 << 20


@dataclass(frozen=True)
class Build:
    size: int
    patched_sha256: str
    # File offsets of the length branch in the show check and in the clear check.
    branches: tuple[int, int]


# Keyed by the SHA-256 of QIDI's executable.
BUILDS = {
    # QIDI Max 4 firmware 01.01.07.01
    "b5285d1a52f1180757360d0893f0f8ed619aaaa31d2d621917c0a963bc3fde4e": Build(
        size=91630352,
        patched_sha256="12d9fb74dc73db348cd53460aa3a343f7562890408cc8c32bbe37db2e34b119a",
        branches=(0x227368, 0x227510),
    ),
    # QIDI Plus 5 firmware 01.01.02.01
    "6c09a58a636427d468b77b78149e5305f0b1182e785a0abff67aec53b3aeb855": Build(
        size=91626272,
        patched_sha256="7a95c573ff94455e128fe5f6d9e8b95b55ed693cb6f27d24ca7da72a5ac97802",
        branches=(0x226518, 0x2266C0),
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


def patched_sha256(stock_sha256: str) -> str | None:
    build = BUILDS.get(stock_sha256)
    return build.patched_sha256 if build is not None else None


def needs_patch(path: Path) -> bool:
    # The executable is about a fifth of the printer's memory, so it is hashed and copied in pieces.
    if path.stat().st_size in {build.size for build in BUILDS.values()}:
        sha256 = sha256_file(path)
        if sha256 in BUILDS:
            return True
        if any(build.patched_sha256 == sha256 for build in BUILDS.values()):
            return False
    raise QidiClientFirmwarePopupError("The touchscreen app is not a reviewed QIDI build; preserving it.")


def write_patched(source: Path, destination: Path) -> None:
    build = BUILDS.get(sha256_file(source))
    if build is None:
        raise QidiClientFirmwarePopupError("The touchscreen app is not a reviewed QIDI build; preserving it.")
    digest = hashlib.sha256()
    with source.open("rb") as reader, destination.open("wb") as writer:
        position = 0
        while chunk := bytearray(reader.read(CHUNK)):
            for offset in build.branches:
                if position <= offset and offset + len(BRANCH_IF_EQUAL) <= position + len(chunk):
                    start = offset - position
                    if chunk[start:start + len(BRANCH_IF_EQUAL)] == BRANCH_IF_EQUAL:
                        chunk[start:start + len(BRANCH_IF_EQUAL)] = BRANCH_IF_NOT_LONGER
            digest.update(chunk)
            writer.write(chunk)
            position += len(chunk)
    if digest.hexdigest() != build.patched_sha256:
        destination.unlink(missing_ok=True)
        raise QidiClientFirmwarePopupError("The patched touchscreen app does not match the reviewed result.")
