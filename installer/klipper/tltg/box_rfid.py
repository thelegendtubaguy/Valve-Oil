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
#   Creality tags open with a key AES-encrypted from the UID and hold an
#   AES-encrypted ASCII record in blocks 4-6 (CREALITY_KEYGEN, CREALITY_DATA_KEY).
# - NFC Type 2 tags: pages are read and Anycubic, Elegoo, TigerTag, OpenTag3D,
#   or QIDI-layout data is turned into a QIDI v1 block for the matching
#   database entry: the brand's filament by name, then the brand's plain
#   filament of that type, then Generic's.
#
# VO_BOX_RFID_WRITE writes a filament (the slot's own unless given) to the
# spool tag in a slot: QIDI's v1 block, then the database entry's name
# (NAME_MAGIC, in MIFARE blocks 5-6 or NTAG pages 8-15). When another
# printer's database has that name under another ID, its read gives the slot
# its own ID, so tags for entries an operator added read the same anywhere.
# Only blank tags and tags already holding a QIDI block are written, and each
# write is read back. A spool on an empty slot's rollers is turned backward
# (the slot motor drives the roller, as for tltg_box_spool_roll) through
# tltg_box_slot_jog until its tag comes past, polling the reader after each
# TURN_PIECE; with filament in the slot the operator turns it by hand.
#
# VO_BOX_RFID_TEST is the hardware test tool behind it: it scans a reader for
# every tag it selects, or writes with every option exposed. Both publish
# their progress and result in this object's status under "job".
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
import collections
import copy
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
TYPE2_PAGE_COUNT = 32  # pages 4..35 hold Anycubic, Elegoo, TigerTag, and OpenTag3D data
NTAG_PAGES = {0x0F: 45, 0x11: 135, 0x13: 231}  # GET_VERSION storage size byte: NTAG213/215/216
PUBLIC_KEYS = (("default", DEFAULT_KEY), ("mad", bytes.fromhex("a0a1a2a3a4a5")),
               ("ndef", bytes.fromhex("d3f7d3f7d3f7")))
MAX_AUTOMATIC_REPORTS = 20
NAME_MAGIC, NAME_LENGTH = b"VO\x01", 28  # name field: magic, length, UTF-8 name, zero padding to 32 bytes
WRITE_AREA = 48  # QIDI block and name field: MIFARE blocks 4-6, NTAG pages 4-15
NTAG_CFG0 = {45: 0x29, 135: 0x83, 231: 0xE3}  # NTAG213/215/216 CFG0 page (AUTH0 is its byte 3)
TEST_INTERVAL = 0.2
TURN_PIECE, TURN_SPEED, MAX_TURN = 10., 20., 2000.
WRITE_TURN = 800.  # a little over one turn of a full spool (450-650 mm on a Plus 5)
WRITE_SECONDS = 60.  # turning by hand
WRITE_TIMEOUT = 10  # reader timer units for write frames; tags take up to 10 ms to program
INIT_READ = "INIT_RFID_READ"
INIT_READ_POLL, INIT_READ_WAIT = 0.25, 60.
ANYCUBIC_HEADER = b"\x7b\x00\x65\x00"
ELEGOO_HEADER = b"\x36\xee\xee\xee"
# Bambu Lab tags: MIFARE Classic 1K whose sector keys come from the UID.
BAMBU_SALT = bytes.fromhex("9a759cf2c4f7caff222cb9769b41bc96")
BAMBU_BLOCKS = (1, 2, 4, 5, 16)  # material IDs, type, detailed type, color, extra colors
BAMBU_PRIVATE_BLOCKS = (9,)  # the spool's serial number, left out of reports
# Creality tags (K1, K2, CFS): sector 1's key is AES-128(CREALITY_KEYGEN, UID
# repeated to 16 bytes)[:6], and blocks 4-6 hold a 48-character ASCII record
# under AES-128-ECB with CREALITY_DATA_KEY: date, vendor, batch, a type digit
# and the 5-digit material code (characters 12-16), "0" and the RGB color
# (18-23), length, serial. Layout from Bambu-Research-Group/RFID-Tag-Guide.
CREALITY_KEYGEN = b"q3bu^t1nqfZ(pf$1"
CREALITY_DATA_KEY = b"H@CFkRnz@KAtBJp2"
# TigerTag (tigertag-project.github.io/TigerTag-RFID-Guide): NTAG pages 4-23,
# big-endian. Format IDs for TigerTag and TigerTag+; type 0x8E is filament.
TIGERTAG_IDS = (0x5BF59264, 0xBC0FCB97)
TIGERTAG_FILAMENT = 0x8E
# OpenTag3D (opentag3d.info/spec): an NDEF record of this MIME type.
OPENTAG3D_MIME = b"application/opentag3d"

# What a vendor tag says: the brand, its name for the filament, colors, the
# base material type when the tag gives one, and the tag format.
Decoded = collections.namedtuple("Decoded", "brand material colors kind layout")


def aes_tables():
    sbox, rsbox, value, inverse = [0] * 256, [0] * 256, 1, 1
    while True:
        value ^= ((value << 1) ^ (0x1B if value & 0x80 else 0)) & 0xFF
        inverse ^= inverse << 1
        inverse ^= inverse << 2
        inverse ^= inverse << 4
        inverse &= 0xFF
        if inverse & 0x80:
            inverse ^= 0x09
        rot = lambda x, n: ((x << n) | (x >> (8 - n))) & 0xFF
        sbox[value] = inverse ^ rot(inverse, 1) ^ rot(inverse, 2) ^ rot(inverse, 3) ^ rot(inverse, 4) ^ 0x63
        if value == 1:
            break
    sbox[0] = 0x63
    for i, x in enumerate(sbox):
        rsbox[x] = i
    return sbox, rsbox


AES_SBOX, AES_RSBOX = aes_tables()


def gmul(a, b):
    out = 0
    while b:
        if b & 1:
            out ^= a
        a = ((a << 1) ^ (0x1B if a & 0x80 else 0)) & 0xFF
        b >>= 1
    return out


def aes128_ecb(key, data, decrypt=False):
    """AES-128 in ECB mode (FIPS-197), for Creality tags; the printer's Python
    has no AES library."""
    words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        word = list(words[i - 1])
        if i % 4 == 0:
            word = [AES_SBOX[b] for b in word[1:] + word[:1]]
            word[0] ^= rcon
            rcon = gmul(rcon, 2)
        words.append([a ^ b for a, b in zip(words[i - 4], word)])
    round_keys = [sum(words[4 * r:4 * r + 4], []) for r in range(11)]
    out = bytearray()
    for start in range(0, len(data), 16):
        state = list(data[start:start + 16])
        add = lambda r: [a ^ b for a, b in zip(state, round_keys[r])]
        if not decrypt:
            state = add(0)
            for r in range(1, 11):
                state = [AES_SBOX[b] for b in state]
                state = [state[i % 4 + 4 * ((i // 4 + i % 4) % 4)] for i in range(16)]
                if r < 10:
                    state = sum(([gmul(c[0], 2) ^ gmul(c[1], 3) ^ c[2] ^ c[3],
                                  c[0] ^ gmul(c[1], 2) ^ gmul(c[2], 3) ^ c[3],
                                  c[0] ^ c[1] ^ gmul(c[2], 2) ^ gmul(c[3], 3),
                                  gmul(c[0], 3) ^ c[1] ^ c[2] ^ gmul(c[3], 2)]
                                 for c in (state[j:j + 4] for j in range(0, 16, 4))), [])
                state = add(r)
        else:
            state = add(10)
            for r in range(9, -1, -1):
                state = [state[i % 4 + 4 * ((i // 4 - i % 4) % 4)] for i in range(16)]
                state = [AES_RSBOX[b] for b in state]
                state = add(r)
                if r:
                    state = sum(([gmul(c[0], 14) ^ gmul(c[1], 11) ^ gmul(c[2], 13) ^ gmul(c[3], 9),
                                  gmul(c[0], 9) ^ gmul(c[1], 14) ^ gmul(c[2], 11) ^ gmul(c[3], 13),
                                  gmul(c[0], 13) ^ gmul(c[1], 9) ^ gmul(c[2], 14) ^ gmul(c[3], 11),
                                  gmul(c[0], 11) ^ gmul(c[1], 13) ^ gmul(c[2], 9) ^ gmul(c[3], 14)]
                                 for c in (state[j:j + 4] for j in range(0, 16, 4))), [])
        out += bytes(state)
    return bytes(out)


def creality_key(uid):
    """Creality's sector 1 key for a tag's UID."""
    return aes128_ecb(CREALITY_KEYGEN, (bytes(uid) * 4)[:16])[:6]


def decode_creality(area, catalog):
    """area: MIFARE blocks 4-6 as stored. Returns Decoded or None."""
    try:
        text = aes128_ecb(CREALITY_DATA_KEY, bytes(area[:48]), decrypt=True).decode("ascii")
    except UnicodeDecodeError:
        return None
    code, color = text[12:17], text[18:24]
    if not code.isdigit() or not all(c in "0123456789abcdefABCDEF" for c in color):
        return None
    known = catalog["creality_materials"].get(code)
    brand, material, kind = ("Creality", code, None) if known is None else known
    return Decoded(brand, material, [color.upper()], kind, "creality")


def decode_tigertag(memory, catalog):
    """memory: pages 4.. as bytes. Returns Decoded or None."""
    if len(memory) < 44 or int.from_bytes(memory[0:4], "big") not in TIGERTAG_IDS:
        return None
    if memory[12] != TIGERTAG_FILAMENT:
        return None
    material = catalog["tigertag_materials"].get(str(int.from_bytes(memory[8:10], "big")))
    brand = catalog["tigertag_brands"].get(str(int.from_bytes(memory[14:16], "big")))
    if material is None or brand is None:
        return None
    label, kind = material
    aspect = catalog["tigertag_aspects"].get(str(memory[10]))
    colors = ["%02X%02X%02X" % tuple(memory[16:19])]
    colors += ["%02X%02X%02X" % tuple(memory[o:o + 3]) for o in (36, 40) if memory[o:o + 3].strip(b"\0")]
    return Decoded(brand, "%s %s" % (label, aspect) if aspect else label, colors, kind, "tigertag")


def ndef_records(memory):
    """(TNF, type, payload) of each record in the first NDEF message of
    MEMORY (pages 4..)."""
    i = 0
    while i + 1 < len(memory):
        kind = memory[i]
        if kind == 0x00:
            i += 1
            continue
        if kind == 0xFE:
            return []
        length, i = memory[i + 1], i + 2
        if length == 0xFF:
            length, i = int.from_bytes(memory[i:i + 2], "big"), i + 2
        if kind != 0x03:
            i += length
            continue
        message, records, j = memory[i:i + length], [], 0
        while j + 3 <= len(message):
            header, type_length = message[j], message[j + 1]
            if header & 0x10:
                payload_length, j = message[j + 2], j + 3
            else:
                payload_length, j = int.from_bytes(message[j + 2:j + 6], "big"), j + 6
            id_length = 0
            if header & 0x08:
                id_length, j = message[j], j + 1
            record_type = message[j:j + type_length]
            j += type_length + id_length
            records.append((header & 0x07, bytes(record_type), bytes(message[j:j + payload_length])))
            j += payload_length
            if header & 0x40:
                break
        return records
    return []


def decode_opentag3d(memory):
    """memory: pages 4.. as bytes. Returns Decoded or None."""
    payload = next((p for tnf, t, p in ndef_records(memory) if tnf == 2 and t == OPENTAG3D_MIME), None)
    if payload is None or len(payload) < 0x40:
        return None
    payload = payload.ljust(0x4C, b"\0")
    text = lambda start, length: payload[start:start + length].split(b"\0", 1)[0].decode("utf-8", "replace").strip()
    material, modifier, brand = text(0x02, 5), text(0x07, 5), text(0x0C, 16)
    if not material or not brand:
        return None
    colors = ["%02X%02X%02X" % tuple(payload[0x3C:0x3F])]
    colors += ["%02X%02X%02X" % tuple(payload[o:o + 3]) for o in (0x40, 0x44, 0x48) if payload[o + 3]]
    return Decoded(brand, "%s %s" % (material, modifier) if modifier else material, colors, material, "opentag3d")


def decode_type2(memory, catalog):
    """Vendor data on an NFC Type 2 tag (pages 4..), or None."""
    return (decode_anycubic(memory) or decode_elegoo(memory, catalog) or decode_tigertag(memory, catalog)
            or decode_opentag3d(memory))


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


def is_filament_id(value):
    """True for IDs shaped like QFA00, the only shape a QIDI v1 block can carry."""
    return (isinstance(value, str) and len(value) == 5 and value.startswith("QF")
            and "A" <= value[2] <= "Z" and value[3:].isdigit())


def block_id(block):
    return "QF%c%02d" % (block[0], block[1])


def block_colors(block):
    return ["%02X%02X%02X" % tuple(block[3 + 3 * i:6 + 3 * i]) for i in range(min(block[2] & 0x0F, 4))]


def name_field(name):
    """32-byte name field for NAME; ValueError when it is empty or too long."""
    raw = name.encode("utf-8")
    if not 0 < len(raw) <= NAME_LENGTH:
        raise ValueError(name)
    return (NAME_MAGIC + bytes([len(raw)]) + raw).ljust(32, b"\0")


def decode_name_field(data):
    if len(data) < 32 or data[:3] != NAME_MAGIC or not 0 < data[3] <= NAME_LENGTH:
        return None
    if data[4 + data[3]:32].strip(b"\0"):
        return None
    try:
        return data[4:4 + data[3]].decode("utf-8")
    except UnicodeDecodeError:
        return None


def type2_blank(memory):
    """True when MEMORY (pages 4..) is zeros or holds only empty NDEF TLVs
    (lock and memory control, an empty message, the terminator), as new tags do."""
    i = 0
    while i < len(memory):
        kind = memory[i]
        if kind == 0x00:
            i += 1
            continue
        if kind == 0xFE:
            return not memory[i + 1:].strip(b"\0")
        if kind not in (0x01, 0x02, 0x03) or i + 1 >= len(memory) or memory[i + 1] == 0xFF:
            return False
        if kind == 0x03 and memory[i + 1]:
            return False  # an NDEF message with content
        i += 2 + memory[i + 1]
    return True


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
    return Decoded("Anycubic", material, ["%02X%02X%02X" % (red, green, blue)], None, "anycubic")


def decode_elegoo(memory, catalog):
    offset = (16 - 4) * 4
    if memory[offset:offset + 4] != ELEGOO_HEADER:
        return None
    material = catalog["elegoo_materials"].get(memory[offset + 8:offset + 12].hex())
    subtype = catalog["elegoo_subtypes"].get(memory[offset + 12:offset + 14].hex())
    if material is None:
        return None
    red, green, blue = memory[offset + 16:offset + 19]
    return Decoded("Elegoo", subtype or material, ["%02X%02X%02X" % (red, green, blue)], None, "elegoo")


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
    """blocks: {block: 16 bytes} for BAMBU_BLOCKS. Returns Decoded or None,
    with the filament type as its kind. The material is the catalog name for
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
    return Decoded("Bambu", material, colors, filament_type, "bambu")


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

    def find(self, brand, material, kind=None):
        """ID of the database entry for BRAND's MATERIAL: exact name, then the
        brand's entry for the base material (KIND, else MATERIAL's first word),
        then Generic's by name, then a Generic or QIDI entry of that type."""
        entries = {name: entry for name, entry in self.entries().items() if hasattr(entry, "get")
                   and is_filament_id(entry.get("filament_id"))}
        by_name = {name.lower(): (name, entry) for name, entry in entries.items()}
        brand = self.catalog.get("brand_aliases", {}).get(brand.lower(), brand)
        base = kind or (material.split()[0] if material else "")
        for name in ("%s %s" % (brand, material), "%s %s" % (brand, base), "Generic %s" % (base,)):
            if name.lower() in by_name:
                return name, by_name[name.lower()][1]["filament_id"]
        for vendor in ("Generic", "QIDI"):
            for name in sorted(entries):
                entry = entries[name]
                if entry.get("filament_vendor") == vendor and str(entry.get("type", "")).upper() == base.upper():
                    return name, entry["filament_id"]
        return None, None

    def name_for(self, filament_id):
        return next((name for name, entry in self.entries().items()
                     if hasattr(entry, "get") and entry.get("filament_id") == filament_id), None)

    def id_for(self, name):
        entry = self.entries().get(name)
        filament_id = entry.get("filament_id") if hasattr(entry, "get") else None
        return filament_id if is_filament_id(filament_id) else None


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

    def _reselect(self):
        """Select the tag again after exchanging with it. A tag left active
        (logged in, or after GET_VERSION) drops the first wake-up to idle and
        answers the second; a tag left idle answers the first."""
        status, ident = self._select()
        if status != 1:
            status, ident = self._select()
        return status, ident

    def _xfer(self, command, timeout, frame):
        params = self.transceive.send([self.rfid.oid, bytes([command, timeout]) + frame])
        data = bytes(params["data"])
        if len(data) < 4:
            return False, 0, b""
        bits = data[2] | data[3] << 8
        return params["status"] == 1, data[1], data[4:4 + (bits + 7) // 8]

    def _login(self, ident, block, key, key_type=0x60):
        """MIFARE Classic login to BLOCK with KEY as key A (0x60) or B (0x61);
        True when Crypto1 is on."""
        ok, status2, _ = self._xfer(MFAUTHENT_CMD, 2, bytes([key_type, block]) + key + uid_tail(ident))
        return ok and bool(status2 & 0x08)

    def _creality_area(self, ident):
        """Blocks 4-6 of a tag opened with Creality's key from its UID, as key A
        or key B, and the tag's ident; (None, ident) when neither opens it."""
        key = creality_key(uid_bytes(ident))
        for key_type in (0x60, 0x61):
            status, ident = self._reselect()
            if status != 1:
                return None, ident
            if self._login(ident, 4, key, key_type):
                return self._classic_blocks((4, 5, 6)), ident
        return None, ident

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

    def _classic_blocks(self, blocks):
        """BLOCKS read in the logged-in sector, joined, or None."""
        out = b""
        for block in blocks:
            ok, _, data = self._xfer(TRANSCEIVE_CMD, 3, bytes([0x30, block]))
            if not ok or len(data) < 16:
                return None
            out += data[:16]
        return out

    def _ack(self, frame):
        """Send a write frame; the tag answers a 4-bit ACK (0x0A). Returns
        (acked, the reader's raw answer for the test report)."""
        params = self.transceive.send([self.rfid.oid, bytes([TRANSCEIVE_CMD, WRITE_TIMEOUT]) + frame])
        data = bytes(params["data"])
        if len(data) < 4:
            return False, "no answer"
        error, bits = data[0], data[2] | data[3] << 8
        acked = bits == 4 and len(data) > 4 and data[4] & 0x0F == 0x0A
        return acked, "error=%02x bits=%d data=%s" % (error, bits, data[4:].hex() or "-")

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
                area = self._classic_blocks((4, 5, 6))
                if area is not None:
                    block = area[:16]
                    if qidi_block_valid(block) and not text_block(block):
                        name = decode_name_field(area[16:48])
                        self._record(tag="mifare_classic", layout="qidi", name=name)
                        return {"oid": oid, "status": 1, "data": self.owner.named(block, name)}
                    decoded = decode_creality(area, self.owner.catalog)  # a copy left with the default key
                    if decoded:
                        return self._answer(oid, ident, "mifare_classic", decoded, {"blocks_4_6": area.hex()})
                # The tag is still selected: try it as a Bambu tag below.
            else:
                # The refused login left the tag idle. Creality tags open with a key from the UID.
                area, ident = self._creality_area(ident)
                decoded = area and decode_creality(area, self.owner.catalog)
                if decoded:
                    return self._answer(oid, ident, "mifare_classic", decoded, {"blocks_4_6": area.hex()})
                status, ident = self._reselect()
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
        decoded = decode_type2(memory, self.owner.catalog)
        if decoded is None:
            if qidi_block_valid(memory[:16]):
                name = decode_name_field(memory[16:48])
                self._record(tag="type2", layout="qidi", name=name)
                return {"oid": oid, "status": 1, "data": self.owner.named(memory[:16], name)}
            self._record(tag="type2", layout="unknown")
            self.owner.capture(self, ident, {"first_page": 4, "pages": memory.hex()}, "unknown")
            return self._empty(1, oid)
        return self._answer(oid, ident, "type2", decoded, {"first_page": 4, "pages": memory.hex()})

    def _answer(self, oid, ident, tag, decoded, data):
        """QIDI v1 block for a decoded vendor tag's database entry."""
        name, filament_id = self.owner.database.find(decoded.brand, decoded.material, decoded.kind)
        if filament_id is None and decoded.kind:
            name, filament_id = self.owner.database.find(decoded.brand, decoded.kind)  # e.g. Bambu's filament type
        colors = decoded.colors
        self._record(tag=tag, layout=decoded.layout, brand=decoded.brand, material=decoded.material,
                     colors=colors, filament=name, filament_id=filament_id)
        if filament_id is None:
            self.owner.capture(self, ident, data, "no_database_entry")
            return self._empty(1, oid)
        return {"oid": oid, "status": 1, "data": qidi_block(filament_id, colors)}

    def inspect(self, ident):
        """What the selected tag holds and whether VO_BOX_RFID_TEST may write it.
        Leaves the tag in any state; select it again before using it."""
        sak = tag_sak(ident)
        if sak & 0x08:
            info = {"type": "mifare_classic"}
            if not self._login(ident, 4, DEFAULT_KEY):
                area, ident = self._creality_area(ident)
                if area is not None and decode_creality(area, self.owner.catalog):
                    return self._vendor(info, decode_creality(area, self.owner.catalog))
                status, ident = self._reselect()
                bambu = status == 1 and self._login(ident, 4, bambu_keys(uid_tail(ident))[1])
                info.update(layout="bambu" if bambu else "locked", writable=False,
                            reason="a Bambu Lab tag" if bambu else "locked: the default key doesn't open it")
                return info
            area = self._classic_blocks((4, 5, 6))
            if area is None:
                return dict(info, layout="read_failed", writable=False, reason="the tag left the field")
            if text_block(area[:16]):
                return dict(info, layout="bambu", writable=False, reason="a copy of a Bambu Lab tag")
            if decode_creality(area, self.owner.catalog):
                return self._vendor(info, decode_creality(area, self.owner.catalog))
            return self._classify(info, area, area[:16].strip(b"\0") == b"" and not area[16:].strip(b"\0"))
        if sak & 0x7F:
            return {"type": "sak_%02x" % (sak,), "layout": "unsupported", "writable": False,
                    "reason": "not a MIFARE Classic or NFC Type 2 tag"}
        ok, _, version = self._xfer(TRANSCEIVE_CMD, 3, b"\x60")
        pages = NTAG_PAGES.get(version[6]) if ok and len(version) >= 8 else None
        info = {"type": {45: "ntag213", 135: "ntag215", 231: "ntag216"}.get(pages, "type2")}
        status, ident = self._reselect()
        if status != 1:
            return dict(info, layout="read_failed", writable=False,
                        reason="the tag left the field (select after GET_VERSION)")
        memory, failed = self._type2_pages(0, 4 + TYPE2_PAGE_COUNT)
        config = None
        if failed is None and pages in NTAG_CFG0:
            config, failed = self._type2_pages(NTAG_CFG0[pages], 4)
            failed = None if failed is None else NTAG_CFG0[pages]
        if failed is not None:
            return dict(info, layout="read_failed", writable=False,
                        reason="the tag left the field (read of page %d)" % (failed,))
        user = memory[16:]
        decoded = decode_type2(user, self.owner.catalog)
        if decoded:
            return self._vendor(info, decoded)
        if memory[10] & 0xF0 or memory[11]:
            return dict(info, layout="locked", writable=False, reason="lock bits are set on pages 4-15")
        if config is not None and config[3] < 4 + WRITE_AREA // 4:
            return dict(info, layout="locked", writable=False,
                        reason="password protected from page %d" % (config[3],))
        return self._classify(info, user[:WRITE_AREA], type2_blank(user))

    def _vendor(self, info, decoded):
        """A tag holding vendor data: never written."""
        name, filament_id = self.owner.database.find(decoded.brand, decoded.material, decoded.kind)
        brand = {"opentag3d": "OpenTag3D", "tigertag": "TigerTag"}.get(decoded.layout, decoded.brand)
        return dict(info, layout=decoded.layout, writable=False, colors=decoded.colors,
                    reason="holds %s data" % (brand,), sets_id=filament_id, sets_name=name)

    def _classify(self, info, area, blank):
        block, name = area[:16], decode_name_field(area[16:48])
        if blank:
            return dict(info, layout="blank", writable=True)
        if (qidi_block_valid(block) and not text_block(block) and block[2] >> 4
                and (name is not None or not area[16:48].strip(b"\0"))):
            sets = bytes(self.owner.named(block, name))
            return dict(info, layout="qidi+name" if name else "qidi", writable=True,
                        filament_id=block_id(block), name=name, colors=block_colors(block),
                        sets_id=block_id(sets), sets_name=self.owner.database.name_for(block_id(sets)))
        return dict(info, layout="unknown", writable=False, reason="holds data this tool doesn't write over",
                    data=area.hex())

    def _write_area(self, tag, mifare):
        """Read back the write area of the tag TAG; None when it isn't in the field."""
        status, ident = self._reselect()
        if status != 1 or tag_id(ident) != tag:
            return None
        if mifare:
            if not self._login(ident, 4, DEFAULT_KEY):
                return None
            return self._classic_blocks((4, 5, 6))
        memory, failed = self._type2_pages(4, WRITE_AREA // 4)
        return memory if failed is None else None

    def write(self, ident, area):
        """Write AREA (QIDI block and name field) to the selected tag when
        inspect allows it, then read it back from a fresh select."""
        tag, mifare = tag_id(ident), bool(tag_sak(ident) & 0x08)
        info = dict(self.inspect(ident), tag=tag)
        lost = dict(info, written=False, verified=False, lost=True, reason="the tag left the field")
        if info["layout"] == "read_failed":
            return lost
        if not info["writable"]:
            return dict(info, written=False, verified=False)
        status, ident = self._reselect()
        if status != 1 or tag_id(ident) != tag:
            return lost
        acks = []
        if mifare:
            if not self._login(ident, 4, DEFAULT_KEY):
                return lost
            for i, block in enumerate((4, 5, 6)):
                acks.append(self._ack(bytes([0xA0, block])))
                acks.append(self._ack(area[16 * i:16 * i + 16]))
        else:
            for i, page in enumerate(range(4, 4 + WRITE_AREA // 4)):
                acks.append(self._ack(bytes([0xA2, page]) + area[4 * i:4 * i + 4]))
        readback = self._write_area(tag, mifare)
        verified = readback == area
        result = dict(info, written=True, verified=verified, acked=sum(ok for ok, _ in acks),
                      frames=len(acks), acks=[text for _, text in acks])
        if readback is None:
            result["reason"] = "the tag left the field before the read-back"
        elif not verified:
            result.update(reason="the read-back differs", readback=readback.hex())
        return result

    def full_read(self, ident):
        """Everything a report needs from the selected tag."""
        sak = tag_sak(ident)
        if sak & 0x08:
            sectors = {}
            keys = bambu_keys(uid_tail(ident))
            creality = creality_key(uid_bytes(ident))
            for sector in range(16):
                candidates = [(name, key, 0x60) for name, key in PUBLIC_KEYS + (("bambu", keys[sector]),)]
                candidates += [("creality", creality, 0x60), ("creality_b", creality, 0x61)]
                for name, key, key_type in candidates:
                    status, ident = self._reselect()  # the last sector's read left the tag logged in
                    if status != 1 or not self._login(ident, sector * 4, key, key_type):
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
            sector_1 = sectors.get(1, {}).get("blocks", [None] * 4)[:3]
            if not decoded and all(sector_1):
                decoded = decode_creality(bytes.fromhex("".join(sector_1)), self.owner.catalog)
            if decoded:
                report["decoded"] = {"format": decoded.layout, "brand": decoded.brand,
                                     "material": decoded.material, "colors": decoded.colors}
            if decoded or any(sector["key_a"] == "bambu" for sector in sectors.values()):
                for block in BAMBU_PRIVATE_BLOCKS:
                    if sectors.get(block // 4, {}).get("blocks", [None] * 4)[block % 4]:
                        sectors[block // 4]["blocks"][block % 4] = "00" * 16
            return report
        if sak & 0x7F:
            return {"tag": "sak_%02x" % (sak,)}
        ok, _, version = self._xfer(TRANSCEIVE_CMD, 3, b"\x60")
        count = NTAG_PAGES.get(version[6], 16) if ok and len(version) >= 8 else 16
        status, ident = self._reselect()
        memory, failed = self._type2_pages(0, count)
        memory = bytes(9) + memory[9:] if len(memory) >= 9 else memory  # UID and BCC bytes
        report = {"tag": "type2", "get_version": version.hex() if ok else None,
                  "first_page": 0, "pages": memory.hex()}
        if failed is not None:
            report["unreadable_from_page"] = failed
        decoded = len(memory) >= 16 + TYPE2_PAGE_COUNT * 4 and decode_type2(memory[16:], self.owner.catalog)
        if decoded:
            report["decoded"] = {"format": decoded.layout, "brand": decoded.brand,
                                 "material": decoded.material, "colors": decoded.colors}
        return report


def tag_sak(ident):
    """SAK of the tag: the last cascade level's, without the cascade bit (0x04)."""
    return next((ident[17 + i] for i in range(3) if not ident[17 + i] & 0x04), ident[19])


def uid_bytes(ident):
    """The tag's whole UID: each cascade level's four bytes, without the
    cascade tag (0x88) of levels that continue."""
    levels = next((i + 1 for i in range(3) if not ident[17 + i] & 0x04), 3)
    out = b""
    for i in range(levels):
        part = bytes(ident[2 + 5 * i:6 + 5 * i])
        out += part[1:] if i < levels - 1 else part
    return out


def uid_tail(ident):
    """The four UID bytes a MIFARE Classic login uses: the last cascade level's."""
    level = next((i for i in range(3) if not ident[17 + i] & 0x04), 0)
    return ident[2 + 5 * level:6 + 5 * level]


def tag_id(ident):
    """Short hash of the tag's UID, to tell tags apart without showing the UID."""
    levels = next((i + 1 for i in range(3) if not ident[17 + i] & 0x04), 3)
    return hashlib.sha256(bytes(ident[2:2 + 5 * levels])).hexdigest()[:8]


def slot_label(slot):
    return "%d%s" % (slot // 4 + 1, "ABCD"[slot % 4])


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
        self.job = {"run": 0, "running": False}
        self.printer.register_event_handler("klippy:connect", self._guard_init_read)
        self.printer.register_event_handler("klippy:ready", self._ready)
        self.printer.lookup_object("gcode").register_command(
            "TLTG_BOX_RFID_REPORT", self.cmd_TLTG_BOX_RFID_REPORT,
            desc="Read the spool tag in a QIDI Box slot into a shareable report")
        self.printer.lookup_object("gcode").register_command(
            "VO_BOX_RFID_WRITE", self.cmd_VO_BOX_RFID_WRITE,
            desc="Write a filament to the spool tag in a QIDI Box slot")
        self.printer.lookup_object("gcode").register_command(
            "VO_BOX_RFID_TEST", self.cmd_VO_BOX_RFID_TEST,
            desc="Test tool: scan a QIDI Box reader's tags, or write a filament to a tag")

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

    def named(self, block, name):
        """BLOCK with its ID replaced by the ID this database has for NAME."""
        filament_id = self.database.id_for(name) if name and block[2] >> 4 else None
        if filament_id is None or filament_id == block_id(block):
            return block
        return bytes([ord(filament_id[2]), int(filament_id[3:5])]) + bytes(block[2:])

    def _slot_reader(self, gcmd, slot, label):
        reader = self.readers.get("card_reader_%d" % (slot // 2 + 1))
        if reader is None:
            raise gcmd.error("Slot %s's Box does not run the Valve Oil Box firmware, which reading tags needs. "
                             "See docs/firmware.md." % (label,))
        if getattr(reader.rfid, "read_rfid_timer", None) is not None:
            raise gcmd.error("QIDI is reading slot %s's tag; try again when it finishes." % (label,))
        return reader

    def cmd_TLTG_BOX_RFID_REPORT(self, gcmd):
        slot = gcmd.get_int("SLOT", minval=0, maxval=15)
        seconds = gcmd.get_float("SECONDS", 60., minval=1., maxval=300.)
        label = slot_label(slot)
        reader = self._slot_reader(gcmd, slot, label)
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

    def _job_update(self, **fields):
        self.job = dict(self.job, **fields)

    def _tag_area(self, gcmd, filament_id, colors, with_name):
        """The 48 bytes to write: FILAMENT_ID's QIDI block and, with WITH_NAME,
        its database name."""
        filament_id = str(filament_id).strip().upper()
        name = self.database.name_for(filament_id)
        if not is_filament_id(filament_id) or name is None:
            raise gcmd.error("%s isn't in this printer's filament database." % (filament_id,))
        colors = [str(c).strip().lstrip("#").upper() for c in colors if str(c).strip()]
        if not 1 <= len(colors) <= 4 or not all(
                len(c) == 6 and all(x in "0123456789ABCDEF" for x in c) for c in colors):
            raise gcmd.error("A tag takes one to four RGB colors, such as FF0000,00FF00.")
        try:
            names = name_field(name) if with_name else bytes(32)
        except ValueError:
            raise gcmd.error("%s is longer than the %d bytes a tag holds for the name." % (name, NAME_LENGTH))
        return qidi_block(filament_id, colors) + names

    def _turner(self, gcmd, slot, label):
        """tltg_box_slot_jog when SLOT's spool may be turned now, else raise."""
        jog = self.printer.lookup_object("tltg_box_slot_jog", None)
        if jog is None:
            raise gcmd.error("Turning a spool needs tltg_box_slot_jog.")
        blocker = jog.blocker()
        if blocker is not None:
            raise gcmd.error("Slot %s not turned: %s." % (label, blocker))
        if not self._slot_empty(slot):
            raise gcmd.error("Slot %s not turned: it has filament in it. Only a spool sitting on the "
                             "rollers with no filament fed is turned." % (label,))
        return jog

    def cmd_VO_BOX_RFID_WRITE(self, gcmd):
        slot = gcmd.get_int("SLOT", minval=0, maxval=15)
        label = slot_label(slot)
        reader = self._slot_reader(gcmd, slot, label)
        variables = getattr(self.printer.lookup_object("save_variables", None), "allVariables", None) or {}
        filament_id = gcmd.get("FILAMENT", "") or variables.get("filament_slot%d" % (slot,), "")
        colors = gcmd.get("COLORS", "")
        colors = colors.split(",") if colors else variables.get("color_slot%d" % (slot,))
        area = self._tag_area(gcmd, filament_id, colors if isinstance(colors, list) else [],
                              gcmd.get_int("NAME", 1, minval=0, maxval=1))
        seconds = gcmd.get_float("SECONDS", WRITE_SECONDS, minval=5., maxval=300.)
        jog = self._turner(gcmd, slot, label) if self._slot_empty(slot) else None
        self._job(gcmd, "write", slot, label, reader, seconds=seconds, area=area,
                  turn=-WRITE_TURN if jog else 0., jog=jog)

    def cmd_VO_BOX_RFID_TEST(self, gcmd):
        action = gcmd.get("ACTION", "SCAN").upper()
        if action not in ("SCAN", "WRITE"):
            raise gcmd.error("ACTION must be SCAN or WRITE.")
        slot = gcmd.get_int("SLOT", minval=0, maxval=15)
        seconds = gcmd.get_float("SECONDS", 15. if action == "SCAN" else 30., minval=1., maxval=120.)
        label = slot_label(slot)
        reader = self._slot_reader(gcmd, slot, label)
        only = gcmd.get("TAG", "").strip().lower() or None
        turn = gcmd.get_float("TURN", 0., minval=-MAX_TURN, maxval=MAX_TURN)
        step = gcmd.get_float("STEP", TURN_PIECE, minval=2., maxval=50.)
        jog = self._turner(gcmd, slot, label) if turn else None
        area = None
        if action == "WRITE":
            area = self._tag_area(gcmd, gcmd.get("FILAMENT"), gcmd.get("COLORS").split(","),
                                  gcmd.get_int("NAME", 0, minval=0, maxval=1))
        self._job(gcmd, action.lower(), slot, label, reader, seconds=seconds, area=area, turn=turn,
                  jog=jog, step=step, only=only, test=True)

    def _job(self, gcmd, action, slot, label, reader, seconds, area=None, turn=0., jog=None,
             step=TURN_PIECE, only=None, test=False):
        """Scan READER, or write AREA to the first tag (or tag ONLY) it
        selects, while SLOT's spool turns TURN mm or for SECONDS."""
        first = (slot // 2) * 2
        slots = [slot_label(first), slot_label(first + 1)]
        name = self.database.name_for(block_id(area)) if area else None
        self._job_update(run=self.job["run"] + 1, action=action, test=test, slot=label, reader=reader.name,
                         reader_slots=slots, running=True, seconds=seconds, elapsed=0., polls=0, empty=0,
                         tags=[], only=only, result=None, error=None, lost=0, turn=turn, turned=0.,
                         filament=name)
        if action == "write" and not test:
            gcmd.respond_info("Writing %s to slot %s's spool tag. %s" % (
                name, label, "The Box turns the spool until its tag comes past the reader." if turn else
                "Turn the spool slowly by hand for up to %d s." % (seconds,)))
        elif action == "write":
            gcmd.respond_info("Writing %s to the first %s past reader %s (slots %s)%s." % (
                name, "tag " + only if only else "tag", reader.name, " and ".join(slots),
                " while the spool turns %.0f mm" % (turn,) if turn else
                " for up to %d s. Turn the spool slowly by hand" % (seconds,)))
        elif turn:
            gcmd.respond_info("Scanning reader %s (slots %s) while slot %s's spool turns %.0f mm." % (
                reader.name, " and ".join(slots), label, turn))
        else:
            gcmd.respond_info("Scanning reader %s (slots %s) for %d s. Turn spools by hand to see which "
                              "tags it reads." % (reader.name, " and ".join(slots), seconds))
        try:
            if turn:
                self._job_turn(jog, slot, turn, step, reader, only, area)
            else:
                self._job_run(reader, seconds, only, area)
        except Exception as e:
            self._job_update(running=False, error=str(e))
            raise
        job = self.job
        if action == "scan":
            seen = ", ".join("%s (%s, %s, %d of %d polls%s)" % (
                t["tag"], t["type"], t["layout"], t["hits"], job["polls"],
                ", at %.0f to %.0f mm" % (t["first_mm"], t["last_mm"]) if "first_mm" in t else "")
                for t in job["tags"]) or "none"
            gcmd.respond_info("Reader %s saw: %s." % (reader.name, seen))
            return
        result = job["result"]
        if result is None:
            raise gcmd.error("No %s came past slot %s's reader %s%s. The tag is read on the side of the "
                             "spool facing the reader; if it's on the other side, turn the spool over." % (
                                 "tag " + only if only else "tag", label,
                                 "while the spool turned %.0f mm" % (abs(turn),) if turn else "in %d s" % (seconds,),
                                 " long enough to write" if job["lost"] else ""))
        if not result["written"]:
            raise gcmd.error("Slot %s's spool tag was not written: %s." % (label, result["reason"]))
        if not result["verified"]:
            raise gcmd.error("Slot %s's spool tag was written, but %s. Write it again." % (label, result["reason"]))
        gcmd.respond_info("Slot %s's spool tag now holds %s, checked by reading it back." % (label, name))

    def _slot_empty(self, slot):
        """QIDI counts the slot empty and its inlet sensor sees no filament,
        as tltg_box_spool_roll requires before turning a spool."""
        variables = getattr(self.printer.lookup_object("save_variables", None), "allVariables", None) or {}
        name = "slot%d" % (slot,)
        if variables.get(name) != 0 or name in (variables.get("slot_sync"), variables.get("last_load_slot")):
            return False
        stepper = self.printer.lookup_object("box_stepper " + name, None)
        return getattr(stepper, "r_endstop_state", None) == 1

    def _job_turn(self, jog, slot, turn, step, reader, only, area):
        start = self.reactor.monotonic()
        seen = {}
        position = [0.]

        def poll():
            position[0] += step
            return self._job_poll(reader, only, area, seen, start, min(position[0], abs(turn)))
        self._job_poll(reader, only, area, seen, start, 0.)
        if self.job["result"] is None:
            jog.move(slot, turn, TURN_SPEED, until=poll, piece=step)
        self._job_update(running=False)

    def _job_run(self, reader, seconds, only, area):
        start = self.reactor.monotonic()
        seen = {}
        while True:
            if self._job_poll(reader, only, area, seen, start) or self.reactor.monotonic() - start >= seconds:
                break
            self.reactor.pause(self.reactor.monotonic() + TEST_INTERVAL)
        self._job_update(running=False)

    def _job_poll(self, reader, only, area, seen, start, turned=None):
        """One select of READER: inspect a new tag, or write the tag to write.
        Returns True once a write has a result."""
        now = self.reactor.monotonic()
        with reader.mutex:
            status, ident = reader._select()
            if status == 2:
                raise self.printer.command_error("Reader %s is not responding." % (reader.name,))
            tag = tag_id(ident) if status == 1 else None
            if tag is not None and (only is None or tag == only) and area is not None:
                result = reader.write(ident, area)
                if result.get("lost"):
                    self._job_update(lost=self.job["lost"] + 1)  # try again as it comes past
                else:
                    self._job_update(result=result)
            elif tag is not None and tag not in seen:
                seen[tag] = dict(reader.inspect(ident), tag=tag, hits=0, first=round(now - start, 1))
                if turned is not None:
                    seen[tag]["first_mm"] = turned
            elif tag is not None and seen[tag]["layout"] == "read_failed":
                # The tag left the field mid-read: read it again as it comes past.
                kept = ("tag", "hits", "first", "first_mm")
                seen[tag] = dict(reader.inspect(ident), **{k: seen[tag][k] for k in kept if k in seen[tag]})
        if tag in seen:
            seen[tag] = dict(seen[tag], hits=seen[tag]["hits"] + 1, last=round(now - start, 1))
            if turned is not None:
                seen[tag]["last_mm"] = turned
        self._job_update(elapsed=round(now - start, 1), polls=self.job["polls"] + 1,
                         empty=self.job["empty"] + (tag is None), tags=list(seen.values()),
                         **({"turned": turned} if turned is not None else {}))
        return self.job["result"] is not None

    def get_status(self, eventtime):
        return {"readers": {name: reader.last for name, reader in self.readers.items()},
                "last_tag": {name: reader.last_tag for name, reader in self.readers.items()},
                "catalog_added": self.added, "last_report": self.last_report,
                "job": copy.deepcopy(self.job)}


def load_config(config):
    return BoxRfid(config)
