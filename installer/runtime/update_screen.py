"""The touchscreen message while Valve Oil has QIDI's touchscreen app stopped: a firmware update, or an install
(install_screen.py).

A still image is written to the framebuffer: a landscape screen drawn upright on the Max 4's 800x480 panel and turned
for the Plus 5's 480x800 panel, which QIDI's app also draws on its side (see tltg_remote_screen.py). The geometry, pixel
format, and line length are read from the framebuffer each time. A screen is either text drawn from the 5x7 font below,
or an Image: an 800x480 PNG rendered by scripts/render_touchscreen_images.py, saved so that zlib alone decodes it, with
text to draw instead when it can't be shown. No third-party module is needed. Drawing is best-effort: any failure is
logged and the work goes on. The app repaints the whole panel when it starts again.

QIDI's app puts the panel to sleep by setting the backlight's brightness to 0 and leaves it there when it is stopped,
so the message would sit on a dark panel; light() turns the backlight on. The app sets its own brightness as it starts.
"""
from __future__ import annotations

import fcntl
import itertools
import os
import shlex
import struct
import zlib
from dataclasses import dataclass
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
STOPPED = (
    (4, TEXT, "The firmware update"),
    (4, TEXT, "didn't finish."),
    (4, WARNING, "Don't turn the printer off."),
    (3, TEXT, "Open Fluidd on a computer"),
    (3, TEXT, "to see what to do next."),
)

INSTALL_STOPPED = (
    (4, TEXT, "Valve Oil's install"),
    (4, TEXT, "didn't finish."),
    (4, WARNING, "Don't turn the printer off."),
    (3, TEXT, "Run the command the installer"),
    (3, TEXT, "printed to finish it."),
)


@dataclass(frozen=True)
class Image:
    """An upright landscape PNG, and the text to draw when it can't be shown."""

    path: Path
    fallback: tuple


IMAGE_SIZE = (800, 480)
IMAGES_DIR = Path(__file__).resolve().parents[1] / "system/touchscreen"
INSTALLING_IMAGE = IMAGES_DIR / "installing.png"
UPDATING_IMAGE = IMAGES_DIR / "updating.png"
UPDATING = Image(UPDATING_IMAGE, (
    (4, TEXT, "Valve Oil is updating"),
    (4, TEXT, "the printer's firmware."),
    (4, WARNING, "Don't turn the printer off."),
    (3, TEXT, "The printer restarts by itself"),
    (3, TEXT, "when the update is done."),
))
INSTALLING = Image(INSTALLING_IMAGE, (
    (5, TEXT, "Installing Valve Oil"),
    (4, WARNING, "Don't turn the printer off."),
    (3, TEXT, "The touchscreen comes back"),
    (3, TEXT, "when the install is done."),
))

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


def draw(screen=UPDATING, *, device: str = FRAMEBUFFER, reporter=None) -> bool:
    """Write the screen, lines of text or an Image, to the framebuffer; False, after logging why, when it can't. An
    Image that can't be shown is drawn as its fallback text."""
    if isinstance(screen, Image):
        try:
            _draw(_image_rows(screen.path), device)
            return True
        except Exception as exc:  # noqa: BLE001 - the work must never stop for the screen
            if not Path(device).exists():
                return _failed(reporter, exc)
            if reporter is not None:
                reporter.line(f"  (The touchscreen image could not be shown, so its text is: {type(exc).__name__}: "
                              f"{exc})")
            screen = screen.fallback
    try:
        _draw(_text_rows(screen), device)
        return True
    except Exception as exc:  # noqa: BLE001 - the work must never stop for the screen
        return _failed(reporter, exc)


def _failed(reporter, exc) -> bool:
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


def decode_png(data: bytes) -> list[bytes]:
    """The rows of an 8-bit RGB, non-interlaced PNG whose rows all use filter 0, as RGB bytes; ValueError for any
    other PNG, which scripts/render_touchscreen_images.py never writes."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    position, header, compressed = 8, None, []
    while position + 8 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, position)
        chunk = data[position + 8:position + 8 + length]
        if len(chunk) != length:
            raise ValueError("truncated PNG")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            compressed.append(chunk)
        elif kind == b"IEND":
            break
        position += 12 + length
    if header is None or header[2:] != (8, 2, 0, 0, 0):
        raise ValueError(f"unsupported PNG format {header}")
    width, height = header[:2]
    raw = zlib.decompress(b"".join(compressed))
    stride = 1 + 3 * width
    if len(raw) != stride * height:
        raise ValueError("PNG data doesn't match its size")
    rows = []
    for start in range(0, len(raw), stride):
        if raw[start] != 0:
            raise ValueError("PNG rows must use filter 0")
        rows.append(raw[start + 1:start + stride])
    return rows


def _image_rows(path: Path):
    """A function giving the image's rows of RGB keys for an upright screen of (width, height)."""
    rows = decode_png(Path(path).read_bytes())

    def rows_for(width: int, height: int) -> list[list[int]]:
        if (width, height) != (len(rows[0]) // 3, len(rows)):
            raise ValueError(f"the image is {len(rows[0]) // 3}x{len(rows)}, the screen {width}x{height}")
        return [[(row[i] << 16) | (row[i + 1] << 8) | row[i + 2] for i in range(0, len(row), 3)] for row in rows]

    return rows_for


def _text_rows(lines):
    """A function giving the text's rows of RGB keys for an upright screen of (width, height)."""
    keys = [_key(color) for color in (BACKGROUND, TEXT, WARNING)]

    def rows_for(width: int, height: int) -> list[list[int]]:
        return [[keys[value] for value in row] for row in _render(lines, width, height)]

    return rows_for


def _key(color) -> int:
    return (color[0] << 16) | (color[1] << 8) | color[2]


@dataclass(frozen=True)
class Geometry:
    """A framebuffer's size, pixel format, and where its visible screen starts. QIDI's app draws a landscape UI
    turned 90 degrees counterclockwise into a portrait framebuffer: upright (x, y) shows at framebuffer
    (y, height - 1 - x)."""

    width: int
    height: int
    pixel_bytes: int
    channels: tuple  # (offset, length) of R, G, B
    stride: int
    start: int

    @property
    def turned(self) -> bool:
        return self.height > self.width

    @property
    def upright(self) -> tuple[int, int]:
        return (self.height, self.width) if self.turned else (self.width, self.height)


def geometry(fd: int) -> Geometry:
    """The geometry of the framebuffer open as `fd`; ValueError for a pixel format it can't draw."""
    info = struct.unpack_from("17I", fcntl.ioctl(fd, FBIOGET_VSCREENINFO, bytes(160)))
    width, height, xoffset, yoffset, bits = info[0], info[1], info[4], info[5], info[6]
    channels = ((info[8], info[9]), (info[11], info[12]), (info[14], info[15]))
    if bits not in (16, 24, 32) or not width or not height or any(not 0 < length <= 8 for _, length in channels):
        raise ValueError(f"unsupported framebuffer format: {bits} bpp, RGB {channels}")
    pixel_bytes = bits // 8
    try:
        stride = FIX_SCREENINFO.unpack_from(fcntl.ioctl(fd, FBIOGET_FSCREENINFO, bytes(FIX_SCREENINFO.size)))[9]
    except OSError:
        stride = 0
    stride = stride or info[2] * pixel_bytes
    return Geometry(width, height, pixel_bytes, channels, stride, yoffset * stride + xoffset * pixel_bytes)


def _draw(rows_for, device: str) -> None:
    fd = os.open(device, os.O_RDWR | os.O_CLOEXEC)
    try:
        screen = geometry(fd)
        width, height, turned = screen.width, screen.height, screen.turned
        upright = rows_for(*screen.upright)
        pixels: dict[int, bytes] = {}
        for y in range(height):
            # Upright (x, y) shows at framebuffer (y, height - 1 - x) when turned.
            row = upright[y] if not turned else [upright[x][height - 1 - y] for x in range(width)]
            data = b"".join(_cached_pixel(pixels, key, screen.channels, screen.pixel_bytes) * len(tuple(run))
                            for key, run in itertools.groupby(row))
            os.pwrite(fd, data, screen.start + y * screen.stride)
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


def _cached_pixel(cache: dict, key: int, channels, pixel_bytes: int) -> bytes:
    pixel = cache.get(key)
    if pixel is None:
        pixel = cache[key] = _pixel((key >> 16, (key >> 8) & 0xFF, key & 0xFF), channels, pixel_bytes)
    return pixel


def _pixel(color, channels, pixel_bytes: int) -> bytes:
    value = 0
    for component, (offset, length) in zip(color, channels):
        value |= (component >> (8 - length)) << offset
    return value.to_bytes(pixel_bytes, "little")
