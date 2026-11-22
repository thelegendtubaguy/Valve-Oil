"""Valve Oil's Klipper: upstream Klipper v0.14 with QIDI's changes, installed beside QIDI's ~/klipper.

The source is klipper/ in the repository. Its c_helper.so is built from klipper/klippy/chelper with the printers'
toolchain by scripts/build_klipper_chelper.py and shipped prebuilt, because QIDI's printers have no compiler; Klipper
rebuilds the library only when a source file is newer than it, so the installer keeps the library the newest file.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


TREE_SOURCE = Path(__file__).resolve().parents[2] / "klipper"
TREE_ROOT = "/home/qidi/vo-klipper"
CHELPER_PAYLOAD_SUBDIR = "system/klipper_tree"
CHELPER_PAYLOAD = Path(__file__).resolve().parents[1] / CHELPER_PAYLOAD_SUBDIR / "c_helper.so"
# klipper/klippy/chelper's sources and the library scripts/build_klipper_chelper.py builds from them.
CHELPER_SOURCES_SHA256 = "aad2932c4a653038a22506cec17c0c3bf15fb20fdebed79881353b202be32d3e"
CHELPER_SHA256 = "0a6a093e38e7891efad52828b08c76d366795eca9ba8242b9d88e9b1ce9d4ffe"
CHELPER_SIZE = 394840


def chelper_sources_sha256(source: Path = TREE_SOURCE) -> str:
    """One digest over every file in klippy/chelper except the library, by name."""
    digest = hashlib.sha256()
    chelper = source / "klippy" / "chelper"
    for path in sorted(chelper.iterdir()):
        if path.name == "c_helper.so" or path.name == "__pycache__" or not path.is_file():
            continue
        digest.update(path.name.encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
