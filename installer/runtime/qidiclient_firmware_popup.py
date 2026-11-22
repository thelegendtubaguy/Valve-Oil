from __future__ import annotations

import hashlib
import os
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


@dataclass(frozen=True)
class Build:
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


def _sites(show_branch: int, clear_branch: int, need_update_store: int, extract: int, detector: int,
           p2p: tuple[Site, ...], *,
           store: tuple[bytes, bytes] = (STORE_NEED_UPDATE, STORE_ZERO)) -> tuple[Site, ...]:
    return (
        Site(show_branch, BRANCH_IF_EQUAL, BRANCH_IF_NOT_LONGER),
        Site(clear_branch, BRANCH_IF_EQUAL, BRANCH_IF_NOT_LONGER),
        Site(need_update_store, *store, blocking=True),
        Site(extract, EXTRACT_PROLOGUE, RETURN_FALSE, retired=True),
        Site(detector, DETECTOR_CHANGED, DETECTOR_UNCHANGED),
        *p2p,
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


# Keyed by the SHA-256 of QIDI's executable.
BUILDS = {
    # QIDI Max 4 firmware 01.01.07.01
    "b5285d1a52f1180757360d0893f0f8ed619aaaa31d2d621917c0a963bc3fde4e": Build(
        size=91630352,
        sites=_sites(0x227368, 0x227510, 0x2C7FF0, 0x31BC90, 0x264288,
                     _p2p_sites(0x47568C, 0x475524, 0x43042C, 0x5592CFC)),
        patched_sha256="e111dc312b0f1b6e0b33a80058efae0abd8721fa73130b01f9838391a4e55898",
        blocked_sha256="2ead362a7fa4b09fd63f82db60b8d168bacc09cdbab9a73c1335a7211b88ad43",
        other_patched_sha256=("1985cd3e695a043eb3b6eabc45d7f800dba03339c4fb52e8dddedc4fb12eeb39",
                              "12d9fb74dc73db348cd53460aa3a343f7562890408cc8c32bbe37db2e34b119a",
                              "a3870f07e664f7382a22a9ee6167b5c356ed0bc836ed6e99b0f40f00c1a386c2"),
        other_blocked_sha256=("f76fd32b77cdaaacf97daa8c8947862ae7d15aaf9b7df485d600a0f41ddebc08",
                              "5630d1f9405de7a6599c48a6758287dd25ce8bc23c80643bc3e66654daa0058a",
                              "b0e9a4a6ce399ea4feaa096851ae022d410caaa47eb2f60227c3483f46b5e138"),
    ),
    # QIDI Plus 5 firmware 01.01.02.01
    "6c09a58a636427d468b77b78149e5305f0b1182e785a0abff67aec53b3aeb855": Build(
        size=91626272,
        sites=_sites(0x226518, 0x2266C0, 0x2C71A0, 0x31AE40, 0x263438,
                     _p2p_sites(0x47551C, 0x4753B4, 0x4302BC, 0x5591D4C)),
        patched_sha256="595302f560325d63da9a79e39db52bf0d73d663c425cae18c589a9f2b81d3ed8",
        blocked_sha256="e3899d3a82060e8d25aee962f119d67552a742b93b10d5bb2504febd941b1115",
        other_patched_sha256=("3747eacf01bd9ac65971d01f27a265d2c4bccb14a49fec2f8297f17f120c5a87",
                              "7a95c573ff94455e128fe5f6d9e8b95b55ed693cb6f27d24ca7da72a5ac97802",
                              "6d39c942beca99a53ab1f3107d0e355f573f9450e3d5b66b33c83544e5b22661"),
        other_blocked_sha256=("7d836a445197a2d5cd1c4b056380f6c12868d42fd66c5ca4a4bafa3fcd618791",
                              "8db64463e4a8eb24b233c6d8878e5948532a8fce8545575ed7d45423acc146ea",
                              "58fe6ed91c5f609777d8b4226dae59219563b1c900d0b4ad7edfe1a2fb4a8cdc"),
    ),
    # QIDI Smart 5 firmware 01.01.02.01
    "6beea0a13cc7448c7f6ab8c345915f22e6d5c04c917a66478d6e43b1ceecd6bf": Build(
        size=91322056,
        sites=_sites(0x1F0558, 0x1F0700, 0x293200, 0x2E85D0, 0x22EA38,
                     _p2p_sites(0x43FD4C, 0x43FBE4, 0x3FAAEC, 0x555AB14),
                     store=(STORE_NEED_UPDATE_SMART5, STORE_ZERO_SMART5)),
        patched_sha256="fc5b94b3a4e6c6f62f08ae0556403e9a7eaeb82723ef69632ea9102ee67c9224",
        blocked_sha256="3dcbf061ba26045022aaf3c3e3f2239feb4b4bc4b30bf0103ab8250d62b8858b",
        other_patched_sha256=("2ca522f96c95f2dd2836024f09fb267065d0da21be05161495b855f12808958c",),
        other_blocked_sha256=("69173cefdf2791d328353a215a3214ec99493a09f5d5953fc5fa34c2c01dcbfa",
                              # The Smart 5 test printer's development install blocked the unpack step too.
                              "5f00200ad708916bfaa939be5e746d43e39e852db6cf19b2178956f0af94afb6"),
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
