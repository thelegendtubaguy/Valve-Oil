#!/usr/bin/env python3
"""Build the maskrom rescue kit for a QIDI printer that stops at its boot logo.

QIDI's own recovery partition can't reach the eMMC on a Max 4: its device tree waits on a power-management chip the
board doesn't have, so the eMMC driver never probes. The rescue image keeps QIDI's recovery ramdisk (ADB over USB,
e2fsck, mount, dd) and boots it with the printer's normal kernel and device tree, which find the eMMC. Two start
scripts are left out: S40recovery, whose recovery program reboots the board when it has nothing to do, and
S00mountall.sh, which would fsck and resize partitions on its own once the eMMC is visible.

Inputs are partition images from a working printer on the same QIDI firmware:
  --boot      QIDI's boot partition (Valve Oil keeps it as vo_system_backups/boot-partition-*.img)
  --recovery  QIDI's recovery partition (/dev/block/by-name/recovery, or sectors 0x4800-0xe800 of a maskrom export)

Outputs, written to --output-dir:
  rescue-recovery.img     written at sector 0x4800 (the recovery partition)
  recovery-original.img   QIDI's recovery partition, to put back afterwards
  misc-boot-recovery.img  written at sector 0x4000 (misc): the next boot starts the recovery partition
  misc-clear.img          written at sector 0x4000: normal boots again
  SHA256SUMS
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import struct
import sys
from pathlib import Path

RECOVERY_SECTORS = 0xA000
SECTOR = 512
DROPPED = ("etc/init.d/S40recovery", "etc/init.d/S00mountall.sh")
MISC_SIZE = 64 * 1024
# Rockchip U-Boot reads the bootloader message 16 KiB into misc; Android's layout puts it at 0. Both get a copy.
MISC_OFFSETS = (0, 16 * 1024)


def boot_parts(image: bytes) -> tuple[int, list[bytes]]:
    magic, kernel_size, _, ramdisk_size, _, second_size, _, _, page = struct.unpack_from("<8s8I", image, 0)
    (version,) = struct.unpack_from("<I", image, 40)
    if magic != b"ANDROID!" or version != 0 or page not in (2048, 4096):
        raise SystemExit("not an Android boot image (version 0)")
    parts, offset = [], page
    for size in (kernel_size, ramdisk_size, second_size):
        parts.append(image[offset:offset + size])
        offset += -(-size // page) * page
    if offset > len(image):
        raise SystemExit("boot image is longer than the partition image")
    return page, parts


def image_id(parts: list[bytes]) -> bytes:
    digest = hashlib.sha1()
    for part in parts:
        digest.update(part)
        digest.update(struct.pack("<I", len(part)))
    return digest.digest()


def drop_from_newc(archive: bytes, names: tuple[str, ...]) -> bytes:
    out, offset, dropped = bytearray(), 0, set()
    while True:
        header = archive[offset:offset + 110]
        if header[:6] != b"070701":
            raise SystemExit("recovery ramdisk isn't a newc cpio archive")
        file_size, name_size = int(header[54:62], 16), int(header[94:102], 16)
        name = archive[offset + 110:offset + 110 + name_size - 1].decode()
        data_start = (offset + 110 + name_size + 3) & ~3
        end = (data_start + file_size + 3) & ~3
        bare = name[2:] if name.startswith("./") else name
        if bare in names:
            dropped.add(bare)
        else:
            out += archive[offset:end]
        offset = end
        if name == "TRAILER!!!":
            break
    if dropped != set(names):
        raise SystemExit(f"recovery ramdisk is missing {sorted(set(names) - dropped)}")
    return bytes(out)


def misc_image(command: bytes | None) -> bytes:
    image = bytearray(MISC_SIZE)
    if command:
        message = bytearray(2048)
        message[:len(command)] = command
        message[64:64 + 9] = b"recovery\n"
        for offset in MISC_OFFSETS:
            image[offset:offset + len(message)] = message
    return bytes(image)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--boot", type=Path, required=True)
    parser.add_argument("--recovery", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    boot = args.boot.read_bytes()
    recovery = args.recovery.read_bytes()[:RECOVERY_SECTORS * SECTOR]
    page, (kernel, _, second) = boot_parts(boot)
    _, (_, ramdisk, _) = boot_parts(recovery)
    if b"RSCE" != second[:4]:
        raise SystemExit("boot image has no resource image (device tree)")

    new_ramdisk = gzip.compress(drop_from_newc(gzip.decompress(ramdisk), DROPPED), 9, mtime=0)
    header = bytearray(boot[:page])
    struct.pack_into("<I", header, 16, len(new_ramdisk))
    struct.pack_into("<I", header, 20, struct.unpack_from("<I", recovery, 20)[0])  # ramdisk load address
    header[576:608] = image_id([kernel, new_ramdisk, second]) + bytes(12)

    def pad(data: bytes) -> bytes:
        return data + bytes(-len(data) % page)

    rescue = bytes(header) + pad(kernel) + pad(new_ramdisk) + pad(second)
    if len(rescue) > RECOVERY_SECTORS * SECTOR:
        raise SystemExit("rescue image doesn't fit the recovery partition")
    rescue += bytes(RECOVERY_SECTORS * SECTOR - len(rescue))

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "rescue-recovery.img": rescue,
        "recovery-original.img": recovery,
        "misc-boot-recovery.img": misc_image(b"boot-recovery"),
        "misc-clear.img": misc_image(None),
    }
    sums = []
    for name, data in files.items():
        (out / name).write_bytes(data)
        sums.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print("\n".join(sums))
    return 0


if __name__ == "__main__":
    sys.exit(main())
