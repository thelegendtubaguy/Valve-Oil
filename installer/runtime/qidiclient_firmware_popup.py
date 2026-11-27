from __future__ import annotations

import hashlib
import os
import struct
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
# Plus 5 01.01.02.02 and Max 4 01.01.07.02 give each popup its own copy of the show check. The closed-loop board's copy first leaves out a
# board that reports nothing or 00.00.00.00, then branches past memcmp to show the popup when the lengths differ.
# Branching there only when the manifest version is longer makes memcmp the same test.
SHOW_IF_LENGTHS_DIFFER = bytes.fromhex("81fcff54")  # b.ne -0x70
SHOW_IF_LONGER = bytes.fromhex("88fcff54")  # b.hi -0x70
# With QIDI's package updates blocked, one more change applies.
#
# The app announces QIDI's online update from one byte, where its check of /backend/v1/fireware/upgrade-info stores
# the reply's needUpdate. That byte alone makes the "New SOC firmware available" popup (QDC_100_005_005_004) show the
# online version, the firmware page list it as the latest SOC version, and the page's Update button start the online
# update instead of the per-board one. Storing zero leaves the app as if QIDI offered nothing, while the per-board
# notices and update still compare the boards with ~/update/firmware_manifest.json.
STORE_NEED_UPDATE = bytes.fromhex("43c00139")  # strb w3, [x2, #0x70]
STORE_ZERO = bytes.fromhex("5fc00139")  # strb wzr, [x2, #0x70]
# The Smart 5 build keeps the byte 0x50 earlier in the same structure.
STORE_NEED_UPDATE_SMART5 = bytes.fromhex("43800039")  # strb w3, [x2, #0x20]
STORE_ZERO_SMART5 = bytes.fromhex("5f800039")  # strb wzr, [x2, #0x20]
# Plus 5 01.01.02.02 and Max 4 01.01.07.02 keep it 0x140 later.
STORE_NEED_UPDATE_PLUS5_0202 = bytes.fromhex("43c00639")  # strb w3, [x2, #0x1b0]
STORE_ZERO_PLUS5_0202 = bytes.fromhex("5fc00639")  # strb wzr, [x2, #0x1b0]
# The online, USB, and mandatory-file updates all unpack the package through one function, which deletes
# ~/update, recreates it, and runs unzip. Development builds made it return false on entry, so every QIDI package
# update stopped at "Extract failed". That also took away the USB update, the one way back onto a printer that has
# lost its network, so every form now keeps QIDI's prologue and updates put it back.
EXTRACT_PROLOGUE = bytes.fromhex("fd7baaa9fd030091")  # stp x29, x30, [sp, #-0x160]!; mov x29, sp
RETURN_FALSE = bytes.fromhex("00008052c0035fd6")  # mov w0, #0; ret
# The app's USB Box detector returns whether the Boxes it found give another box.cfg than its own last result, and on
# true writes box.cfg and restarts Klipper: at startup without checking for a print, and on a hotplug 4 s after the
# last event, after saving box_count. Its last result tracks the moments a Box board reset drops the Boxes off USB, so
# the Boxes coming back count as a change and restart Klipper for an unchanged file. Returning false leaves detection,
# the Boxes' versions and notices, and MCU_ID.cfg to the app, and box.cfg to Valve Oil's Box monitor (box_config.py).
DETECTOR_CHANGED = bytes.fromhex("34008052")  # mov w20, #1
DETECTOR_UNCHANGED = bytes.fromhex("14008052")  # mov w20, #0
# QIDI's phone app reaches the printer's camera and files through Peergine's P2P library, linked into the app. QIDI
# leaves the library's node forwarding on and raises its forwarding rate: the printer offers itself to QIDI's P2P server
# as a relay for other users' sessions, and routes its own sessions through other users' devices when a direct path
# fails. It also answers NAT test requests (0x8b) by sending a packet to an address in the request. For security, these
# sites keep the printer out of that mesh and leave QIDI's own relay, direct sessions, and the printer's own NAT test
# as they are. The app sends no camera picture through QIDI's relay, so away from the printer's network the phone app's
# camera stays black:
# - The forwarding client's rate, use of forwarders, and session limit are stored as zero, in its constructor and in
#   the SetOption that QIDI's init string and server-pushed options reach. It then never asks a forwarder for its own
#   sessions, so it makes no key, and refuses to forward for others; its report to the server offers nothing.
# - Options the server pushes are ignored except item 0, the server list: items 1-5 set the forwarding client's options
#   and items 6-7 configure remote log pulls.
# - In the UDP driver's message dispatcher, the jump-table entries for 0x8b and for the forwarding messages 0xa1-0xa7
#   point at its common exit, entry 0x001a, as unused types already do, so they are dropped. That also refuses a
#   forwarded session the phone sets up.
STORE_FORWARD_SPEED = bytes.fromhex("011800b9")  # str w1, [x0, #0x18]
STORE_FORWARD_USE = bytes.fromhex("012400b9")  # str w1, [x0, #0x24]
STORE_FORWARD_MAX_SESSIONS = bytes.fromhex("012800b9")  # str w1, [x0, #0x28]
STORE_ZERO_FORWARD_SPEED = bytes.fromhex("1f1800b9")  # str wzr, [x0, #0x18]
STORE_ZERO_FORWARD_USE = bytes.fromhex("1f2400b9")  # str wzr, [x0, #0x24]
STORE_ZERO_FORWARD_MAX_SESSIONS = bytes.fromhex("1f2800b9")  # str wzr, [x0, #0x28]
PUSHED_FORWARD_ITEMS = bytes.fromhex("36010035")  # cbnz w22, items 1-5
PUSHED_ITEMS_IGNORED = bytes.fromhex("f6000035")  # cbnz w22, the exit unknown items take
PUSHED_LOG_PULL_ITEMS = bytes.fromhex("49030054")  # b.ls items 6-7
NOP = bytes.fromhex("1f2003d5")
DISPATCH_FIRST_TYPE = 0x82
DISPATCH_NAT_TEST = bytes.fromhex("2702")
DISPATCH_FORWARDING = bytes.fromhex("fc0768081a00eb081b0920092509")  # 0xa1-0xa7
DISPATCH_EXIT = bytes.fromhex("1a00")
# The app raises "MQTT connection error" (QDC_400_004_010_001) from QIDI's MQTT state as Moonraker reports it (online,
# offline, or retrying), the app's QIDI account, and LAN only mode, each time Moonraker's mqtt_status or the app's
# bind_success, clear_user_info, or lan_mode_config_updated event arrives. Moonraker reports MQTT not connected when the
# app starts, so the app raises the error at every start, and deletes it only once its first request to QIDI's cloud
# comes back: about 10 ms later in LAN only mode, 0.9 s on a Max 4, and never with QIDI's servers blocked, so it stays
# on the touchscreen. The error's event callback, which no other error uses, tests the raise condition and branches to
# the add call, then tests the clear condition. Not branching leaves the error unraised, at start and when MQTT drops
# later, and the clear finds nothing to delete. The app's MQTT handling, its other errors, and LAN only mode are as
# QIDI wrote them.
RAISE_MQTT_ERROR = bytes.fromhex("41040054")  # b.ne +0x88, to the add call
# A print's Match filament screen greys out every Box slot and the external spool whose label isn't the file's filament
# type, and the app labels a slot with the first word of its filament's name: "PLA" for QIDI's PLA Basic, but "PLA+"
# for Generic PLA+ and "Rapid" for Elegoo Rapid PLA+, so those never match a PLA file. Klipper publishes each slot's
# filament_type beside the name, but the app reads only the name. The Box and external-spool parsers each read the name
# into the slot and then take its first word for the label; both ways out of the name read now reach a jump to new
# code instead, which looks up filament_type as the parser looks up the slot's other keys and, when Klipper sent it as a
# string, moves it into the label. An update without filament_type keeps the label, as one without the name keeps the
# name. The name stays as QIDI reads it, because the slot's edit dialog picks the current filament from it. The new
# code sits in the zero padding between .plt and .text, in the executable segment. It has no unwind entry, so an
# exception through it would end the app: it checks that the key is present and holds a string itself, rather than
# through the parser's dereference, which throws, and the calls it makes throw only when memory runs out.
# Plus 5 01.01.02.02's and Max 4 01.01.07.02's apps read filament_type into the label themselves, so their builds have
# none of these sites.
#
# Offsets are file offsets. The executable is mapped at 0x400000, a whole number of pages, so branches and ADRP
# computed between file offsets are the ones the loaded code needs.
NAME_READ_SKIPS_LABEL = bytes.fromhex("41030054")  # b.ne past the first-word label, 0x68 on
FIRST_WORD_LABEL = bytes.fromhex("020080d2")  # mov x2, #0: the start of name.substr(0, name.find(' '))
LABEL_BLOCK = 0x30  # from the branch to the first-word label
LABEL_BLOCK_END = 0x68  # from the branch to the parser's next field
# The parsers' stack frames, the same in every reviewed build: the slot's label string, the lookup's arguments, and the
# two slots each parser only uses as scratch for the first-word label.
SLOT_LABEL = 0x658
BOX_LOOKUP_KEYS, BOX_LOOKUP_PREFIX = 0x180, 0x3B8
BOX_ITERATOR, BOX_SCRATCH = 0x4D8, 0x4B8
EXTERNAL_ITERATOR, EXTERNAL_SCRATCH = 0x5D8, 0x5B8
# nlohmann::json value types, and where a std::map node of an object keeps its value.
JSON_OBJECT, JSON_STRING, MAP_NODE_VALUE = 1, 3, 0x40
# Valve Oil does everything else the app does besides drawing the screen on the printer and its network
# (vo_qidi_agent.py and its helpers, the Box monitor, and the USB mounter), and keeps the printer off QIDI's servers,
# so each of those duties is patched out of the app, and its cloud work and AI pause are done by nothing. That leaves
# what an operator starts from its screen and its power-loss resume prompt; with QIDI's LAN-only mode kept on, its
# account screen shows LAN Only Mode. Each site below names its duty; the analysis behind every site is in
# openspec/observations/qidi-platform.md (Touchscreen app duties):
#   print_state         the print-state writer power-loss resume reads: return at entry
#   mcu_id_cfg          rewriting MCU_ID.cfg, and on the Max 4 and Plus 5 deleting printer-*.cfg in the same function
#   printer_cfg_backups deleting printer-*.cfg (Smart 5, a separate call)
#   klipper_restart     the device monitor's heaters-off Klipper restart (Smart 5; elsewhere only the detector reached it)
#   box_count           saving box_count
#   ai_pause            the PAUSE sent for QIDI's AI service's spaghetti report, done by nothing since Valve Oil keeps
#                       that service stopped; its popup stays
#   agent               identifying to Moonraker as an agent: the type string becomes another client type, so
#                       Valve Oil's agent is the only one under the app's name
#   error_list_push     pushing its error list to QIDI's cloud
#   device_report       the background device status reports to QIDI's cloud. The one logout sends stays: the app
#                       reports an emptied user.token only after its own logout set its flag for it, so the empty
#                       token it reads at every start reports nothing
#   update_check        the background online update checks
#   p2p                 starting QIDI's P2P remote access
#   ssdp, mkswifi       LAN discovery announcements and replies
#   ntpdate             running ntpdate on Wi-Fi connect (not installed on these printers)
#   usb                 mounting and unmounting USB drives: the app instead takes a drive already mounted at
#                       gcodes/USB as ready when udev reports it, and Valve Oil's mounter mounts it and repeats the event
CHUNK = 1 << 20
RELEASED = "The touchscreen app is not a reviewed QIDI build; it was left as it is, and uninstall no longer restores it."


@dataclass(frozen=True)
class Site:
    offset: int
    original: bytes
    patched: bytes
    # Patched only while QIDI's package updates are blocked.
    blocking: bool = False
    # Patched by development builds only; every form now has QIDI's bytes here.
    retired: bool = False
    # The app's duty a takeover site switches off, or "" for the earlier sites.
    duty: str = ""


@dataclass(frozen=True)
class Build:
    # The name the app connects to Moonraker as, which Valve Oil's agent takes over.
    model: str
    size: int
    # File offsets, each inside one CHUNK, with QIDI's bytes and the patched ones.
    sites: tuple[Site, ...]
    patched_sha256: str
    blocked_sha256: str
    # Other combinations of the sites that package versions have written, without and with QIDI's package updates
    # blocked; updates finish or undo them.
    other_patched_sha256: tuple[str, ...] = ()
    other_blocked_sha256: tuple[str, ...] = ()

    def forms(self) -> tuple[str, ...]:
        return (self.patched_sha256, self.blocked_sha256, *self.other_patched_sha256, *self.other_blocked_sha256)

    def target(self, source_sha256: str, *, block: bool | None) -> str:
        """The patched result for a file of this build: blocked or not, or with block None, blocked only when the
        file already is."""
        if block is None:
            block = source_sha256 in (self.blocked_sha256, *self.other_blocked_sha256)
        return self.blocked_sha256 if block else self.patched_sha256

    @property
    def detector(self) -> Site:
        return next(site for site in self.sites if site.original == DETECTOR_CHANGED)

    @property
    def agent(self) -> Site:
        """The takeover site that tells the app is Valve Oil's: it no longer identifies as Moonraker's agent."""
        return next(site for site in self.sites if site.duty == "agent")


def _sites(show_branch: int | tuple[int, ...], clear_branch: int, need_update_store: int, extract: int, detector: int,
           mqtt_error: int, p2p: tuple[Site, ...], *,
           store: tuple[bytes, bytes] = (STORE_NEED_UPDATE, STORE_ZERO), takeover=()) -> tuple[Site, ...]:
    show_branches = show_branch if isinstance(show_branch, tuple) else (show_branch,)
    return (
        *(Site(branch, BRANCH_IF_EQUAL, BRANCH_IF_NOT_LONGER) for branch in show_branches),
        Site(clear_branch, BRANCH_IF_EQUAL, BRANCH_IF_NOT_LONGER),
        Site(need_update_store, *store, blocking=True),
        Site(extract, EXTRACT_PROLOGUE, RETURN_FALSE, retired=True),
        Site(detector, DETECTOR_CHANGED, DETECTOR_UNCHANGED),
        Site(mqtt_error, RAISE_MQTT_ERROR, NOP),
        *p2p,
        *(Site(offset, bytes.fromhex(original), bytes.fromhex(patched), duty=duty)
          for offset, original, patched, duty in takeover),
    )


def _p2p_sites(forward_constructor: int, forward_set_option: int, pushed_options: int,
               dispatch_table: int) -> tuple[Site, ...]:
    """The sites that keep P2P out of Peergine's relay mesh, from the forwarding client's constructor's rate store, its
    SetOption's rate store, the pushed-option branch to items 1-5, and the dispatcher's jump table."""
    def entry(message_type: int) -> int:
        return dispatch_table + 2 * (message_type - DISPATCH_FIRST_TYPE)

    return (
        Site(forward_constructor, STORE_FORWARD_SPEED, STORE_ZERO_FORWARD_SPEED),
        Site(forward_constructor + 0x10, STORE_FORWARD_USE, STORE_ZERO_FORWARD_USE),
        Site(forward_constructor + 0x18, STORE_FORWARD_MAX_SESSIONS, STORE_ZERO_FORWARD_MAX_SESSIONS),
        Site(forward_set_option, STORE_FORWARD_SPEED, STORE_ZERO_FORWARD_SPEED),
        Site(forward_set_option + 0x1C, STORE_FORWARD_USE, STORE_ZERO_FORWARD_USE),
        Site(forward_set_option + 0x28, STORE_FORWARD_MAX_SESSIONS, STORE_ZERO_FORWARD_MAX_SESSIONS),
        Site(pushed_options, PUSHED_FORWARD_ITEMS, PUSHED_ITEMS_IGNORED),
        Site(pushed_options + 0x18, PUSHED_LOG_PULL_ITEMS, NOP),
        Site(entry(0x8B), DISPATCH_NAT_TEST, DISPATCH_EXIT),
        Site(entry(0xA1), DISPATCH_FORWARDING, DISPATCH_EXIT * 7),
    )


@dataclass(frozen=True)
class TypeMatch:
    """A build's places for the filament-type label: the padding the new code goes in, a "filament_type" string, the
    std::string move-assign and dispose and nlohmann::json get<std::string> the parsers call, and for each parser the
    branch that skips its first-word label when the name is absent, its key lookup, and for the external spool the
    register holding the filament_info iterator and the iterator dereference it is read through."""
    padding: int
    padding_end: int
    type_key: int
    get_string: int
    assign: int
    dispose: int
    box_skip: int
    box_lookup: int
    external_skip: int
    external_lookup: int
    external_info: int
    dereference: int


SP = 31


def _add(rd: int, rn: int, imm: int) -> int:
    return 0x91000000 | imm << 10 | rn << 5 | rd


def _adrp(at: int, target: int, rd: int) -> int:
    pages = ((target >> 12) - (at >> 12)) & 0x1FFFFF
    return 0x90000000 | (pages & 3) << 29 | (pages >> 2) << 5 | rd


def _bl(at: int, target: int) -> int:
    return 0x94000000 | ((target - at) >> 2) & 0x3FFFFFF


def _b(at: int, target: int) -> int:
    return 0x14000000 | ((target - at) >> 2) & 0x3FFFFFF


def _b_cond(at: int, target: int, cond: int) -> int:
    return 0x54000000 | (((target - at) >> 2) & 0x7FFFF) << 5 | cond


EQ, NE = 0, 1


def _label_from_type(at: int, match: TypeMatch, lookup: list, iterator: int, scratch: int, back: int) -> list[int]:
    """Code at `at` that runs lookup (instruction makers leaving a json iterator at sp+iterator) and, when the key is in
    the object and its value is a string, moves the string into the slot's label through sp+scratch, then returns to
    the parser at `back`."""
    code: list[int] = []
    skips: list[tuple[int, int]] = []

    def here() -> int:
        return at + 4 * len(code)

    def skip(cond: int) -> None:
        skips.append((len(code), cond))
        code.append(0)

    for make in lookup:
        code.append(make(here()))
    code += [
        0xF9400000 | (iterator // 8) << 10 | SP << 5 | 0,  # ldr x0, [sp, iterator]: the object
        0x39400001,  # ldrb w1, [x0]
        0x7100001F | JSON_OBJECT << 10 | 1 << 5,  # cmp w1, object
    ]
    skip(NE)
    code += [
        0xF9400401,  # ldr x1, [x0, #8]: its std::map
        _add(1, 1, 8),  # the map's header, the end node
        0xF9400000 | ((iterator + 8) // 8) << 10 | SP << 5 | 0,  # ldr x0, [sp, iterator + 8]: the node found
        0xEB01001F,  # cmp x0, x1
    ]
    skip(EQ)
    code += [0x39400001 | MAP_NODE_VALUE << 10, 0x7100001F | JSON_STRING << 10 | 1 << 5]  # ldrb w1, [x0, value]; cmp
    skip(NE)
    code += [_add(0, 0, MAP_NODE_VALUE), _add(8, SP, scratch)]
    code.append(_bl(here(), match.get_string))
    code += [_add(1, SP, scratch), _add(0, SP, SLOT_LABEL)]
    code.append(_bl(here(), match.assign))
    code.append(_add(0, SP, scratch))
    code.append(_bl(here(), match.dispose))
    done = here()
    code.append(_b(done, back))
    for index, cond in skips:
        code[index] = _b_cond(at + 4 * index, done, cond)
    return code


def _type_match_sites(match: TypeMatch) -> tuple[Site, ...]:
    """The sites that label a Box slot or the external spool with Klipper's filament_type."""
    key = match.type_key
    box_lookup = [
        lambda at: _add(0, SP, BOX_LOOKUP_KEYS), lambda at: _add(1, SP, BOX_LOOKUP_PREFIX),
        lambda at: _adrp(at, key, 2), lambda at: _add(2, 2, key & 0xFFF), lambda at: _add(8, SP, BOX_ITERATOR),
        lambda at: _bl(at, match.box_lookup),
    ]
    box = _label_from_type(match.padding, match, box_lookup, BOX_ITERATOR, BOX_SCRATCH,
                           match.box_skip + LABEL_BLOCK_END)
    external_at = match.padding + 4 * len(box)
    external_lookup = [
        lambda at: 0xAA0003E0 | match.external_info << 16, lambda at: _bl(at, match.dereference),
        lambda at: _adrp(at, key, 1), lambda at: _add(1, 1, key & 0xFFF), lambda at: _add(8, SP, EXTERNAL_ITERATOR),
        lambda at: _bl(at, match.external_lookup),
    ]
    external = _label_from_type(external_at, match, external_lookup, EXTERNAL_ITERATOR, EXTERNAL_SCRATCH,
                                match.external_skip + LABEL_BLOCK_END)
    code = struct.pack("<%dI" % (len(box) + len(external)), *box, *external)
    if match.padding + len(code) > match.padding_end:
        raise ValueError("the filament-type label code does not fit the padding")

    def word(value: int) -> bytes:
        return struct.pack("<I", value)

    sites = [Site(match.padding, bytes(len(code)), code)]
    for skip, label in ((match.box_skip, match.padding), (match.external_skip, external_at)):
        sites.append(Site(skip, NAME_READ_SKIPS_LABEL, word(_b_cond(skip, skip + LABEL_BLOCK, NE))))
        sites.append(Site(skip + LABEL_BLOCK, FIRST_WORD_LABEL, word(_b(skip + LABEL_BLOCK, label))))
    return tuple(sites)


# Keyed by the SHA-256 of QIDI's executable.
BUILDS = {
    # QIDI Max 4 firmware 01.01.07.01
    "b5285d1a52f1180757360d0893f0f8ed619aaaa31d2d621917c0a963bc3fde4e": Build(
        model="QD_MAX4",
        size=91630352,
        sites=_sites(0x227368, 0x227510, 0x2C7FF0, 0x31BC90, 0x264288, 0x232228,
                     _p2p_sites(0x47568C, 0x475524, 0x43042C, 0x5592CFC), takeover=(
        (0x2C5520, "fd7bb4a9", "c0035fd6", "print_state"),
        (0x25D3E0, "ff030dd1", "c0035fd6", "mcu_id_cfg"),
        (0x25CDC0, "fd7bb8a9", "c0035fd6", "box_count"),
        (0x21D388, "c67f0194", "1f2003d5", "ai_pause"),
        (0x55644C8, "6167656e74000000", "646973706c617900", "agent"),
        (0x2D5580, "ff430dd1", "c0035fd6", "error_list_push"),
        (0x19AE00, "ff830bd1", "c0035fd6", "device_report"),
        (0x1C8864, "fd7baaa9", "c0035fd6", "device_report"),
        (0x2EF91C, "e80316aae50315aae40313aae3430391e20318aae1630291e00314aa060080520195fd97", "c0420091c00200f9df0600f9df4200391f2003d51f2003d51f2003d51f2003d51f2003d5", "device_report"),
        (0x2DE3EC, "1f1c0072779402f0f7c21291f9079f1a", "191c0072a0a1ff54779402f0f7c21291", "device_report"),
        (0x1C3F38, "7ab30314", "c0035fd6", "update_check"),
        (0x143C9C, "21b40514", "c0035fd6", "update_check"),
        (0x1B7FAC, "5de30314", "c0035fd6", "update_check"),
        (0x2B3620, "c83f0294", "1f2003d5", "p2p"),
        (0x2FB8E4, "01fcdf08", "c0035fd6", "ssdp"),
        (0x2FEE40, "ff0309d1", "c0035fd6", "mkswifi"),
        (0x33BBE0, "e078ff97", "1f2003d5", "ntpdate"),
        (0x261000, "a8f9ff97", "00008052", "usb"),
        (0x26104C, "e1031aaa", "01630191", "usb"),
        (0x261058, "83fdff97", "92f9ff97", "usb"),
        (0x261060, "400d0054", "80030054", "usb"),
        (0x261140, "80000054", "04000014", "usb"),
        (0x2612BC, "f9f8ff97", "00008052", "usb"),
        (0x2614D4, "e1830291", "61620191", "usb"),
        (0x2614D8, "63fcff97", "72f8ff97", "usb"),
        (0x2614F0, "600b0054", "20040054", "usb"),
        ))
        + _type_match_sites(TypeMatch(
            padding=0x49E90, padding_end=0x4A000, type_key=0x5C3DC8, get_string=0x2E5AE0, assign=0x48630,
            dispose=0x49060, box_skip=0x2CB6C8, box_lookup=0x2B2EB0, external_skip=0x2C9860, external_lookup=0x2E1DB0,
            external_info=26, dereference=0x2E1AF0)),
        patched_sha256="652b7dd316e6a958de11eda0e120fad1eb3f2e373cd9e5f45761cae47134bdcf",
        blocked_sha256="814d22f97fd084d7c3b4342ec93c74b5b767d26b7b519421a547c3d333bac465",
        other_patched_sha256=("4fb901e9ff3cb3e7f8a0052566214a0aabb31a9e6a6a079593aba766a934d42f",
                              "4990726977597ce26d2e589e288dee24a0ac3c3048e5dc62b8df939f0a6d9c1b",
                              "e111dc312b0f1b6e0b33a80058efae0abd8721fa73130b01f9838391a4e55898",
                              "1985cd3e695a043eb3b6eabc45d7f800dba03339c4fb52e8dddedc4fb12eeb39",
                              "12d9fb74dc73db348cd53460aa3a343f7562890408cc8c32bbe37db2e34b119a",
                              "a3870f07e664f7382a22a9ee6167b5c356ed0bc836ed6e99b0f40f00c1a386c2"),
        other_blocked_sha256=("8ffb4528a66a22b3de26ace03028dd771c234813634220fb5e17570e1540fd94",
                              "c04e3d0e55a3be6107c687990e86f3b26f51b6d5b2c8e6d3b87f36cceb5cc04f",
                              "2ead362a7fa4b09fd63f82db60b8d168bacc09cdbab9a73c1335a7211b88ad43",
                              "f76fd32b77cdaaacf97daa8c8947862ae7d15aaf9b7df485d600a0f41ddebc08",
                              "5630d1f9405de7a6599c48a6758287dd25ce8bc23c80643bc3e66654daa0058a",
                              "b0e9a4a6ce399ea4feaa096851ae022d410caaa47eb2f60227c3483f46b5e138"),
    ),
    # QIDI Max 4 firmware 01.01.07.02
    "9287c89267f997c8a20d3f7d2f372a4d2cb96203c2ee461e6cf90bc69741e1d7": Build(
        model="QD_MAX4",
        size=91659672,
        # The show checks of the buffer, online update, and MCU and THR popups, then the closed-loop board's.
        sites=_sites((0x2292D0, 0x2297A0, 0x229904), 0x229130, 0x2CAF10, 0x320510, 0x2665C8, 0x233A98,
                     _p2p_sites(0x47A8BC, 0x47A754, 0x43565C, 0x55992FC),
                     store=(STORE_NEED_UPDATE_PLUS5_0202, STORE_ZERO_PLUS5_0202), takeover=(
        (0x2C8430, "fd7bb4a9", "c0035fd6", "print_state"),
        (0x25F720, "ff030dd1", "c0035fd6", "mcu_id_cfg"),
        (0x25F100, "fd7bb8a9", "c0035fd6", "box_count"),
        (0x21EC38, "76820194", "1f2003d5", "ai_pause"),
        (0x556A310, "6167656e74000000", "646973706c617900", "agent"),
        (0x2D7D60, "ff430dd1", "c0035fd6", "error_list_push"),
        (0x19D360, "ff830bd1", "c0035fd6", "device_report"),
        (0x1CAE14, "fd7baaa9", "c0035fd6", "device_report"),
        (0x2F2D3C, "e80316aae50315aae40313aae3430391e20318aae1630291e00314aa06008052c990fd97", "c0420091c00200f9df0600f9df4200391f2003d51f2003d51f2003d51f2003d51f2003d5", "device_report"),
        (0x2E0CF0, "1f1c0072979402f0f7822a91f9079f1a", "191c007280a1ff54979402f0f7822a91", "device_report"),
        (0x1C64E8, "f6b60314", "c0035fd6", "update_check"),
        (0x14417C, "d1bf0514", "c0035fd6", "update_check"),
        (0x1BA52C, "e5e60314", "c0035fd6", "update_check"),
        (0x2B651C, "cd470294", "1f2003d5", "p2p"),
        (0x2FED04, "01fcdf08", "c0035fd6", "ssdp"),
        (0x302260, "ff0309d1", "c0035fd6", "mkswifi"),
        (0x340AE0, "4077ff97", "1f2003d5", "ntpdate"),
        (0x263340, "a8f9ff97", "00008052", "usb"),
        (0x26338C, "e1031aaa", "01630191", "usb"),
        (0x263398, "83fdff97", "92f9ff97", "usb"),
        (0x2633A0, "400d0054", "80030054", "usb"),
        (0x263480, "80000054", "04000014", "usb"),
        (0x2635FC, "f9f8ff97", "00008052", "usb"),
        (0x263814, "e1830291", "61620191", "usb"),
        (0x263818, "63fcff97", "72f8ff97", "usb"),
        (0x263830, "600b0054", "20040054", "usb"),
        ))
        + (Site(0x22900C, SHOW_IF_LENGTHS_DIFFER, SHOW_IF_LONGER),),
        patched_sha256="bfea6e716bed15bc6608cbafc7c2356933eb1625f1d753a9f2960864af275166",
        blocked_sha256="82921abc9d9d3fbbb9f60be84263526ea78ae66210ea001fe4f939463a00a568",
        other_patched_sha256=("089d888cea1fcb6baa9a54712aac55cdd3b5beff9cfcff4fd902aba1137e8ed6",),
        other_blocked_sha256=("849a86b7e1708d31af8d19346d247e197e2b68a9c9ab1eda8f7233e298a43a9e",),
    ),
    # QIDI Plus 5 firmware 01.01.02.01
    "6c09a58a636427d468b77b78149e5305f0b1182e785a0abff67aec53b3aeb855": Build(
        model="QD_PLUS5",
        size=91626272,
        sites=_sites(0x226518, 0x2266C0, 0x2C71A0, 0x31AE40, 0x263438, 0x2313D8,
                     _p2p_sites(0x47551C, 0x4753B4, 0x4302BC, 0x5591D4C), takeover=(
        (0x2C46D0, "fd7bb4a9", "c0035fd6", "print_state"),
        (0x25C590, "ff030dd1", "c0035fd6", "mcu_id_cfg"),
        (0x25BF70, "fd7bb8a9", "c0035fd6", "box_count"),
        (0x21C538, "c67f0194", "1f2003d5", "ai_pause"),
        (0x5563518, "6167656e74000000", "646973706c617900", "agent"),
        (0x2D4730, "ff430dd1", "c0035fd6", "error_list_push"),
        (0x19AC40, "ff830bd1", "c0035fd6", "device_report"),
        (0x1C8A04, "fd7baaa9", "c0035fd6", "device_report"),
        (0x2EEACC, "e80316aae50315aae40313aae3430391e20318aae1630291e00314aa060080520195fd97", "c0420091c00200f9df0600f9df4200391f2003d51f2003d51f2003d51f2003d51f2003d5", "device_report"),
        (0x2DD59C, "1f1c0072779402f0f7021491f9079f1a", "191c0072a0a1ff54779402f0f7021491", "device_report"),
        (0x1C40D8, "7eaf0314", "c0035fd6", "update_check"),
        (0x1438BC, "85b10514", "c0035fd6", "update_check"),
        (0x1B814C, "61df0314", "c0035fd6", "update_check"),
        (0x2B27D0, "00430294", "1f2003d5", "p2p"),
        (0x2FAA94, "01fcdf08", "c0035fd6", "ssdp"),
        (0x2FDFF0, "ff0309d1", "c0035fd6", "mkswifi"),
        (0x33BA70, "a875ff97", "1f2003d5", "ntpdate"),
        (0x2601B0, "a8f9ff97", "00008052", "usb"),
        (0x2601FC, "e1031aaa", "01630191", "usb"),
        (0x260208, "83fdff97", "92f9ff97", "usb"),
        (0x260210, "400d0054", "80030054", "usb"),
        (0x2602F0, "80000054", "04000014", "usb"),
        (0x26046C, "f9f8ff97", "00008052", "usb"),
        (0x260684, "e1830291", "61620191", "usb"),
        (0x260688, "63fcff97", "72f8ff97", "usb"),
        (0x2606A0, "600b0054", "20040054", "usb"),
        ))
        + _type_match_sites(TypeMatch(
            padding=0x49E90, padding_end=0x4A000, type_key=0x5C2CA8, get_string=0x2E4C90, assign=0x48630,
            dispose=0x49060, box_skip=0x2CA878, box_lookup=0x2B2060, external_skip=0x2C8A10, external_lookup=0x2E0F60,
            external_info=26, dereference=0x2E0CA0)),
        patched_sha256="fa8040eabbee33def3a79dfe83f1a64ecd6d81e2194b66b8792e5d196b845849",
        blocked_sha256="347265b0fd091ab6036b7a02f443e31dffece290968d551f7cca244a4ea3b021",
        other_patched_sha256=("b347c19f5c150a5ce7ae95be9eefc0fe028bd6ea874f0775db214f6c5ee33b96",
                              "11450f6cd76586075d1c68820e86279f2f1c53abe1599d4bcb31e03b5f798434",
                              "595302f560325d63da9a79e39db52bf0d73d663c425cae18c589a9f2b81d3ed8",
                              "3747eacf01bd9ac65971d01f27a265d2c4bccb14a49fec2f8297f17f120c5a87",
                              "7a95c573ff94455e128fe5f6d9e8b95b55ed693cb6f27d24ca7da72a5ac97802",
                              "6d39c942beca99a53ab1f3107d0e355f573f9450e3d5b66b33c83544e5b22661"),
        other_blocked_sha256=("cf070af1552acc183b5ea7d3db919357e6ce689bc8f2f0eb1c5849d745ec1698",
                              "950321f35419bfa9e7597c0e2a5b28e234b612a45d989b26e5bab52699aa9691",
                              "e3899d3a82060e8d25aee962f119d67552a742b93b10d5bb2504febd941b1115",
                              "7d836a445197a2d5cd1c4b056380f6c12868d42fd66c5ca4a4bafa3fcd618791",
                              "8db64463e4a8eb24b233c6d8878e5948532a8fce8545575ed7d45423acc146ea",
                              "58fe6ed91c5f609777d8b4226dae59219563b1c900d0b4ad7edfe1a2fb4a8cdc",
                              # The Plus 5 test printer's development install of the duties takeover.
                              "7d09badd9da98ff8bc684f96dc9084bb02a910f3e2b2f0d38a2b9f0dbfb29e12"),
    ),
    # QIDI Plus 5 firmware 01.01.02.02
    "51e8f485089febd97a0ba27874710c1e95c96d367700ed15d0c6b44a960c0e2a": Build(
        model="QD_PLUS5",
        size=91659688,
        # The show checks of the buffer, online update, and MCU and THR popups, then the closed-loop board's.
        sites=_sites((0x2294A0, 0x229970, 0x229AD4), 0x229300, 0x2CB0E0, 0x31F6E0, 0x266798, 0x233C68,
                     _p2p_sites(0x47976C, 0x479604, 0x43450C, 0x559934C),
                     store=(STORE_NEED_UPDATE_PLUS5_0202, STORE_ZERO_PLUS5_0202), takeover=(
        (0x2C8600, "fd7bb4a9", "c0035fd6", "print_state"),
        (0x25F8F0, "ff030dd1", "c0035fd6", "mcu_id_cfg"),
        (0x25F2D0, "fd7bb8a9", "c0035fd6", "box_count"),
        (0x21EE08, "76820194", "1f2003d5", "ai_pause"),
        (0x556A360, "6167656e74000000", "646973706c617900", "agent"),
        (0x2D7F30, "ff430dd1", "c0035fd6", "error_list_push"),
        (0x19C1C0, "ff830bd1", "c0035fd6", "device_report"),
        (0x1C9FD4, "fd7baaa9", "c0035fd6", "device_report"),
        (0x2F1F0C, "e80316aae50315aae40313aae3430391e20318aae1630291e00314aa06008052c994fd97", "c0420091c00200f9df0600f9df4200391f2003d51f2003d51f2003d51f2003d51f2003d5", "device_report"),
        (0x2E0EC0, "1f1c0072979402f0f7c22b91f9079f1a", "191c007280a1ff54979402f0f7c22b91", "device_report"),
        (0x1C56A8, "faba0314", "c0035fd6", "update_check"),
        (0x143DBC, "35c10514", "c0035fd6", "update_check"),
        (0x1B96EC, "e9ea0314", "c0035fd6", "update_check"),
        (0x2B66EC, "05430294", "1f2003d5", "p2p"),
        (0x2FDED4, "01fcdf08", "c0035fd6", "ssdp"),
        (0x301430, "ff0309d1", "c0035fd6", "mkswifi"),
        (0x33F990, "0878ff97", "1f2003d5", "ntpdate"),
        (0x263510, "a8f9ff97", "00008052", "usb"),
        (0x26355C, "e1031aaa", "01630191", "usb"),
        (0x263568, "83fdff97", "92f9ff97", "usb"),
        (0x263570, "400d0054", "80030054", "usb"),
        (0x263650, "80000054", "04000014", "usb"),
        (0x2637CC, "f9f8ff97", "00008052", "usb"),
        (0x2639E4, "e1830291", "61620191", "usb"),
        (0x2639E8, "63fcff97", "72f8ff97", "usb"),
        (0x263A00, "600b0054", "20040054", "usb"),
        ))
        + (Site(0x2291DC, SHOW_IF_LENGTHS_DIFFER, SHOW_IF_LONGER),),
        patched_sha256="111a72d6ec18625664bc739192d1212056fd753d8f97f5b98b5f3d6fca3ac056",
        blocked_sha256="ea48eace33760277ba600c5922cea5f8f8065b9f4eb0f21b8d85d9ae8b50865c",
        other_patched_sha256=("8df1f51426fd2190809f7a3168edc52cefceda590e20abf7fc761d620aeb0db8",),
        other_blocked_sha256=("e7ed0165dd7d4a831a40c905d11a80b8caa7c4133ad5da56e7be7f355d802498",),
    ),
    # QIDI Smart 5 firmware 01.01.02.01
    "6beea0a13cc7448c7f6ab8c345915f22e6d5c04c917a66478d6e43b1ceecd6bf": Build(
        model="S5",
        size=91322056,
        sites=_sites(0x1F0558, 0x1F0700, 0x293200, 0x2E85D0, 0x22EA38, 0x1FB028,
                     _p2p_sites(0x43FD4C, 0x43FBE4, 0x3FAAEC, 0x555AB14),
                     store=(STORE_NEED_UPDATE_SMART5, STORE_ZERO_SMART5), takeover=(
        (0x290250, "fd7bb4a9", "c0035fd6", "print_state"),
        (0x227E3C, "65ef0294", "1f2003d5", "mcu_id_cfg"),
        (0x228234, "67ee0294", "1f2003d5", "mcu_id_cfg"),
        (0x2283E8, "b67df897", "1f2003d5", "printer_cfg_backups"),
        (0x2267FC, "fea10094", "1f2003d5", "klipper_restart"),
        (0x22ECDC, "c6800094", "1f2003d5", "klipper_restart"),
        (0x227560, "fd7bb8a9", "c0035fd6", "box_count"),
        (0x1E6308, "ee850194", "1f2003d5", "ai_pause"),
        (0x552C468, "6167656e74", "6f74686572", "agent"),
        (0x2A07E0, "ff430dd1", "c0035fd6", "error_list_push"),
        (0x167F60, "ff830bd1", "c0035fd6", "device_report"),
        (0x18F4A4, "fd7bb0a9", "c0035fd6", "device_report"),
        (0x18F120, "fd7bb0a9", "c0035fd6", "device_report"),
        (0x2BB25C, "e80316aae50315aae40313aae3430391e20318aae1630291e00314aa06008052998cfd97", "c0420091c00200f9df0600f9df4200391f2003d51f2003d51f2003d51f2003d51f2003d5", "device_report"),
        (0x2A9A5C, "1f1c0072fc031991f3079f1a", "131c0072fc031991c0030054", "device_report"),
        (0x21DE78, "be930294", "f1000014", "update_check"),
        (0x217AB0, "fd7ba9a9fd030091f35301a9f55b02a9f76303a9", "00410091000100f91f0500f91f410039c0035fd6", "update_check"),
        (0x27D664, "fd7bb0a9", "c0035fd6", "p2p"),
        (0x2C6C10, "fd7bb6a9", "c0035fd6", "ssdp"),
        (0x2CB780, "ff0309d1fd7b00a9", "20008052c0035fd6", "mkswifi"),
        (0x308198, "c679ff97", "1f2003d5", "ntpdate"),
        (0x22B7B0, "a8f9ff97", "00008052", "usb"),
        (0x22B7FC, "e1031aaa", "01630191", "usb"),
        (0x22B808, "83fdff97", "92f9ff97", "usb"),
        (0x22B810, "400d0054", "80030054", "usb"),
        (0x22B8F0, "80000054", "04000014", "usb"),
        (0x22BA6C, "f9f8ff97", "00008052", "usb"),
        (0x22BC84, "e1830291", "61620191", "usb"),
        (0x22BC88, "63fcff97", "72f8ff97", "usb"),
        (0x22BCA0, "600b0054", "20040054", "usb"),
        ))
        + _type_match_sites(TypeMatch(
            padding=0x49F20, padding_end=0x4A000, type_key=0x54D3598, get_string=0x2B13C0, assign=0x486B0,
            dispose=0x490E0, box_skip=0x2968E0, box_lookup=0x27D4E4, external_skip=0x294A70, external_lookup=0x2AD6A0,
            external_info=25, dereference=0x2AD3E0)),
        patched_sha256="223e527898650d7cb462d18e92bfc1aea7acd1397eb59fd643c68584c21ac9f8",
        blocked_sha256="ca0b9d53cc2fcdd712d87e8982c7f3261e2e4ea063f1b40033d7a6d152d6bf13",
        other_patched_sha256=("5d37af608d72a372ac813d46836b3897e2b7c715ec6b28d41d3eaecd89283e2e",
                              "a8ac743886d5da5928aa8f9ec51bd2628290910dc5fca4bb36cf2872dfa21284",
                              "fc5b94b3a4e6c6f62f08ae0556403e9a7eaeb82723ef69632ea9102ee67c9224",
                              "2ca522f96c95f2dd2836024f09fb267065d0da21be05161495b855f12808958c"),
        other_blocked_sha256=("07be7469e3a3aff068784070188838626ec2754b3b5ecadc667052f3925fd0a1",
                              "799383efe3f1ea32cd4384fa27eb7d96b17d3f5d591e54157ee4619f2da22a1b",
                              "3dcbf061ba26045022aaf3c3e3f2239feb4b4bc4b30bf0103ab8250d62b8858b",
                              "69173cefdf2791d328353a215a3214ec99493a09f5d5953fc5fa34c2c01dcbfa",
                              # The Smart 5 test printer's development installs: one blocked the unpack step too,
                              # one took over the app's duties without the P2P sites.
                              "5f00200ad708916bfaa939be5e746d43e39e852db6cf19b2178956f0af94afb6",
                              "02b2659da7c0e98a62147c09ba2a8c55a87b42a7e460ef2540c2af066568bd28"),
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


def reviewed_sizes() -> frozenset[int]:
    """Sizes of every reviewed build; the patches keep the size, so other files aren't hashed."""
    return frozenset(build.size for build in BUILDS.values())


def _lookup(sha256: str) -> tuple[str, Build] | None:
    """QIDI's SHA-256 and the reviewed build that sha256 is QIDI's executable of, or one of this package's patches of."""
    for stock, build in BUILDS.items():
        if sha256 == stock or sha256 in build.forms():
            return stock, build
    return None


def _build(sha256: str) -> Build | None:
    found = _lookup(sha256)
    return found[1] if found is not None else None


def stock_sha256(sha256: str) -> str | None:
    """QIDI's SHA-256 of the reviewed build that sha256 is a form of."""
    found = _lookup(sha256)
    return found[0] if found is not None else None


def same_build(sha256: str, other_sha256: str) -> bool:
    """Whether both are forms, QIDI's or patched, of one reviewed build."""
    found = _lookup(sha256)
    return found is not None and found == _lookup(other_sha256)


def patched_sha256(source_sha256: str, *, block: bool | None) -> str | None:
    build = _build(source_sha256)
    return build.target(source_sha256, block=block) if build is not None else None


def needs_patch(path: Path, *, block: bool | None) -> bool:
    # The executable is about a fifth of the printer's memory, so it is hashed and copied in pieces.
    if path.stat().st_size in {build.size for build in BUILDS.values()}:
        sha256 = sha256_file(path)
        build = _build(sha256)
        if build is not None:
            return sha256 != build.target(sha256, block=block)
    raise QidiClientFirmwarePopupError("The touchscreen app is not a reviewed QIDI build; preserving it.")


def write_patched(source: Path, destination: Path, *, block: bool | None) -> None:
    sha256 = sha256_file(source)
    build = _build(sha256)
    if build is None:
        raise QidiClientFirmwarePopupError("The touchscreen app is not a reviewed QIDI build; preserving it.")
    target = build.target(sha256, block=block)
    blocked = target == build.blocked_sha256
    _write_form(source, destination, build, target,
                lambda site: site.original if site.retired or (site.blocking and not blocked) else site.patched)


def write_original(source: Path, destination: Path) -> str:
    """Write QIDI's executable of the build source is a form of, and return its SHA-256."""
    found = _lookup(sha256_file(source))
    if found is None:
        raise QidiClientFirmwarePopupError("The touchscreen app is not a reviewed QIDI build; preserving it.")
    stock, build = found
    _write_form(source, destination, build, stock, lambda site: site.original)
    return stock


def _write_form(source: Path, destination: Path, build: Build, expected: str, wanted) -> None:
    digest = hashlib.sha256()
    with source.open("rb") as reader, destination.open("wb") as writer:
        position = 0
        while chunk := bytearray(reader.read(CHUNK)):
            for site in build.sites:
                if position <= site.offset and site.offset + len(site.original) <= position + len(chunk):
                    start = site.offset - position
                    if chunk[start:start + len(site.original)] in (site.original, site.patched):
                        chunk[start:start + len(site.original)] = wanted(site)
            digest.update(chunk)
            writer.write(chunk)
            position += len(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    if digest.hexdigest() != expected:
        destination.unlink(missing_ok=True)
        raise QidiClientFirmwarePopupError("The patched touchscreen app does not match the reviewed result.")
