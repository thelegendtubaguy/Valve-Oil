#!/usr/bin/env python3
"""Check the Valve Oil Max 4 THR artifacts, emulate the bounded LIS2DW FIFO query, and the identify dictionary."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "firmware/max4-thr-valve-oil-v1.json"
# Stock 02.02.01.08 identify handler and the sendf it tail-calls.
IDENTIFY, SENDF = 0x080097A4, 0x0800848C


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def check_artifacts(stock_path: Path, patched_dir: Path, manifest: dict) -> tuple[bytes, bytes]:
    require(file_hash(stock_path) == manifest["stock_archive"]["sha256"], "Unknown stock archive")
    archive = patched_dir / manifest["outputs"]["archive"]
    raw_path = patched_dir / manifest["outputs"]["thr"]
    report_path = patched_dir / manifest["outputs"]["report"]
    for path in (archive, raw_path, report_path):
        require(not path.is_symlink() and path.is_file(), f"Not a regular artifact: {path.name}")
        require(
            path.with_name(path.name + ".sha256").read_text(encoding="utf-8")
            == f"{file_hash(path)}  {path.name}\n",
            f"Checksum sidecar mismatch: {path.name}",
        )

    metadata = ("date_time", "compress_type", "comment", "extra", "create_system",
                "create_version", "extract_version", "external_attr", "internal_attr", "flag_bits")
    members = []
    with zipfile.ZipFile(stock_path) as source, zipfile.ZipFile(archive) as target:
        require(source.namelist() == target.namelist(), "Archive members changed")
        require(source.comment == target.comment, "Archive comment changed")
        for original, result in zip(source.infolist(), target.infolist(), strict=True):
            require(all(getattr(original, key) == getattr(result, key) for key in metadata),
                    f"ZIP member metadata changed: {original.filename}")
            with source.open(original) as before, target.open(result) as after:
                stock_hash = hashlib.file_digest(before, "sha256").hexdigest()
                patched_hash = hashlib.file_digest(after, "sha256").hexdigest()
            members.append({"name": result.filename, "sha256": patched_hash})
            if original.filename != manifest["thr"]["member"]:
                require(stock_hash == patched_hash, f"Unexpected payload change: {original.filename}")
        stock = source.read(manifest["thr"]["member"])
        patched = target.read(manifest["thr"]["member"])
    require(raw_path.read_bytes() == patched, "Standalone THR differs from package")
    require(len(stock) == len(patched) == manifest["thr"]["size"], "THR image size changed")
    require(hashlib.sha256(stock).hexdigest() == manifest["thr"]["stock_sha256"], "Stock THR hash mismatch")
    require(hashlib.sha256(patched).hexdigest() == manifest["thr"]["patched_sha256"], "Patched THR hash mismatch")
    allowed, fifo = set(), {}
    for patch in manifest["thr"]["patches"]:
        offset = int(patch["offset"], 0)
        after = bytes.fromhex(patch["after"])
        require(patched[offset:offset + len(after)] == after, "Patch replacement mismatch")
        allowed.update(range(offset, offset + len(after)))
        if "before" in patch:
            before = bytes.fromhex(patch["before"])
            require(len(before) == len(after), "Instruction replacement changes length")
            require(stock[offset:offset + len(before)] == before, "Instruction preimage mismatch")
            if offset < IDENTIFY - int(manifest["thr"]["load_address"], 0):
                fifo.update({offset + i: (old, new) for i, (old, new) in enumerate(zip(before, after)) if old != new})
        else:
            require(hashlib.sha256(stock[offset:offset + len(after)]).hexdigest() == patch["before_sha256"],
                    "Dictionary preimage mismatch")
    changed = {i for i, (old, new) in enumerate(zip(stock, patched)) if old != new}
    require(changed <= allowed, "THR bytes changed outside the manifest")
    require({i: (stock[i], patched[i]) for i in changed if i in fifo} == fifo and len(fifo) == 5,
            "Unexpected FIFO instruction delta")
    stock_dictionary = json.loads(zlib.decompress(emulate_identify(stock, manifest)))
    patched_dictionary = json.loads(zlib.decompress(emulate_identify(patched, manifest)))
    require(stock_dictionary["version"] == manifest["thr"]["stock_version"], "Stock THR dictionary version")
    require(patched_dictionary["version"] == manifest["thr"]["patched_version"], "Patched THR dictionary version")
    patched_dictionary["version"] = stock_dictionary["version"]
    require(patched_dictionary == stock_dictionary, "THR dictionary differs beyond the version")

    report = json.loads(report_path.read_text(encoding="utf-8"))
    require(report == {
        "schema_version": 1,
        "patch_id": manifest["patch_id"],
        "patch_manifest_sha256": file_hash(MANIFEST),
        "upstream_fix": manifest["upstream_fix"],
        "stock_archive": manifest["stock_archive"],
        "thr": manifest["thr"],
        "members": members,
        "artifacts": [{"name": path.name, "sha256": file_hash(path)} for path in (archive, raw_path)],
    }, "Provenance report mismatch")
    print("Verified: five FIFO instruction bytes and the version-only identify dictionary changed; "
          "other payloads and ZIP member metadata unchanged")
    return stock, patched


def emulate_identify(image: bytes, manifest: dict) -> bytes:
    """Run the THR's identify handler and collect the data dictionary it serves."""
    from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB
    from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP

    base, sentinel, args = int(manifest["thr"]["load_address"], 0), 0x08000001, 0x20001000
    machine = Uc(UC_ARCH_ARM, UC_MODE_MCLASS | UC_MODE_THUMB)
    machine.mem_map(0x08000000, 0x100000)
    machine.mem_write(base, image)
    machine.mem_map(0x20000000, 0x10000)
    captured = []

    def sendf(uc, address, size, user_data):
        captured.append((uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2), uc.reg_read(UC_ARM_REG_R3)))
        uc.emu_stop()

    machine.hook_add(UC_HOOK_CODE, sendf, begin=SENDF, end=SENDF + 1)
    data = b""
    while True:
        machine.mem_write(args, struct.pack("<II", len(data), 40))
        machine.reg_write(UC_ARM_REG_R0, args)
        machine.reg_write(UC_ARM_REG_SP, 0x2000F000)
        machine.reg_write(UC_ARM_REG_LR, sentinel)
        captured.clear()
        machine.emu_start(IDENTIFY | 1, sentinel & ~1, count=200)
        require(len(captured) == 1, "THR identify did not send a response")
        offset, count, pointer = captured[0]
        require(offset == len(data), "THR identify answered the wrong offset")
        chunk = bytes(machine.mem_read(pointer, count)) if count else b""
        data += chunk
        if len(chunk) < 40:
            return data


def check_fifo_execution(stock: bytes, patched: bytes, manifest: dict) -> None:
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_MCLASS, CS_MODE_THUMB
    from unicorn import Uc, UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB
    from unicorn.arm_const import (
        UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
        UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_SP,
    )

    base = int(manifest["thr"]["load_address"], 0)
    for patch in manifest["thr"]["patches"]:
        if "before" not in patch:
            continue
        offset = int(patch["offset"], 0)
        instructions = Cs(CS_ARCH_ARM, CS_MODE_MCLASS | CS_MODE_THUMB).disasm(
            patched[offset:offset + len(bytes.fromhex(patch["after"]))], base + offset
        )
        print("Patched instructions:", "; ".join(f"{i.address:#010x}: {i.mnemonic} {i.op_str}" for i in instructions))

    ram, ax, stack, clock = 0x20000000, 0x20002000, 0x2000F000, 0xE0001000
    entry, exit_pc = base + 0x0B74, base + 0x09D6
    spi, sendf, add_timer = base + 0x3338, base + 0x048C, base + 0x01F8
    sample = bytes([1, 2, 3, 4, 5, 6])
    cases = 0
    for image, fixed in ((stock, False), (patched, True)):
        machine = Uc(UC_ARCH_ARM, UC_MODE_MCLASS | UC_MODE_THUMB)
        machine.mem_map(base, 0x9000)
        machine.mem_write(base, image)
        machine.mem_map(ram, 0x10000)
        machine.mem_map(clock, 0x1000)
        state = {}

        def intercept(uc, address, size, user_data):
            if address == spi:
                length, pointer = uc.reg_read(UC_ARM_REG_R2), uc.reg_read(UC_ARM_REG_R3)
                command = uc.mem_read(pointer, 1)[0]
                state["spi"].append((command, length))
                if (command, length) == (0xA8, 7):
                    uc.mem_write(pointer + 1, sample)
                elif (command, length) == (0xAF, 2):
                    uc.mem_write(pointer + 1, bytes([state["status"]]))
                else:
                    raise ValueError("Unexpected SPI transaction")
            elif address == sendf:
                state["reports"].append((uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2),
                                         uc.reg_read(UC_ARM_REG_R3)))
            elif address == add_timer:
                state["timers"] += 1
            else:
                require(entry <= address < base + 0x0C2E or base + 0x1260 <= address < base + 0x1280,
                        f"Execution escaped FIFO block: {address:#x}")
                return
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

        machine.hook_add(UC_HOOK_CODE, intercept)
        for status in range(256):
            for buffered in (0, 42):
                state.update(status=status, spi=[], reports=[], timers=0)
                machine.mem_write(ram, bytes(0x10000))
                machine.mem_write(clock + 4, struct.pack("<I", 123456))
                machine.mem_write(ram + 8, b"\x01")
                machine.mem_write(ax + 0x0C, struct.pack("<I", 180000))
                machine.mem_write(ax + 0x10, struct.pack("<I", ram + 0x3000))
                machine.mem_write(ax + 0x14, b"\x01")
                machine.mem_write(ax + 0x16, struct.pack("<HHB", 7, 17, buffered))
                machine.reg_write(UC_ARM_REG_SP, stack)
                machine.reg_write(UC_ARM_REG_R4, 3)
                machine.reg_write(UC_ARM_REG_R5, ax)
                machine.emu_start(entry | 1, exit_pc, count=256)
                require(machine.reg_read(UC_ARM_REG_PC) == exit_pc, "FIFO block did not terminate")
                wake = bool(status & 0x3F) if fixed else not bool(status & 0x3F)
                context = f"{'patched' if fixed else 'stock'} status={status:#x} buffered={buffered}"
                require(state["spi"] == [(0xA8, 7), (0xAF, 2)], f"SPI sample read changed: {context}")
                require(state["timers"] == int(not wake), f"FIFO timer decision wrong: {context}")
                require(machine.mem_read(ram + 0x5C, 1)[0] == int(wake), f"Task wake wrong: {context}")
                require(machine.mem_read(ram + 8, 1)[0] == int(not wake), f"Scheduler state wrong: {context}")
                require(machine.mem_read(ax + 0x14, 1)[0] == int(wake), f"Pending flag wrong: {context}")
                require(machine.mem_read(ax + 0x1B + buffered, 6) == sample, f"Sample copy changed: {context}")
                sequence, overflows, count = struct.unpack("<HHB", machine.mem_read(ax + 0x16, 5))
                require((sequence, overflows, count) == (7 + int(buffered == 42),
                        17 + int(bool(status & 0x40)), 0 if buffered == 42 else 6),
                        f"Reporting or overflow accounting changed: {context}")
                require(state["reports"] == ([(3, 7, 48)] if buffered == 42 else []),
                        f"Bulk report changed: {context}")
                if not wake:
                    waketime = struct.unpack("<I", machine.mem_read(ax + 8, 4))[0]
                    require(waketime == 303456, f"Polling interval changed: {context}")
                cases += 1
    print(f"Verified {cases} stock/patched ARM executions: all FIFO status bytes, partial/full report buffers")


def check_rejections(stock_path: Path, patched_dir: Path, manifest: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="thr-fifo-rejections-") as temporary:
        root = Path(temporary)
        corrupt = root / "corrupt.zip"
        shutil.copyfile(stock_path, corrupt)
        with corrupt.open("r+b") as handle:
            handle.write(b"\x00")
        for index, path in enumerate((corrupt, patched_dir / manifest["outputs"]["archive"],
                                      patched_dir / manifest["outputs"]["thr"])):
            output = root / f"rejected-{index}"
            result = subprocess.run([sys.executable, str(ROOT / "scripts/build_thr_firmware.py"),
                                     "--stock-archive", str(path), "--output-dir", str(output)],
                                    capture_output=True, text=True)
            require(result.returncode == 1 and not output.exists(), "Unsafe input produced artifacts")
        existing = root / "existing"
        existing.mkdir()
        sentinel = existing / "keep.txt"
        sentinel.write_bytes(b"keep")
        result = subprocess.run([sys.executable, str(ROOT / "scripts/build_thr_firmware.py"),
                                 "--stock-archive", str(stock_path), "--output-dir", str(existing)],
                                capture_output=True, text=True)
        require(result.returncode == 1 and list(existing.iterdir()) == [sentinel]
                and sentinel.read_bytes() == b"keep", "Existing output was overwritten")
    require(file_hash(stock_path) == manifest["stock_archive"]["sha256"], "Input archive was modified")
    print("Verified rejection of modified/already-patched archives, raw images, and existing destinations")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-archive", type=Path, required=True)
    parser.add_argument("--patched-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        stock, patched = check_artifacts(args.stock_archive, args.patched_dir, manifest)
        check_fifo_execution(stock, patched, manifest)
        check_rejections(args.stock_archive, args.patched_dir, manifest)
    except ImportError as error:
        print(f"Missing checking dependency: {error}. Install firmware/check-requirements.txt", file=sys.stderr)
        return 1
    except (OSError, ValueError, zipfile.BadZipFile, zlib.error) as error:
        print(f"Firmware check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
