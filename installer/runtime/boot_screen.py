"""Valve Oil's boot screen: the boot logo in the boot partition, and the boot animation until QIDI's app draws.

The bootloader shows the boot logo from the moment the printer is switched on, and the kernel keeps it until Linux
starts; boot_logo.py replaces QIDI's logo with installer/system/touchscreen/boot-logo.png. From a few seconds after
Linux starts until QIDI's touchscreen app first draws, tltg-boot-animation.service plays boot-animation.bin, whose first
frame is the boot logo, so the animation starts where the logo leaves off.

The animation file holds its first frame whole and, for each later frame, only the rectangles that changed:

    b"VOBA", version (1), frames per second, width, height, frame count (<4sBBHHH)
    per frame: rectangle count (<B), then per rectangle x, y, width, height, compressed size (<HHHHI) and that many
    bytes of zlib-compressed RGB rows

Coordinates are those of the upright 800x480 screen; the player turns them for a portrait framebuffer.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .errors import InstallerError

MAGIC = b"VOBA"
VERSION = 1
HEADER = struct.Struct("<4sBBHHH")
RECTANGLE = struct.Struct("<HHHHI")

IMAGES_DIR = Path(__file__).resolve().parents[1] / "system/touchscreen"
BOOT_LOGO_IMAGE = IMAGES_DIR / "boot-logo.png"
ANIMATION_FILE = IMAGES_DIR / "boot-animation.bin"

UNIT_OPERATION = "boot_animation_unit"
PLAYER_OPERATION = "boot_animation_player"
UNIT = "tltg-boot-animation.service"
UNIT_FILE = f"/etc/systemd/system/{UNIT}"
PLAYER_FILE = "/usr/local/lib/tltg_boot_animation.py"
PLAYER_PAYLOAD = Path(__file__).resolve().parents[1] / "system/vo_boot_animation.py"
PLAYER_MARKER = "# tltg-optimized:boot-animation\n"
UNIT_MARKER = "# tltg-optimized:boot-animation-unit\n"
# The player's data line, which the installed copy carries the animation in.
DATA_LINE = "ANIMATION = None\n"

# Started as soon as local file systems are mounted, without waiting for the rest of the boot. It plays until QIDI's
# touchscreen app runs, or at most 2 minutes, at low priority so the boot isn't slowed. Starting it after boot draws
# nothing, since the app is already running.
UNIT_CONTENT = UNIT_MARKER + f"""\
# Valve Oil's boot animation, shown until the touchscreen app draws. Installed by Valve Oil.
[Unit]
Description=Valve Oil boot animation
DefaultDependencies=no
After=local-fs.target
ConditionPathExists={PLAYER_FILE}

[Service]
Type=simple
User=qidi
SupplementaryGroups=video
ExecStart=/usr/bin/python3 -I -S {PLAYER_FILE}
TimeoutStopSec=5

[Install]
WantedBy=sysinit.target
"""


@dataclass(frozen=True)
class Rectangle:
    x: int
    y: int
    width: int
    height: int
    rgb: bytes  # rows of RGB, width * height * 3 bytes


@dataclass(frozen=True)
class Animation:
    fps: int
    width: int
    height: int
    frames: tuple[tuple[Rectangle, ...], ...]


def encode(animation: Animation) -> bytes:
    parts = [HEADER.pack(MAGIC, VERSION, animation.fps, animation.width, animation.height, len(animation.frames))]
    for frame in animation.frames:
        parts.append(struct.pack("<B", len(frame)))
        for rect in frame:
            data = zlib.compress(rect.rgb, 9)
            parts.append(RECTANGLE.pack(rect.x, rect.y, rect.width, rect.height, len(data)) + data)
    return b"".join(parts)


def decode(data: bytes) -> Animation:
    try:
        magic, version, fps, width, height, count = HEADER.unpack_from(data, 0)
        if magic != MAGIC or version != VERSION or not fps:
            raise ValueError("not a Valve Oil boot animation")
        position, frames = HEADER.size, []
        for _ in range(count):
            rectangles = []
            (number,) = struct.unpack_from("<B", data, position)
            position += 1
            for _ in range(number):
                x, y, w, h, size = RECTANGLE.unpack_from(data, position)
                position += RECTANGLE.size
                rgb = zlib.decompress(data[position:position + size])
                position += size
                if len(rgb) != w * h * 3 or x + w > width or y + h > height:
                    raise ValueError("a rectangle doesn't fit its frame")
                rectangles.append(Rectangle(x, y, w, h, rgb))
            frames.append(tuple(rectangles))
        if position != len(data):
            raise ValueError("trailing data")
    except (struct.error, zlib.error) as exc:
        raise ValueError(f"damaged boot animation: {exc}") from exc
    first = frames[0] if frames else ()
    if len(first) != 1 or (first[0].x, first[0].y, first[0].width, first[0].height) != (0, 0, width, height):
        raise ValueError("the first frame must be whole")
    return Animation(fps, width, height, tuple(frames))


@lru_cache(maxsize=1)
def player_source() -> str:
    """The installed player: the bundled player with the bundled animation in it."""
    import base64

    try:
        source = PLAYER_PAYLOAD.read_text(encoding="utf-8")
        animation = ANIMATION_FILE.read_bytes()
    except OSError as exc:
        raise InstallerError(f"The boot animation is missing from the package: {exc}") from exc
    if not source.startswith(PLAYER_MARKER) or source.count(DATA_LINE) != 1:
        raise InstallerError("The boot animation player lacks its package marker or data line.")
    decode(animation)
    source = source.replace(DATA_LINE, f"ANIMATION = {base64.b64encode(animation).decode('ascii')!r}\n")
    compile(source, "tltg_boot_animation.py", "exec")
    return source


def _patch_created(current: str, desired: str, marker: str, name: str) -> str:
    if current == "" or current.startswith(marker):
        return desired
    raise InstallerError(f"Existing {name} is unrecognized and was preserved.")


def patch_player(current: str) -> str:
    return _patch_created(current, player_source(), PLAYER_MARKER, PLAYER_FILE)


def patch_unit(current: str) -> str:
    return _patch_created(current, UNIT_CONTENT, UNIT_MARKER, UNIT_FILE)
