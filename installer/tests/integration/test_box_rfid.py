from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from installer.tests.helpers import REPO_ROOT

MODULES = REPO_ROOT / "installer/klipper/tltg"
sys.path.insert(0, str(MODULES))
try:
    spec = importlib.util.spec_from_file_location("tltg_box_rfid", MODULES / "box_rfid.py")
    box_rfid = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(box_rfid)
finally:
    sys.path.remove(str(MODULES))

# Real tags read through a Max 4 Box (first 9 bytes, the UID, replaced): pages 0..44.
ANYCUBIC = bytes.fromhex(
    "04a1b2c3d4e5f60708480000e11012007b0065004148485342522d3130360000"
    "00000000000000004143000000000000000000000000000000000000504c4120"
    "48696768205370656564000000000000ff0006e1000000000000000032009600"
    "be00d20096002c01d200e6002c015802e600040137004100af004a01e8030000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000bd04000004470000000000000000000000"
)
ELEGOO = bytes.fromhex(
    "04a1b2c3d4e5f60708480000e11012000103a00c34030fd1010b5502656c6567"
    "6f6f2e636f6dfe00000000000000000000000000000000000000000000000000"
    "36eeeeeeee0000000080766500000000ff0000ff00be00e60000000000af03e8"
    "0036c80000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "000000bd040000ff000500000000000000000000"
)
QIDI_BLOCK = bytes.fromhex("41001100ff000000000000000000000a")
# Bambu Lab PLA Silk Multi-Color data blocks (layout from the Bambu Lab RFID Tag Guide).
BAMBU_BLOCKS = {
    1: b"A05-M1\0\0GFA05\0\0\0", 2: b"PLA".ljust(16, b"\0"), 4: b"PLA Silk".ljust(16, b"\0"),
    5: bytes.fromhex("ec008cfffa0000000000e03f00000000"), 6: bytes.fromhex("3700080001002d00dc00dc0000000000"),
    9: bytes.fromhex("d7ac3b89a16b47c4b061728044e1f2d5"), 16: bytes.fromhex("02000200ff0a4dff0000000000000000"),
}


class Tag:
    def __init__(self, kind, memory=b"", key=b"\xff" * 6, block4=bytes(16), blocks=None, bambu=False):
        self.kind, self.key = kind, key
        self.uid = bytes.fromhex("04a1b2c3d4e5f6") if kind == "ntag" else bytes.fromhex("c0ffee42")
        self.memory = bytearray(memory.ljust(180, b"\0"))
        self.blocks = {4: bytearray(block4), **(blocks or {})}
        self.sector_keys = box_rfid.bambu_keys(self.uid) if bambu else [key] * 16


class Reader:
    """fm17550_select and fm17550_transceive answered from a tag model."""

    def __init__(self, tag=None):
        self.tag, self.crypto, self.halted, self.log, self.lost_after = tag, False, False, [], None

    def select(self, args):
        self.crypto = self.halted = False
        tag = self.tag
        if tag is None:
            return {"status": 0, "data": bytes(20)}
        if len(tag.uid) == 4:
            part = tag.uid + bytes([tag.uid[0] ^ tag.uid[1] ^ tag.uid[2] ^ tag.uid[3]])
            return {"status": 1, "data": b"\x04\x00" + part + bytes(10) + b"\x08\x00\x00"}
        one, two = b"\x88" + tag.uid[:3], tag.uid[3:]
        bcc = lambda p: bytes([p[0] ^ p[1] ^ p[2] ^ p[3]])
        return {"status": 1, "data": b"\x44\x00" + one + bcc(one) + two + bcc(two) + bytes(5) + b"\x04\x00\x00"}

    def transceive(self, args):
        command, frame = args[1][0], bytes(args[1][2:])
        self.log.append(frame)
        tag = self.tag

        def reply(data=b"", ok=True, bits=None):
            bits = len(data) * 8 if bits is None else bits
            return {"status": 1 if ok else 0,
                    "data": bytes([0 if ok else 0x20, 0x08 if self.crypto else 0, bits & 0xFF, bits >> 8]) + data}

        if self.halted or self.lost_after is not None and len(self.log) > self.lost_after:
            return reply(ok=False)
        if command == 0x0E:
            # A refused login leaves the tag silent until it is selected again.
            self.crypto = (tag.kind == "classic" and frame[2:8] == tag.sector_keys[frame[1] // 4]
                           and frame[8:12] == tag.uid)
            self.halted = not self.crypto
            return reply(ok=self.crypto)
        if tag.kind == "ntag":
            if frame == b"\x60":
                return reply(bytes.fromhex("0053040201000f03"))
            if frame[0] == 0x30:
                start = frame[1] * 4
                return reply(bytes(tag.memory[(start + i) % 180] for i in range(16)))
        elif self.crypto and frame[0] == 0x30:
            return reply(bytes(tag.blocks.get(frame[1], bytes(16))))
        return reply(ok=False)


class Query:
    def __init__(self, handler):
        self.handler = handler

    def send(self, args):
        return self.handler(args)


class Mcu:
    def __init__(self, reader, firmware="02.03.01.21-tltg-rfid1"):
        self.reader, self.firmware = reader, firmware

    def lookup_query_command(self, msg, response, **kwargs):
        if "rfid" not in self.firmware:
            raise LookupError(msg)
        return Query(self.reader.select if msg.startswith("fm17550_select") else self.reader.transceive)

    def get_name(self):
        return "mcu_box2"

    def get_status(self, eventtime=None):
        return {"mcu_version": self.firmware}


class Spi:
    def get_command_queue(self):
        return None


class BoxRfid:
    """QIDI's box_rfid as the module sees it."""

    def __init__(self, mcu):
        self.oid, self.mcu, self.spi, self.read_rfid_timer = 21, mcu, Spi(), None
        self.fm17550_read_card = Query(lambda args: {"status": 1, "data": b"stale QIDI buffer"})


class Reactor:
    def __init__(self):
        self.now = 0.

    def mutex(self):
        return contextlib.nullcontext()

    def monotonic(self):
        return self.now

    def pause(self, until):
        self.now = until


class CommandError(Exception):
    pass


class Gcmd:
    error = CommandError

    def __init__(self, **params):
        self.params, self.messages = params, []

    def get_int(self, name, default=None, **kwargs):
        return int(self.params.get(name, default))

    def get_float(self, name, default=None, **kwargs):
        return float(self.params.get(name, default))

    def respond_info(self, message):
        self.messages.append(message)


class Printer:
    def __init__(self, rfid, log_dir):
        self.rfid, self.log_dir, self.reactor, self.handlers, self.commands = rfid, log_dir, Reactor(), {}, {}

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return {"log_file": str(self.log_dir / "klippy.log")}

    def register_event_handler(self, event, handler):
        self.handlers[event] = handler

    def lookup_object(self, name):
        assert name == "gcode"
        return self

    def register_command(self, name, handler, desc=None):
        self.commands[name] = handler

    def lookup_objects(self, module):
        return [("box_rfid card_reader_4", self.rfid)]


class Config:
    def __init__(self, printer, directory):
        self.printer, self.directory = printer, directory

    def get_printer(self):
        return self.printer

    def get(self, name, default=None):
        return str(self.directory) if name == "database_dir" else default


def entry(filament_id, vendor="QIDI"):
    return {"type": "PLA", "filament_id": filament_id, "filament_vendor": vendor, "nozzle_temperature": [190, 240]}


class BoxRfidTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="box-rfid-"))
        self.database = self.root / "filament_config"
        self.database.mkdir()
        self.existing = {"QIDI PLA Basic": entry("QFA00"), "Generic PLA": entry("QFA90", "Generic"),
                         "Elegoo PLA": entry("QFA94", "Elegoo"), "Mine": entry("QFX00", "Mine")}
        for nozzle in ("0.2", "0.4", "0.6", "0.8"):
            (self.database / f"filament-{nozzle}.json").write_text(json.dumps(self.existing))

    def module(self, tag=None, firmware="02.03.01.21-tltg-rfid1"):
        self.reader = Reader(tag)
        self.rfid = BoxRfid(Mcu(self.reader, firmware))
        self.printer = Printer(self.rfid, self.root)
        module = box_rfid.load_config(Config(self.printer, self.database))
        self.printer.handlers["klippy:ready"]()
        return module

    def qidi_read(self):
        return self.rfid.fm17550_read_card.send([self.rfid.oid])

    def reports(self):
        return sorted(self.root.glob("tltg_rfid_*.json"))

    def test_catalog_adds_only_missing_filaments_and_never_changes_existing_ones(self):
        self.module()
        catalog = box_rfid.CATALOG["filaments"]
        for nozzle in ("0.2", "0.4", "0.6", "0.8"):
            entries = json.loads((self.database / f"filament-{nozzle}.json").read_text())
            for name, value in self.existing.items():
                self.assertEqual(entries[name], value)
            expected = {name for name, item in catalog.items() if nozzle in item["nozzles"]
                        and name not in self.existing and item["entry"]["filament_id"] != "QFX00"}
            self.assertEqual(set(entries) - set(self.existing), expected)
            ids = [value["filament_id"] for value in entries.values()]
            self.assertEqual(len(ids), len(set(ids)))
        before = {path: path.read_bytes() for path in self.database.iterdir()}
        self.module()
        self.assertEqual(before, {path: path.read_bytes() for path in self.database.iterdir()})

    def test_vendor_tags_become_qidi_blocks_for_their_database_entries(self):
        self.module(Tag("ntag", ANYCUBIC))
        block = self.qidi_read()["data"]
        self.assertTrue(box_rfid.qidi_block_valid(block))
        self.assertEqual(block[:6], b"X" + bytes([16, 0x11]) + bytes.fromhex("e10600"))
        self.assertEqual(box_rfid.CATALOG["filaments"]["Anycubic PLA High Speed"]["entry"]["filament_id"], "QFX16")
        # A tag that leaves the field mid-read reads as no tag, so QIDI keeps polling.
        self.reader.lost_after = len(self.reader.log) + 2
        self.assertEqual(self.qidi_read(), {"oid": 21, "status": 0, "data": b""})
        self.module(Tag("ntag", ELEGOO))
        # The operator's own Elegoo PLA entry wins over the catalog's.
        self.assertEqual(self.qidi_read()["data"][:6], b"A" + bytes([94, 0x11]) + bytes.fromhex("ff0000"))
        # Bambu tags open with keys from their UID; both of a two-color spool's colors are kept.
        self.module(Tag("classic", blocks=BAMBU_BLOCKS, bambu=True))
        silk = box_rfid.CATALOG["filaments"][box_rfid.CATALOG["bambu_ids"]["GFA05"]]["entry"]["filament_id"]
        self.assertEqual(self.qidi_read()["data"][:9],
                         silk[2].encode() + bytes([int(silk[3:]), 0x12]) + bytes.fromhex("ec008cff4d0a"))
        self.reader.lost_after = len(self.reader.log) + 4
        self.assertEqual(self.qidi_read(), {"oid": 21, "status": 0, "data": b""})
        # A copy on a blank tag that kept the default key reads as Bambu, never as
        # a QIDI block made of its "PLA Silk" text, which QIDI's parser would accept.
        self.assertTrue(box_rfid.qidi_block_valid(BAMBU_BLOCKS[4]))
        self.module(Tag("classic", blocks=BAMBU_BLOCKS, block4=BAMBU_BLOCKS[4]))
        self.assertEqual(self.qidi_read()["data"][:3], silk[2].encode() + bytes([int(silk[3:]), 0x12]))
        # A Bambu filament the catalog lacks falls back to the brand's base material.
        newer = {**BAMBU_BLOCKS, 1: b"A99-K0\0\0GFA99\0\0\0", 4: b"PLA Future".ljust(16, b"\0")}
        self.existing["Bambu PLA"] = entry("QFL30", "Bambu")
        (self.database / "filament-0.4.json").write_text(json.dumps(self.existing))
        self.module(Tag("classic", blocks=newer, bambu=True))
        self.assertEqual(self.qidi_read()["data"][:3], b"L" + bytes([30, 0x12]))
        self.assertEqual(self.reports(), [])

    def test_qidi_tags_read_as_stock_and_unusable_tags_never_return_old_data(self):
        self.module(Tag("classic", block4=QIDI_BLOCK))
        self.assertEqual(self.qidi_read(), {"oid": 21, "status": 1, "data": QIDI_BLOCK})
        self.module(Tag("classic", key=b"\x12" * 6))
        for _ in range(3):
            self.assertEqual(self.qidi_read()["data"], bytes(16))
        unknown = Tag("ntag", bytes(16) + b"\x99" * 112)
        self.module(unknown)
        self.assertEqual(self.qidi_read()["data"], bytes(16))
        self.module()
        self.assertEqual(self.qidi_read(), {"oid": 21, "status": 0, "data": b""})
        reports = [json.loads(path.read_text()) for path in self.reports()]
        self.assertEqual(sorted(report["reason"] for report in reports), ["locked", "unknown"])
        for report in reports:
            self.assertNotIn(unknown.uid.hex(), json.dumps(report))
            self.assertNotIn("c0ffee42", json.dumps(report))

    def test_stock_box_firmware_keeps_qidis_read(self):
        self.module(Tag("ntag", ANYCUBIC), firmware="02.03.01.21-tltg-shutdownfix1")
        self.assertEqual(self.qidi_read()["data"], b"stale QIDI buffer")
        with self.assertRaises(CommandError):
            self.printer.commands["TLTG_BOX_RFID_REPORT"](Gcmd(SLOT=7))

    def test_report_command_saves_the_whole_tag_without_its_uid(self):
        self.module(Tag("ntag", ANYCUBIC))
        gcmd = Gcmd(SLOT=7)
        self.printer.commands["TLTG_BOX_RFID_REPORT"](gcmd)
        path, = self.reports()
        report = json.loads(path.read_text())
        self.assertIn("_2D_", path.name)
        self.assertEqual(report["read"]["decoded"], {"brand": "Anycubic", "material": "PLA High Speed",
                                                     "colors": ["E10600"]})
        pages = bytes.fromhex(report["read"]["pages"])
        self.assertEqual(len(pages), 180)
        self.assertEqual(pages[:9], bytes(9))
        self.assertEqual(pages[9:], ANYCUBIC[9:])
        self.assertEqual(report["ident"], {"atqa": "4400", "uid_bytes": 7, "sak": "0400"})
        self.assertIn("Anycubic PLA High Speed", gcmd.messages[-1])
        self.module(Tag("classic", blocks=BAMBU_BLOCKS, bambu=True))
        self.printer.commands["TLTG_BOX_RFID_REPORT"](Gcmd(SLOT=7))
        bambu, = set(self.reports()) - {path}
        report = json.loads(bambu.read_text())
        self.assertEqual(report["read"]["decoded"], {"brand": "Bambu", "material": "PLA Silk",
                                                     "colors": ["EC008C", "FF4D0A"]})
        sectors = report["read"]["readable_sectors"]
        self.assertEqual(len(sectors), 16)
        self.assertEqual(sectors["1"]["blocks"][2], BAMBU_BLOCKS[6].hex())
        self.assertNotIn(BAMBU_BLOCKS[9].hex(), json.dumps(report))  # the spool's serial number
        self.assertNotIn("c0ffee42", json.dumps(report))
        self.module(Tag("classic", blocks=BAMBU_BLOCKS, block4=BAMBU_BLOCKS[4]))
        self.printer.commands["TLTG_BOX_RFID_REPORT"](Gcmd(SLOT=7))
        report = json.loads(max(self.reports(), key=lambda p: p.stat().st_mtime_ns).read_text())
        self.assertEqual(report["read"]["decoded"]["material"], "PLA Silk")
        self.assertNotIn(BAMBU_BLOCKS[9].hex(), json.dumps(report))
        self.module()
        with self.assertRaises(CommandError):
            self.printer.commands["TLTG_BOX_RFID_REPORT"](Gcmd(SLOT=7, SECONDS=1))


if __name__ == "__main__":
    unittest.main()
