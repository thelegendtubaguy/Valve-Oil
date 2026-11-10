#!/usr/bin/env python3
"""Offline checks for the Valve Oil QIDI Box firmware (shutdown fix and RFID commands) and the Box transfer tool.

Always: the patch applies only to the pinned stock image, changes only the
manifest regions, the patched data dictionary served by `identify` (run in
emulation) differs from stock only in its version and FM17550 commands, and
the fm17550_select and fm17550_transceive handlers run in emulation against
modelled NTAG and MIFARE Classic tags (QIDI's reader routines stubbed at their
entry points).

--bootloader-backup: runs the transfer tool against the captured bootloader's
own code (USB stack, delays, PLL setup, and the final jump or reset stubbed;
flash controller modelled at register level): stock and patched transfers,
lost acknowledgements, an unacknowledged packet 0, a non-erasing probe, and a
resident bootloader.

--ram-dump: runs the application's shutdown handlers from a configured Box's
RAM (read with debug_read): stock must re-enter shutdown(), patched must reach
the shutdown report with the original reason.

Requires firmware/check-requirements.txt. No binaries are shipped; pass your own.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_THUMB, Cs
from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc, UcError
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flash_box_firmware as tool  # noqa: E402
from build_box_firmware import load_manifest, patch_box  # noqa: E402

BASE = 0x08008000
# Application (stock 02.03.01.21) addresses.
IDENTIFY, SENDF = 0x08009524, 0x080083A4
SHUTDOWN_HANDLERS, SHUTDOWN, SHUTDOWN_REPORT = 0x08009048, 0x0800831C, 0x08009392
DICTIONARY, DICTIONARY_BYTES = 0x080103F8, 3156
# Recovered hooks for the admitted bootloader build.
BOOTLOADER_HOOKS = {
    "64962da860449bd3af7ead7cc194ec01192493b0b3e7eedc70e384f61bf7b1e4": dict(
        reset=0x080049DC, main_loop=0x08004494, main_entry=0x080049D0, systick=0x0800363C, cdc_rx=0x08003774,
        tx=0x08003820, jump=0x08003C78, system_reset=0x08003CE4, error=0x08004810,
        stubs=(0x080045EC, 0x08003684, 0x08000AFC, 0x080029B2, 0x08002A00, 0x08002A66)),
    # COLOR_BOOT build: no NVIC reset path; leaves the rest of the last page erased.
    "1e00811df777a8cf21098b1386319a440c90e04aa2a9ab0aeef11dc810891b1e": dict(
        reset=0x08004AB4, main_loop=0x08004584, main_entry=0x08004AAC, systick=0x080035C0, cdc_rx=0x080036F8,
        tx=0x08003784, jump=0x08003BDC, system_reset=None, error=0x080048E0, fill=0xFF,
        stubs=(0x080046BC, 0x08003608, 0x08000A80, 0x08002936, 0x08002984, 0x080029EA)),
}
SECTORS = [(0x08000000, 0x4000), (0x08004000, 0x4000), (0x08008000, 0x4000), (0x0800C000, 0x4000),
           (0x08010000, 0x10000), (0x08020000, 0x20000)]
FLASH_CR = 0x40023C10
SENTINEL = 0x2000F001


class CheckFailed(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


# ---- application image -----------------------------------------------------

def emulate_identify(image: bytes) -> bytes:
    mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    mu.mem_map(0x08000000, 0x40000)
    mu.mem_write(BASE, image)
    mu.mem_map(0x20000000, 0x10000)
    captured = []

    def hook(uc, address, size, user):
        captured.append((uc.reg_read(UC_ARM_REG_R1), uc.reg_read(UC_ARM_REG_R2), uc.reg_read(UC_ARM_REG_R3)))
        uc.emu_stop()

    mu.hook_add(UC_HOOK_CODE, hook, begin=SENDF, end=SENDF + 1)
    data = b""
    while True:
        mu.mem_write(0x20001000, struct.pack("<II", len(data), 40))
        mu.reg_write(UC_ARM_REG_R0, 0x20001000)
        mu.reg_write(UC_ARM_REG_SP, 0x2000F000)
        mu.reg_write(UC_ARM_REG_LR, 0x08000001)
        captured.clear()
        mu.emu_start(IDENTIFY | 1, 0x08000000, count=200)
        require(len(captured) == 1, "identify did not send a response")
        offset, count, pointer = captured[0]
        require(offset == len(data), "identify answered the wrong offset")
        chunk = bytes(mu.mem_read(pointer, count)) if count else b""
        data += chunk
        if len(chunk) < 40:
            return data


def check_image(stock: bytes, manifest: dict) -> bytes:
    box = manifest["box"]
    require(tool.image_identity(stock, manifest) == "stock", "stock image is not the pinned 02.03.01.21 image")
    patched = patch_box(stock, manifest)
    require(tool.image_identity(patched, manifest) == "patched", "patched image hash differs from the manifest")
    for bad in (patched, stock[:-1], stock[:-1] + bytes([stock[-1] ^ 1])):
        try:
            patch_box(bad, manifest)
        except ValueError:
            continue
        raise CheckFailed("patch accepted an image that is not the pinned stock image")
    allowed = set()
    for patch in box["patches"]:
        offset = int(patch["offset"], 0)
        allowed.update(range(offset, offset + len(bytes.fromhex(patch["after"]))))
    require(all(i in allowed for i in range(len(stock)) if stock[i] != patched[i]), "bytes changed outside the manifest")
    disassembler = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    stock_dictionary = json.loads(zlib.decompress(emulate_identify(stock)))
    served = emulate_identify(patched)
    patched_dictionary = json.loads(zlib.decompress(served))
    length = len(served)
    expected = {0x080090B6: ("b", "#0x80090ec"), 0x08009528: ("movw", f"r3, #{length - 1:#x}"),
                0x08009534: ("movw", f"r3, #{length:#x}"), 0x0800953C: ("rsb.w", f"r2, r1, #{length & 0xFF:#x}")}
    for address, (mnemonic, operands) in expected.items():
        instruction = next(disassembler.disasm(patched[address - BASE:address - BASE + 4], address))
        require((instruction.mnemonic, instruction.op_str) == (mnemonic, operands), f"unexpected instruction at {address:#x}")
    require(stock_dictionary["version"] == box["stock_version"], "stock dictionary version")
    require(patched_dictionary["version"] == box["patched_version"], "patched dictionary version")
    expected_dictionary = json.loads(json.dumps(stock_dictionary))
    expected_dictionary["version"] = box["patched_version"]
    for name in RFID_COMMANDS_REMOVED:
        del expected_dictionary["commands"][name]
    for name in RFID_RESPONSES_REMOVED:
        del expected_dictionary["responses"][name]
    expected_dictionary["commands"].update(RFID_COMMANDS)
    expected_dictionary["responses"].update(RFID_RESPONSES)
    require(patched_dictionary == expected_dictionary, "patched dictionary differs beyond the version and FM17550 commands")
    print("image: patch, changed regions, instructions, and identify dictionary OK")
    return patched


# ---- RFID handler emulation ------------------------------------------------

RFID_COMMANDS = {"fm17550_select oid=%c": 15, "fm17550_transceive oid=%c data=%*s": 17}
RFID_RESPONSES = {"fm17550_select_return oid=%c status=%c data=%*s": -24,
                  "fm17550_transceive_return oid=%c status=%c data=%*s": -22}
RFID_COMMANDS_REMOVED = ("fm17550_write_card_cb oid=%c", "fm17550_read_fifo_cb oid=%c",
                         "fm17550_read_regs_cb oid=%c", "fm17550_init_cb oid=%c")
RFID_RESPONSES_REMOVED = ("fm17550_write_card_return oid=%c status=%c", "fm17550_read_fifo_return oid=%c data=%*s",
                          "fm17550_read_regs_return oid=%c data=%*s", "fm17550_init_return oid=%c")
COMMAND_TABLE, FM17550_TYPE = 0x0801121C, 0x08009755
SPI_TRANSFER, INVALID_OID = 0x0800CC48, 0x08009708
FM_SET_BITS, FM_INIT, FM_TIMEOUT, FM_EXECUTE = 0x0800FBFC, 0x0800FC4C, 0x0800F9D4, 0x0800A970
FM_ANTICOLLISION, FM_SELECT = 0x0800AC8C, 0x0800ADF0
SELECT_ENCODER, TRANSCEIVE_ENCODER = 0x080102C8, 0x080102D8
OID_COUNT, OID_TABLE = 0x20000244, 0x20000248
READER, SPI, ARGS, FRAME, FAKE_TABLE = 0x20003000, 0x20003800, 0x20001000, 0x20001100, 0x20002000
RETURN = 0x08000000


class Tag:
    """ISO14443A tag at frame level: NTAG-style Type 2 (7-byte UID) or MIFARE Classic 1K (4-byte UID)."""

    def __init__(self, kind):
        self.kind = kind
        self.uid = bytes.fromhex("04a1b2c3d4e5f6") if kind == "ntag" else bytes.fromhex("c0ffee42")
        self.memory = bytearray(range(256)) if kind == "ntag" else bytearray(1024)
        self.state, self.pending_write = "idle", None

    def cascade(self, level):
        uid = self.uid
        if len(uid) == 4:
            parts = [uid]
        else:
            parts = [b"\x88" + uid[:3], uid[3:]]
        if level >= len(parts):
            return None
        part = parts[level]
        return part + bytes([part[0] ^ part[1] ^ part[2] ^ part[3]]), (0x04 if level < len(parts) - 1 else
                                                                       (0x00 if self.kind == "ntag" else 0x08))


class Reader:
    def __init__(self, image: bytes, tag=None, *, faulty=False):
        self.tag, self.faulty, self.crypto = tag, faulty, False
        self.regs = {0x14: 0x80, 0x15: 0x00}
        self.pending_read, self.sent, self.calls, self.outcome = None, [], [], None
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        uc.mem_map(0x08000000, 0x40000)
        uc.mem_write(BASE, image)
        uc.mem_map(0x20000000, 0x10000)
        uc.mem_write(OID_COUNT, bytes([3]))
        uc.mem_write(OID_TABLE, struct.pack("<I", FAKE_TABLE))
        uc.mem_write(FAKE_TABLE, struct.pack("<IIIIII", FM17550_TYPE, READER, 0x08009001, READER, FM17550_TYPE, READER))
        uc.mem_write(READER + 0x10, struct.pack("<I", SPI))
        uc.hook_add(UC_HOOK_CODE, self._code)

    # Reader registers through QIDI's two-transfer read and two-byte write pattern.
    def _spi(self, receive, length, buffer):
        data = bytearray(self.uc.mem_read(buffer, length))
        if receive:
            require(length == 1, "register read with more than one byte")
            value = self.regs.get(self.pending_read, 0) if self.pending_read is not None else 0
            self.pending_read = (data[0] >> 1) & 0x3F if data[0] & 0x80 else None
            self.uc.mem_write(buffer, bytes([value]))
        else:
            require(length == 2 and not data[0] & 0x80, "unexpected register write")
            self.regs[data[0] >> 1] = data[1]
            if data[0] >> 1 == 0x08:
                self.crypto = bool(data[1] & 0x08)

    def _execute(self, command, frame):
        """QIDI's fm_execute contract: error flags in r0, received bytes and bit count."""
        tag, regs = self.tag, self.regs
        if command == 0x0E:
            ok = (tag is not None and tag.kind == "classic" and tag.state == "active" and len(frame) == 12
                  and frame[0] in (0x60, 0x61) and frame[2:8] == b"\xff" * 6 and frame[8:12] == tag.uid)
            self.crypto = ok
            regs[0x08] = (regs.get(0x08, 0) & ~0x08) | (0x08 if ok else 0)
            return (0, b"", 0) if ok else (0x01, b"", 0)
        if tag is None:
            return 0x01, b"", 0
        if regs.get(0x0D, 0) & 7 == 7:
            if frame == b"\x52" or (frame == b"\x26" and tag.state == "idle"):
                tag.state = "ready"
                return 0, bytes([0x44 if tag.kind == "ntag" else 0x04, 0x00]), 16
            return 0x01, b"", 0
        require(regs.get(0x12, 0) & 0x80 and regs.get(0x13, 0) & 0x80, "frame sent without CRC")
        if tag.state != "active":
            return 0x01, b"", 0
        if tag.pending_write is not None:
            address, tag.pending_write = tag.pending_write, None
            require(len(frame) == 16, "MIFARE write data phase is not 16 bytes")
            tag.memory[address * 16:address * 16 + 16] = frame
            return 0, b"\x0a", 4
        if frame == b"\x50\x00":
            tag.state = "halt"
            return 0x01, b"", 0
        if tag.kind == "ntag":
            if frame[0] == 0x30 and len(frame) == 2:
                start = frame[1] * 4
                return 0, bytes(tag.memory[(start + i) % 256] for i in range(16)), 128
            if frame[0] == 0xA2 and len(frame) == 6:
                tag.memory[frame[1] * 4:frame[1] * 4 + 4] = frame[2:]
                return 0, b"\x0a", 4
            if frame[0] == 0x3A:  # FAST_READ start..end, longer than the reply limit
                count = (frame[2] - frame[1] + 1) * 4
                return 0, bytes(tag.memory[frame[1] * 4:frame[1] * 4 + count]), count * 8
            return 0x01, b"", 0
        if not self.crypto:
            return 0x01, b"", 0
        if frame[0] == 0x30 and len(frame) == 2:
            return 0, bytes(tag.memory[frame[1] * 16:frame[1] * 16 + 16]), 128
        if frame[0] == 0xA0 and len(frame) == 2:
            tag.pending_write = frame[1]
            return 0, b"\x0a", 4
        return 0x01, b"", 0

    def _return(self, value=None):
        uc = self.uc
        if value is not None:
            uc.reg_write(UC_ARM_REG_R0, value)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def _code(self, uc, address, size, user):
        r = [uc.reg_read(reg) for reg in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        stack = lambda n: struct.unpack("<I", uc.mem_read(uc.reg_read(UC_ARM_REG_SP) + 4 * n, 4))[0]
        if address == SPI_TRANSFER:
            require(r[0] == SPI, "SPI transfer on the wrong device")
            self._spi(r[1], r[2], r[3])
            self._return()
        elif address == FM_SET_BITS:
            if not (self.faulty and r[1] in (0x14, 0x15)):
                self.regs[r[1]] = self.regs.get(r[1], 0) | r[2]
            self._return()
        elif address == FM_INIT:
            self.regs.update({0x12: 0, 0x13: 0})
            if not self.faulty:
                self.regs[0x15] = self.regs.get(0x15, 0) | 0x40
            self.calls.append("init")
            self._return()
        elif address == FM_TIMEOUT:
            self.calls.append(("timeout", r[1]))
            self._return()
        elif address == FM_EXECUTE:
            frame = bytes(uc.mem_read(r[2], r[3]))
            self.calls.append(("execute", r[1], frame))
            error, reply, bits = self._execute(r[1], frame)
            uc.mem_write(stack(0), reply)
            uc.mem_write(stack(1), struct.pack("<i", bits))
            self._return(error)
        elif address == FM_ANTICOLLISION:
            level = (r[1] - 0x93) // 2
            found = self.tag.cascade(level) if self.tag and self.tag.state == "ready" else None
            self.regs.update({0x0D: 0, 0x12: self.regs.get(0x12, 0) & 0x7F, 0x13: self.regs.get(0x13, 0) & 0x7F})
            if found:
                uc.mem_write(r[2], found[0])
            self._return(0 if found else 1)
        elif address == FM_SELECT:
            level = (r[1] - 0x93) // 2
            found = self.tag.cascade(level) if self.tag else None
            ok = found is not None and bytes(uc.mem_read(r[2], 5)) == found[0]
            if ok:
                uc.mem_write(r[3], bytes([found[1]]))
                if not found[1] & 0x04:
                    self.tag.state = "active"
            self.regs.update({0x0D: 0, 0x12: self.regs.get(0x12, 0) | 0x80, 0x13: self.regs.get(0x13, 0) | 0x80,
                              0x08: self.regs.get(0x08, 0) & ~0x08})
            self.crypto = False
            self._return(0 if ok else 1)
        elif address == SENDF:
            self.sent.append((r[0], r[1], r[2], bytes(uc.mem_read(stack(0), r[3]))))
            self._return()
        elif address == INVALID_OID:
            self.outcome = "shutdown"
            uc.emu_stop()
        elif address == RETURN:
            uc.emu_stop()

    def call(self, handler, args, frame=b""):
        uc = self.uc
        uc.mem_write(FRAME, frame)
        uc.mem_write(ARGS, struct.pack("<III", *(list(args) + [0, 0, 0])[:3]))
        uc.reg_write(UC_ARM_REG_R0, ARGS)
        uc.reg_write(UC_ARM_REG_SP, 0x2000F000)
        uc.reg_write(UC_ARM_REG_LR, RETURN | 1)
        self.sent.clear()
        self.outcome = None
        uc.emu_start(handler | 1, RETURN, count=200_000)
        if self.outcome:
            return self.outcome
        require(len(self.sent) == 1, f"handler sent {len(self.sent)} responses")
        return self.sent[0]

    def select(self, oid=0):
        result = self.call(SELECT_HANDLER, [oid])
        if isinstance(result, str):
            return result
        encoder, reply_oid, status, data = result
        require(encoder == SELECT_ENCODER and reply_oid == oid and len(data) == 20, "select response shape")
        return status, data

    def transceive(self, command, timeout, frame, oid=0):
        payload = bytes([command, timeout]) + frame
        result = self.call(TRANSCEIVE_HANDLER, [oid, len(payload), FRAME], payload)
        if isinstance(result, str):
            return result
        encoder, reply_oid, status, data = result
        require(encoder == TRANSCEIVE_ENCODER and reply_oid == oid and len(data) >= 4, "transceive response shape")
        bits = data[2] | data[3] << 8
        return status, data[0], data[1], bits, data[4:]


SELECT_HANDLER, TRANSCEIVE_HANDLER = 0x0800B228, 0x08009990


def check_rfid(patched: bytes) -> None:
    def table(number):
        return struct.unpack_from("<HBBB3xII", patched, COMMAND_TABLE + 16 * number - BASE)

    def encoder(address):
        msgid, max_size, count, types = struct.unpack_from("<HBBI", patched, address - BASE)
        return msgid, max_size, list(patched[types - BASE:types - BASE + count])

    require(table(15)[5] == SELECT_HANDLER | 1 and table(15)[1:4:2] == (1, 1), "command 15 is not fm17550_select oid=%c")
    t17 = table(17)
    require(t17[5] == TRANSCEIVE_HANDLER | 1 and (t17[1], t17[3]) == (3, 2)
            and list(patched[t17[4] - BASE:t17[4] - BASE + 2]) == [4, 7], "command 17 is not fm17550_transceive oid=%c data=%*s")
    require(table(18)[5] == table(19)[5] == table(9)[5], "commands 18 and 19 do not point at debug_nop")
    require(encoder(SELECT_ENCODER) == (0x68, 0x40, [4, 4, 7]) and encoder(TRANSCEIVE_ENCODER) == (0x6A, 0x40, [4, 4, 7]),
            "response encoders are not oid=%c status=%c data=%*s")

    reader = Reader(patched)
    require(reader.select() == (0, bytes(20)), "no tag: select did not report status 0 with empty data")
    require(reader.select(oid=1) == "shutdown" and reader.select(oid=3) == "shutdown", "invalid oid did not shut down")
    require(Reader(patched, Tag("ntag"), faulty=True).select()[0] == 2, "unresponsive reader not reported as status 2")
    print("rfid: tables, encoders, no tag, invalid oid, reader fault OK")

    tag = Tag("ntag")
    reader = Reader(patched, tag)
    status, data = reader.select()
    expected = bytes([0x44, 0]) + b"\x88" + tag.uid[:3] + bytes([0x88 ^ tag.uid[0] ^ tag.uid[1] ^ tag.uid[2]]) \
        + tag.uid[3:] + bytes([tag.uid[3] ^ tag.uid[4] ^ tag.uid[5] ^ tag.uid[6]]) + bytes(5) + bytes([0x04, 0x00, 0x00])
    require(status == 1 and data == expected, f"NTAG select: {status} {data.hex()}")
    require(("execute", 0x0C, b"\x52") in reader.calls, "select did not send WUPA")
    status, error, _status2, bits, reply = reader.transceive(0x0C, 3, b"\x30\x04")
    require((status, error, bits, reply) == (1, 0, 128, bytes(range(16, 32))), "NTAG READ page 4")
    require(reader.transceive(0x0C, 10, b"\xa2\x04QIDI")[1:] == (0, reader.regs.get(0x08, 0), 4, b"\x0a"), "NTAG WRITE ack")
    require(reader.transceive(0x0C, 3, b"\x30\x04")[4][:4] == b"QIDI", "NTAG write did not read back")
    status, error, _status2, bits, reply = reader.transceive(0x0C, 3, b"\x3a\x00\x0f")
    require(status == 1 and bits == 512 and reply == bytes(tag.memory[:44]), "long reply not capped at 44 bytes")
    reader.transceive(0x0C, 1, b"\x50\x00")
    require(tag.state == "halt" and reader.select()[0] == 1, "WUPA did not wake a halted tag")
    calls = len(reader.calls)
    for command, frame in ((0x01, b"\x00"), (0x0C, b""), (0x0C, bytes(33))):
        require(reader.transceive(command, 1, frame) == (0, 0xFF, 0, 0, b""), "invalid transceive input was not refused")
    require(len(reader.calls) == calls, "invalid transceive input reached the reader")
    print("rfid: NTAG select, WUPA, READ, WRITE, reply cap, HLTA, input limits OK")

    tag = Tag("classic")
    reader = Reader(patched, tag)
    status, data = reader.select()
    require(status == 1 and data[:7] == bytes([0x04, 0]) + tag.uid + bytes([tag.uid[0] ^ tag.uid[1] ^ tag.uid[2] ^ tag.uid[3]])
            and data[17:] == bytes([0x08, 0, 0]), f"Classic select: {status} {data.hex()}")
    require(reader.transceive(0x0C, 3, b"\x30\x04")[0] == 0, "Classic READ before authentication succeeded")
    reader.select()
    status, error, status2, _bits, _reply = reader.transceive(0x0E, 2, b"\x60\x04" + b"\xff" * 6 + tag.uid)
    require(status == 1 and error == 0 and status2 & 0x08, "MIFARE authentication with the default key")
    block = bytes.fromhex("41001155667700000000000000000000")
    require(reader.transceive(0x0C, 3, b"\xa0\x04")[3:] == (4, b"\x0a"), "Classic WRITE command ack")
    require(reader.transceive(0x0C, 10, block)[3:] == (4, b"\x0a"), "Classic WRITE data ack")
    require(reader.transceive(0x0C, 3, b"\x30\x04")[4] == block, "Classic block 4 did not read back")
    reader.select()
    require(reader.transceive(0x0E, 2, b"\x60\x04" + b"\x00" * 6 + tag.uid)[2] & 0x08 == 0, "wrong key authenticated")
    print("rfid: MIFARE Classic select, authentication, block 4 READ and WRITE OK")


# ---- bootloader emulation --------------------------------------------------

class Board:
    def __init__(self, boot: bytes, app_region: bytes):
        self.hooks = BOOTLOADER_HOOKS[tool.digest(boot)]
        self.boot = boot
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        for base, size in ((0x08000000, 0x40000), (0x1FFF0000, 0x10000), (0x20000000, 0x10000),
                           (0x40000000, 0x30000), (0x50000000, 0x40000), (0xE0000000, 0x100000)):
            uc.mem_map(base, size)
        uc.mem_write(0x08000000, boot)
        uc.mem_write(BASE, b"\xff" * 0x38000)
        uc.mem_write(BASE, app_region)
        uc.mem_write(FLASH_CR, struct.pack("<I", 0x80000000))
        self.sent, self.erases, self.bad, self.keys = [], [], [], []
        self.halted, self.ms, self.pending = None, 0, None
        uc.hook_add(UC_HOOK_CODE, self._code)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._flash_write, begin=0x08000000, end=0x0803FFFF)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._controller, begin=0x40023C00, end=0x40023C1F)
        uc.reg_write(UC_ARM_REG_SP, 0x20010000)
        self._run(self.hooks["reset"] | 1, self.hooks["main_entry"])
        self.main_sp = uc.reg_read(UC_ARM_REG_SP)
        # Frames sent before USB enumeration never reach the host.
        self.sent.clear()

    def _return(self, value=0):
        self.uc.reg_write(UC_ARM_REG_R0, value)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def _code(self, uc, address, size, user):
        if self.pending:
            target, value = self.pending
            self.pending = None
            uc.mem_write(target, struct.pack("<I", value))
        hooks = self.hooks
        if address in hooks["stubs"]:
            self._return()
        elif address == hooks["tx"]:
            self.sent.append(bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_R0), uc.reg_read(UC_ARM_REG_R1))))
            self._return()
        elif address in (hooks["jump"], hooks["system_reset"], hooks["error"]):
            self.halted = {hooks["jump"]: "jump", hooks["system_reset"]: "system_reset", hooks["error"]: "error"}[address]
            uc.emu_stop()

    def _controller(self, uc, access, address, size, value, user):
        if address == 0x40023C04:
            self.keys.append(value)
            if self.keys[-2:] == [0x45670123, 0xCDEF89AB]:
                control = struct.unpack("<I", uc.mem_read(FLASH_CR, 4))[0]
                uc.mem_write(FLASH_CR, struct.pack("<I", control & 0x7FFFFFFF))
        elif address == FLASH_CR and value & (1 << 16) and value & 2:
            sector = (value >> 3) & 0xF
            self.erases.append(sector)
            if sector < len(SECTORS):
                start, length = SECTORS[sector]
                uc.mem_write(start, b"\xff" * length)
            self.pending = (FLASH_CR, value & ~(1 << 16))

    def _flash_write(self, uc, access, address, size, value, user):
        control = struct.unpack("<I", uc.mem_read(FLASH_CR, 4))[0]
        if not control & 1 or control & 0x80000000 or address < BASE:
            self.bad.append(hex(address))
        previous = struct.unpack("<I", uc.mem_read(address & ~3, 4))[0]
        if size == 4:
            if previous & value != value:
                self.bad.append(hex(address))
            self.pending = (address, previous & value)

    def _run(self, start, until):
        try:
            self.uc.emu_start(start, until, count=20_000_000)
        except UcError as error:
            self.halted = "fault " + str(error)
        if self.pending:
            target, value = self.pending
            self.pending = None
            self.uc.mem_write(target, struct.pack("<I", value))

    def call(self, function, *arguments):
        if self.halted:
            return
        self.uc.reg_write(UC_ARM_REG_SP, self.main_sp)
        self.uc.reg_write(UC_ARM_REG_LR, SENTINEL)
        for register, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1), arguments):
            self.uc.reg_write(register, value)
        self._run(function | 1, SENTINEL & ~1)

    def tick(self, milliseconds=1):
        for _ in range(milliseconds):
            if self.halted:
                return
            self.ms += 1
            self.call(self.hooks["systick"])
            self.call(self.hooks["main_loop"])
            self.call(self.hooks["main_loop"])

    def usb_out(self, raw: bytes):
        # A transfer that ends on a packet boundary is closed by a zero-length packet.
        for offset in range(0, len(raw) + (0 if len(raw) % 64 else 1), 64):
            chunk = raw[offset:offset + 64]
            self.uc.mem_write(0x2000E000, chunk.ljust(64, b"\0"))
            self.uc.mem_write(0x2000E100, struct.pack("<I", len(chunk)))
            self.call(self.hooks["cdc_rx"], 0x2000E000, 0x2000E100)
        self.call(self.hooks["main_loop"])

    def flash(self, address, length):
        return bytes(self.uc.mem_read(address, length))


class EmulatedPort:
    def __init__(self, board: Board, drop=()):
        self.board, self.output, self.seen, self.drop = board, bytearray(), 0, set(drop)

    def _collect(self):
        for raw in self.board.sent[self.seen:]:
            key = ("data", int.from_bytes(raw[4:8], "big")) if raw[1] == 2 else ("kind", raw[1])
            if key in self.drop:
                self.drop.discard(key)
                continue
            self.output.extend(raw)
        self.seen = len(self.board.sent)

    def write(self, raw):
        self.board.usb_out(raw)
        self._collect()
        return len(raw)

    def flush(self):
        pass

    def read(self, count):
        if not self.output:
            self.board.tick(5)
            self._collect()
        chunk = bytes(self.output[:count])
        del self.output[:count]
        return chunk


def check_transfers(boot: bytes, stock: bytes, patched: bytes, manifest: dict) -> None:
    profile = tool.bootloader_profile(boot, manifest)
    padded = (len(stock) + 1023) // 1024 * 1024
    fill = BOOTLOADER_HOOKS[tool.digest(boot)].get("fill", 0)
    installed = stock + bytes([fill]) * (padded - len(stock))

    def board(app_region=installed):
        b = Board(boot, app_region)
        b.tick(600)
        return b

    def written(b, image):
        region = b.flash(BASE, 0x18000)
        return region[:len(image)] == image and set(region[len(image):padded]) == {fill} and set(region[padded:]) == {0xFF}

    cases = (("stock", stock, (), installed), ("patched", patched, (), installed),
             ("patched, lost ack on packet 5", patched, (("data", 5),), installed),
             ("patched, lost ack on last packet", patched, (("data", padded // 1024 - 1),), installed),
             ("patched, bootloader resident", patched, (), b"\xff" * 0x1000))
    for name, image, drop, region in cases:
        b = board(region)
        result = tool.Bootloader(EmulatedPort(b, drop), profile, clock=lambda b=b: b.ms / 1000).transfer(image, manifest)
        b.tick(3000)
        require(result["transfer_acknowledged"] and written(b, image), f"{name}: flash differs from the image")
        require(b.flash(0x08000000, 0x8000) == boot and not b.bad, f"{name}: bootloader or unerased write")
        require(b.halted == "jump", f"{name}: application not started")
        print(f"transfer: {name} OK")

    b = board()
    try:
        tool.Bootloader(EmulatedPort(b, (("data", 0),)), profile, clock=lambda: b.ms / 1000).transfer(patched, manifest)
        raise CheckFailed("packet 0 failure was not reported")
    except tool.TransferFailed:
        pass
    b.tick(12000)  # the slower build gives up on a session after 10 s
    require(set(b.flash(BASE, 0x1000)) == {0xFF} and b.flash(0x08000000, 0x8000) == boot,
            "packet 0 failure: first application sector not blank or bootloader changed")
    print("transfer: unacknowledged packet 0 leaves the bootloader resident OK")

    b = board()
    reply = tool.Bootloader(EmulatedPort(b), profile, clock=lambda: b.ms / 1000).probe()
    b.tick(12000)  # the slower build gives up on a session after 10 s
    require(reply == bytes.fromhex(profile["identity_reply"]) and not b.erases and b.halted == "jump"
            and b.flash(BASE, len(installed)) == installed, "probe erased or did not return to the application")
    print("transfer: non-erasing probe returns to the application OK")


def check_shutdown(boot: bytes, ram: bytes, stock: bytes, patched: bytes) -> None:
    for name, image, expect_report in (("stock", stock, False), ("patched", patched, True)):
        for reason in (0x0A, 0x1C, 0x20):
            mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
            for base in (0x00000000, 0x08000000):
                mu.mem_map(base, 0x40000)
                mu.mem_write(base, boot)
                mu.mem_write(base + 0x8000, image)
            mu.mem_map(0x20000000, 0x10000)
            mu.mem_write(0x20000000, ram)
            for base, size in ((0x40000000, 0x80000), (0x50000000, 0x40000), (0xE0000000, 0x100000)):
                mu.mem_map(base, size)
            outcome = {}

            def hook(uc, address, size, user, outcome=outcome):
                if address == SHUTDOWN:
                    outcome["reentered"] = uc.reg_read(UC_ARM_REG_R0)
                    uc.emu_stop()
                elif address == SHUTDOWN_REPORT:
                    outcome["reported"] = uc.reg_read(UC_ARM_REG_R2)
                    uc.emu_stop()

            mu.hook_add(UC_HOOK_CODE, hook)
            mu.reg_write(UC_ARM_REG_SP, 0x2000FC00)
            mu.reg_write(UC_ARM_REG_R0, reason)
            mu.emu_start(SHUTDOWN_HANDLERS | 1, 0xFFFFFFFF, count=5_000_000)
            if expect_report:
                require(outcome.get("reported") == reason, f"patched shutdown reason {reason} not reported: {outcome}")
            else:
                require("reentered" in outcome, f"stock shutdown reason {reason} did not reproduce the fault: {outcome}")
        print(f"shutdown: {name} {'reports the original reason' if expect_report else 're-enters shutdown()'} OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stock-image", type=Path, required=True)
    parser.add_argument("--bootloader-backup", type=Path)
    parser.add_argument("--ram-dump", type=Path, help="64 KiB RAM read from a configured Box; needs --bootloader-backup")
    args = parser.parse_args()
    if args.ram_dump and not args.bootloader_backup:
        parser.error("--ram-dump needs --bootloader-backup")
    try:
        manifest = load_manifest()
        stock = args.stock_image.read_bytes()
        patched = check_image(stock, manifest)
        check_rfid(patched)
        if args.bootloader_backup:
            boot = args.bootloader_backup.read_bytes()
            require(tool.digest(boot) in BOOTLOADER_HOOKS, "bootloader backup is not an admitted build")
            check_transfers(boot, stock, patched, manifest)
            if args.ram_dump:
                check_shutdown(boot, args.ram_dump.read_bytes(), stock, patched)
    except CheckFailed as failure:
        print(f"Box firmware check failed: {failure}", file=sys.stderr)
        return 1
    print("Box firmware checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
