#!/usr/bin/env python3
"""Regenerate firmware/max4-box-valve-oil-v1.json from QIDI's stock Box image.

Maintainer tool. It compiles firmware/box-rfid/*.c for the Box's Cortex-M4,
places the code in two handler bodies QIDI's host software never calls,
rewrites the data dictionary, and records every change as a hash-locked patch.
Installs and scripts/build_box_firmware.py use only the manifest.

Needs clang with the Thumb target and the zopfli Python package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "firmware/box-rfid"
OUTPUT = ROOT / "firmware/max4-box-valve-oil-v1.json"

BASE = 0x08008000
STOCK_SIZE = 39392
STOCK_SHA256 = "dcf6bb191d77fb2d70e2cdabce7b24f277d4eb143ed28fe55c9ce19f1531cd12"
STOCK_VERSION = "02.03.01.21"
PATCHED_VERSION = "02.03.01.21-valve-oil.1"
PATCH_ID = "max4-box-valve-oil-v1"
SUPERSEDED = [{"patch_id": "max4-box-rfid-v1", "version": "02.03.01.21-tltg-rfid1",
               "sha256": "564bee0576a7f11830507994cb3e0be3c7d2a919028f9d6ca3a8d8bb6b0f4f93"},
              {"patch_id": "max4-box-shutdown-fix-v1", "version": "02.03.01.21-tltg-shutdownfix1",
               "sha256": "bbd5c025ba98956cd7c5ed83d52601d55db50e1f96be4fcf597376510c863a5d"}]
BOOTLOADERS = [
    {"sha256": "64962da860449bd3af7ead7cc194ec01192493b0b3e7eedc70e384f61bf7b1e4", "identity_reply": "0112"},
    {"sha256": "1e00811df777a8cf21098b1386319a440c90e04aa2a9ab0aeef11dc810891b1e", "identity_reply": "01120103"},
]

CLANG_FLAGS = ["--target=thumbv7em-none-eabi", "-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=soft", "-Os",
               "-ffreestanding", "-fno-builtin", "-fno-common", "-fno-stack-protector", "-fno-jump-tables",
               "-fno-unwind-tables", "-fno-asynchronous-unwind-tables"]

# Handler bodies replaced by the new code: (source, start, end, entry symbol, old handler).
CAVES = [
    ("select.c", 0x0800B228, 0x0800B400, "fm17550_select_cb", "QIDI's fm17550_write_card_cb, which wrote a fixed test pattern"),
    ("transceive.c", 0x08009990, 0x08009B28, "fm17550_transceive_cb", "QIDI's fm17550_init_cb and fm17550_read_regs_cb debug handlers"),
]
COMMAND_TABLE = 0x0801121C  # entry n at COMMAND_TABLE + 16 * n
DEBUG_NOP = 0x08008215  # debug_nop handler (bx lr)
PARAMS_OID_BUFFER = 0x08011054  # [byte, buffer]
PARAMS_OID_STATUS_BUFFER = 0x080110B4  # [byte, byte, buffer]
ENCODERS = [(0x080102C8, 0x68, "fm17550_write_card_return -> fm17550_select_return"),
            (0x080102D8, 0x6A, "fm17550_read_fifo_return -> fm17550_transceive_return")]
DICTIONARY, DICTIONARY_BYTES = 0x080103F8, 3156
SHUTDOWN_FIX = {"offset": "0x10b6", "before": "0024", "after": "19e0",
                "description": "Shutdown fix: skip QIDI's MCB shutdown block that dereferences the NULL loop "
                               "pointer and re-enters shutdown: movs r4, #0 -> b.n 0x080090ec."}

COMMANDS_REMOVED = ["fm17550_write_card_cb oid=%c", "fm17550_read_fifo_cb oid=%c",
                    "fm17550_read_regs_cb oid=%c", "fm17550_init_cb oid=%c"]
COMMANDS_ADDED = {"fm17550_select oid=%c": 15, "fm17550_transceive oid=%c data=%*s": 17}
RESPONSES_REMOVED = ["fm17550_write_card_return oid=%c status=%c", "fm17550_read_fifo_return oid=%c data=%*s",
                     "fm17550_read_regs_return oid=%c data=%*s", "fm17550_init_return oid=%c"]
RESPONSES_ADDED = {"fm17550_select_return oid=%c status=%c data=%*s": -24,
                   "fm17550_transceive_return oid=%c status=%c data=%*s": -22}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def elf_text(path: Path, symbol: str) -> bytes:
    """.text of a relocatable ELF object, refusing relocations or a non-zero entry offset."""
    data = path.read_bytes()
    shoff, = struct.unpack_from("<I", data, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x2E)
    sections = [struct.unpack_from("<IIIIIIIIII", data, shoff + i * shentsize) for i in range(shnum)]
    names_offset = sections[shstrndx][4]

    def name(section):
        return data[names_offset + section[0]:].split(b"\0", 1)[0].decode()

    by_name = {name(s): s for s in sections}
    if ".rel.text" in by_name or ".rela.text" in by_name:
        raise ValueError(f"{path.name}: code needs relocations; call QIDI routines by absolute address only")
    text = by_name[".text"]
    symtab, strtab = by_name[".symtab"], sections[by_name[".symtab"][6]]
    for offset in range(0, symtab[5], 16):
        st_name, st_value, _size, _info, _other, st_shndx = struct.unpack_from("<IIIBBH", data, symtab[4] + offset)
        if data[strtab[4] + st_name:].split(b"\0", 1)[0].decode() == symbol:
            if st_value & ~1:
                raise ValueError(f"{path.name}: {symbol} must start the section")
            return data[text[4]:text[4] + text[5]]
    raise ValueError(f"{path.name}: {symbol} not found")


def compile_handler(source: Path, symbol: str, workdir: Path) -> bytes:
    obj = workdir / (source.stem + ".o")
    subprocess.run(["clang", *CLANG_FLAGS, "-c", str(source), "-o", str(obj)], check=True)
    return elf_text(obj, symbol)


def dictionary_json(stock: bytes) -> bytes:
    raw = stock[DICTIONARY - BASE:DICTIONARY - BASE + DICTIONARY_BYTES]
    data = json.loads(zlib.decompressobj().decompress(raw))
    for key in COMMANDS_REMOVED:
        del data["commands"][key]
    for key in RESPONSES_REMOVED:
        del data["responses"][key]
    data["commands"].update(COMMANDS_ADDED)
    data["responses"].update(RESPONSES_ADDED)
    data["version"] = PATCHED_VERSION
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode()


def movw_r3(value: int) -> bytes:
    """Thumb-2 movw r3, #value."""
    first = 0xF240 | ((value >> 11) & 1) << 10 | (value >> 12) & 0xF
    second = ((value >> 8) & 7) << 12 | 3 << 8 | value & 0xFF
    return struct.pack("<HH", first, second)


def patch_entry(stock: bytes, address: int, after: bytes, description: str) -> dict:
    offset = address - BASE
    before = stock[offset:offset + len(after)]
    entry = {"offset": hex(offset)}
    if len(after) <= 32:
        entry["before"] = before.hex()
    else:
        entry["before_sha256"] = sha256(before)
    entry["after"] = after.hex()
    entry["description"] = description
    return entry


def build_manifest(stock: bytes, workdir: Path) -> dict:
    import zopfli.zlib

    if len(stock) != STOCK_SIZE or sha256(stock) != STOCK_SHA256:
        raise ValueError("Not QIDI's stock Box " + STOCK_VERSION + " image")
    patches = [SHUTDOWN_FIX]

    text = dictionary_json(stock)
    compressed = zopfli.zlib.compress(text, numiterations=100)
    length = len(compressed)
    if not (0x100 <= length <= DICTIONARY_BYTES):
        raise ValueError(f"Compressed dictionary is {length} bytes; it must fit the {DICTIONARY_BYTES}-byte slot")
    patches += [
        {"offset": "0x1528", "before": "40f65343", "after": movw_r3(length - 1).hex(),
         "description": f"identify: last valid offset for the new data dictionary length (movw r3, #0xc53 -> #{length - 1:#x})."},
        {"offset": "0x1534", "before": "40f65443", "after": movw_r3(length).hex(),
         "description": f"identify: new data dictionary length (movw r3, #0xc54 -> #{length:#x})."},
        {"offset": "0x153e", "before": "54", "after": f"{length & 0xFF:02x}",
         "description": f"identify: low byte of the new data dictionary length (rsb.w r2, r1, #0x54 -> #{length & 0xFF:#x})."},
    ]

    for source, start, end, symbol, old in CAVES:
        code = compile_handler(SOURCE_DIR / source, symbol, workdir)
        size = end - start
        if len(code) > size:
            raise ValueError(f"{source}: {len(code)} bytes does not fit the {size}-byte handler body")
        patches.append(patch_entry(stock, start, code + bytes(size - len(code)),
                                   f"{symbol} ({len(code)} bytes from firmware/box-rfid/{source}, zero-filled) "
                                   f"in place of {old}."))

    entry17 = COMMAND_TABLE + 16 * 17
    old17 = stock[entry17 - BASE:entry17 - BASE + 16]
    msgid, _nargs, flags, _nparams = struct.unpack_from("<HBBB", old17)
    new17 = struct.pack("<HBBB3xII", msgid, 3, flags, 2, PARAMS_OID_BUFFER, CAVES[1][1] | 1)
    patches.append(patch_entry(stock, entry17, new17,
                               "Command 17: fm17550_transceive oid=%c data=%*s (three argument words, "
                               "parameters [byte, buffer], handler fm17550_transceive_cb)."))
    for number in (18, 19):
        patches.append(patch_entry(stock, COMMAND_TABLE + 16 * number + 12, struct.pack("<I", DEBUG_NOP),
                                   f"Command {number}: handler -> debug_nop; its old body now holds fm17550_transceive_cb "
                                   "and the command is no longer in the data dictionary."))
    for address, msgid, what in ENCODERS:
        patches.append(patch_entry(stock, address, struct.pack("<HBBI", msgid, 0x40, 3, PARAMS_OID_STATUS_BUFFER),
                                   f"Response encoder {what}: oid=%c status=%c data=%*s, 64-byte maximum."))
    patches.append(patch_entry(stock, DICTIONARY, compressed + bytes(DICTIONARY_BYTES - length),
                               f"Data dictionary with version {PATCHED_VERSION}, fm17550_select and "
                               f"fm17550_transceive in place of QIDI's four FM17550 debug commands, otherwise "
                               f"identical JSON; zopfli (100 iterations) to {length} bytes, zero-filled to "
                               f"{DICTIONARY_BYTES}."))

    patched = bytearray(stock)
    for patch in patches:
        offset = int(patch["offset"], 16)
        after = bytes.fromhex(patch["after"])
        patched[offset:offset + len(after)] = after
    patches.sort(key=lambda p: int(p["offset"], 16))
    return {
        "schema_version": 1,
        "patch_id": PATCH_ID,
        "box": {
            "size": STOCK_SIZE, "load_address": hex(BASE), "stock_version": STOCK_VERSION,
            "patched_version": PATCHED_VERSION, "stock_sha256": STOCK_SHA256,
            "patched_sha256": sha256(bytes(patched)), "superseded": SUPERSEDED, "patches": patches,
        },
        "bootloaders": BOOTLOADERS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-image", type=Path, required=True, help="QD_MAX4_BOX_02.03.01.21 application image")
    parser.add_argument("--check", action="store_true", help="fail if the committed manifest differs instead of writing it")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as workdir:
        manifest = build_manifest(args.stock_image.read_bytes(), Path(workdir))
    text = json.dumps(manifest, indent=2) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text() != text:
            print(f"{OUTPUT.relative_to(ROOT)} is out of date", file=sys.stderr)
            return 1
        print(f"{OUTPUT.relative_to(ROOT)} matches the sources")
        return 0
    OUTPUT.write_text(text)
    print(f"Wrote {OUTPUT.relative_to(ROOT)} (patched SHA-256 {manifest['box']['patched_sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
