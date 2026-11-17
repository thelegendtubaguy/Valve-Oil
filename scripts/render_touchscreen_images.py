#!/usr/bin/env python3
"""Render what Valve Oil draws on the printer's touchscreen: the still images shown while QIDI's touchscreen app is
stopped or not yet running, the boot animation, and the touchscreen front end's glyphs and sprites.

Text is set in Montserrat (scripts/fonts/Montserrat-VF.ttf, SIL Open Font License 1.1). HarfBuzz's hb-view rasterizes
each glyph of every text style touch_canvas.STYLES names, and hb-shape measures advances and kerning. The logo mark
and the icons of the front end's screens (openspec/changes/archive/2026-11-17-add-usb-touchscreen-installer/screens.html) are drawn as SVG
and rasterized with rsvg-convert. All of it is saved as touch-ui.bin (installer/runtime/touch_canvas.py).

Each still image is drawn as SVG around the Valve Oil logo, rasterized with rsvg-convert, flattened with ImageMagick,
its text drawn with the same glyphs, and saved as an 800x480 8-bit RGB PNG whose rows all use PNG filter 0, so the
printer decodes it with zlib alone (installer/runtime/update_screen.py). The boot animation's frames are drawn the same
way and saved as boot-animation.bin (installer/runtime/boot_screen.py); its first frame is the boot logo. The files are
committed; run this after changing a screen, a style, or the logo (needs rsvg-convert, ImageMagick, and HarfBuzz's
tools, e.g. `brew install librsvg imagemagick harfbuzz`):

    python3 scripts/render_touchscreen_images.py
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from installer.runtime import boot_screen, touch_canvas, update_screen  # noqa: E402

LOGO = REPO_ROOT / ".github/images/valve-oil-logo-dark.svg"
FONT_FILE = REPO_ROOT / "scripts/fonts/Montserrat-VF.ttf"
WIDTH, HEIGHT = update_screen.IMAGE_SIZE

# Styles the still images use, rendered for the build only.
STILL_STYLES = {
    "still_title": (46, 700, False),
    "still_warning": (30, 700, False),
    "still_note": (22, 400, False),
}


def _hex(color) -> str:
    return "#" + "".join(f"{component:02X}" for component in color)


def _logo_inner(*, drop=None) -> str:
    source = LOGO.read_text(encoding="utf-8")
    inner = re.sub(r"^.*?<svg[^>]*>|</svg>\s*$", "", source, flags=re.S)
    inner = re.sub(r"<title>.*?</title>", "", inner, flags=re.S)
    if drop is not None:
        inner = inner.replace(_drop_path(inner), drop)
    return inner


def _logo(x: float, y: float, width: float, *, drop=None) -> str:
    """The logo at (x, y), `width` wide; `drop`, if given, replaces its oil drop (see _drop)."""
    view_box = re.search(r'viewBox="([^"]+)"', LOGO.read_text(encoding="utf-8")).group(1)
    _, _, box_width, box_height = (float(value) for value in view_box.split())
    height = width * box_height / box_width
    return (f'<svg x="{x}" y="{y}" width="{width}" height="{height}" viewBox="{view_box}" overflow="visible">'
            f'{_logo_inner(drop=drop)}</svg>')


def _drop_path(inner: str) -> str:
    """The logo's oil drop under the canister, which hangs from (150, 392) in logo units."""
    return re.search(r'<path d="M150,392 .*?/>', inner).group(0)


# ---- Glyphs ----------------------------------------------------------------------------------------------------------

def _variations(weight: int) -> str:
    return f"--variations=wght={weight}"


def _features(tnum: bool) -> str:
    return "--features=-liga,-clig" + (",tnum" if tnum else "")


def _shape_lines(lines: list[str], weight: int, tnum: bool) -> list[list[int]]:
    """The advances, in font units, of each line's glyphs."""
    with tempfile.TemporaryDirectory() as directory:
        text = Path(directory) / "text.txt"
        text.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out = subprocess.run(["hb-shape", f"--font-file={FONT_FILE}", _variations(weight), _features(tnum),
                              "--font-size=upem", "--output-format=json", f"--text-file={text}"],
                             check=True, capture_output=True, text=True).stdout
    shaped = [json.loads(line) for line in out.splitlines() if line.strip()]
    if len(shaped) != len(lines):
        raise RuntimeError(f"hb-shape returned {len(shaped)} lines for {len(lines)}")
    return [[glyph["ax"] for glyph in line] for line in shaped]


# Kerning smaller than this, in font units, is left out: under a fifth of a pixel at 18 px.
MIN_KERNING = 10


def _kerning(weight: int, tnum: bool) -> tuple[dict, dict]:
    """Each character's advance, and the kerning as {first character: [second characters, [adjustments]]}, in font
    units."""
    chars = touch_canvas.CHARSET
    advances = {char: line[0] for char, line in zip(chars, _shape_lines(list(chars), weight, tnum))}
    pairs = [a + b for a in chars for b in chars]
    kerning = {}
    for pair, line in zip(pairs, _shape_lines(pairs, weight, tnum)):
        if len(line) == 2 and abs(line[0] - advances[pair[0]]) >= MIN_KERNING:
            seconds, values = kerning.setdefault(pair[0], ["", []])
            kerning[pair[0]] = [seconds + pair[1], values + [line[0] - advances[pair[0]]]]
    return advances, kerning


def _hb_view(lines: list[str], size: int, weight: int, tnum: bool, ascent: int, descent: int,
             margin: int) -> tuple[int, bytes]:
    """hb-view's rendering of `lines`, white on black: its width and its gray bytes."""
    with tempfile.TemporaryDirectory() as directory:
        text, image = Path(directory) / "text.txt", Path(directory) / "glyphs.png"
        text.write_text("\n".join(lines) + "\n", encoding="utf-8")
        subprocess.run(["hb-view", f"--font-file={FONT_FILE}", _variations(weight), _features(tnum),
                        f"--font-size={size}", f"--margin={margin}", f"--font-extents={ascent},{descent},0",
                        "--background=000000", "--foreground=FFFFFF", f"--text-file={text}", "-o", str(image)],
                       check=True)
        width = int(subprocess.run(["magick", "identify", "-format", "%w", str(image)], check=True,
                                   capture_output=True, text=True).stdout)
        gray = subprocess.run(["magick", str(image), "-colorspace", "gray", "-depth", "8", "gray:-"], check=True,
                              capture_output=True).stdout
    return width, gray


def _column_coverage(gray: bytes, width: int, rows: range) -> list[int]:
    return [sum(gray[y * width + x] for y in rows) for x in range(width)]


def _glyph_masks(size: int, weight: int, tnum: bool) -> dict:
    """Each character's coverage mask: (left, top, width, height, mask) from its pen position on the baseline."""
    chars = touch_canvas.CHARSET
    ascent, descent = math.ceil(size * 1.1) + 2, math.ceil(size * 0.4) + 2
    margin = size
    # hb-view moves everything right by whole pixels when a glyph inks left of its pen position, as "j" does. An "H"
    # drawn alone and as the first line shows by how much.
    alone_width, alone = _hb_view(["H"], size, weight, tnum, ascent, descent, margin)
    width, gray = _hb_view(["H", *chars], size, weight, tnum, ascent, descent, margin)
    first_line = range(margin, margin + ascent + descent)
    reference = _column_coverage(alone, alone_width, first_line)
    sentinel = _column_coverage(gray, width, first_line)
    shift = min(range(size), key=lambda s: sum(abs(sentinel[x + s] - value) for x, value in enumerate(reference)
                                              if x + s < width))
    masks = {}
    for index, char in enumerate(chars, 1):
        baseline = margin + ascent + index * (ascent + descent)
        rows = range(baseline - ascent, baseline + descent)
        ink_rows = [y for y in rows if any(gray[y * width:(y + 1) * width])]
        if not ink_rows:
            masks[char] = (0, 0, 0, 0, b"")
            continue
        top, bottom = ink_rows[0], ink_rows[-1] + 1
        columns = [x for x in range(width) if any(gray[y * width + x] for y in range(top, bottom))]
        left, right = columns[0], columns[-1] + 1
        mask = b"".join(gray[y * width + left:y * width + right] for y in range(top, bottom))
        masks[char] = (left - margin - shift, top - baseline, right - left, bottom - top, mask)
    return masks


def render_glyphs(styles: dict) -> tuple[dict, dict, bytearray]:
    """The index entries of `styles` and of their kerning, and the masks' data."""
    blob = bytearray()
    index_styles, index_kerning, measured = {}, {}, {}
    for name, (size, weight, tnum) in styles.items():
        key = f"{weight}{'-tnum' if tnum else ''}"
        if key not in measured:
            measured[key] = _kerning(weight, tnum)
            index_kerning[key] = measured[key][1]
        advances = measured[key][0]
        glyphs = {}
        for char, (left, top, width, height, mask) in _glyph_masks(size, weight, tnum).items():
            glyphs[char] = [round(advances[char] * size / touch_canvas.UNITS_PER_EM, 3), left, top, width, height,
                            len(blob)]
            blob.extend(mask)
        index_styles[name] = {"size": size, "weight": weight, "tnum": tnum, "kerning": key, "glyphs": glyphs}
    return index_styles, index_kerning, blob


# ---- Sprites ---------------------------------------------------------------------------------------------------------

def _icon(stroke: str, width: float, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{stroke}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


# The icons of screens.html.
_ALERT = '<circle cx="12" cy="12" r="9.5"/><path d="M12 7v6M12 16.6v.4"/>'
ICONS = {
    "icon_check": _icon("#62C28E", 3, '<path d="M4 12.5l5 5L20 6.5"/>'),
    "icon_stop": _icon("#F08A97", 2.6, _ALERT),
    "icon_pause": _icon("#F2B05A", 2.6, _ALERT),
    "icon_down": _icon("#F2B05A", 2.6, '<path d="M12 3v12M7 10.5l5 5 5-5M4 20.5h16"/>'),
    "icon_up": _icon("#F2B05A", 2.6, '<path d="M12 16V4M7 8.5l5-5 5 5M4 20.5h16"/>'),
    "icon_q": _icon("#F2B05A", 2.6, '<circle cx="12" cy="12" r="9.5"/>'
                    '<path d="M9.2 9.3a2.9 2.9 0 1 1 4.3 2.6c-.9.5-1.5 1-1.5 2M12 17v.3"/>'),
}


def _spinner(size: int, border: int) -> str:
    """screens.html's spinner: a ring whose top quarter is amber, as a CSS border draws it."""
    radius = (size - border) / 2
    center = size / 2

    def point(degrees):
        angle = math.radians(degrees)
        return center + radius * math.cos(angle), center + radius * math.sin(angle)

    (x0, y0), (x1, y1) = point(225), point(315)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
            f'<circle cx="{center}" cy="{center}" r="{radius}" fill="none" stroke="#1D2530" stroke-width="{border}"/>'
            f'<path d="M{x0:.3f},{y0:.3f} A{radius},{radius} 0 0 1 {x1:.3f},{y1:.3f}" fill="none" stroke="#F2B05A" '
            f'stroke-width="{border}"/></svg>')


def sprite_svgs() -> dict:
    """name -> (width, height, SVG)."""
    sprites = {name: (30, 30, svg) for name, svg in ICONS.items()}
    sprites["mark"] = (21, 40, f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="40 8 220 470">{_logo_inner()}</svg>')
    sprites["list"] = (18, 18, _icon("#F0F0F0", 2.6, '<path d="M4 6h16M4 12h16M4 18h10"/>'))
    sprites["spinner"] = (56, 56, _spinner(56, 6))
    sprites["spinner_small"] = (24, 24, _spinner(24, 4))
    # The steps' check mark, which Montserrat lacks: a light check like the system font screens.html falls back to.
    sprites["tick"] = (18, 18, _icon("#62C28E", 1.7, '<path d="M5.5 12.5l4 4.5L18 6"/>'))
    return sprites


def render_sprites() -> tuple[dict, bytearray]:
    index, blob = {}, bytearray()
    for name, (width, height, svg) in sprite_svgs().items():
        rgb = rasterize(svg, width, height, touch_canvas.SPRITE_BACKGROUNDS[name])
        index[name] = [width, height, len(blob)]
        blob.extend(rgb)
    return index, blob


def render_assets(styles: dict) -> bytes:
    index_styles, index_kerning, blob = render_glyphs(styles)
    sprites, sprite_blob = render_sprites()
    offset = len(blob)
    for entry in sprites.values():
        entry[2] += offset
    blob.extend(sprite_blob)
    return touch_canvas.encode_assets({"styles": index_styles, "kerning": index_kerning, "sprites": sprites},
                                      bytes(blob))


# ---- Still images ----------------------------------------------------------------------------------------------------

def _still_svg() -> str:
    """Valve Oil's layout for a screen shown while QIDI's touchscreen app is stopped, without its text: the logo and
    the brand accent under the title."""
    logo_width = 400
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
<rect width="{WIDTH}" height="{HEIGHT}" fill="{_hex(update_screen.BACKGROUND)}"/>
{_logo((WIDTH - logo_width) / 2, 48, logo_width)}
<rect x="{WIDTH / 2 - 60}" y="294" width="120" height="4" rx="2" fill="#F2B05A"/>
</svg>
"""


def still_image(assets, title: str, warning: str, note: str) -> bytes:
    """The logo, a title with the brand accent under it, a warning in amber, and a quieter note."""
    canvas = touch_canvas.Canvas(WIDTH, HEIGHT, update_screen.BACKGROUND, assets)
    canvas.pixels[:] = rasterize(_still_svg(), WIDTH, HEIGHT, update_screen.BACKGROUND)
    for baseline, style, color, text in ((268, "still_title", update_screen.TEXT, title),
                                         (356, "still_warning", update_screen.WARNING, warning),
                                         (404, "still_note", (0x9A, 0xA0, 0xAA), note)):
        font = canvas.font(style)
        canvas.text((WIDTH - font.measure(text)) / 2, baseline, text, font, color, update_screen.BACKGROUND)
    return bytes(canvas.pixels)


STILLS = {
    update_screen.INSTALLING_IMAGE: ("Installing Valve Oil", "Don't turn the printer off.",
                                     "The touchscreen comes back when the install is done."),
    update_screen.UPDATING_IMAGE: ("Updating Firmware", "Don't turn the printer off.",
                                   "The printer restarts by itself when the update is done."),
}


# ---- Boot logo and animation -----------------------------------------------------------------------------------------

# The boot logo and the boot animation: the logo large, slightly above center, with room below for the line the oil
# drop lands on.
BOOT_LOGO_WIDTH = 660
BOOT_LOGO_HEIGHT = BOOT_LOGO_WIDTH * 530 / 1500  # the logo's viewBox
BOOT_LINE_GAP = 22
BOOT_LOGO_X = (WIDTH - BOOT_LOGO_WIDTH) / 2
BOOT_LOGO_Y = (HEIGHT - (BOOT_LOGO_HEIGHT + BOOT_LINE_GAP + 4)) / 2
BOOT_LINE_Y = BOOT_LOGO_Y + BOOT_LOGO_HEIGHT + BOOT_LINE_GAP
BOOT_SCALE = BOOT_LOGO_WIDTH / 1500
BOOT_FPS = 15
BOOT_FRAMES = 48  # 3.2 s


def _drop(dy: float, scale: float, opacity: float, inner: str) -> str:
    return (f'<g opacity="{opacity:.3f}" transform="translate(150,392) translate(0,{dy:.2f}) scale({scale:.3f}) '
            f'translate(-150,-392)">{_drop_path(inner)}</g>')


def boot_frame_svg(t: float) -> str:
    """The boot animation at t in [0, 1): the drop hangs under the canister, falls onto a line, the oil spreads along
    the line and fades, and a new drop forms. t = 0 is the boot logo."""
    inner = LOGO.read_text(encoding="utf-8")
    drop_x = BOOT_LOGO_X + (150 + 30) * BOOT_SCALE  # the logo's viewBox starts at -30
    nozzle_y = BOOT_LOGO_Y + (392 + 30) * BOOT_SCALE
    fall = (BOOT_LINE_Y - nozzle_y - 10) / BOOT_SCALE
    if t < 0.15:
        drop = _drop(0, 1, 1, inner)
    elif t < 0.35:
        progress = (t - 0.15) / 0.2
        drop = _drop(fall * progress * progress, 1, 1, inner)
    elif t < 0.6:
        drop = _drop(0, 0, 0, inner)
    else:
        grow = (t - 0.6) / 0.4
        drop = _drop(0, 0.15 + 0.85 * (1 - (1 - grow) ** 2), min(1.0, grow * 3), inner)
    left_end, right_end = BOOT_LOGO_X + 10, BOOT_LOGO_X + BOOT_LOGO_WIDTH - 10
    extras = ""
    if t >= 0.35:
        spread = min(1.0, (t - 0.35) / 0.25)
        ease = 1 - (1 - spread) ** 3
        left = drop_x - (drop_x - left_end) * ease
        right = drop_x + (right_end - drop_x) * ease
        fade = 1.0 if t < 0.7 else max(0.0, 1 - (t - 0.7) / 0.25)
        if fade > 0:
            extras += (f'<rect x="{left:.2f}" y="{BOOT_LINE_Y:.2f}" width="{max(0.1, right - left):.2f}" height="4" '
                       f'rx="2" fill="#F2B05A" opacity="{fade:.3f}"/>')
        if t < 0.5:
            ring = (t - 0.35) / 0.15
            radius = 4 + 40 * ring
            extras += (f'<ellipse cx="{drop_x:.2f}" cy="{BOOT_LINE_Y + 2:.2f}" rx="{radius:.2f}" ry="{radius / 5:.2f}" '
                       f'fill="none" stroke="#F2B05A" stroke-width="2" opacity="{1 - ring:.3f}"/>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
<rect width="{WIDTH}" height="{HEIGHT}" fill="{_hex(update_screen.BACKGROUND)}"/>
{extras}{_logo(BOOT_LOGO_X, BOOT_LOGO_Y, BOOT_LOGO_WIDTH, drop=drop)}
</svg>
"""


def boot_logo_svg() -> str:
    return boot_frame_svg(0.0)


def rasterize(svg: str, width: int = WIDTH, height: int = HEIGHT, background=update_screen.BACKGROUND) -> bytes:
    """The SVG as width x height rows of RGB on `background`."""
    with tempfile.TemporaryDirectory() as directory:
        source, raster = Path(directory) / "screen.svg", Path(directory) / "screen.png"
        source.write_text(svg, encoding="utf-8")
        subprocess.run(["rsvg-convert", "-w", str(width), "-h", str(height), "-o", str(raster), str(source)],
                       check=True)
        return subprocess.run(["magick", str(raster), "-background", _hex(background), "-flatten",
                               "-depth", "8", "rgb:-"], check=True, capture_output=True).stdout


def save_png(rgb: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(touch_canvas.encode_png(WIDTH, HEIGHT, rgb))
    update_screen.decode_png(destination.read_bytes())  # the printer must be able to read it


def render_animation(destination: Path) -> None:
    frames = [rasterize(boot_frame_svg(index / BOOT_FRAMES)) for index in range(BOOT_FRAMES)]
    first = boot_screen.Rectangle(0, 0, WIDTH, HEIGHT, frames[0])
    # Each later frame holds what changed since the one before; the last brings the screen back to the first, so the
    # player loops from frame 1.
    encoded = [(first,)] + [_changes(before, after) for before, after in zip(frames, frames[1:] + frames[:1])]
    animation = boot_screen.Animation(BOOT_FPS, WIDTH, HEIGHT, tuple(encoded))
    data = boot_screen.encode(animation)
    boot_screen.decode(data)  # the printer must be able to read it
    destination.write_bytes(data)


def _changes(before: bytes, after: bytes) -> tuple:
    """The rectangles of `after` that differ from `before`: one per band of changed rows."""
    stride = 3 * WIDTH
    changed_rows = [y for y in range(HEIGHT) if before[y * stride:(y + 1) * stride] != after[y * stride:(y + 1) * stride]]
    bands, rectangles = [], []
    for y in changed_rows:
        if bands and y - bands[-1][1] <= 8:
            bands[-1][1] = y
        else:
            bands.append([y, y])
    for top, bottom in bands:
        columns = [x for x in range(WIDTH) if any(
            before[(y * WIDTH + x) * 3:(y * WIDTH + x) * 3 + 3] != after[(y * WIDTH + x) * 3:(y * WIDTH + x) * 3 + 3]
            for y in range(top, bottom + 1))]
        left, right = min(columns), max(columns) + 1
        rows = b"".join(after[(y * WIDTH + left) * 3:(y * WIDTH + right) * 3] for y in range(top, bottom + 1))
        rectangles.append(boot_screen.Rectangle(left, top, right - left, bottom + 1 - top, rows))
    return tuple(rectangles)


def main() -> int:
    touch_canvas.ASSET_FILE.write_bytes(render_assets(touch_canvas.STYLES))
    print(touch_canvas.ASSET_FILE.relative_to(REPO_ROOT))
    still_assets = touch_canvas.decode_assets(render_assets(STILL_STYLES))
    for path, text in STILLS.items():
        save_png(still_image(still_assets, *text), path)
        print(path.relative_to(REPO_ROOT))
    save_png(rasterize(boot_logo_svg()), boot_screen.BOOT_LOGO_IMAGE)
    print(boot_screen.BOOT_LOGO_IMAGE.relative_to(REPO_ROOT))
    render_animation(boot_screen.ANIMATION_FILE)
    print(boot_screen.ANIMATION_FILE.relative_to(REPO_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
