"""The touchscreen front end's screens: drawn to the framebuffer, and tapped on the Goodix touchscreen.

    ui = touch_ui.open(reporter)            # None when there is no framebuffer to draw on
    buttons = ui.show("found", where="QIDI Max 4", title="Install Valve Oil 26.11.12.1", rows=[...], text="...")
    tapped = ui.wait(buttons, timeout=1.0)  # a button id, or None when the timeout passes first
    ui.update(header_step="Step 4 of 10 · 1:53")  # redraws only what changed
    ui.close()

The screens are those of openspec/changes/archive/2026-11-17-add-usb-touchscreen-installer/screens.html, laid out as it lays them out at
800x480, and drawn turned on the Plus 5's and Smart 5's portrait panels (update_screen.Geometry). compose() is the pure
part: a screen and its fields to an RGB canvas and its buttons' rectangles. A screen that can't be composed is drawn as
update_screen's text instead, and show() then returns no buttons. Nothing is drawn while the screen waits for a tap;
update() and show() write only the rectangles that changed, so ticking a clock costs one small write.

Fields every screen takes:
    footer        the line at the bottom, e.g. "Valve Oil 26.11.12.1 · USB installer · QIDI firmware 01.01.07.01"
    where         the printer's name in the header, e.g. "QIDI Max 4"
    header_step   the header's right-hand text, e.g. "Question 1 of 2", "Step 4 of 10 · 1:52", "Finished in 13:48"

Text fields take a paragraph or a list of paragraphs. A paragraph is a str, a (tone, str) pair, or a list of such
pairs drawn as one paragraph; tone is "plain", "muted", "good", "warn", or "bad" (the smaller pink error line), and a
str uses the tone the screen gives that paragraph. **text** is drawn bold in the brightest ink. "\\n" breaks a line.
Rows are (label, value) or (label, value, note) tuples; the note is drawn smaller and dimmer after the value.

Screens (mockup id in screens.html; fields; buttons, in order, besides "log" for the header's Log button on every screen
but the log itself):
    checking             01-checking. text, note (default "Checking the printer…", "Nothing has been changed yet.").
    found                02-welcome, 02b-firmware-first. title, rows, text (muted), firmware_first. quit, start.
    question             03-auto-updates, 04-ai-detection. title, text (first paragraph plain, the rest muted),
                         no_label, yes_label. back, no, yes.
    ready                07-ready. title (default "Ready to install"), rows, text (small, muted). back, install.
    installing           08-installing, 09-box-firmware, 18-ssh-install, 19-auto-update. title (default
                         "Installing"; a title starting "Updating" gets the update icon), step, detail, steps (names),
                         index (1-based, the current step), progress (0 to 1; default the steps done), warning (the
                         amber line at the bottom). No buttons but log.
    done                 10-done. title (default "Valve Oil is installed"), lines (the numbered instructions; default
                         the mockup's, with fluidd_url), fluidd_url, qr_url (the code beside them), waiting_text (the
                         amber box; none when empty). restart.
    update               11-update. title (default "Update available"), rows, text (muted). settings, update, quit.
    up_to_date           12-up-to-date. title (default "Valve Oil is up to date"), rows, restore (default True: offer
                         "Restore a backup"). restore, settings, again, quit.
    busy                 14-busy. title, text, rows, note (small, muted). quit, again.
    unsupported          15-unsupported. title, rows, text. back_to_qidi.
    board_not_supported  15b-board-not-supported. title, rows, text, qr_url. quit, back_to_qidi. A code too large
                         for the space beside the text moves beside the rows, from the top of the body.
    failed               16-failed. title (default "Install stopped"), text (plain), log_name (the file named in the
                         closing small note). quit, view_log, again.
    log                  17-log. title (default "Installer log"), lines (the last that fit are shown). close.
    backups              not in the mockups. title (default "Restore a backup"), rows (up to 4 of (label, value)),
                         text (muted). back, backup_0 to backup_3 (one per row; the row is the button).
    confirm              not in the mockups. title, text (first paragraph plain, the rest muted), ok_label. back, ok.

EXAMPLES holds every mockup's content by its screens.html id. To check screens without a printer:

    python3 -m installer.runtime.touch_ui --preview all build/touch-ui-previews

writes each example upright and as the turned portrait framebuffer holds it.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import math
import os
import re
import select
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import qr_code, update_screen
from .touch_canvas import (AMBER, AMBER_2, BG, BUTTON_LINE, GREEN, INK, INK_2, INK_3, LINE, LOG_BG, PINK,
                           PRIMARY_INK, SURFACE, SURFACE_2, WAIT_BG, Canvas, encode_png, line_width, load_assets, mix,
                           wrap)

HEIGHT, WIDTH = 480, 800
LEFT, RIGHT = 28, 772  # the body's edges
BODY_WIDTH = RIGHT - LEFT
BODY_TOP = 118
ACTIONS_TOP, ACTIONS_HEIGHT = 376, 64
LINE_FACTOR = 1.38  # the body's line height per px of font size

TONES = ("plain", "muted", "good", "warn", "bad")
COMMON_FIELDS = frozenset({"footer", "where", "header_step"})
REPOSITORY_URL = "https://github.com/thelegendtubaguy/valve-oil"


class TouchUnavailable(RuntimeError):
    pass


# ---- Layout ----------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    id: str
    label: str
    kind: str = ""  # "primary", "small", "wide", or a mix
    note: str = ""


@dataclass
class Composition:
    """A composed screen: its canvas and each button's rectangles (x, y, w, h) on the upright screen."""

    screen: str
    canvas: Canvas
    buttons: dict = field(default_factory=dict)
    header_bottom: int = 58


class _Screen:
    def __init__(self, screen_id: str, fields: dict):
        self.id = screen_id
        self.f = fields
        self.c = Canvas(assets=load_assets())
        self.buttons: dict[str, list] = {}

    def font(self, name):
        return self.c.font(name)

    def button(self, button_id: str, x: float, y: float, w: float, h: float) -> None:
        self.buttons.setdefault(button_id, []).append((round(x), round(y), round(x + w) - round(x),
                                                       round(y + h) - round(y)))


def _baseline(top: float, line_height: float, font) -> float:
    """The baseline of a line box `line_height` tall at `top`, as CSS centers the font's ascent and descent in it."""
    return top + (line_height - font.line_height) / 2 + font.ascent


def _runs(paragraph, base: str, tone: str) -> list[tuple]:
    """A paragraph's runs (text, style, color, spacing) in the `base` style, with str parts in `tone`."""
    if isinstance(paragraph, str):
        parts = [(tone, paragraph)]
    elif isinstance(paragraph, tuple) and len(paragraph) == 2 and paragraph[0] in TONES:
        parts = [paragraph]
    else:
        parts = [(tone, part) if isinstance(part, str) else part for part in paragraph]
    runs = []
    for part_tone, text in parts:
        if part_tone not in TONES:
            raise ValueError(f"unknown tone {part_tone!r}")
        style = "detail" if part_tone == "bad" else base
        color = {"plain": INK, "muted": INK_2, "good": GREEN, "warn": AMBER, "bad": PINK}[part_tone]
        bold = {"body": "body_bold", "note": "note_bold", "detail": "detail_now"}[style]
        for index, piece in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
            if piece:
                runs.append((piece, bold if index % 2 else style, INK if index % 2 else color, 0.0))
    return runs


def _paragraphs(value) -> list:
    if value is None or value == "":
        return []
    if isinstance(value, str) or (isinstance(value, tuple) and len(value) == 2 and value[0] in TONES):
        return [value]
    return list(value)


class _Flow:
    """The body's blocks top to bottom, with CSS's collapsing margins between them."""

    def __init__(self, s: _Screen, top: float = BODY_TOP, left: float = LEFT, width: float = BODY_WIDTH):
        self.s, self.y, self.left, self.width = s, top, left, width
        self.margin = None  # the last block's bottom margin; None before the first block

    def space(self, margin_top: float) -> float:
        self.y += margin_top if self.margin is None else max(self.margin, margin_top)
        self.margin = 0
        return self.y

    def end(self, height: float, margin_bottom: float = 0) -> None:
        self.y += height
        self.margin = margin_bottom

    def paragraph(self, paragraph, *, base="body", tone="plain", margin_top=0, margin_bottom=10, bg=BG,
                  draw=True) -> float:
        runs = [(text, self.s.font(style), color, spacing) for text, style, color, spacing in
                _runs(paragraph, base, tone)]
        if not runs:
            return 0
        top = self.space(margin_top)
        size_font = runs[0][1]
        line_height = size_font.size * LINE_FACTOR
        lines = wrap(runs, self.width)
        if draw:
            for index, line in enumerate(lines):
                baseline = _baseline(top + index * line_height, line_height, size_font)
                for offset, text, font, color, spacing in line:
                    self.s.c.text(self.left + offset, baseline, text, font, color, bg, spacing)
        self.end(len(lines) * line_height, margin_bottom)
        return len(lines) * line_height

    def card(self, rows, *, margin_top=8, row_buttons=None, draw=True, padding=7) -> None:
        s = self.s
        top = self.space(margin_top)
        body, small = s.font("body"), s.font("small")
        line_height = body.size * LINE_FACTOR
        inner_left, inner_width = self.left + 19, self.width - 38
        label_width, gap = 190, 14
        value_width = inner_width - label_width - gap - (24 if row_buttons else 0)
        laid, height = [], 1 + 6
        for index, row in enumerate(rows):
            label, value, note = (tuple(row) + ("",))[:3]
            value_runs = [(str(value), body, INK, 0.0)]
            if note:
                value_runs += [(" ", body, INK, 0.0), ("", None, None, 8.0), (str(note), small, INK_2, 0.0)]
            labels = wrap([(str(label), body, INK_2, 0.0)], label_width)
            values = wrap(value_runs, value_width)
            row_height = (1 if index else 0) + padding + max(len(labels), len(values)) * line_height + padding
            laid.append((top + height, row_height, labels, values, index))
            height += row_height
        height += 6 + 1
        if not draw:
            self.end(height)
            return
        s.c.round_rect(self.left, top, self.width, height, 12, SURFACE, LINE)
        for row_top, row_height, labels, values, index in laid:
            if index:
                s.c.fill(inner_left, row_top, inner_width, 1, LINE)
            content_top = row_top + (1 if index else 0) + padding
            for column_left, lines in ((inner_left, labels), (inner_left + label_width + gap, values)):
                for number, line in enumerate(lines):
                    baseline = _baseline(content_top + number * line_height, line_height, body)
                    for offset, text, font, color, spacing in line:
                        s.c.text(column_left + offset, baseline, text, font, color, SURFACE, spacing)
            if row_buttons:
                chevron = s.font("body")
                s.c.text(inner_left + inner_width - chevron.measure("›"), _baseline(content_top, line_height, body),
                         "›", chevron, INK_2, SURFACE)
                s.button(row_buttons[index], self.left + 1, row_top, self.width - 2, row_height)
        self.end(height)


def _wrap_text(s, text, style, width):
    return wrap([(text, s.font(style), INK, 0.0)], width)


def _header(s: _Screen, quit_chip: bool) -> None:
    c, f = s.c, s.f
    c.sprite("mark", 28, 16)
    x = 61.0
    brand = s.font("brand")
    x += c.text(x, _baseline(14, 44, brand), "VALVE OIL", brand, INK, BG, 14 * 0.16) + 12
    where = f.get("where")
    if where:
        sep, hdr = s.font("sep"), s.font("hdr")
        x += c.text(x, _baseline(14, 44, sep), "|", sep, INK_3, BG) + 12
        c.text(x, _baseline(14, 44, hdr), str(where), hdr, INK_2, BG, clip=440)
    chip = s.font("chip")
    left = 776.0
    if s.id != "log":  # the log screen is already showing the log
        log_width = 2 + 32 + 18 + 8 + chip.measure("Log")
        left -= log_width
        _chip(s, "log", left, log_width, "Log", icon="list")
    if quit_chip:
        quit_width = 2 + 32 + chip.measure("Quit")
        left = left - 12 - quit_width
        _chip(s, "quit", left, quit_width, "Quit")
    step = f.get("header_step")
    if step:
        font = s.font("hdr_tnum")
        gap = 12 + 6 if left < 776.0 else 0  # beside a chip, or at the right margin on the log screen
        c.text(left - gap - font.measure(str(step)), _baseline(14, 44, font), str(step), font, INK_2, BG)


def _chip(s: _Screen, button_id: str, x: float, width: float, label: str, icon: str | None = None) -> None:
    c, chip = s.c, s.font("chip")
    c.round_rect(x, 16, width, 40, 10, SURFACE_2, LINE)
    text_x = x + 17
    if icon:
        c.sprite(icon, round(text_x), 27)
        text_x += 18 + 8
    c.text(text_x, _baseline(17, 38, chip), label, chip, INK, SURFACE_2)
    s.button(button_id, x, 16, width, 40)


def _title(s: _Screen, icon: str, default: str = "", *, top: float = 70, style: str = "title") -> None:
    font = s.font(style)
    title = str(s.f.get("title") or default)
    height = max(30.0, font.line_height)
    s.c.sprite(icon, 28, round(top + (height - 30) / 2))
    left, available = 70, RIGHT - 70
    spacing = -0.01 * font.size
    if font.measure(title, spacing) > available and style == "title":
        font, spacing = s.font("title_small"), -0.24
    s.c.text(left, _baseline(top + (height - font.line_height) / 2, font.line_height, font), title, font, INK, BG,
             spacing, clip=RIGHT)


def _footer(s: _Screen) -> None:
    text = s.f.get("footer")
    if text:
        font = s.font("footer")
        s.c.text(400 - font.measure(str(text)) / 2, _baseline(480 - 11 - font.line_height, font.line_height, font),
                 str(text), font, INK_3, BG)


def _actions(s: _Screen, actions: list[Action]) -> None:
    if not actions:
        return
    gaps = 14 * (len(actions) - 1)
    fixed = sum(150 for action in actions if "small" in action.kind)
    grow = sum(1.6 if "wide" in action.kind else 1 for action in actions if "small" not in action.kind)
    free = BODY_WIDTH - gaps - fixed
    x = float(LEFT)
    label_font, note_font = s.font("button"), s.font("button_note")
    for action in actions:
        if "small" in action.kind:
            width = 150.0
        else:
            width = free * (1.6 if "wide" in action.kind else 1) / grow
        primary = "primary" in action.kind
        fill, edge = (AMBER, AMBER) if primary else (SURFACE_2, BUTTON_LINE)
        ink = PRIMARY_INK if primary else INK
        s.c.round_rect(x, ACTIONS_TOP, width, ACTIONS_HEIGHT, 12, fill, edge)
        label_lines = _wrap_text(s, action.label, "button", width - 26)
        label_height = label_font.size * 1.15
        note_height = (2 + note_font.size * 1.15) if action.note else 0
        top = ACTIONS_TOP + 1 + (ACTIONS_HEIGHT - 2 - len(label_lines) * label_height - note_height) / 2
        for index, line in enumerate(label_lines):
            text = "".join(piece[1] for piece in line)
            s.c.text(x + width / 2 - line_width(line) / 2, _baseline(top + index * label_height, label_height,
                                                                     label_font), text, label_font, ink, fill)
        if action.note:
            note_top = top + len(label_lines) * label_height + 2
            s.c.text(x + width / 2 - note_font.measure(action.note) / 2,
                     _baseline(note_top, note_font.size * 1.15, note_font), action.note, note_font,
                     mix(ink, fill, 0.75), fill)
        s.button(action.id, x, ACTIONS_TOP, width, ACTIONS_HEIGHT)
        x += width + 14


def _center(s: _Screen, top: float, text: str, style: str, color) -> None:
    font = s.font(style)
    s.c.text(400 - font.measure(text) / 2, _baseline(top, font.line_height, font), text, font, color, BG)


def _qr_modules(url: str) -> list:
    try:
        return qr_code.encode(url, "M" if len(url.encode("utf-8")) <= 213 else "L")
    except qr_code.DataTooLong:
        return []


def _qr(s: _Screen, x: float, y: float, size: int, modules: list) -> None:
    s.c.round_rect(x, y, size, size, 8, INK)
    if not modules:
        return
    scale = max(1, (size - 8) // (len(modules) + 4))
    side = len(modules) * scale
    left, top = round(x) + (size - side) // 2, round(y) + (size - side) // 2
    s.c.qr(left, top, modules, scale, BG, INK)


def _qr_size(modules: list, minimum: int = 104) -> int:
    """The plate for `modules`: at least 2 px per module and a quiet zone of 2 modules on each side."""
    if not modules:
        return minimum
    return max(minimum, (len(modules) + 4) * 2 + 8)


# ---- Screens ---------------------------------------------------------------------------------------------------------

def _actions_for(screen_id: str, f: dict) -> list[Action]:
    if screen_id == "found":
        return [Action("quit", "Quit", "small"), Action("start", "Get started", "primary")]
    if screen_id == "question":
        return [Action("back", "Back", "small"), Action("no", str(f.get("no_label") or "No")),
                Action("yes", str(f.get("yes_label") or "Yes"), "primary")]
    if screen_id == "ready":
        return [Action("back", "Back", "small"), Action("install", "Install", "primary")]
    if screen_id == "done":
        return [Action("restart", "Restart now", "small", "leave the drive in")]
    if screen_id == "update":
        return [Action("settings", "Change settings"), Action("update", "Update", "primary wide")]
    if screen_id == "up_to_date":
        restore = [Action("restore", "Restore a backup")] if f.get("restore", True) else []
        return restore + [Action("settings", "Change settings"), Action("again", "Install again")]
    if screen_id == "busy":
        return [Action("quit", "Quit", "small"), Action("again", "Check again", "primary")]
    if screen_id in ("unsupported", "board_not_supported"):
        return [Action("back_to_qidi", "Back to QIDI's screen", "primary")]
    if screen_id == "failed":
        return [Action("quit", "Quit", "small"), Action("view_log", "View log"), Action("again", "Try again", "primary")]
    if screen_id == "log":
        return [Action("close", "Close", "primary")]
    if screen_id == "backups":
        return [Action("back", "Back", "small")]
    if screen_id == "confirm":
        return [Action("back", "Back", "small"), Action("ok", str(f.get("ok_label") or "OK"), "primary")]
    return []


def _draw_checking(s: _Screen) -> None:
    s.c.sprite("spinner", 372, 178)
    _center(s, 252, str(s.f.get("text") or "Checking the printer…"), "center", INK)
    _center(s, 288, str(s.f.get("note") or "Nothing has been changed yet."), "note", INK_2)


def _draw_found(s: _Screen) -> None:
    _title(s, "icon_down")
    flow = _Flow(s)
    flow.card(s.f.get("rows") or ())
    for paragraph in _paragraphs(s.f.get("text")):
        flow.paragraph(paragraph, tone="muted", margin_top=14)


def _draw_question(s: _Screen) -> None:
    _title(s, "icon_q")
    flow = _Flow(s)
    for index, paragraph in enumerate(_paragraphs(s.f.get("text"))):
        flow.paragraph(paragraph, tone="plain" if index == 0 else "muted")


def _draw_ready(s: _Screen) -> None:
    _title(s, "icon_down", "Ready to install")
    flow = _Flow(s, top=112)
    flow.card(s.f.get("rows") or ())
    for paragraph in _paragraphs(s.f.get("text")):
        _fitting_paragraph(flow, paragraph, base="note", tone="muted", margin_top=9)


def _fitting_paragraph(flow: _Flow, paragraph, **kwargs) -> None:
    """A closing note, left out when it would run into the buttons."""
    probe = _Flow(flow.s, flow.y, flow.left, flow.width)
    probe.margin = flow.margin
    probe.paragraph(paragraph, draw=False, **kwargs)
    if probe.y <= ACTIONS_TOP - 4:
        flow.paragraph(paragraph, **kwargs)


def _draw_installing(s: _Screen) -> None:
    f = s.f
    title = str(f.get("title") or "Installing")
    _title(s, "icon_up" if title.startswith("Updating") else "icon_down", title)
    flow = _Flow(s)
    runs = [("plain", str(f.get("step") or ""))]
    if f.get("detail"):
        runs.append(("muted", " · " + str(f["detail"])))
    flow.paragraph(runs, margin_bottom=12)
    steps = [str(step) for step in f.get("steps") or ()]
    index = int(f.get("index") or 0)
    progress = f.get("progress")
    if progress is None:
        progress = (index - 1) / len(steps) if steps and index else 0.0
    top = flow.space(0)
    s.c.round_rect(LEFT, top, BODY_WIDTH, 14, 7, SURFACE_2)
    filled = BODY_WIDTH * min(1.0, max(0.0, float(progress)))
    if filled >= 1:
        s.c.round_rect(LEFT, top, filled, 14, 7, AMBER, gradient=(AMBER_2, AMBER))
    flow.end(14)
    _steps(s, flow.space(16), steps, index - 1)
    warning = f.get("warning")
    if warning:
        font = s.font("note")
        s.c.text(400 - font.measure(str(warning)) / 2, _baseline(440 - font.line_height, font.line_height, font),
                 str(warning), font, AMBER, BG)


def _steps(s: _Screen, top: float, steps: list[str], now: int) -> None:
    rows = max(5, math.ceil(len(steps) / 2))
    row_height = 17 * LINE_FACTOR
    column_width = (BODY_WIDTH - 28) / 2
    for index, name in enumerate(steps):
        column, row = divmod(index, rows)
        x = LEFT + column * (column_width + 28)
        row_top = top + row * (row_height + 3)
        done, current = index < now, index == now
        font = s.font("detail_now" if current else "detail")
        baseline = _baseline(row_top, row_height, font)
        if done:
            s.c.sprite("tick", x, round(row_top + (row_height - 18) / 2))
        else:
            marker, color = ("›", AMBER) if current else ("•", INK_3)
            s.c.text(x + (18 - font.measure(marker)) / 2, baseline, marker, font, color, BG)
        color = INK_2 if done else INK if current else INK_3
        s.c.text(x + 28, baseline, name, font, color, BG, clip=x + column_width)


def _draw_done(s: _Screen) -> None:
    f = s.f
    _title(s, "icon_check", "Valve Oil is installed")
    lines = f.get("lines")
    if lines is None:
        lines = ["Take out the USB drive. The log is already saved on it.",
                 "The printer restarts by itself. Wait for the home screen."]
        if f.get("fluidd_url"):
            lines.append(f"Open Fluidd at **{f['fluidd_url']}**")
        lines.append("Set up your slicer: scan the code, or see\n**github.com/thelegendtubaguy/valve-oil**")
    modules = _qr_modules(str(f.get("qr_url") or REPOSITORY_URL))
    size = _qr_size(modules)
    top = BODY_TOP
    text_width = BODY_WIDTH - 22 - size
    flow = _Flow(s, top=top + 6, left=LEFT + 26, width=text_width - 26)
    body = s.font("body")
    for number, line in enumerate(_paragraphs(lines), 1):
        marker = f"{number}. "
        line_top = flow.space(0)
        s.c.text(flow.left - body.measure(marker), _baseline(line_top, body.size * LINE_FACTOR, body), marker, body,
                 INK, BG)
        flow.paragraph(line, margin_bottom=5)
    text_height = flow.y + (flow.margin or 0) - top
    _qr(s, RIGHT - size, top, size, modules)
    waiting = f.get("waiting_text")
    if waiting:
        box_top = top + max(text_height, size) + 14
        height = 1 + 12 + body.size * LINE_FACTOR + 12 + 1
        s.c.round_rect(LEFT, box_top, BODY_WIDTH, height, 12, WAIT_BG, AMBER)
        s.c.sprite("spinner_small", LEFT + 17, round(box_top + (height - 24) / 2))
        semibold = s.font("body_semibold")
        s.c.text(LEFT + 17 + 24 + 12, _baseline(box_top + 13, body.size * LINE_FACTOR, semibold), str(waiting),
                 semibold, AMBER, WAIT_BG, clip=RIGHT - 17)


def _draw_update(s: _Screen) -> None:
    _title(s, "icon_up", "Update available")
    flow = _Flow(s)
    flow.card(s.f.get("rows") or ())
    for paragraph in _paragraphs(s.f.get("text")):
        flow.paragraph(paragraph, tone="muted", margin_top=14)


def _draw_up_to_date(s: _Screen) -> None:
    _title(s, "icon_check", "Valve Oil is up to date")
    _Flow(s).card(s.f.get("rows") or ())


def _draw_busy(s: _Screen) -> None:
    _title(s, "icon_pause", "Can't install right now")

    def body(padding: int, draw: bool) -> float:
        flow = _Flow(s)
        for paragraph in _paragraphs(s.f.get("text")):
            flow.paragraph(paragraph, draw=draw)
        flow.card(s.f.get("rows") or (), padding=padding, draw=draw)
        for paragraph in _paragraphs(s.f.get("note")):
            if draw:
                _fitting_paragraph(flow, paragraph, base="note", tone="muted", margin_top=10)
            else:
                flow.paragraph(paragraph, base="note", tone="muted", margin_top=10, draw=False)
        return flow.y

    # The mockup's rows leave the note no room above the buttons once the reason takes two lines: they close up.
    body(7 if body(7, False) <= ACTIONS_TOP - 4 else 4, True)


def _draw_unsupported(s: _Screen) -> None:
    _title(s, "icon_stop", "This QIDI firmware isn't supported yet")
    flow = _Flow(s)
    flow.card(s.f.get("rows") or ())
    for paragraph in _paragraphs(s.f.get("text")):
        flow.paragraph(paragraph, margin_top=14)


def _draw_board_not_supported(s: _Screen) -> None:
    f = s.f
    _title(s, "icon_stop", "This board isn't supported yet")
    modules = _qr_modules(str(f.get("qr_url") or REPOSITORY_URL + "/issues/new"))
    size = _qr_size(modules)
    probe = _Flow(s)
    probe.card(f.get("rows") or (), draw=False)
    split_top = probe.y + 14
    if split_top + size <= ACTIONS_TOP - 8:
        # As in the mockup: the rows, then the text with the code beside it.
        flow = _Flow(s)
        flow.card(f.get("rows") or ())
        text_flow = _Flow(s, top=flow.space(14), width=BODY_WIDTH - 22 - size)
        for paragraph in _paragraphs(f.get("text")):
            text_flow.paragraph(paragraph)
        _qr(s, RIGHT - size, split_top, size, modules)
        return
    # A long link's code is too tall for that: it goes beside the rows and the text, from the top of the body.
    size = min(size, ACTIONS_TOP - 8 - (BODY_TOP + 8))
    flow = _Flow(s, width=BODY_WIDTH - 22 - size)
    flow.card(f.get("rows") or ())
    for paragraph in _paragraphs(f.get("text")):
        flow.paragraph(paragraph, margin_top=14)
    _qr(s, RIGHT - size, BODY_TOP + 8, size, modules)


def _draw_failed(s: _Screen) -> None:
    f = s.f
    _title(s, "icon_stop", str(f.get("title") or "Install stopped"))
    flow = _Flow(s)
    for paragraph in _paragraphs(f.get("text")):
        flow.paragraph(paragraph)
    if f.get("log_name"):
        _fitting_paragraph(flow, f"The log is saved on the USB drive as **{f['log_name']}**. Attach it if you "
                                 "report this.", base="note", tone="muted")


def _draw_log(s: _Screen) -> None:
    _title(s, "icon_down", "Installer log", top=66, style="title_small")
    top, height = 106, 250
    s.c.round_rect(LEFT, top, BODY_WIDTH, height, 10, LOG_BG, LINE)
    text, stamp = s.font("log"), s.font("log_time")
    line_height = 13.5 * 1.5
    fit = int((height - 2 - 24) // line_height)
    lines = [str(line) for line in (s.f.get("lines") or ())][-fit:]
    clip = RIGHT - 15
    for index, line in enumerate(lines):
        baseline = _baseline(top + 13 + index * line_height, line_height, text)
        x = LEFT + 15
        match = re.match(r"(\d\d:\d\d:\d\d)(\s.*)?$", line)
        if match:
            x += s.c.text(x, baseline, match.group(1), stamp, INK, LOG_BG, clip=clip)
            line = match.group(2) or ""
        s.c.text(x, baseline, line, text, INK_2, LOG_BG, clip=clip)


def _draw_backups(s: _Screen) -> None:
    _title(s, "icon_down", "Restore a backup")
    flow = _Flow(s)
    rows = list(s.f.get("rows") or ())[:4]
    flow.card(rows, row_buttons=[f"backup_{index}" for index in range(len(rows))])
    for paragraph in _paragraphs(s.f.get("text")):
        _fitting_paragraph(flow, paragraph, tone="muted", margin_top=14)


SCREENS = {
    "checking": (_draw_checking, False, {"text", "note"}),
    "found": (_draw_found, True, {"title", "rows", "text", "firmware_first"}),
    "question": (_draw_question, False, {"title", "text", "no_label", "yes_label"}),
    "ready": (_draw_ready, False, {"title", "rows", "text"}),
    "installing": (_draw_installing, False, {"title", "step", "detail", "steps", "index", "progress", "warning"}),
    "done": (_draw_done, False, {"title", "lines", "fluidd_url", "qr_url", "waiting_text"}),
    "update": (_draw_update, True, {"title", "rows", "text"}),
    "up_to_date": (_draw_up_to_date, True, {"title", "rows", "restore"}),
    "busy": (_draw_busy, True, {"title", "text", "rows", "note"}),
    "unsupported": (_draw_unsupported, False, {"title", "rows", "text"}),
    "board_not_supported": (_draw_board_not_supported, True, {"title", "rows", "text", "qr_url"}),
    "failed": (_draw_failed, False, {"title", "text", "log_name"}),
    "log": (_draw_log, False, {"title", "lines"}),
    "backups": (_draw_backups, False, {"title", "rows", "text"}),
    "confirm": (_draw_question, False, {"title", "text", "ok_label"}),  # laid out as a question
}


def check_fields(screen_id: str, fields: dict) -> None:
    """TypeError for a screen or field that doesn't exist."""
    if screen_id not in SCREENS:
        raise TypeError(f"no touchscreen screen {screen_id!r}")
    unknown = set(fields) - SCREENS[screen_id][2] - COMMON_FIELDS
    if unknown:
        raise TypeError(f"screen {screen_id!r} takes no field {', '.join(sorted(unknown))}")


def buttons_for(screen_id: str, fields: dict) -> tuple[str, ...]:
    """The ids of the screen's buttons, as show() returns them."""
    check_fields(screen_id, fields)
    ids = ["quit"] if SCREENS[screen_id][1] else []
    for action in _actions_for(screen_id, fields):
        if action.id not in ids:
            ids.append(action.id)
    if screen_id == "backups":
        ids += [f"backup_{index}" for index in range(len(list(fields.get("rows") or ())[:4]))]
    return tuple(ids) + (() if screen_id == "log" else ("log",))


def compose(screen_id: str, **fields) -> Composition:
    """The screen drawn on the upright 800x480 canvas, with its buttons."""
    check_fields(screen_id, fields)
    draw, quit_chip, _ = SCREENS[screen_id]
    s = _Screen(screen_id, fields)
    _header(s, quit_chip)
    draw(s)
    _actions(s, _actions_for(screen_id, fields))
    _footer(s)
    buttons = {button_id: s.buttons[button_id] for button_id in buttons_for(screen_id, fields)}
    return Composition(screen_id, s.c, buttons)


def starting_screen(*, where: str, footer: str) -> bytes:
    """The USB hand-off's starting screen (installer/usb/launch.sh), as an upright 800x480 PNG for update_screen.draw:
    the checking screen saying the installer is starting, without the header's Log button, which nothing answers
    until the installer runs. The installer's first screen then replaces it with no visible jump."""
    composition = compose("checking", text="Starting the Valve Oil installer…", where=where, footer=footer)
    for rectangles in composition.buttons.values():
        for x, y, w, h in rectangles:
            composition.canvas.fill(x, y, w, h, BG)
    return encode_png(WIDTH, HEIGHT, bytes(composition.canvas.pixels))


def compose_header(previous: Composition, screen_id: str, **fields) -> Composition:
    """`previous` with its header drawn again for `fields`, which differ from its own only in the header."""
    canvas = previous.canvas.copy()
    canvas.fill(0, 0, WIDTH, previous.header_bottom, BG)
    s = _Screen(screen_id, fields)
    s.c = canvas
    _header(s, SCREENS[screen_id][1])
    return Composition(screen_id, canvas, previous.buttons, previous.header_bottom)


def fallback_lines(screen_id: str, fields: dict) -> tuple:
    """The screen as update_screen's text lines, for when it can't be composed."""
    title = fields.get("title") or {"checking": "Checking the printer", "installing": "Installing",
                                    "failed": "Install stopped", "log": "Installer log"}.get(screen_id, "Valve Oil")
    lines = [(4, update_screen.TEXT, line) for line in _wrap_plain(str(title), 30)[:2]]
    detail = fields.get("step") or fields.get("header_step") or ""
    for line in _wrap_plain(str(detail), 40)[:2]:
        lines.append((3, update_screen.TEXT, line))
    lines.append((3, update_screen.WARNING, "Don't turn the printer off."))
    return tuple(lines)


def _wrap_plain(text: str, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.replace("·", "-").replace("…", "...").split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    return lines + ([line] if line else [])


def changed_rectangles(before: bytes, after: bytes, width: int = WIDTH, height: int = HEIGHT) -> list[tuple]:
    """Rectangles (x, y, w, h) covering every pixel that differs: one per band of changed rows."""
    stride, chunk = width * 3, 48
    bands = []
    for y in range(height):
        start = y * stride
        if before[start:start + stride] == after[start:start + stride]:
            continue
        first = 0
        while before[start + first:start + first + chunk] == after[start + first:start + first + chunk]:
            first += chunk
        last = stride
        while before[start + last - chunk:start + last] == after[start + last - chunk:start + last]:
            last -= chunk
        left, right = first // 3, -(-last // 3)
        if bands and y - bands[-1][3] <= 8:
            band = bands[-1]
            bands[-1] = [min(band[0], left), band[1], max(band[2], right), y]
        else:
            bands.append([left, y, right, y])
    return [(left, top, right - left, bottom + 1 - top) for left, top, right, bottom in bands]


# ---- Framebuffer -----------------------------------------------------------------------------------------------------

class Panel:
    """The framebuffer, written in the coordinates of the upright screen."""

    def __init__(self, fd: int, geometry: update_screen.Geometry):
        self.fd, self.geometry = fd, geometry
        self.pixels: dict = {}
        channels, size = geometry.channels, geometry.pixel_bytes
        # R, G, and B byte positions in a pixel when each is a whole byte: pixels then convert by slicing.
        self.bytes_at = None
        if size in (3, 4) and all(length == 8 and offset % 8 == 0 for offset, length in channels):
            self.bytes_at = tuple(offset // 8 for offset, _ in channels)

    @classmethod
    def open(cls, device: str = update_screen.FRAMEBUFFER) -> "Panel":
        fd = os.open(device, os.O_RDWR | os.O_CLOEXEC)
        try:
            return cls(fd, update_screen.geometry(fd))
        except BaseException:
            os.close(fd)
            raise

    def close(self) -> None:
        os.close(self.fd)

    def _convert(self, red: bytes, green: bytes, blue: bytes) -> bytes:
        size = self.geometry.pixel_bytes
        if self.bytes_at is not None:
            out = bytearray(len(red) * size)
            for position, channel in zip(self.bytes_at, (red, green, blue)):
                out[position::size] = channel
            return bytes(out)
        return b"".join(update_screen._cached_pixel(self.pixels, (r << 16) | (g << 8) | b, self.geometry.channels,
                                                    size) for r, g, b in zip(red, green, blue))

    def write(self, canvas: Canvas, x: int, y: int, w: int, h: int) -> None:
        g, pixels, row_bytes = self.geometry, canvas.pixels, canvas.width * 3
        if (canvas.width, canvas.height) != g.upright:
            raise ValueError(f"the screen is {g.upright[0]}x{g.upright[1]}, the canvas {canvas.width}x{canvas.height}")
        if not g.turned:
            for row in range(y, y + h):
                line = pixels[row * row_bytes + x * 3:row * row_bytes + (x + w) * 3]
                os.pwrite(self.fd, self._convert(line[0::3], line[1::3], line[2::3]),
                          g.start + row * g.stride + x * g.pixel_bytes)
            return
        # Each upright column is a framebuffer row, from the bottom one up.
        for column in range(x, x + w):
            start = y * row_bytes + column * 3
            stop = start + h * row_bytes
            data = self._convert(pixels[start:stop:row_bytes], pixels[start + 1:stop + 1:row_bytes],
                                 pixels[start + 2:stop + 2:row_bytes])
            os.pwrite(self.fd, data, g.start + (g.height - 1 - column) * g.stride + y * g.pixel_bytes)


# ---- Touch -----------------------------------------------------------------------------------------------------------

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0
BTN_TOUCH = 0x14A
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x2F, 0x35, 0x36, 0x39
INPUT_PROP_DIRECT = 0x01
# struct input_event on a 64-bit kernel: struct timeval, type, code, value.
INPUT_EVENT = struct.Struct("llHHi")
EVIOCGABS = 0x80184540  # + axis: struct input_absinfo (value, minimum, maximum, fuzz, flat, resolution)
INPUT_ROOT = Path("/sys/class/input")


def _bitmask(text: str) -> int:
    word_bits = struct.calcsize("l") * 8
    value = 0
    for word in text.split():
        value = (value << word_bits) | int(word, 16)
    return value


def find_touchscreen(root: Path = INPUT_ROOT) -> Path | None:
    """The Goodix touchscreen's event device, or else the first direct-touch device with multitouch positions."""
    nodes = sorted(root.glob("event*"), key=lambda node: int(re.sub(r"\D", "", node.name) or 0))
    for node in nodes:
        try:
            if "goodix" in (node / "device/name").read_text().lower():
                return Path("/dev/input") / node.name
        except OSError:
            continue
    for node in nodes:
        try:
            props = _bitmask((node / "device/properties").read_text())
            axes = _bitmask((node / "device/capabilities/abs").read_text())
        except (OSError, ValueError):
            continue
        if props & (1 << INPUT_PROP_DIRECT) and axes & (1 << ABS_MT_POSITION_X) and axes & (1 << ABS_MT_POSITION_Y):
            return Path("/dev/input") / node.name
    return None


@dataclass(frozen=True)
class TouchAxes:
    """The touchscreen's raw ranges and the framebuffer they cover. QIDI's panels report touches in the
    framebuffer's own coordinates, so a portrait panel's touches turn with its picture."""

    x_min: int
    x_max: int
    y_min: int
    y_max: int
    framebuffer_width: int
    framebuffer_height: int

    def to_upright(self, raw_x: int, raw_y: int) -> tuple[int, int]:
        fx = (raw_x - self.x_min) * (self.framebuffer_width - 1) / max(1, self.x_max - self.x_min)
        fy = (raw_y - self.y_min) * (self.framebuffer_height - 1) / max(1, self.y_max - self.y_min)
        fx = min(max(round(fx), 0), self.framebuffer_width - 1)
        fy = min(max(round(fy), 0), self.framebuffer_height - 1)
        if self.framebuffer_height > self.framebuffer_width:
            # Upright (x, y) shows at framebuffer (y, height - 1 - x).
            return self.framebuffer_height - 1 - fy, fx
        return fx, fy


class Taps:
    """Turns evdev events into presses and releases on the upright screen."""

    def __init__(self, axes: TouchAxes):
        self.axes = axes
        self.slot = 0
        self.raw = [None, None]
        self.down = False
        self.pending = None  # BTN_TOUCH's new state in this frame

    def feed(self, kind: int, code: int, value: int):
        """("press" or "release", x, y) when a frame ends with the finger put down or lifted; otherwise None."""
        if kind == EV_ABS:
            if code == ABS_MT_SLOT:
                self.slot = value
            elif code in (ABS_X, ABS_MT_POSITION_X) and (code == ABS_X or self.slot == 0):
                self.raw[0] = value
            elif code in (ABS_Y, ABS_MT_POSITION_Y) and (code == ABS_Y or self.slot == 0):
                self.raw[1] = value
            elif code == ABS_MT_TRACKING_ID and self.slot == 0 and value == -1:
                self.pending = False
        elif kind == EV_KEY and code == BTN_TOUCH:
            self.pending = bool(value)
        elif kind == EV_SYN and code == SYN_REPORT:
            change, self.pending = self.pending, None
            if change is None or change == self.down or None in self.raw:
                return None
            self.down = change
            return ("press" if change else "release", *self.axes.to_upright(*self.raw))
        return None


# Run as root by `sudo python3 -I -S -c RELAY <device>` when the printer user can't read the touchscreen (QIDI's
# /dev/input/event* are root:input 0660 and qidi isn't in input): it writes the X and Y axis ranges as four native
# ints, then passes the device's events through unchanged until its reader goes away.
RELAY = """import fcntl, os, struct, sys
fd = os.open(sys.argv[1], os.O_RDONLY)
ranges = []
for code in (0x35, 0x36):
    info = struct.unpack("6i", fcntl.ioctl(fd, 0x80184540 + code, bytes(24)))
    ranges += [info[1], info[2]]
out = sys.stdout.buffer
out.write(struct.pack("4i", *ranges)); out.flush()
while True:
    data = os.read(fd, 4096)
    if not data:
        break
    out.write(data); out.flush()
"""


class Touch:
    def __init__(self, path: Path, geometry: update_screen.Geometry, *, root=None):
        """root(command) starts `command` as root and returns its Popen with stdout piped; it is used when the device
        can't be opened as this user."""
        self.process = None
        try:
            self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        except PermissionError:
            if root is None:
                raise
            self._open_relay(path, geometry, root)
            return
        try:
            ranges = []
            for code in (ABS_MT_POSITION_X, ABS_MT_POSITION_Y):
                info = struct.unpack("6i", fcntl.ioctl(self.fd, EVIOCGABS + code, bytes(24)))
                ranges += [info[1], info[2]]
        except BaseException:
            os.close(self.fd)
            raise
        self.taps = Taps(TouchAxes(*ranges, geometry.width, geometry.height))

    def _open_relay(self, path, geometry, root) -> None:
        self.process = root([sys.executable, "-I", "-S", "-c", RELAY, str(path)])
        self.fd = self.process.stdout.fileno()
        header = b""
        deadline = time.monotonic() + 10
        while len(header) < 16:
            ready, _, _ = select.select([self.fd], [], [], max(0.0, deadline - time.monotonic()))
            chunk = os.read(self.fd, 16 - len(header)) if ready else b""
            if not chunk:
                self.close()
                raise OSError("the touchscreen relay didn't start")
            header += chunk
        os.set_blocking(self.fd, False)
        self.taps = Taps(TouchAxes(*struct.unpack("4i", header), geometry.width, geometry.height))

    def events(self) -> list[tuple]:
        """The presses and releases queued now."""
        out = []
        while True:
            try:
                data = os.read(self.fd, INPUT_EVENT.size * 64)
            except BlockingIOError:
                return out
            except OSError as exc:
                if exc.errno == errno.EAGAIN:
                    return out
                raise
            if not data:
                return out
            for _, _, kind, code, value in INPUT_EVENT.iter_unpack(data[:len(data) - len(data) % INPUT_EVENT.size]):
                tap = self.taps.feed(kind, code, value)
                if tap is not None:
                    out.append(tap)

    def close(self) -> None:
        if self.process is None:
            os.close(self.fd)
            return
        self.process.kill()
        try:
            self.process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        self.process.stdout.close()


def hit(buttons: dict, x: int, y: int) -> str | None:
    for button_id, rectangles in buttons.items():
        for left, top, width, height in rectangles:
            if left <= x < left + width and top <= y < top + height:
                return button_id
    return None


# ---- The front end ---------------------------------------------------------------------------------------------------

class TouchUi:
    def __init__(self, panel: Panel, touch: Touch | None, *, reporter=None, device: str = update_screen.FRAMEBUFFER,
                 clock=time.monotonic):
        self.panel, self.touch, self.reporter, self.device, self.clock = panel, touch, reporter, device, clock
        self.current: Composition | None = None
        self.fields: dict = {}
        self.screen: str | None = None
        self.buttons: dict = {}
        self.pressed: str | None = None
        self.lock = threading.RLock()  # a clock may tick the screen from another thread

    @property
    def touch_available(self) -> bool:
        return self.touch is not None

    def _report(self, message: str) -> None:
        if self.reporter is not None:
            self.reporter.line(f"  ({message})")

    def show(self, screen_id: str, **fields) -> tuple[str, ...]:
        """Draw the screen; its button ids, or none when it was drawn as text."""
        with self.lock:
            return self._show(screen_id, fields)

    def _show(self, screen_id: str, fields: dict) -> tuple[str, ...]:
        check_fields(screen_id, fields)
        try:
            composed = compose(screen_id, **fields)
        except Exception as exc:  # noqa: BLE001 - the run goes on with the text screen
            self._report(f"The touchscreen screen {screen_id} could not be drawn, so its text is: "
                         f"{type(exc).__name__}: {exc}")
            update_screen.draw(fallback_lines(screen_id, fields), device=self.device, reporter=self.reporter)
            self.current, self.screen, self.fields = None, screen_id, dict(fields)
            self._reset_touch()
            return ()
        if not self._present(composed):
            self.screen, self.fields = screen_id, dict(fields)
            return ()
        if self.screen != screen_id or composed.buttons != self.buttons:
            self._reset_touch()
        self.screen, self.fields, self.buttons, self.current = screen_id, dict(fields), composed.buttons, composed
        return tuple(composed.buttons)

    def update(self, **fields) -> tuple[str, ...]:
        """Show the current screen again with `fields` changed; only what changed is written."""
        with self.lock:
            if self.screen is None:
                raise RuntimeError("no screen is shown")
            merged = {**self.fields, **fields}
            if self.current is not None and set(fields) <= {"header_step", "where"}:
                check_fields(self.screen, merged)
                composed = compose_header(self.current, self.screen, **merged)
                if not self._present(composed):
                    return ()
                self.current, self.fields = composed, merged
                return tuple(composed.buttons)
            return self._show(self.screen, merged)

    def _present(self, composed: Composition) -> bool:
        """Write what differs from the screen shown; False, after logging why, when it can't be written."""
        try:
            if self.current is None:
                self.panel.write(composed.canvas, 0, 0, WIDTH, HEIGHT)
            else:
                for rectangle in changed_rectangles(self.current.canvas.pixels, composed.canvas.pixels):
                    self.panel.write(composed.canvas, *rectangle)
            return True
        except (OSError, ValueError) as exc:
            self._report(f"The touchscreen could not be drawn: {type(exc).__name__}: {exc}")
            self.current, self.buttons = None, {}
            return False

    def _reset_touch(self) -> None:
        """Forget touches that started before this screen, so a finger still down doesn't tap it."""
        self.pressed = None
        if self.touch is not None:
            try:
                self.touch.events()
            except OSError:
                pass
            self.pressed = "-" if self.touch.taps.down else None

    def wait(self, buttons=None, timeout: float | None = None) -> str | None:
        """The id of the button tapped, among `buttons` (default every button on screen): pressed and released inside
        it. None when `timeout` seconds pass first."""
        allowed = set(buttons) if buttons is not None else set(self.buttons)
        if self.touch is None:
            if timeout is None:
                raise TouchUnavailable("the touchscreen can't be read")
            time.sleep(timeout)
            return None
        deadline = None if timeout is None else self.clock() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - self.clock())
            ready, _, _ = select.select([self.touch.fd], [], [], remaining)
            for action, x, y in self.touch.events() if ready else ():
                target = hit(self.current.buttons, x, y) if self.current is not None else None
                if action == "press":
                    self.pressed = target
                elif self.pressed is not None and target == self.pressed and target in allowed:
                    self.pressed = None
                    return target
                else:
                    self.pressed = None
            if deadline is not None and self.clock() >= deadline:
                return None

    def close(self) -> None:
        for part in (self.touch, self.panel):
            if part is not None:
                try:
                    part.close()
                except OSError:
                    pass
        self.touch = self.panel = None


def open(reporter=None, *, device: str = update_screen.FRAMEBUFFER, sudo=None,  # noqa: A001
         root=None, touch: bool = True) -> TouchUi | None:
    """The front end on the printer's screen; None, after logging why, when the framebuffer can't be opened. `sudo`,
    which runs a command as root, turns on a backlight QIDI's app left off. `root(command)`, which starts a command as
    root with its output piped, reads a touchscreen this user can't (Touch). Without a readable touchscreen, screens
    are still drawn and wait() only waits out its timeout; touch False doesn't try, for a screen nobody taps."""
    try:
        panel = Panel.open(device)
    except Exception as exc:  # noqa: BLE001
        if reporter is not None:
            reporter.line(f"  (The touchscreen can't be drawn on: {type(exc).__name__}: {exc})")
        return None
    if sudo is not None:
        update_screen.light(sudo, reporter=reporter)
    if not touch:
        return TouchUi(panel, None, reporter=reporter, device=device)
    touch = None
    path = find_touchscreen()
    try:
        if path is None:
            raise OSError("no touchscreen input device found")
        touch = Touch(path, panel.geometry, root=root)
    except Exception as exc:  # noqa: BLE001
        if reporter is not None:
            reporter.line(f"  (Touches can't be read: {type(exc).__name__}: {exc})")
    return TouchUi(panel, touch, reporter=reporter, device=device)


class FakeUi:
    """A TouchUi without a screen, for testing a controller: it records what is shown and answers wait() from
    `taps`, a list of button ids, with None for a timeout."""

    def __init__(self, taps=()):
        self.taps = list(taps)
        self.shown: list[tuple[str, dict]] = []
        self.updates: list[dict] = []
        self.screen: str | None = None
        self.fields: dict = {}
        self.closed = False
        self.touch_available = True

    def show(self, screen_id: str, **fields) -> tuple[str, ...]:
        buttons = buttons_for(screen_id, fields)
        self.shown.append((screen_id, dict(fields)))
        self.screen, self.fields = screen_id, dict(fields)
        return buttons

    def update(self, **fields) -> tuple[str, ...]:
        if self.screen is None:
            raise RuntimeError("no screen is shown")
        merged = {**self.fields, **fields}
        buttons = buttons_for(self.screen, merged)
        self.updates.append(dict(fields))
        self.fields = merged
        return buttons

    def wait(self, buttons=None, timeout: float | None = None) -> str | None:
        allowed = tuple(buttons) if buttons is not None else buttons_for(self.screen, self.fields)
        if not self.taps:
            if timeout is not None:
                return None
            raise AssertionError(f"no tap scripted for screen {self.screen!r}, which waits for {allowed}")
        tap = self.taps.pop(0)
        if tap is not None and tap not in allowed:
            raise AssertionError(f"the scripted tap {tap!r} isn't one of {allowed} on screen {self.screen!r}")
        return tap

    def close(self) -> None:
        self.closed = True


# ---- Examples and previews -------------------------------------------------------------------------------------------

_VERSION = "26.11.12.1"
_FOOTER = f"Valve Oil {_VERSION} · USB installer · QIDI firmware 01.01.07.01"
_STEPS = ["Check the printer", "Back up your configuration", "Macros and Klipper fixes", "System changes",
          "Fluidd and Valve Oil UI", "Toolhead and mainboard firmware", "QIDI Box 1 firmware", "QIDI Box 2 firmware",
          "Restart Klipper and verify", "Finish"]
_UPDATE_STEPS = ["Check the printer", "Back up your configuration", "Macros and Klipper fixes", "System changes",
                 "Fluidd and Valve Oil UI", "Board firmware", "Restart Klipper and verify", "Finish"]
_HEADER = {"where": "QIDI Max 4", "footer": _FOOTER}
_REPORT_URL = (REPOSITORY_URL + "/issues/new?template=printer-support.yml&title=%5BSupport%5D%3A+QIDI+Max+4+THR+"
               "bootloader&printer=QIDI+Max+4&firmware=01.01.07.01&board_report=%7B%22board%22%3A%22THR%22%2C%22"
               "bootloader_sha256%22%3A%229f2c4e1b7a3d5f60e8b1c2d3a4f5e6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3%22%2C%22"
               "supported_bootloader%22%3Afalse%7D")

# Each screen of screens.html, by its id there: (screen id, fields).
EXAMPLES = {
    "01-checking": ("checking", dict(_HEADER)),
    "02-welcome": ("found", {**_HEADER, "title": f"Install Valve Oil {_VERSION}", "rows": [
        ("Printer", "QIDI Max 4"), ("QIDI firmware", "01.01.07.01", "supported"), ("QIDI Box", "2 connected"),
        ("Valve Oil", "Not installed")],
        "text": "A few questions first, then the install runs by itself: about 6 minutes, plus 4 minutes for each "
                "QIDI Box."}),
    "02b-firmware-first": ("found", {**_HEADER, "firmware_first": True, "title": f"Install Valve Oil {_VERSION}",
                                     "rows": [("Printer", "QIDI Max 4"),
                                              ("QIDI firmware", "01.01.06.05", "updated to 01.01.07.01 first"),
                                              ("QIDI Box", "2 connected"), ("Valve Oil", "Not installed")],
                                     "text": "This Valve Oil runs on QIDI firmware 01.01.07.01, so the installer "
                                             "updates the printer to it first, from this drive. That adds about 15 "
                                             "minutes.",
                                     "footer": f"Valve Oil {_VERSION} · USB installer · QIDI firmware 01.01.06.05"}),
    "03-auto-updates": ("question", {**_HEADER, "header_step": "Question 1 of 2", "title": "Keep Valve Oil updated?",
                                     "text": ["The printer checks for a new release every hour and installs it by "
                                              "itself.",
                                              "It never updates during a print, while a print is paused, or while a "
                                              "heater is on. You can turn this off later."],
                                     "no_label": "No, I'll update by hand", "yes_label": "Yes, update automatically"}),
    "04-ai-detection": ("question", {**_HEADER, "header_step": "Question 2 of 2", "title": "Turn off QIDI AI detection?",
                                     "text": ["QIDI's spaghetti and foreign object detection can use about 190 MB of "
                                              "the printer's 486 MB of memory during a print.",
                                              "If you turn it off, also switch off Spaghetti Detection and Foreign "
                                              "Object Detection in the touchscreen's Settings → Printing Options "
                                              "afterwards, so the screen matches."],
                                     "no_label": "Keep it on", "yes_label": "Turn it off"}),
    "07-ready": ("ready", {**_HEADER, "rows": [
        ("Valve Oil", _VERSION, "on QIDI Max 4"), ("Automatic updates", "On"), ("QIDI AI detection", "Turned off"),
        ("QIDI Box", "2 found", "Box support turned on"), ("Board firmware", "Toolhead, mainboard, Box 1, Box 2")],
        "text": "Backed up first. About 14 minutes. Keep the printer and Boxes on."}),
    "08-installing": ("installing", {**_HEADER, "header_step": "Step 4 of 10 · 1:52", "title": "Installing",
                                     "step": "System changes", "detail": "restarting Moonraker…", "steps": _STEPS,
                                     "index": 4, "progress": 0.38,
                                     "warning": "Don't turn the printer off. It restarts by itself at the end."}),
    "09-box-firmware": ("installing", {**_HEADER, "header_step": "Step 8 of 10 · 9:31", "title": "Installing",
                                       "step": "QIDI Box 2 firmware", "detail": "flashing, about 4 minutes",
                                       "steps": _STEPS, "index": 8, "progress": 0.74,
                                       "warning": "Keep the printer and Box 2 powered on."}),
    "10-done": ("done", {**_HEADER, "header_step": "Finished in 13:48", "title": "Valve Oil is installed",
                         "fluidd_url": "http://192.168.1.50", "qr_url": REPOSITORY_URL,
                         "waiting_text": "Waiting for the USB drive to be taken out…"}),
    "11-update": ("update", {**_HEADER, "rows": [
        ("Installed", "26.11.11.3"), ("On this drive", _VERSION, "newer"),
        ("Settings kept", "Automatic updates on · AI detection off · Box support on")],
        "text": "Your settings and configuration are kept. Your configuration is backed up first."}),
    "12-up-to-date": ("up_to_date", {**_HEADER, "rows": [
        ("Installed", _VERSION, "3 days ago"), ("Settings", "Automatic updates on · AI detection off · Box support on"),
        ("Last backup", "12 Nov 2026, 09:14")]}),
    "18-ssh-install": ("installing", {**_HEADER, "header_step": "Step 4 of 10 · 1:52", "title": "Installing",
                                      "step": "System changes", "detail": "restarting Moonraker…", "steps": _STEPS,
                                      "index": 4, "progress": 0.38,
                                      "warning": "Don't turn the printer off. It restarts by itself at the end.",
                                      "footer": f"Valve Oil {_VERSION} · installing over SSH · QIDI firmware "
                                                "01.01.07.01"}),
    "19-auto-update": ("installing", {**_HEADER, "header_step": "Step 3 of 8 · 0:48", "title": "Updating Valve Oil",
                                      "step": "Macros and Klipper fixes", "detail": f"26.11.11.3 to {_VERSION}",
                                      "steps": _UPDATE_STEPS, "index": 3, "progress": 0.30,
                                      "warning": "Automatic updates are on. Don't turn the printer off.",
                                      "footer": f"Valve Oil 26.11.11.3 → {_VERSION} · automatic update"}),
    "14-busy": ("busy", {**_HEADER, "title": "Can't install right now",
                         "text": "A heater is on. Installing restarts Klipper, and that isn't safe while something is "
                                 "heating.",
                         "rows": [("Bed", "60 °C target", "turn off"), ("Nozzle", "Off"), ("Chamber", "Off"),
                                  ("Box dryers", "Off")],
                         "note": "Nothing has been changed."}),
    "15-unsupported": ("unsupported", {**_HEADER, "title": "This QIDI firmware isn't supported yet", "rows": [
        ("This printer", "QIDI firmware 01.01.08.00"), (f"Valve Oil {_VERSION}", "Supports 01.01.06.03 to 01.01.07.01")],
        "text": "Nothing has been changed. A newer Valve Oil may support it: download the latest USB installer from "
                "**github.com/thelegendtubaguy/valve-oil** and try again.",
        "footer": f"Valve Oil {_VERSION} · USB installer · QIDI firmware 01.01.08.00"}),
    "15b-board-not-supported": ("board_not_supported", {
        **_HEADER, "title": "This toolhead board isn't supported yet",
        "rows": [("Board", "Toolhead (THR)"), ("Found", "A bootloader this release doesn't know")],
        "text": "Nothing was changed. Scan the code to report it. The report is filled in with this printer's "
                "details, so a release can add support for it.", "qr_url": _REPORT_URL}),
    "16-failed": ("failed", {**_HEADER, "header_step": "Stopped after 2:07", "title": "Install stopped", "text": [
        "Step 4, System changes, did not finish:", ("bad", "Moonraker did not come back after it was restarted."),
        [("good", "Everything was put back the way it was."), ("plain", " Your configuration backup is kept.")]],
        "log_name": "valve-oil-install.log"}),
    "16b-not-restarted": ("failed", {**_HEADER, "title": "The printer wasn't restarted", "text": [
        "Valve Oil is installed, but the printer would stop at its boot logo if it restarted now:",
        ("bad", "/sbin is a directory, not a link to usr/sbin."),
        "Don't turn the printer off. Run **~/valve-oil/install.sh** over SSH, which repairs this, then tap Try "
        "again. Or follow **github.com/thelegendtubaguy/valve-oil/blob/main/docs/recovering-an-unbootable-printer.md**."]}),
    "17-log": ("log", {**_HEADER, "header_step": "Step 4 of 10 · 1:52", "lines": [
        "09:14:02  Printer: QIDI Max 4, QIDI firmware 01.01.07.01",
        f"09:14:02  Valve Oil: not installed. Installing {_VERSION}",
        "09:14:03  Printer is idle. Heaters off. 1.9 GB free",
        f"09:14:11  Backup: backup-vo-01.01.07.01-{_VERSION}.zip",
        "09:14:19  Macros installed: 41 files, 17 guarded patches",
        "09:14:20  Applying system changes",
        "09:14:26    zram swap: on",
        "09:14:31    ADB: off",
        "09:14:38    Fluidd build: installed",
        "09:14:52    Restarting Moonraker..."]}),
    "x-backups": ("backups", {**_HEADER, "rows": [
        ("12 Nov 2026, 09:14", f"Valve Oil {_VERSION}"), ("9 Nov 2026, 18:40", "Valve Oil 26.11.11.3"),
        ("2 Nov 2026, 10:05", "Before Valve Oil")],
        "text": "Your configuration as it is now is backed up first."}),
    "x-confirm": ("confirm", {**_HEADER, "title": "Restore this backup?",
                              "text": ["Your configuration goes back to 12 Nov 2026, 09:14.",
                                       "Your configuration as it is now is backed up first."],
                              "ok_label": "Restore"}),
}

PORTRAIT = update_screen.Geometry(480, 800, 4, ((16, 8), (8, 8), (0, 8)), 480 * 4, 0)


def portrait_rgb(canvas: Canvas) -> bytes:
    """What a Plus 5's or Smart 5's 480x800 framebuffer holds once the screen is written to it, as RGB."""
    import tempfile

    with tempfile.TemporaryFile() as handle:
        handle.truncate(PORTRAIT.stride * PORTRAIT.height)
        panel = Panel(handle.fileno(), PORTRAIT)
        panel.write(canvas, 0, 0, WIDTH, HEIGHT)
        handle.seek(0)
        data = handle.read()
    rgb = bytearray(PORTRAIT.width * PORTRAIT.height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = data[2::4], data[1::4], data[0::4]
    return bytes(rgb)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m installer.runtime.touch_ui",
                                     description="Write PNG previews of the touchscreen screens.")
    parser.add_argument("--preview", metavar="EXAMPLES", required=True,
                        help=f"'all', or a comma-separated list of: {', '.join(EXAMPLES)}")
    parser.add_argument("output", type=Path, help="the folder to write <example>.png and <example>-portrait.png to")
    args = parser.parse_args(argv)
    names = list(EXAMPLES) if args.preview == "all" else args.preview.split(",")
    args.output.mkdir(parents=True, exist_ok=True)
    for name in names:
        screen_id, fields = EXAMPLES[name]
        canvas = compose(screen_id, **fields).canvas
        (args.output / f"{name}.png").write_bytes(encode_png(WIDTH, HEIGHT, bytes(canvas.pixels)))
        (args.output / f"{name}-portrait.png").write_bytes(encode_png(480, 800, portrait_rgb(canvas)))
        print(args.output / f"{name}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
