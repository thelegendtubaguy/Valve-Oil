"""The touchscreen message while a firmware update has QIDI's touchscreen app stopped.

A still image is written to the framebuffer: landscape text drawn upright on the Max 4's 800x480 panel and turned for
the Plus 5's 480x800 panel, which QIDI's app also draws on its side (see tltg_remote_screen.py). The geometry, pixel
format, and line length are read from the framebuffer each time; the text is drawn from the 5x7 font below, so no
image file or third-party module is needed. Drawing is best-effort: any failure is logged and the update goes on.
The app repaints the whole panel when it starts again.

QIDI's app puts the panel to sleep by setting the backlight's brightness to 0 and leaves it there when it is stopped,
so the message would sit on a dark panel; light() turns the backlight on. The app sets its own brightness as it starts.
"""
from __future__ import annotations

import fcntl
import itertools
import os
import shlex
import struct
from pathlib import Path

FRAMEBUFFER = "/dev/fb0"
BACKLIGHT = "/sys/class/backlight/backlight"
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602
# struct fb_fix_screeninfo up to line_length, with the platform's alignment.
FIX_SCREENINFO = struct.Struct("@16sLIIIIHHHI")

BACKGROUND = (16, 18, 24)
TEXT = (235, 235, 235)
WARNING = (255, 176, 0)

# Lines of (scale, color, text), centered top to bottom on the upright landscape screen.
UPDATING = (
    (4, TEXT, "Valve Oil is updating"),
    (4, TEXT, "the printer's firmware."),
    (4, WARNING, "Don't turn the printer off."),
    (3, TEXT, "The printer restarts by itself"),
    (3, TEXT, "when the update is done."),
)
STOPPED = (
    (4, TEXT, "The firmware update"),
    (4, TEXT, "didn't finish."),
    (4, WARNING, "Don't turn the printer off."),
    (3, TEXT, "Open Fluidd on a computer"),
    (3, TEXT, "to see what to do next."),
)

# 5x7 glyphs: seven rows, bit 4 the leftmost column.
FONT = {
    ' ': "00000000000000", '.': "00000000000C0C", ',': "000000000C0408", "'": "0C040800000000",
    '-': "0000001F000000", ':': "000C0C000C0C00", '!': "04040404040004", '?': "0E110102040004",
    '(': "02040808080402", ')': "08040202020408", '/': "00010204081000", '0': "0E11131519110E",
    '1': "040C040404040E", '2': "0E11010204081F", '3': "1F02040201110E", '4': "02060A121F0202",
    '5': "1F101E0101110E", '6': "0608101E11110E", '7': "1F010204080808", '8': "0E11110E11110E",
    '9': "0E11110F01020C", 'A': "0E11111F111111", 'B': "1E11111E11111E", 'C': "0E11101010110E",
    'D': "1C12111111121C", 'E': "1F10101E10101F", 'F': "1F10101E101010", 'G': "0E11101711110F",
    'H': "1111111F111111", 'I': "0E04040404040E", 'J': "0702020202120C", 'K': "11121418141211",
    'L': "1010101010101F", 'M': "111B1515111111", 'N': "11111915131111", 'O': "0E11111111110E",
    'P': "1E11111E101010", 'Q': "0E11111115120D", 'R': "1E11111E141211", 'S': "0F10100E01011E",
    'T': "1F040404040404", 'U': "1111111111110E", 'V': "11111111110A04", 'W': "1111111515150A",
    'X': "11110A040A1111", 'Y': "1111110A040404", 'Z': "1F01020408101F", 'a': "00000E010F110F",
    'b': "1010161911111E", 'c': "00000E1010110E", 'd': "01010D1311110F", 'e': "00000E111F100E",
    'f': "0609081C080808", 'g': "000F11110F010E", 'h': "10101619111111", 'i': "04000C0404040E",
    'j': "0200060202120C", 'k': "10101214181412", 'l': "0C04040404040E", 'm': "00001A15151111",
    'n': "00001619111111", 'o': "00000E1111110E", 'p': "00001E111E1010", 'q': "00000D130F0101",
    'r': "00001619101010", 's': "00000E100E011E", 't': "08081C08080906", 'u': "0000111111130D",
    'v': "00001111110A04", 'w': "0000111115150A", 'x': "0000110A040A11", 'y': "000011110F010E",
    'z': "00001F0204081F",
}
GLYPH_WIDTH, GLYPH_HEIGHT, GLYPH_GAP = 5, 7, 1


def draw(lines=UPDATING, *, device: str = FRAMEBUFFER, reporter=None) -> bool:
    """Write the message to the framebuffer; False, after logging why, when it can't."""
    try:
        _draw(lines, device)
        return True
    except Exception as exc:  # noqa: BLE001 - the update must never stop for the screen
        if reporter is not None:
            reporter.line(f"  (The touchscreen message could not be drawn: {type(exc).__name__}: {exc})")
        return False


def light(sudo, *, reporter=None) -> bool:
    """Turn on a backlight that QIDI's app left off; sudo(command) runs a command as root, since only root may write
    the brightness. A backlight that is on is left as it is. False, after logging why, when it stays off."""
    try:
        directory = Path(BACKLIGHT)
        if int((directory / "brightness").read_text()) != 0:
            return True
        level = int((directory / "max_brightness").read_text())
        if level <= 0 or sudo(["sh", "-c", f"echo {level} > {shlex.quote(str(directory / 'brightness'))}"]).returncode:
            raise OSError("the brightness could not be set")
        return True
    except FileNotFoundError:
        return False  # no backlight to turn on
    except Exception as exc:  # noqa: BLE001 - the update must never stop for the screen
        if reporter is not None:
            reporter.line(f"  (The touchscreen's backlight could not be turned on: {type(exc).__name__}: {exc})")
        return False


def _draw(lines, device: str) -> None:
    fd = os.open(device, os.O_RDWR | os.O_CLOEXEC)
    try:
        info = struct.unpack_from("17I", fcntl.ioctl(fd, FBIOGET_VSCREENINFO, bytes(160)))
        width, height, xoffset, yoffset, bits = info[0], info[1], info[4], info[5], info[6]
        channels = ((info[8], info[9]), (info[11], info[12]), (info[14], info[15]))  # (offset, length) of R, G, B
        if bits not in (16, 24, 32) or not width or not height or any(not 0 < length <= 8 for _, length in channels):
            raise ValueError(f"unsupported framebuffer format: {bits} bpp, RGB {channels}")
        pixel_bytes = bits // 8
        try:
            stride = FIX_SCREENINFO.unpack_from(fcntl.ioctl(fd, FBIOGET_FSCREENINFO, bytes(FIX_SCREENINFO.size)))[9]
        except OSError:
            stride = 0
        stride = stride or info[2] * pixel_bytes
        # QIDI's app draws a landscape UI turned 90 degrees counterclockwise into a portrait framebuffer.
        turned = height > width
        upright_width, upright_height = (height, width) if turned else (width, height)
        mask = _render(lines, upright_width, upright_height)
        colors = [_pixel(color, channels, pixel_bytes) for color in (BACKGROUND, TEXT, WARNING)]
        start = yoffset * stride + xoffset * pixel_bytes
        for y in range(height):
            # Upright (x, y) shows at framebuffer (y, height - 1 - x) when turned.
            row = mask[y] if not turned else bytes(mask[x][height - 1 - y] for x in range(width))
            data = b"".join(colors[value] * len(tuple(run)) for value, run in itertools.groupby(row))
            os.pwrite(fd, data, start + y * stride)
    finally:
        os.close(fd)


def _render(lines, width: int, height: int) -> list[bytearray]:
    """The message as rows of color indexes (0 background, 1 text, 2 warning)."""
    mask = [bytearray(width) for _ in range(height)]
    heights = [scale * (GLYPH_HEIGHT + 3) for scale, _, _ in lines]
    top = max(0, (height - sum(heights)) // 2)
    for (scale, color, text), line_height in zip(lines, heights):
        index = 2 if color == WARNING else 1
        advance = scale * (GLYPH_WIDTH + GLYPH_GAP)
        left = max(0, (width - advance * len(text) + scale * GLYPH_GAP) // 2)
        for position, char in enumerate(text):
            glyph = bytes.fromhex(FONT.get(char, FONT["?"]))
            for row, bits in enumerate(glyph):
                for column in range(GLYPH_WIDTH):
                    if not bits & (0x10 >> column):
                        continue
                    x = left + position * advance + column * scale
                    for dy in range(scale):
                        y = top + row * scale + dy
                        if 0 <= y < height:
                            mask[y][x:x + scale] = bytes([index]) * len(mask[y][x:x + scale])
        top += line_height
    return mask


def _pixel(color, channels, pixel_bytes: int) -> bytes:
    value = 0
    for component, (offset, length) in zip(color, channels):
        value |= (component >> (8 - length)) << offset
    return value.to_bytes(pixel_bytes, "little")
