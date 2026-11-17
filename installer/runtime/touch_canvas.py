"""Drawing for the touchscreen front end (touch_ui.py), with no third-party module.

A Canvas is the upright 800x480 screen as RGB bytes. It draws flat and rounded boxes, sprites, QR codes, and text in
Montserrat. The text comes from glyph masks, and the logo mark and icons from sprites. scripts/render_touchscreen_images.py
renders both from the font and the artwork at build time into one file that zlib alone decodes:

    b"VOTU", format version (<4sB), then zlib-compressed: index length (<I), the index as JSON, the pixel data

The index lists each text style's glyphs (advance, left and top bearing, width, height, offset of the mask), the
kerning of each weight in font units ({first character: [second characters, [adjustments]]}), and each sprite's size
and offset. A mask is one byte of coverage per
pixel and a sprite is RGB, drawn on the background its index names.

Rows are written with slice assignment and text is colored with bytes.translate, so a screen is composed in a few
tens of milliseconds even on the printer.
"""
from __future__ import annotations

import json
import re
import struct
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ASSET_FILE = Path(__file__).resolve().parents[1] / "system/touchscreen/touch-ui.bin"
MAGIC = b"VOTU"
FORMAT_VERSION = 1
HEADER = struct.Struct("<4sB")

WIDTH, HEIGHT = 800, 480

# Montserrat's ascender and descender per em. CSS's "normal" line height is their sum.
ASCENT, DESCENT = 0.968, 0.251
UNITS_PER_EM = 1000

# Text styles: name -> (size in px, weight, tabular figures). The build renders each one into the asset file.
STYLES = {
    "brand": (14, 800, False),
    "sep": (16, 400, False),
    "hdr": (15, 400, False),
    "hdr_tnum": (15, 400, True),
    "chip": (15, 600, False),
    "title": (30, 700, False),
    "title_small": (24, 700, False),
    "body": (18, 400, False),
    "body_bold": (18, 700, False),
    "body_semibold": (18, 600, False),
    "small": (15, 400, False),
    "note": (16, 400, False),
    "note_bold": (16, 700, False),
    "detail": (17, 400, False),
    "detail_now": (17, 650, False),
    "button": (19, 650, False),
    "button_note": (13, 500, False),
    "footer": (13, 400, False),
    "center": (22, 400, False),
    "log": (13, 400, True),
    "log_time": (13, 500, True),
}
# Every style carries these characters; any other is drawn as "?".
CHARSET = "".join(chr(code) for code in range(0x20, 0x7F)) + "…·→°›•×–—‘’“”"

# The palette of screens.html.
BG = (0x0D, 0x11, 0x17)
SURFACE = (0x16, 0x1C, 0x25)
SURFACE_2 = (0x1D, 0x25, 0x30)
LINE = (0x2B, 0x34, 0x40)
INK = (0xF0, 0xF0, 0xF0)
INK_2 = (0xA3, 0xAD, 0xBA)
INK_3 = (0x6F, 0x7A, 0x88)
AMBER = (0xF2, 0xB0, 0x5A)
AMBER_2 = (0xE3, 0x9A, 0x3E)
CRIMSON = (0xC8, 0x4A, 0x5A)
PINK = (0xF0, 0x8A, 0x97)
GREEN = (0x62, 0xC2, 0x8E)
BUTTON_LINE = (0x3A, 0x45, 0x53)
PRIMARY_INK = (0x14, 0x10, 0x0A)
LOG_BG = (0x08, 0x0B, 0x10)


def mix(over, under, alpha: float) -> tuple:
    """`over` at `alpha` opacity on `under`."""
    return tuple(round(a * alpha + b * (1 - alpha)) for a, b in zip(over, under))


WAIT_BG = mix(AMBER, BG, 0.12)

# Sprites: name -> background they are drawn on. The build renders each.
SPRITE_BACKGROUNDS = {
    "mark": BG, "icon_check": BG, "icon_stop": BG, "icon_pause": BG, "icon_down": BG, "icon_up": BG, "icon_q": BG,
    "list": SURFACE_2, "spinner": BG, "spinner_small": WAIT_BG, "tick": BG,
}


@dataclass(frozen=True)
class Glyph:
    advance: float
    left: int  # from the pen position to the mask's left edge
    top: int  # from the baseline to the mask's top edge, negative above it
    width: int
    height: int
    mask: bytes


class Font:
    def __init__(self, name: str, size: float, glyphs: dict, kerning: dict):
        self.name, self.size = name, size
        self.ascent, self.descent = ASCENT * size, DESCENT * size
        self.glyphs = glyphs
        self.kerning = kerning  # "AV" -> px

    @property
    def line_height(self) -> float:
        return self.ascent + self.descent

    def glyph(self, char: str) -> Glyph:
        return self.glyphs.get(char) or self.glyphs["?"]

    def measure(self, text: str, spacing: float = 0.0) -> float:
        width, previous = 0.0, None
        for char in text:
            if previous is not None:
                width += self.kerning.get(previous + char, 0.0)
            width += self.glyph(char).advance + spacing
            previous = char
        return width


@dataclass(frozen=True)
class Sprite:
    width: int
    height: int
    rgb: bytes


@dataclass(frozen=True)
class Assets:
    fonts: dict
    sprites: dict


def encode_assets(index: dict, blob: bytes) -> bytes:
    payload = json.dumps(index, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return HEADER.pack(MAGIC, FORMAT_VERSION) + zlib.compress(struct.pack("<I", len(payload)) + payload + blob, 9)


def decode_assets(data: bytes) -> Assets:
    magic, version = HEADER.unpack_from(data)
    if magic != MAGIC or version != FORMAT_VERSION:
        raise ValueError("not a Valve Oil touchscreen asset file of a known version")
    raw = zlib.decompress(data[HEADER.size:])
    (length,) = struct.unpack_from("<I", raw)
    index = json.loads(raw[4:4 + length])
    blob = memoryview(raw)[4 + length:]
    fonts = {}
    for name, style in index["styles"].items():
        size = style["size"]
        kerning = {first + second: units * size / UNITS_PER_EM
                   for first, (seconds, values) in index["kerning"][style["kerning"]].items()
                   for second, units in zip(seconds, values)}
        glyphs = {char: Glyph(advance, left, top, width, height, bytes(blob[offset:offset + width * height]))
                  for char, (advance, left, top, width, height, offset) in style["glyphs"].items()}
        fonts[name] = Font(name, size, glyphs, kerning)
    sprites = {name: Sprite(width, height, bytes(blob[offset:offset + width * height * 3]))
               for name, (width, height, offset) in index["sprites"].items()}
    return Assets(fonts, sprites)


@lru_cache(maxsize=2)
def load_assets(path: Path = ASSET_FILE) -> Assets:
    return decode_assets(Path(path).read_bytes())


# Coverage is raised to this power before blending, which thickens text towards the weight a browser draws it with.
COVERAGE_GAMMA = 0.6


@lru_cache(maxsize=256)
def _color_tables(color: tuple, bg: tuple) -> tuple:
    """bytes.translate tables taking a coverage byte to each channel of `color` drawn at that coverage on `bg`."""
    alphas = [(a / 255) ** COVERAGE_GAMMA for a in range(256)]
    return tuple(bytes(round(b + (c - b) * alpha) for alpha in alphas) for c, b in zip(color, bg))


@lru_cache(maxsize=64)
def _corner(radius: int, border: int) -> tuple:
    """For the top-left corner's radius x radius square: per pixel, the coverage (0 to 1) of the outer rounded shape
    and of the shape inside the border."""
    samples = 4
    outer, inner = [], []
    inner_radius = max(0, radius - border)
    for y in range(radius):
        for x in range(radius):
            hits_outer = hits_inner = 0
            for sy in range(samples):
                for sx in range(samples):
                    px, py = x + (sx + 0.5) / samples, y + (sy + 0.5) / samples
                    dx, dy = radius - px, radius - py
                    if dx * dx + dy * dy <= radius * radius:
                        hits_outer += 1
                    if px >= border and py >= border:
                        ix, iy = radius - px, radius - py
                        if ix <= 0 or iy <= 0 or ix * ix + iy * iy <= inner_radius * inner_radius:
                            hits_inner += 1
            outer.append(hits_outer / samples ** 2)
            inner.append(hits_inner / samples ** 2)
    return tuple(outer), tuple(inner)


class Canvas:
    """The upright screen as rows of RGB."""

    def __init__(self, width: int = WIDTH, height: int = HEIGHT, color=BG, assets: Assets | None = None):
        self.width, self.height = width, height
        self.pixels = bytearray(bytes(color) * (width * height))
        self.assets = assets

    def copy(self) -> "Canvas":
        other = Canvas.__new__(Canvas)
        other.width, other.height, other.assets = self.width, self.height, self.assets
        other.pixels = bytearray(self.pixels)
        return other

    def font(self, name: str) -> Font:
        return self.assets.fonts[name]

    def _clip(self, x, y, w, h):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.width, x + w), min(self.height, y + h)
        return x0, y0, x1, y1

    def fill(self, x: float, y: float, w: float, h: float, color) -> None:
        x0, y0 = round(x), round(y)
        x0, y0, x1, y1 = self._clip(x0, y0, round(x + w) - x0, round(y + h) - y0)
        if x1 <= x0 or y1 <= y0:
            return
        run = bytes(color) * (x1 - x0)
        stride = self.width * 3
        for row in range(y0, y1):
            start = row * stride + x0 * 3
            self.pixels[start:start + len(run)] = run

    def blit(self, x: int, y: int, w: int, h: int, rgb: bytes) -> None:
        """Copy rows of RGB, w x h, with their top-left corner at (x, y)."""
        stride = self.width * 3
        for row in range(h):
            yy = y + row
            if not 0 <= yy < self.height:
                continue
            left, right = max(0, x), min(self.width, x + w)
            if right <= left:
                return
            source = row * w * 3
            self.pixels[yy * stride + left * 3:yy * stride + right * 3] = \
                rgb[source + (left - x) * 3:source + (right - x) * 3]

    def region(self, x: int, y: int, w: int, h: int) -> bytes:
        stride = self.width * 3
        return b"".join(bytes(self.pixels[(y + row) * stride + x * 3:(y + row) * stride + (x + w) * 3])
                        for row in range(h))

    def _blend(self, x: int, y: int, color, coverage: float) -> None:
        if coverage <= 0 or not (0 <= x < self.width and 0 <= y < self.height):
            return
        at = (y * self.width + x) * 3
        if coverage >= 1:
            self.pixels[at:at + 3] = bytes(color)
            return
        under = self.pixels[at:at + 3]
        self.pixels[at:at + 3] = bytes(round(c * coverage + u * (1 - coverage)) for c, u in zip(color, under))

    def round_rect(self, x: float, y: float, w: float, h: float, radius: int, fill, border=None,
                   gradient: tuple | None = None) -> None:
        """A box with rounded corners and an optional 1 px border; `gradient`, (left color, right color), fills it
        from left to right instead of `fill`."""
        x0, y0 = round(x), round(y)
        x1, y1 = round(x + w), round(y + h)
        width, height = x1 - x0, y1 - y0
        if width <= 0 or height <= 0:
            return
        radius = max(0, min(radius, width // 2, height // 2))
        line = 1 if border is not None else 0
        edge = border if border is not None else fill
        if gradient is not None:
            left, right = gradient
            span = max(1, width - 1)
            colors = [tuple(round(a + (b - a) * i / span) for a, b in zip(left, right)) for i in range(width)]
            fill_row = b"".join(bytes(color) for color in colors)
        else:
            colors = None
            fill_row = bytes(fill) * width
        stride = self.width * 3
        for row in range(height):
            yy = y0 + row
            if not 0 <= yy < self.height:
                continue
            edge_row = row < line or row >= height - line
            data = bytearray(bytes(edge) * width) if edge_row else bytearray(fill_row)
            if line and not edge_row:
                data[0:3] = data[-3:] = bytes(edge)
            from_top = min(row, height - 1 - row)
            inset = radius if from_top < radius else 0
            left, right = max(x0 + inset, 0), min(x1 - inset, self.width)
            if right > left:
                self.pixels[yy * stride + left * 3:yy * stride + right * 3] = \
                    data[(left - x0) * 3:(right - x0) * 3]
        if not radius:
            return
        outer, inner = _corner(radius, line)
        for j in range(radius):
            for i in range(radius):
                cover_outer, cover_inner = outer[j * radius + i], inner[j * radius + i]
                for px, py in ((x0 + i, y0 + j), (x1 - 1 - i, y0 + j), (x0 + i, y1 - 1 - j), (x1 - 1 - i, y1 - 1 - j)):
                    inside = colors[px - x0] if colors is not None else fill
                    if line:
                        self._blend(px, py, edge, cover_outer)
                        self._blend(px, py, inside, cover_inner)
                    else:
                        self._blend(px, py, inside, cover_outer)

    def sprite(self, name: str, x: float, y: float) -> None:
        sprite = self.assets.sprites[name]
        self.blit(round(x), round(y), sprite.width, sprite.height, sprite.rgb)

    def qr(self, x: int, y: int, modules: list, scale: int, dark=BG, light=INK) -> None:
        dark_px, light_px = bytes(dark) * scale, bytes(light) * scale
        stride = self.width * 3
        for my, row in enumerate(modules):
            line = b"".join(dark_px if module else light_px for module in row)
            for dy in range(scale):
                yy = y + my * scale + dy
                self.pixels[yy * stride + x * 3:yy * stride + x * 3 + len(line)] = line

    def text(self, x: float, baseline: float, text: str, font: Font | str, color, bg, spacing: float = 0.0,
             clip: float | None = None) -> float:
        """Draw `text` with its pen starting at (x, baseline) on a background of `bg`; its advance. Pixels right of
        `clip` are left alone."""
        if isinstance(font, str):
            font = self.font(font)
        placed, pen, previous = [], x, None
        for char in text:
            if previous is not None:
                pen += font.kerning.get(previous + char, 0.0)
            glyph = font.glyph(char)
            if glyph.width and glyph.height:
                placed.append((round(pen) + glyph.left, glyph))
            pen += glyph.advance + spacing
            previous = char
        advance = pen - x
        if not placed:
            return advance
        base = round(baseline)
        left = min(gx for gx, _ in placed)
        right = max(gx + glyph.width for gx, glyph in placed)
        top = min(glyph.top for _, glyph in placed)
        bottom = max(glyph.top + glyph.height for _, glyph in placed)
        width, height = right - left, bottom - top
        mask = bytearray(width * height)
        reached = left  # columns left of this already hold a glyph's coverage
        for gx, glyph in placed:
            column = gx - left
            overlap = max(0, min(reached - gx, glyph.width))
            for row in range(glyph.height):
                source = row * glyph.width
                target = (glyph.top - top + row) * width + column
                if overlap:
                    for k in range(overlap):
                        if glyph.mask[source + k] > mask[target + k]:
                            mask[target + k] = glyph.mask[source + k]
                mask[target + overlap:target + glyph.width] = glyph.mask[source + overlap:source + glyph.width]
            reached = max(reached, gx + glyph.width)
        if clip is not None and left + width > clip:
            keep = max(0, int(clip) - left)
            mask = bytearray(b"".join(mask[row * width:row * width + keep] for row in range(height)))
            width = keep
            if not width:
                return advance
        red, green, blue = _color_tables(tuple(color), tuple(bg))
        rgb = bytearray(width * height * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = mask.translate(red), mask.translate(green), mask.translate(blue)
        self.blit(left, base + top, width, height, rgb)
        return advance


# Text that wraps: runs of (text, Font, color, spacing); a run whose Font is None is a gap of `spacing` px.
TOKEN = re.compile(r"\n| +|[^ \n]+")


def wrap(runs, width: float) -> list[list[tuple]]:
    """Lay out runs across lines at most `width` wide: lines of (x offset, text, font, color, spacing). Words wrap at
    spaces, a word wider than a line breaks anywhere, and "\\n" starts a new line."""
    lines, line, x = [], [], 0.0

    def push(text, font, color, spacing, advance):
        nonlocal x
        if line and line[-1][2] is font and line[-1][3] == color and line[-1][4] == spacing \
                and abs(line[-1][0] + line[-1][5] - x) < 1e-6:
            last = line[-1]
            line[-1] = (last[0], last[1] + text, font, color, spacing, last[5] + advance)
        else:
            line.append((x, text, font, color, spacing, advance))
        x += advance

    def newline():
        nonlocal line, x
        while line and not line[-1][1].strip():
            line.pop()
        if line and line[-1][1] != line[-1][1].rstrip(" "):
            last = line[-1]
            line[-1] = (last[0], last[1].rstrip(" "), *last[2:])
        lines.append(line)
        line, x = [], 0.0

    for text, font, color, spacing in runs:
        if font is None:  # a fixed gap, such as a CSS margin between runs
            x += spacing
            continue
        for token in TOKEN.findall(text):
            if token == "\n":
                newline()
                continue
            advance = font.measure(token, spacing)
            if token.startswith(" "):
                if line:
                    push(token, font, color, spacing, advance)
                continue
            if x + advance > width + 0.01 and line:
                newline()
            while advance > width + 0.01 and len(token) > 1:
                cut = len(token) - 1
                while cut > 1 and x + font.measure(token[:cut], spacing) > width:
                    cut -= 1
                push(token[:cut], font, color, spacing, font.measure(token[:cut], spacing))
                newline()
                token = token[cut:]
                advance = font.measure(token, spacing)
            push(token, font, color, spacing, advance)
    newline()
    return [[(offset, text, font, color, spacing) for offset, text, font, color, spacing, _ in line]
            for line in lines]


def line_width(line) -> float:
    if not line:
        return 0.0
    offset, text, font, _, spacing = line[-1]
    return offset + font.measure(text, spacing)


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """An 8-bit RGB PNG with every row unfiltered, which update_screen.decode_png reads."""
    stride = 3 * width
    if len(rgb) != stride * height:
        raise ValueError(f"expected {stride * height} bytes of RGB, got {len(rgb)}")
    raw = b"".join(b"\x00" + bytes(rgb[start:start + stride]) for start in range(0, len(rgb), stride))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
