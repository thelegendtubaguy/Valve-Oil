from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
import tempfile
import types
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
# A Creality Hyper PLA tag (orange), UID c0ffee42: sector 1's key and blocks 4-6 as OpenSSL computes them
# with Creality's two AES keys (the record is AB124 0276 A2 101001 0FF6600 0330 000001 and 14 zeros).
CREALITY_KEY = bytes.fromhex("019c0258bc0b")
CREALITY_BLOCKS = bytes.fromhex("57f25b78076d4c1797b1be35ca26954029b16ee6fe72f9f0b00ebfac8fc87c08"
                                "fac8f07509292df943d4cdf64cba06a1")
# TigerTag's own example (Rosa3D red PLA, Basic), pages 4-23 after a blank NTAG's first four pages. The
# example's brand reads 0x4E19, but Rosa3D's ID is 19961, 0x4DF9.
TIGERTAG = (bytes.fromhex("04a1b2c3d4e5f60708480000e1101200")
            + bytes.fromhex("5bf59264" "ffffffff" "954b6800" "8e384df9" "ff0000ff" "0003e815" "00c300e6"
                            "32053c3c" "2f7a0a5c" "00000000" "00000000" "00e60000") + b"Starter Red".ljust(28, b"\0"))


def opentag3d(brand, material, modifier, colors):
    """An NTAG215 holding an OpenTag3D NDEF record (spec 2.004 memory map)."""
    payload = bytearray(0x60)
    payload[0:2] = (2004).to_bytes(2, "big")
    payload[0x02:0x02 + len(material)] = material.encode()
    payload[0x07:0x07 + len(modifier)] = modifier.encode()
    payload[0x0C:0x0C + len(brand)] = brand.encode()
    for i, color in enumerate(colors):
        payload[0x3C + 4 * i:0x40 + 4 * i] = bytes.fromhex(color) + b"\xff"
    mime = b"application/opentag3d"
    record = bytes([0xD2, len(mime), len(payload)]) + mime + bytes(payload)
    return (bytes.fromhex("04a1b2c3d4e5f60708480000e1103e00" "0103a00c34")
            + bytes([0x03, len(record)]) + record + b"\xfe")


# A new NTAG213: capability container, lock control and empty NDEF TLVs, AUTH0 = 0xff.
BLANK_NTAG = (bytes.fromhex("04a1b2c3d4e5f60708480000e1101200" "0103a00c340300fe").ljust(164, b"\0")
              + bytes.fromhex("040000ff00000000"))
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
        self.pending = None

    def select(self, args):
        self.crypto = self.halted = False
        self.pending = None
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
        ack = {"status": 1, "data": bytes([0, 0x08 if self.crypto else 0, 4, 0, 0x0A])}
        if self.crypto and self.pending is not None:
            tag.blocks[self.pending], self.pending = bytearray(frame), None
            return ack
        if self.crypto and frame[0] == 0xA0:
            self.pending = frame[1]
            return ack
        if tag.kind == "ntag":
            if frame[0] == 0xA2:
                tag.memory[frame[1] * 4:frame[1] * 4 + 4] = frame[2:6]
                return ack
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
    def __init__(self, reader, firmware="02.03.01.21-valve-oil.1"):
        self.reader, self.firmware = reader, firmware

    def lookup_query_command(self, msg, response, **kwargs):
        if not self.firmware.startswith("02.03.01.21-valve-oil"):
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
    NEVER = 9e99

    def __init__(self):
        self.now, self.timers = 0., []

    def register_timer(self, callback, waketime):
        self.timers.append([callback, waketime])

    def advance(self, seconds):
        """Run due timers, as Klipper's reactor does, up to `seconds` from now."""
        end = self.now + seconds
        while True:
            due = min(self.timers, key=lambda timer: timer[1], default=None)
            if due is None or due[1] > end:
                break
            self.now = due[1]
            due[1] = due[0](self.now)
        self.now = end

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

    def get(self, name, default=None):
        if name not in self.params and default is None:
            raise CommandError("missing " + name)
        return str(self.params.get(name, default))

    def get_int(self, name, default=None, **kwargs):
        return int(self.params.get(name, default))

    def get_float(self, name, default=None, **kwargs):
        return float(self.params.get(name, default))

    def respond_info(self, message):
        self.messages.append(message)


class Controller:
    """QIDI's multi_color_controller before its klippy:ready callback has run."""

    def __init__(self, printer):
        self.save_vars = self.box_adapter = None
        self.init_reads = 0
        printer.commands["INIT_RFID_READ"] = self.cmd_READ_RFID_INIT

    def ready(self):
        self.save_vars = self.box_adapter = types.SimpleNamespace(allVariables={}, box_count=1)

    def cmd_READ_RFID_INIT(self, gcmd):
        self.save_vars.allVariables, self.box_adapter.box_count  # raises until ready, as QIDI's does
        self.init_reads += 1


class Printer:
    def __init__(self, rfid, log_dir):
        self.rfid, self.log_dir, self.reactor, self.handlers, self.commands = rfid, log_dir, Reactor(), {}, {}
        self.shutdown = False
        self.command_error = CommandError
        self.controller = Controller(self)
        # Slot 7's spool sits on the rollers with no filament fed, unless a test feeds it.
        self.save_variables = types.SimpleNamespace(allVariables={"slot7": 0, "filament_slot7": "QFA90",
                                                                  "color_slot7": ["228332"]})
        self.slot7 = types.SimpleNamespace(r_endstop_state=1)
        self.jog = Jog(self)

    def is_shutdown(self):
        return self.shutdown

    def run_script(self, script):
        self.commands[script](Gcmd())

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return {"log_file": str(self.log_dir / "klippy.log")}

    def register_event_handler(self, event, handler):
        self.handlers[event] = handler

    def lookup_object(self, name, default=None):
        return {"gcode": self, "multi_color_controller": self.controller, "save_variables": self.save_variables,
                "box_stepper slot7": self.slot7, "tltg_box_slot_jog": self.jog}.get(name, default)

    def register_command(self, name, handler, desc=None):
        previous = self.commands.pop(name, None)
        if handler is not None:
            self.commands[name] = handler
        return previous

    def lookup_objects(self, module):
        return [("box_rfid card_reader_4", self.rfid)]


class Jog:
    """tltg_box_slot_jog: turns a slot in PIECE mm moves, calling UNTIL after each."""

    def __init__(self, printer):
        self.printer, self.moves, self.on_turn = printer, [], None

    def blocker(self):
        return None

    def move(self, slot, distance, speed, until=None, piece=None):
        moved = 0.
        while abs(moved) < abs(distance):
            moved += piece if distance > 0 else -piece
            self.printer.reactor.now += piece / speed
            if self.on_turn:
                self.on_turn()
            if until():
                break
        self.moves.append((slot, moved))
        return moved, True


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

    def module(self, tag=None, firmware="02.03.01.21-valve-oil.1"):
        self.reader = Reader(tag)
        self.rfid = BoxRfid(Mcu(self.reader, firmware))
        self.printer = Printer(self.rfid, self.root)
        module = box_rfid.load_config(Config(self.printer, self.database))
        self.printer.handlers["klippy:connect"]()
        self.printer.handlers["klippy:ready"]()
        return module

    def qidi_read(self):
        return self.rfid.fm17550_read_card.send([self.rfid.oid])

    def reports(self):
        return sorted(self.root.glob("vo_rfid_*.json"))

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
            self.printer.commands["VO_BOX_RFID_REPORT"](Gcmd(SLOT=7))

    def test_startup_tag_read_waits_for_qidis_controller(self):
        self.module()
        printer, controller = self.printer, self.printer.controller
        printer.commands["INIT_RFID_READ"](Gcmd())  # QIDI's own would raise here and shut Klipper down
        printer.reactor.advance(10)
        self.assertEqual(controller.init_reads, 0)
        controller.ready()
        printer.reactor.advance(10)
        self.assertEqual(controller.init_reads, 1)
        printer.commands["INIT_RFID_READ"](Gcmd())
        self.assertEqual(controller.init_reads, 2)
        # A controller that never becomes ready: the read is left out.
        self.module()
        self.printer.commands["INIT_RFID_READ"](Gcmd())
        self.printer.reactor.advance(300)
        self.printer.controller.ready()
        self.printer.reactor.advance(300)
        self.assertEqual(self.printer.controller.init_reads, 0)

    def test_report_command_saves_the_whole_tag_without_its_uid(self):
        self.module(Tag("ntag", ANYCUBIC))
        gcmd = Gcmd(SLOT=7)
        self.printer.commands["VO_BOX_RFID_REPORT"](gcmd)
        path, = self.reports()
        report = json.loads(path.read_text())
        self.assertIn("_2D_", path.name)
        self.assertEqual(report["read"]["decoded"], {"format": "anycubic", "brand": "Anycubic",
                                                     "material": "PLA High Speed", "colors": ["E10600"]})
        pages = bytes.fromhex(report["read"]["pages"])
        self.assertEqual(len(pages), 180)
        self.assertEqual(pages[:9], bytes(9))
        self.assertEqual(pages[9:], ANYCUBIC[9:])
        self.assertEqual(report["ident"], {"atqa": "4400", "uid_bytes": 7, "sak": "0400"})
        self.assertIn("Anycubic PLA High Speed", gcmd.messages[-1])
        self.module(Tag("classic", blocks=BAMBU_BLOCKS, bambu=True))
        self.printer.commands["VO_BOX_RFID_REPORT"](Gcmd(SLOT=7))
        bambu, = set(self.reports()) - {path}
        report = json.loads(bambu.read_text())
        self.assertEqual(report["read"]["decoded"], {"format": "bambu", "brand": "Bambu", "material": "PLA Silk",
                                                     "colors": ["EC008C", "FF4D0A"]})
        sectors = report["read"]["readable_sectors"]
        self.assertEqual(len(sectors), 16)
        self.assertEqual(sectors["1"]["blocks"][2], BAMBU_BLOCKS[6].hex())
        self.assertNotIn(BAMBU_BLOCKS[9].hex(), json.dumps(report))  # the spool's serial number
        self.assertNotIn("c0ffee42", json.dumps(report))
        self.module(Tag("classic", blocks=BAMBU_BLOCKS, block4=BAMBU_BLOCKS[4]))
        self.printer.commands["VO_BOX_RFID_REPORT"](Gcmd(SLOT=7))
        report = json.loads(max(self.reports(), key=lambda p: p.stat().st_mtime_ns).read_text())
        self.assertEqual(report["read"]["decoded"]["material"], "PLA Silk")
        self.assertNotIn(BAMBU_BLOCKS[9].hex(), json.dumps(report))
        self.module()
        with self.assertRaises(CommandError):
            self.printer.commands["VO_BOX_RFID_REPORT"](Gcmd(SLOT=7, SECONDS=1))
        # The command's name before 26.11.16.1 still works.
        self.assertEqual(self.printer.commands["TLTG_BOX_RFID_REPORT"], self.printer.commands["VO_BOX_RFID_REPORT"])

    def test_test_tool_writes_only_blank_and_qidi_tags_and_reads_them_back(self):
        test = lambda **params: self.printer.commands["VO_BOX_RFID_TEST"](Gcmd(SLOT=7, **params))
        # A new NTAG213: the QIDI block and the entry's name, read back, then read by QIDI's poll.
        ntag = Tag("ntag", BLANK_NTAG)
        module = self.module(ntag)
        test(ACTION="SCAN", SECONDS=1)
        seen, = module.get_status(0)["job"]["tags"]
        self.assertEqual((seen["type"], seen["layout"], seen["writable"]), ("ntag213", "blank", True))
        self.assertNotIn(ntag.uid.hex(), json.dumps(module.get_status(0)))
        test(ACTION="WRITE", FILAMENT="QFX00", COLORS="ff0000,00FF00", NAME=1, TAG=seen["tag"])
        result = module.get_status(0)["job"]["result"]
        self.assertTrue(result["verified"])
        self.assertEqual(bytes(ntag.memory[16:32]), box_rfid.qidi_block("QFX00", ["FF0000", "00FF00"]))
        self.assertEqual(self.qidi_read()["data"][:2], b"X\x00")
        # Another printer with that name under another ID gives the slot its own ID.
        for nozzle in ("0.2", "0.4", "0.6", "0.8"):
            (self.database / f"filament-{nozzle}.json").write_text(json.dumps({**self.existing, "Mine": entry("QFX07", "Mine")}))
        module = self.module(ntag)
        self.assertEqual(self.qidi_read()["data"][:2], b"X\x07")
        # Rewriting without the name clears it, so the ID on the tag counts again.
        test(ACTION="WRITE", FILAMENT="QFA90", COLORS="FFFFFF")
        self.assertEqual(bytes(ntag.memory[16:64]), box_rfid.qidi_block("QFA90", ["FFFFFF"]) + bytes(32))
        # A blank MIFARE Classic 1K tag: blocks 4-6, which QIDI's own read takes.
        classic = Tag("classic")
        self.module(classic)
        test(ACTION="WRITE", FILAMENT="QFA00", COLORS="0000FF", NAME=1)
        self.assertEqual(bytes(classic.blocks[4]), box_rfid.qidi_block("QFA00", ["0000FF"]))
        self.assertEqual(self.qidi_read()["data"], bytes(classic.blocks[4]))
        # Another brand's tag, a Bambu Lab tag, and a locked tag are never written.
        for tag in (Tag("ntag", ELEGOO), Tag("ntag", ANYCUBIC), Tag("classic", blocks=BAMBU_BLOCKS, bambu=True),
                    Tag("classic", blocks=BAMBU_BLOCKS, block4=BAMBU_BLOCKS[4]), Tag("classic", key=b"\x12" * 6)):
            before = (bytes(tag.memory), {b: bytes(v) for b, v in tag.blocks.items()})
            self.module(tag)
            with self.assertRaises(CommandError):
                test(ACTION="WRITE", FILAMENT="QFA00", COLORS="0000FF")
            self.assertEqual(before, (bytes(tag.memory), {b: bytes(v) for b, v in tag.blocks.items()}))
        # The operator command writes the slot's own filament with its name, turning an empty slot's
        # spool backward until the tag comes past, and only by hand once filament is fed.
        spool = Tag("ntag", BLANK_NTAG)
        self.module()
        self.printer.jog.on_turn = lambda: setattr(self.reader, "tag", spool)  # the tag comes past
        gcmd = Gcmd(SLOT=7)
        self.printer.commands["VO_BOX_RFID_WRITE"](gcmd)
        self.assertEqual(bytes(spool.memory[16:64]), box_rfid.qidi_block("QFA90", ["228332"])
                         + box_rfid.name_field("Generic PLA"))
        self.assertEqual(self.printer.jog.moves, [(7, -10.)])
        self.assertIn("Generic PLA", gcmd.messages[-1])
        self.printer.slot7.r_endstop_state = 0
        self.printer.commands["VO_BOX_RFID_WRITE"](Gcmd(SLOT=7, FILAMENT="QFX00", COLORS="000000"))
        self.assertEqual(self.printer.jog.moves, [(7, -10.)])
        self.assertEqual(bytes(spool.memory[16:22]), box_rfid.qidi_block("QFX00", ["000000"])[:6])
        # Limited to one tag, a write leaves every other tag alone.
        other = Tag("ntag", BLANK_NTAG)
        self.module(other)
        with self.assertRaises(CommandError):
            test(ACTION="WRITE", FILAMENT="QFA00", COLORS="0000FF", TAG="00000000", SECONDS=1)
        self.assertEqual(bytes(other.memory), BLANK_NTAG.ljust(180, b"\0"))

    def test_creality_tigertag_and_opentag3d_tags_set_their_filaments(self):
        # Creality: the key from the UID opens sector 1 (as key B here) and the record names Hyper PLA.
        self.assertEqual(box_rfid.creality_key(bytes.fromhex("c0ffee42")), CREALITY_KEY)
        blocks = {4 + i: CREALITY_BLOCKS[16 * i:16 * i + 16] for i in range(3)}
        creality = Tag("classic", key=CREALITY_KEY, block4=blocks[4], blocks=blocks)
        module = self.module(creality)
        hyper = box_rfid.CATALOG["filaments"]["Creality Hyper PLA"]["entry"]["filament_id"]
        self.assertEqual(self.qidi_read()["data"][:6], hyper[2].encode() + bytes([int(hyper[3:]), 0x11])
                         + bytes.fromhex("ff6600"))
        # A Creality tag is never written over.
        self.printer.commands["VO_BOX_RFID_TEST"](Gcmd(SLOT=7, ACTION="SCAN", SECONDS=1))
        self.assertEqual(module.get_status(0)["job"]["tags"][0]["layout"], "creality")
        # TigerTag: Rosa3D, which the database lacks, gives Generic PLA, with the tag's red.
        self.module(Tag("ntag", TIGERTAG))
        self.assertEqual(self.qidi_read()["data"][:6], b"A" + bytes([90, 0x11]) + bytes.fromhex("ff0000"))
        # OpenTag3D: the brand's entry by name, with every color; Bambu Lab is QIDI's Bambu.
        self.existing["Bambu PLA Silk"] = entry("QFL33", "Bambu")
        (self.database / "filament-0.4.json").write_text(json.dumps(self.existing))
        self.module(Tag("ntag", opentag3d("Bambu Lab", "PLA", "Silk", ["112233", "445566"])))
        self.assertEqual(self.qidi_read()["data"][:9], b"L" + bytes([33, 0x12]) + bytes.fromhex("112233445566"))
        self.assertEqual(self.reports(), [])


if __name__ == "__main__":
    unittest.main()
