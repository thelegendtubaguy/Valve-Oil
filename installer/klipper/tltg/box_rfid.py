# QIDI Box RFID tags for Valve Oil: Anycubic, Elegoo, and Bambu Lab spools,
# and those brands' filaments in QIDI's filament database.
#
# At startup, catalog entries (tltg_filament_catalog) that QIDI's filament database
# files lack are added to them. Existing entries, QIDI's or the operator's,
# are never changed, and no entry is added whose name or ID is already used.
#
# QIDI's box_rfid polls each reader with fm17550_read_card_cb and parses a
# 16-byte QIDI block. On a USB Box running the Valve Oil Box firmware
# (fm17550_select and fm17550_transceive), each poll is answered here instead:
#
# - MIFARE Classic tags: QIDI's block 4 login and read, so QIDI tags read as
#   they do in stock. When that login fails, or block 4 holds text instead
#   of a QIDI block, the tag is read as a Bambu Lab tag: with the keys
#   derived from its UID, or the default key on copies written to blank
#   tags. Bambu data is turned into a QIDI v1 block. A tag that opens to
#   neither returns empty data, never the reader's previous buffer.
# - NFC Type 2 tags: pages are read and Anycubic, Elegoo, or QIDI-layout data
#   is turned into a QIDI v1 block for the matching database entry.
#
# QIDI's parser, two-read confirmation, slot saving, touchscreen, and Fluidd
# behavior stay QIDI's. Boxes on any other firmware keep QIDI's read. The tag
# layouts are in openspec/observations/qidi-box/material-metadata.md.
#
# QIDI's INIT_GLOBALS runs INIT_RFID_READ 5 s into Klipper's ready callbacks.
# QIDI's controller takes its saved variables and Box adapter in its own
# ready callback, one of the last, and INIT_RFID_READ uses both unchecked.
# When an earlier callback waits past those 5 s the command raised an
# internal error that shut Klipper down. Here it runs once the controller
# has both, and is left out, with a log line, if that takes over a minute.

import hashlib
import hmac
import json
import logging
import os
import time

try:
    from .tltg_filament_catalog import CATALOG
except ImportError:  # imported outside Klipper's extras package
    from filament_catalog import CATALOG

DATABASE_DIR = "/home/qidi/QIDI_Client/resource/filament_config"
SELECT = ("fm17550_select oid=%c", "fm17550_select_return oid=%c status=%c data=%*s")
TRANSCEIVE = ("fm17550_transceive oid=%c data=%*s",
              "fm17550_transceive_return oid=%c status=%c data=%*s")
TRANSCEIVE_CMD, MFAUTHENT_CMD = 0x0C, 0x0E
DEFAULT_KEY = b"\xff" * 6
TYPE2_PAGE_COUNT = 28  # pages 4..31 hold Anycubic and Elegoo data
NTAG_PAGES = {0x0F: 45, 0x11: 135, 0x13: 231}  # GET_VERSION storage size byte: NTAG213/215/216
PUBLIC_KEYS = (("default", DEFAULT_KEY), ("mad", bytes.fromhex("a0a1a2a3a4a5")),
               ("ndef", bytes.fromhex("d3f7d3f7d3f7")))
MAX_AUTOMATIC_REPORTS = 20
INIT_READ = "INIT_RFID_READ"
INIT_READ_POLL, INIT_READ_WAIT = 0.25, 60.
ANYCUBIC_HEADER = b"\x7b\x00\x65\x00"
ELEGOO_HEADER = b"\x36\xee\xee\xee"
# Bambu Lab tags: MIFARE Classic 1K whose sector keys come from the UID.
BAMBU_SALT = bytes.fromhex("9a759cf2c4f7caff222cb9769b41bc96")
BAMBU_BLOCKS = (1, 2, 4, 5, 16)  # material IDs, type, detailed type, color, extra colors
BAMBU_PRIVATE_BLOCKS = (9,)  # the spool's serial number, left out of reports


def ascii_field(data):
    return data.split(b"\0", 1)[0].decode("ascii", "replace").strip()


def text_block(block):
    """True when BLOCK is NUL-padded printable ASCII, as Bambu's text fields
    are. QIDI v1 blocks are binary (byte 2 is 0x11..0x14)."""
    text = block.split(b"\0", 1)[0]
    return len(text) >= 2 and all(0x20 <= c < 0x7F for c in text) and not block[len(text):].strip(b"\0")


def qidi_block_valid(block):
    """True when QIDI's box_rfid parser accepts BLOCK (legacy or v1 layout)."""
    if len(block) < 16:
        return False
    if block[2] >> 4 == 0:
        return 1 <= block[0] <= 50
    return (0x41 <= block[0] <= 0x5A and block[1] <= 99 and block[2] & 0x0F <= 4
            and block[15] <= 127)


def qidi_block(filament_id, colors):
    """QIDI v1 block for filament ID QF<letter><two digits> and RGB hex colors."""
    colors = colors[:4]
    block = bytearray(16)
    block[0] = ord(filament_id[2])
    block[1] = int(filament_id[3:5])
    block[2] = 0x10 | len(colors)
    for i, color in enumerate(colors):
        block[3 + 3 * i:6 + 3 * i] = bytes.fromhex(color)
    return bytes(block)


def decode_anycubic(memory):
    """memory: pages 4.. as bytes. Returns (brand, material, [RRGGBB]) or None."""
    if memory[0:4] != ANYCUBIC_HEADER:
        return None
    material = ascii_field(memory[44:64])
    alpha, blue, green, red = memory[64:68]
    return "Anycubic", material, ["%02X%02X%02X" % (red, green, blue)]


def decode_elegoo(memory, catalog):
    offset = (16 - 4) * 4
    if memory[offset:offset + 4] != ELEGOO_HEADER:
        return None
    material = catalog["elegoo_materials"].get(memory[offset + 8:offset + 12].hex())
    subtype = catalog["elegoo_subtypes"].get(memory[offset + 12:offset + 14].hex())
    if material is None:
        return None
    red, green, blue = memory[offset + 16:offset + 19]
    return "Elegoo", subtype or material, ["%02X%02X%02X" % (red, green, blue)]


def bambu_keys(uid):
    """Bambu Lab's key A for each of the 16 sectors: HKDF-SHA256 (RFC 5869)
    of the tag's UID with Bambu's salt and the info string "RFID-A\\0"."""
    prk = hmac.new(BAMBU_SALT, uid, hashlib.sha256).digest()
    output, block = b"", b""
    for counter in (1, 2, 3):
        block = hmac.new(prk, block + b"RFID-A\0" + bytes([counter]), hashlib.sha256).digest()
        output += block
    return [output[6 * sector:6 * sector + 6] for sector in range(16)]


def decode_bambu(blocks, catalog):
    """blocks: {block: 16 bytes} for BAMBU_BLOCKS. Returns (brand, material,
    [RRGGBB], filament type) or None. The material is the catalog name for
    Bambu's filament ID (block 1), else the tag's detailed type."""
    if not text_block(blocks[2]):
        return None  # every Bambu tag names its filament type here
    material_id = ascii_field(blocks[1][8:16])
    filament_type = ascii_field(blocks[2])
    name = catalog["bambu_ids"].get(material_id)
    material = name[len("Bambu "):] if name else ascii_field(blocks[4]) or filament_type
    if not material:
        return None
    red, green, blue = blocks[5][0:3]
    colors = ["%02X%02X%02X" % (red, green, blue)]
    extra = blocks[16]
    if extra[0:2] == b"\x02\x00" and int.from_bytes(extra[2:4], "little") >= 2:
        alpha, blue, green, red = extra[4:8]
        colors.append("%02X%02X%02X" % (red, green, blue))
    return "Bambu", material, colors, filament_type


class FilamentDatabase:
    """QIDI's filament-<nozzle>.json files: catalog merge and name lookup."""

    def __init__(self, directory, catalog):
        self.directory = directory
        self.catalog = catalog
        self.cache = (None, {})

    def path(self, nozzle):
        return os.path.join(self.directory, "filament-%s.json" % (nozzle,))

    def merge(self):
        added = {}
        for nozzle in ("0.2", "0.4", "0.6", "0.8"):
            path = self.path(nozzle)
            try:
                with open(path) as handle:
                    entries = json.load(handle)
            except (OSError, ValueError):
                logging.exception("tltg_box_rfid: cannot read %s", path)
                continue
            used = {e.get("filament_id") for e in entries.values() if hasattr(e, "get")}
            new = {name: dict(item["entry"]) for name, item in self.catalog["filaments"].items()
                   if nozzle in item["nozzles"] and name not in entries
                   and item["entry"]["filament_id"] not in used}
            if not new:
                continue
            entries.update(new)
            temp = path + ".tltg-tmp"
            try:
                with open(temp, "w") as handle:
                    json.dump(entries, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                mode = os.stat(path).st_mode & 0o777
                os.chmod(temp, mode)
                os.replace(temp, path)
            except OSError:
                logging.exception("tltg_box_rfid: cannot update %s", path)
                if os.path.exists(temp):
                    os.unlink(temp)
                continue
            added[nozzle] = len(new)
        if added:
            logging.info("tltg_box_rfid: added catalog filaments %s", added)
        return added

    def entries(self):
        path = self.path("0.4")
        try:
            mtime = os.stat(path).st_mtime
            if self.cache[0] != mtime:
                with open(path) as handle:
                    self.cache = (mtime, json.load(handle))
        except (OSError, ValueError):
            logging.exception("tltg_box_rfid: cannot read %s", path)
        return self.cache[1]

    def find(self, brand, material):
        """ID of the database entry for BRAND's MATERIAL: exact name, then the
        brand's entry for the base material, then QIDI's Generic entry."""
        entries = self.entries()
        by_name = {name.lower(): entry for name, entry in entries.items() if hasattr(entry, "get")}
        base = material.split()[0] if material else ""
        for name in ("%s %s" % (brand, material), "%s %s" % (brand, base), "Generic %s" % (base,)):
            entry = by_name.get(name.lower())
            if entry is not None and len(entry.get("filament_id", "")) == 5:
                return name, entry["filament_id"]
        return None, None


class TagReader:
    """One Box reader: answers QIDI's fm17550_read_card_cb queries and reads
    tags for reports. A reactor mutex keeps the two from interleaving."""

    def __init__(self, owner, name, rfid, select, transceive, original):
        self.owner, self.name, self.rfid = owner, name, rfid
        self.select, self.transceive, self.original = select, transceive, original
        self.mutex = owner.reactor.mutex()
        self.last, self.last_tag = {}, {}  # latest poll, and latest poll that found a tag

    def _select(self):
        params = self.select.send([self.rfid.oid])
        return params["status"], bytes(params["data"])

    def _xfer(self, command, timeout, frame):
        params = self.transceive.send([self.rfid.oid, bytes([command, timeout]) + frame])
        data = bytes(params["data"])
        if len(data) < 4:
            return False, 0, b""
        bits = data[2] | data[3] << 8
        return params["status"] == 1, data[1], data[4:4 + (bits + 7) // 8]

    def _login(self, ident, block, key):
        """MIFARE Classic key A login to BLOCK; True when Crypto1 is on."""
        ok, status2, _ = self._xfer(MFAUTHENT_CMD, 2, bytes([0x60, block]) + key + uid_tail(ident))
        return ok and bool(status2 & 0x08)

    def _sector_login(self, ident, block, keys):
        """Log in to BLOCK's sector with the first of KEYS that opens it. The
        first try goes to the tag as it is; a refused login leaves the tag
        idle, so each later try selects it again. Returns the ident or None."""
        for attempt, key in enumerate(keys):
            if attempt:
                status, ident = self._select()
                if status != 1:
                    return None
            if self._login(ident, block, key):
                return ident
        return None

    def _bambu_blocks(self, ident):
        """Bambu blocks from the selected tag. Each sector opens with its key
        derived from the UID, tried again from a fresh select in case the
        reader can't switch sectors with Crypto1 on, or with the default key,
        which copies on blank tags may keep. Returns (blocks, None),
        (None, "locked") when no key opens sector 0, or (None, "lost") when
        the tag left the field mid-read."""
        keys = bambu_keys(uid_tail(ident))
        blocks, sector = {}, None
        for block in BAMBU_BLOCKS:
            if block // 4 != sector:
                sector = block // 4
                ident = self._sector_login(ident, block, (keys[sector], keys[sector], DEFAULT_KEY))
                if ident is None:
                    return None, "lost" if blocks else "locked"
            ok, _, data = self._xfer(TRANSCEIVE_CMD, 3, bytes([0x30, block]))
            if not ok or len(data) < 16:
                return None, "lost"
            blocks[block] = data[:16]
        return blocks, None

    def _type2_pages(self, first, count):
        memory = bytearray()
        for page in range(first, first + count, 4):
            ok, _, chunk = self._xfer(TRANSCEIVE_CMD, 3, bytes([0x30, page]))
            if not ok or len(chunk) < 16:
                return bytes(memory), page
            memory += chunk[:16]
        return bytes(memory[:count * 4]), None

    def _empty(self, status, oid):
        # QIDI's firmware sends no data with status 0 and 16 bytes otherwise.
        return {"oid": oid, "status": status, "data": b"" if status == 0 else bytes(16)}

    def _record(self, **info):
        self.last = dict(info, time=self.owner.reactor.monotonic())
        if info.get("tag"):
            self.last_tag = self.last

    def send(self, data, *args, **kwargs):
        oid = data[0]
        with self.mutex:
            try:
                return self._read(oid)
            except Exception:
                logging.exception("tltg_box_rfid: %s read failed; using QIDI's read", self.name)
                return self.original.send(data, *args, **kwargs)

    def _read(self, oid):
        status, ident = self._select()
        if status != 1:
            self._record(tag=None, status=status)
            return self._empty(status, oid)
        sak = tag_sak(ident)
        if sak & 0x08:
            block = None
            if self._login(ident, 4, DEFAULT_KEY):
                ok, _, data = self._xfer(TRANSCEIVE_CMD, 3, b"\x30\x04")
                if ok and len(data) >= 16:
                    block = data[:16]
                    if qidi_block_valid(block) and not text_block(block):
                        self._record(tag="mifare_classic", layout="qidi")
                        return {"oid": oid, "status": 1, "data": block}
                # The tag is still selected: try it as a Bambu tag below.
            else:
                status, ident = self._select()  # the refused login left the tag idle
                if status != 1:
                    self._record(tag="mifare_classic", layout="read_failed")
                    return self._empty(0, oid)
            blocks, failure = self._bambu_blocks(ident)
            if failure == "lost":
                self._record(tag="mifare_classic", layout="read_failed")
                return self._empty(0, oid)
            decoded = blocks and decode_bambu(blocks, self.owner.catalog)
            if decoded:
                data = {"blocks": {str(b): blocks[b].hex() for b in BAMBU_BLOCKS}}
                return self._answer(oid, ident, "mifare_classic", decoded, data)
            if block is None and blocks is None:
                self._record(tag="mifare_classic", layout="locked")
                self.owner.capture(self, ident, {}, "locked")
            else:
                self._record(tag="mifare_classic", layout="unknown")
                self.owner.capture(self, ident, {"block_4": (block or blocks[4]).hex()}, "unknown")
            return self._empty(1, oid)
        if sak & 0x7F:
            self._record(tag="sak_%02x" % (sak,), layout="unsupported")
            self.owner.capture(self, ident, {}, "unsupported")
            return self._empty(1, oid)
        memory, failed = self._type2_pages(4, TYPE2_PAGE_COUNT)
        if failed is not None:
            # The tag left the field mid-read: answer "no tag" so QIDI keeps polling
            # instead of ending the read as an unrecognized label.
            self._record(tag="type2", layout="read_failed", page=failed)
            return self._empty(0, oid)
        decoded = decode_anycubic(memory) or decode_elegoo(memory, self.owner.catalog)
        if decoded is None:
            if qidi_block_valid(memory[:16]):
                self._record(tag="type2", layout="qidi")
                return {"oid": oid, "status": 1, "data": memory[:16]}
            self._record(tag="type2", layout="unknown")
            self.owner.capture(self, ident, {"first_page": 4, "pages": memory.hex()}, "unknown")
            return self._empty(1, oid)
        return self._answer(oid, ident, "type2", decoded, {"first_page": 4, "pages": memory.hex()})

    def _answer(self, oid, ident, tag, decoded, data):
        """QIDI v1 block for a decoded vendor tag's database entry."""
        brand, material, colors = decoded[:3]
        name, filament_id = self.owner.database.find(brand, material)
        if filament_id is None and len(decoded) > 3:
            name, filament_id = self.owner.database.find(brand, decoded[3])  # Bambu's filament type
        self._record(tag=tag, layout=brand.lower(), material=material, colors=colors,
                     filament=name, filament_id=filament_id)
        if filament_id is None:
            self.owner.capture(self, ident, data, "no_database_entry")
            return self._empty(1, oid)
        return {"oid": oid, "status": 1, "data": qidi_block(filament_id, colors)}

    def full_read(self, ident):
        """Everything a report needs from the selected tag."""
        sak = tag_sak(ident)
        if sak & 0x08:
            sectors = {}
            keys = bambu_keys(uid_tail(ident))
            for sector in range(16):
                for name, key in PUBLIC_KEYS + (("bambu", keys[sector]),):
                    status, ident = self._select()
                    if status != 1 or not self._login(ident, sector * 4, key):
                        continue
                    blocks = []
                    for block in range(sector * 4, sector * 4 + 4):
                        ok, _, data = self._xfer(TRANSCEIVE_CMD, 3, bytes([0x30, block]))
                        blocks.append(data[:16].hex() if ok else None)
                    if sector == 0 and blocks[0]:
                        blocks[0] = "00" * 5 + blocks[0][10:]  # UID and BCC
                    sectors[sector] = {"key_a": name, "blocks": blocks}
                    break
            report = {"tag": "mifare_classic", "readable_sectors": sectors}
            found = {block: bytes.fromhex(sectors[block // 4]["blocks"][block % 4] or "")
                     for block in BAMBU_BLOCKS if block // 4 in sectors}
            decoded = (len(found) == len(BAMBU_BLOCKS) and all(len(b) == 16 for b in found.values())
                       and decode_bambu(found, self.owner.catalog))
            if decoded:
                report["decoded"] = {"brand": decoded[0], "material": decoded[1], "colors": decoded[2]}
            if decoded or any(sector["key_a"] == "bambu" for sector in sectors.values()):
                for block in BAMBU_PRIVATE_BLOCKS:
                    if sectors.get(block // 4, {}).get("blocks", [None] * 4)[block % 4]:
                        sectors[block // 4]["blocks"][block % 4] = "00" * 16
            return report
        if sak & 0x7F:
            return {"tag": "sak_%02x" % (sak,)}
        ok, _, version = self._xfer(TRANSCEIVE_CMD, 3, b"\x60")
        count = NTAG_PAGES.get(version[6], 16) if ok and len(version) >= 8 else 16
        status, ident = self._select()  # GET_VERSION fails on some tags and leaves them idle
        memory, failed = self._type2_pages(0, count)
        memory = bytes(9) + memory[9:] if len(memory) >= 9 else memory  # UID and BCC bytes
        report = {"tag": "type2", "get_version": version.hex() if ok else None,
                  "first_page": 0, "pages": memory.hex()}
        if failed is not None:
            report["unreadable_from_page"] = failed
        decoded = len(memory) >= 16 + TYPE2_PAGE_COUNT * 4 and (
            decode_anycubic(memory[16:]) or decode_elegoo(memory[16:], self.owner.catalog))
        if decoded:
            report["decoded"] = {"brand": decoded[0], "material": decoded[1], "colors": decoded[2]}
        return report


def tag_sak(ident):
    """SAK of the tag: the last cascade level's, without the cascade bit (0x04)."""
    return next((ident[17 + i] for i in range(3) if not ident[17 + i] & 0x04), ident[19])


def uid_tail(ident):
    """The four UID bytes a MIFARE Classic login uses: the last cascade level's."""
    level = next((i for i in range(3) if not ident[17 + i] & 0x04), 0)
    return ident[2 + 5 * level:6 + 5 * level]


def redacted_ident(ident):
    """ATQA, UID length, and SAKs; never UID bytes."""
    levels = next((i + 1 for i in range(3) if not ident[17 + i] & 0x04), 3)
    return {"atqa": ident[0:2].hex(), "uid_bytes": {1: 4, 2: 7, 3: 10}[levels],
            "sak": ident[17:17 + levels].hex()}


class BoxRfid:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.catalog = CATALOG
        self.database = FilamentDatabase(config.get("database_dir", DATABASE_DIR), self.catalog)
        self.added = self.database.merge()
        log_file = self.printer.get_start_args().get("log_file")
        self.report_dir = config.get("report_dir", os.path.dirname(log_file) if log_file else "/tmp")
        self.readers = {}
        self.captured = set()
        self.last_report = None
        self.printer.register_event_handler("klippy:connect", self._guard_init_read)
        self.printer.register_event_handler("klippy:ready", self._ready)
        self.printer.lookup_object("gcode").register_command(
            "TLTG_BOX_RFID_REPORT", self.cmd_TLTG_BOX_RFID_REPORT,
            desc="Read the spool tag in a QIDI Box slot into a shareable report")

    def _guard_init_read(self):
        gcode = self.printer.lookup_object("gcode")
        controller = self.printer.lookup_object("multi_color_controller", None)
        if not all(hasattr(controller, attr) for attr in ("save_vars", "box_adapter")):
            return
        original = gcode.register_command(INIT_READ, None)
        if original is None:
            return

        def started():
            return controller.save_vars is not None and controller.box_adapter is not None

        def cmd_INIT_RFID_READ(gcmd):
            if started():
                return original(gcmd)
            logging.info("tltg_box_rfid: %s ran before QIDI's Box controller was ready; waiting for it", INIT_READ)
            deadline = self.reactor.monotonic() + INIT_READ_WAIT

            def retry(eventtime):
                if self.printer.is_shutdown():
                    return self.reactor.NEVER
                if started():
                    gcode.run_script(INIT_READ)
                    logging.info("tltg_box_rfid: %s ran once QIDI's Box controller was ready", INIT_READ)
                elif eventtime < deadline:
                    return eventtime + INIT_READ_POLL
                else:
                    logging.info("tltg_box_rfid: QIDI's Box controller still wasn't ready; %s left out", INIT_READ)
                return self.reactor.NEVER
            self.reactor.register_timer(retry, self.reactor.monotonic() + INIT_READ_POLL)
        gcode.register_command(INIT_READ, cmd_INIT_RFID_READ, desc=getattr(gcode, "gcode_help", {}).get(INIT_READ))

    def _ready(self):
        for name, rfid in self.printer.lookup_objects("box_rfid"):
            query = getattr(rfid, "fm17550_read_card", None)
            if query is None or isinstance(query, TagReader):
                continue
            try:
                cq = rfid.spi.get_command_queue()
                select = rfid.mcu.lookup_query_command(SELECT[0], SELECT[1], oid=rfid.oid, cq=cq)
                transceive = rfid.mcu.lookup_query_command(TRANSCEIVE[0], TRANSCEIVE[1], oid=rfid.oid, cq=cq)
            except Exception:
                continue  # QIDI's or earlier Valve Oil Box firmware: QIDI's read stays.
            reader = TagReader(self, name.split()[-1], rfid, select, transceive, query)
            rfid.fm17550_read_card = reader
            self.readers[reader.name] = reader
        logging.info("tltg_box_rfid: tag reading on %s", sorted(self.readers) or "no reader (stock Box firmware)")

    def _write_report(self, prefix, label, report):
        report = dict(report, created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        stem = os.path.join(self.report_dir, "tltg_rfid_%s_%s_%s" % (
            prefix, label, time.strftime("%Y%m%d-%H%M%S", time.gmtime())))
        for attempt in range(100):
            path = stem + ("-%d" % (attempt,) if attempt else "") + ".json"
            try:
                with open(path, "x") as handle:
                    json.dump(report, handle, indent=2, sort_keys=True)
                    handle.write("\n")
                break
            except FileExistsError:
                continue
            except OSError:
                logging.exception("tltg_box_rfid: cannot write %s", path)
                return None
        else:
            return None
        self.last_report = path
        logging.info("tltg_box_rfid: wrote spool tag report %s", path)
        return path

    def _base_report(self, reader, ident):
        mcu = reader.rfid.mcu
        return {"reader": reader.name, "box_mcu": mcu.get_name(),
                "box_firmware": mcu.get_status().get("mcu_version"), "ident": redacted_ident(ident)}

    def capture(self, reader, ident, data, reason):
        """Save a report for a tag QIDI's read could not use, once per tag contents."""
        key = (reason, json.dumps(redacted_ident(ident), sort_keys=True), json.dumps(data, sort_keys=True))
        if key in self.captured or len(self.captured) >= MAX_AUTOMATIC_REPORTS:
            return
        self.captured.add(key)
        report = self._base_report(reader, ident)
        report.update(reason=reason, data=data)
        self._write_report(reason, reader.name, report)

    def cmd_TLTG_BOX_RFID_REPORT(self, gcmd):
        slot = gcmd.get_int("SLOT", minval=0, maxval=15)
        seconds = gcmd.get_float("SECONDS", 60., minval=1., maxval=300.)
        label = "%d%s" % (slot // 4 + 1, "ABCD"[slot % 4])
        reader = self.readers.get("card_reader_%d" % (slot // 2 + 1))
        if reader is None:
            raise gcmd.error("Slot %s's Box does not run the Valve Oil Box firmware, which reading tags needs. "
                             "See docs/firmware.md." % (label,))
        if getattr(reader.rfid, "read_rfid_timer", None) is not None:
            raise gcmd.error("QIDI is reading slot %s's tag; try again when it finishes." % (label,))
        gcmd.respond_info("Reading slot %s's spool tag for up to %d s. Turn the spool slowly by hand, "
                          "with no filament fed." % (label, seconds))
        deadline = self.reactor.monotonic() + seconds
        while True:
            with reader.mutex:
                status, ident = reader._select()
                if status == 2:
                    raise gcmd.error("Slot %s's RFID reader is not responding." % (label,))
                if status == 1:
                    report = self._base_report(reader, ident)
                    report.update(slot=label, read=reader.full_read(ident))
                    break
            if self.reactor.monotonic() >= deadline:
                raise gcmd.error("No tag came past slot %s's reader in %d s." % (label, seconds))
            self.reactor.pause(self.reactor.monotonic() + 0.2)
        path = self._write_report("report", label, report)
        if path is None:
            raise gcmd.error("Could not write the report; see klippy.log.")
        decoded = report["read"].get("decoded")
        gcmd.respond_info("Saved %s in Klipper's log folder (Fluidd: Logs). %s" % (
            os.path.basename(path),
            "The tag reads as %s %s." % (decoded["brand"], decoded["material"]) if decoded
            else "Attach it to a GitHub issue to get this spool supported."))

    def get_status(self, eventtime):
        return {"readers": {name: reader.last for name, reader in self.readers.items()},
                "last_tag": {name: reader.last_tag for name, reader in self.readers.items()},
                "catalog_added": self.added, "last_report": self.last_report}


def load_config(config):
    return BoxRfid(config)
