"""Printer diagnostics: the catalog of known fault signatures and the log checker that finds them.

Each signature is a fault QIDI's code causes and Valve Oil can recognize in `klippy.log`: first by the `vo_` line the
Valve Oil Klipper module that watches for it writes when it happens, and, for logs from before that watcher shipped,
by QIDI's own lines. The vo-status service runs this file on the printer's logs for Valve Oil UI's Diagnostics
check, at low priority, and `scripts/check_klippy_log.py` runs it on a log someone sends in.

The checker streams the newest part of each log within a byte budget, never holding a file in memory, and stops at a
time budget; the result says how far back it reached. Standard library only, and no imports from this package, so
`python3 -I diagnostics.py` runs it from the installer bundle.

Result (schema 1): {schema, checked: {since, files: [{name, size, read_from, bytes_read}], from, to, complete,
stopped, elapsed}, installed, findings: [{id, title, severity, meaning, action, fixed_in, docs, count, repaired, first,
last, details, evidence}]}. repaired counts the times the Valve Oil release running then repaired it. severity is
"fault" (it happened unrepaired, and the installed release doesn't fix it), "fixed" (it happened on an earlier release,
and the installed one fixes it), "repaired" (every time it happened, the release running then repaired it before it
failed anything), or "info". Which release ran comes from the `variable_package_version` line of the config Klipper
logs at each start.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

SCHEMA = 1
DOCS_URL = "https://github.com/thelegendtubaguy/valve-oil/blob/main/docs/troubleshooting.md"
# The newest part of the logs read at most, and how long a check may run, by default.
BYTE_BUDGET = 48 * 1024 * 1024
TIME_BUDGET = 90.0
SINCE = ("all", "day", "start")
MAX_EVIDENCE = 12
MAX_DETAILS = 20
# The lines kept before a QIDI error code, for what led up to it.
CONTEXT_LINES = 8
# The USB Box MCU's clock when klippy.log doesn't say.
BOX_CLOCK_FREQ = 84_000_000
# A Box slot step clock this far off Klipper's made unloads fail in operator logs; less than this unloaded normally.
CLOCK_OFFSET_FAULT_S = 2.0
# Below this share of the pulls asked for, an unload's extruder counts as not moved (box_flow_recovery MOVED_SHARE).
MOVED_SHARE = 0.5
# Valve Oil Klipper module log prefixes, before and after the rename of tltg_ modules to vo_.
FLOW_RECOVERY = r"(?:tltg|vo)_box_flow_recovery: "
WATCH = "vo_box_watch: "


@dataclass(frozen=True)
class Signature:
    id: str
    title: str
    meaning: str
    action: str
    docs: str
    fixed_in: str | None = None


SIGNATURES = {
    s.id: s
    for s in (
        Signature(
            id="cancelled_part_dropped_moves",
            title="Filament change inside a cancelled part",
            meaning=(
                "A filament change ran while the print was inside a part that had been cancelled. Klipper drops every "
                "move inside a cancelled part, extruder pulls included, so the filament wasn't cut or pulled back and "
                "the Box unload stopped."
            ),
            action=(
                "Update Valve Oil to 26.11.22.2 or later, which lets a filament change's moves through a cancelled "
                "part. Until then, don't cancel parts in prints with filament changes."
            ),
            docs="box-unload-stopped-inside-a-cancelled-part",
            fixed_in="26.11.22.2",
        ),
        Signature(
            id="autofeed_rearmed_during_feed",
            title="Box auto-feed re-armed while it was feeding",
            meaning=(
                "QIDI's code set up the Box's auto-feed again while the Box was feeding a slot, as print start does when "
                "the purge has just tripped the buffer. The Box drops that feed without putting the slot motor's step "
                "clock back, so the clock can stay off Klipper's for the rest of the print."
            ),
            action=(
                "Nothing, on Valve Oil 26.11.22.2 or later, which resets the slot's step clock before each unload. On an "
                "earlier release, update Valve Oil."
            ),
            docs="box-slot-step-clock",
            fixed_in="26.11.22.2",
        ),
        Signature(
            id="box_step_clock_offset",
            title="A Box slot motor's step clock is off Klipper's",
            meaning=(
                "The Box kept a slot motor's step clock seconds away from the one Klipper uses, after its auto-feed was "
                "re-armed during a feed. An unload then runs that slot's motor late, or in bursts, and the toolhead "
                "sensor never clears."
            ),
            action=(
                "Nothing, on Valve Oil 26.11.22.2 or later, which resets the slot's step clock before each unload. On an "
                "earlier release, update Valve Oil; a Klipper restart also clears it until the next print start."
            ),
            docs="box-slot-step-clock",
            fixed_in="26.11.22.2",
        ),
        Signature(
            id="box_feed_during_valve_oil_move",
            title="The Box fed a slot while Valve Oil was moving it",
            meaning=(
                "The Box's auto-feed stepped a slot, or switched its motor, while Valve Oil was moving that slot's "
                "filament itself, such as a jog, a spool roll, or an RFID read. The slot's filament may not be where "
                "either expected."
            ),
            action="Check the slot's filament before the next load. Report it with the support bundle so it can be fixed.",
            docs="the-box-fed-a-slot-during-a-valve-oil-move",
        ),
        Signature(
            id="unload_toolhead_sensor_stuck",
            title="Box unload stopped with filament at the toolhead",
            meaning=(
                "A Box unload pulled the filament back and the toolhead filament sensor still detected it. Something "
                "held the filament: at the toolhead, such as a cutter blade or a swollen cut end, in the Box, or a slot "
                "motor running late, as an off step clock makes it."
            ),
            action=(
                "Free the filament at the toolhead, then retry or unload again. If other findings here name a step "
                "clock or a cancelled part, they are the cause."
            ),
            docs="box-unload-stopped-with-filament-at-the-toolhead",
        ),
        Signature(
            id="qidi_error",
            title="QIDI error codes",
            meaning="QIDI's code reported these errors. The lines before each show what led up to it.",
            action="Look up the code in the table of QIDI's error codes; report it with the support bundle if it recurs.",
            docs="qidi-error-codes",
        ),
    )
}

# --------------------------------------------------------------------------------------------------------------------
# Log lines

TIMESTAMP = re.compile(rb"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) - ")
# Every line a signature reads contains one of these; everything else is skipped without decoding.
MARKERS = re.compile(
    rb"Start printer at|MCU '[\w ]+' config:|got \{|\[MCB\] STATE|Code:QDE_|vo_box_watch: |box_flow_recovery: "
    rb"|ENABLE delay: stepper=slot|Status changed: |^variable_package_version = "
)
VERSION = re.compile(r'^variable_package_version = "([\d.]+)"')
START = "Start printer at"
MCU_CONFIG = re.compile(r"MCU '(?:mcu )?([\w]+)' config: .*\bCLOCK_FREQ=(\d+)")
BOX_REPLY = re.compile(r"mcu '(mcu_box\d+)': got \{(.*)\}")
REPLY_NAME = re.compile(r"'#name': '([^']+)'")
REPLY_FIELD = re.compile(r"'(oid|clk|next_s|now|stepper_oid|state)': (-?\d+)")
MCB_STATE = re.compile(r"\[MCB\] STATE \{.*'state': (\d+)")
QIDI_ERROR = re.compile(r"Code:(QDE_\d+_\d+); Message:(.*)")
UNLOAD = re.compile(
    FLOW_RECOVERY + r"(\S+) unload from the toolhead: .*extruder pulled back ([\d.]+) mm, [\d.]+ of them after its "
    r"pull-back(?:, and moved (unknown|[\d.]+ mm))?"
)
DROPPED = re.compile(FLOW_RECOVERY + r"Box unload of (\S+) stopped: Klipper didn't move the extruder")
STUCK = re.compile(FLOW_RECOVERY + r"Box unload of (\S+) stopped: the toolhead filament sensor still detects")
CANCELLED_CHANGE = re.compile(FLOW_RECOVERY + r"filament change inside cancelled part (.+?);")
CLOCK_RESET = re.compile(FLOW_RECOVERY + r"(\S+) step clock reset before the unload")
UNLOAD_FAILURE = "QDE_004_008"
# Valve Oil's Box watcher (installer/klipper/extras/box_watch.py) writes these.
WATCHING = re.compile(WATCH + r"watching ")
WATCH_CLOCK = re.compile(
    WATCH + r"step clock: slot (\S+) \(Box (\d+)\) is ([\d.]+) s (behind|ahead of) Klipper's"
)
WATCH_REARM = re.compile(WATCH + r"auto-feed re-armed during a feed: slot (\S+) \(Box (\d+)\)")
WATCH_MOVE = re.compile(WATCH + r"Box feed during Valve Oil's (.+?): slot (\S+) \(Box (\d+)\)")
# Lines that put the events before a QIDI error code in context.
CONTEXT = re.compile(r"Status changed: |vo_box_watch: |box_flow_recovery: |Start printer at")


def signed32(value: int) -> int:
    return ((value + 2**31) % 2**32) - 2**31


@dataclass
class _Finding:
    id: str
    count: int = 0
    first: str | None = None
    last: str | None = None
    worst: float = 0.0  # the largest step clock offset seen, in seconds, for box_step_clock_offset
    unrepaired: int = 0  # occurrences the Valve Oil release running then didn't repair

    def __post_init__(self) -> None:
        self.details: list[str] = []
        self.evidence: list[str] = []


class Checker:
    """Reads log lines in order, oldest first, and collects findings. `feed` takes one raw line; `result` reports."""

    def __init__(self, *, since: str = "all", newest: str | None = None, installed: str | None = None):
        if since not in SINCE:
            raise ValueError(f"since must be one of {', '.join(SINCE)}")
        self.since = since
        self.installed = installed
        self.cutoff = _day_before(newest) if since == "day" and newest else None
        self.findings: dict[str, _Finding] = {}
        self.first: str | None = None
        self.last: str | None = None
        self.reached_cutoff = False
        self.started = False  # a Klipper start was read, so a check since the last start read all of it  # a line older than a day's check was seen, so the whole day was read
        self.clock_freq: dict[str, int] = {}
        self._context: list[str] = []
        self._session_reset()

    # ------------------------------------------------------------------------------------------------ the session
    def _session_reset(self) -> None:
        # Raw QIDI lines are read only in a Klipper session without Valve Oil's watcher; their findings wait here until
        # the session is known to have none.
        self._watched = False
        self._version: str | None = None  # the Valve Oil release this session runs, once its config is logged
        self._raw: dict[str, _Finding] = {}
        self._feeding: dict[str, bool] = {}
        self._active: dict[str, int | None] = {}  # the slot stepper oid the Box last saved a clock for
        self._clock: dict[tuple[str, int], int] = {}  # the last clock the Box reported for each slot stepper
        self._rearmed: dict[tuple[str, int], tuple[int, str]] = {}  # the clock before a re-arm, and its time
        self._autofeed_mcu: str | None = None
        # Valve Oil reset a slot's step clock before an unload in this session, so the session's offsets were repaired
        # whatever release its config named (a sideloaded build keeps the version it replaced).
        self._clock_resets = False

    def _session_end(self) -> None:
        if self._clock_resets:
            for sig in ("box_step_clock_offset", "autofeed_rearmed_during_feed"):
                if sig in self._raw:
                    self._raw[sig].unrepaired = 0
        if not self._watched:
            for finding in self._raw.values():
                self._merge(finding)
        self._raw = {}

    # ------------------------------------------------------------------------------------------------ findings
    def _finding(self, sig: str, *, raw: bool = False) -> _Finding:
        table = self._raw if raw else self.findings
        if sig not in table:
            table[sig] = _Finding(sig)
        return table[sig]

    def _note(self, sig: str, when: str | None, *, lines: Iterable[str] = (), detail: str | None = None,
              raw: bool = False, repaired: bool | None = None) -> _Finding:
        finding = self._finding(sig, raw=raw)
        finding.count += 1
        if repaired is None:
            fixed_in = SIGNATURES[sig].fixed_in
            repaired = fixed_in is not None and _at_least(self._version, fixed_in)
        if not repaired:
            finding.unrepaired += 1
        finding.first = finding.first or when
        finding.last = when or finding.last
        if detail and detail not in finding.details and len(finding.details) < MAX_DETAILS:
            finding.details.append(detail)
        for line in lines:
            if len(finding.evidence) < MAX_EVIDENCE:
                finding.evidence.append(line[:600])
        return finding

    def _merge(self, raw: _Finding) -> None:
        finding = self._finding(raw.id)
        finding.count += raw.count
        finding.first = finding.first or raw.first
        finding.last = raw.last or finding.last
        finding.worst = max(finding.worst, raw.worst)
        finding.unrepaired += raw.unrepaired
        for detail in raw.details:
            if detail not in finding.details and len(finding.details) < MAX_DETAILS:
                finding.details.append(detail)
        for line in raw.evidence:
            if len(finding.evidence) < MAX_EVIDENCE:
                finding.evidence.append(line)

    # ------------------------------------------------------------------------------------------------ lines
    def feed(self, raw: bytes) -> None:
        if self.first is None:
            # How far back the check reaches: the first line in its period, whatever it says.
            stamp = TIMESTAMP.match(raw)
            if stamp:
                when = stamp.group(1).decode()
                if self.cutoff is not None and when < self.cutoff:
                    self.reached_cutoff = True
                    return
                self.first = when
        if not MARKERS.search(raw):
            return
        stamp = TIMESTAMP.match(raw)
        when = stamp.group(1).decode() if stamp else None
        if when is not None:
            if self.cutoff is not None and when < self.cutoff:
                self.reached_cutoff = True
                return
            self.last = when
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        self._line(line, when)

    def _line(self, line: str, when: str | None) -> None:
        if START in line:
            self.started = True
            self._session_end()
            if self.since == "start":
                self.findings = {}
                self.first = when
            self._session_reset()
            self._context = []
        if CONTEXT.search(line):
            self._context = (self._context + [line])[-CONTEXT_LINES:]

        version = VERSION.match(line)
        if version:
            self._version = version.group(1)
            return
        config = MCU_CONFIG.search(line)
        if config:
            self.clock_freq[config.group(1)] = int(config.group(2))
            return
        if WATCHING.search(line):
            self._watched = True
            return
        if "got {" in line:
            self._box_reply(line, when)
            return
        state = MCB_STATE.search(line)
        if state:
            if self._autofeed_mcu is not None and state.group(1) == "2":
                self._feeding[self._autofeed_mcu] = True
            return
        if "ENABLE delay: stepper=slot" in line:
            # Klipper is moving a slot itself (a load or unload), which sets its clock legitimately.
            self._rearmed.clear()
            return
        self._valve_oil(line, when)
        error = QIDI_ERROR.search(line)
        if error:
            code, message = error.group(1), error.group(2).strip()
            context = [entry for entry in self._context if entry != line]
            self._note("qidi_error", when, lines=[*context, line], detail=f"{code}: {message}")
            if code == UNLOAD_FAILURE:
                self._note("unload_toolhead_sensor_stuck", when, lines=[line], detail=f"QIDI {code}: {message}")

    def _valve_oil(self, line: str, when: str | None) -> None:
        match = WATCH_CLOCK.search(line)
        if match:
            slot, box, seconds, direction = match.groups()
            finding = self._note("box_step_clock_offset", when, lines=[line],
                                 detail=f"Slot {slot}: {seconds} s {direction} Klipper's",
                                 repaired=True if "resets it" in line else None)
            finding.worst = max(finding.worst, float(seconds))
            return
        match = WATCH_REARM.search(line)
        if match:
            self._note("autofeed_rearmed_during_feed", when, lines=[line], detail=f"Slot {match.group(1)}")
            return
        match = WATCH_MOVE.search(line)
        if match:
            move, slot, _box = match.groups()
            self._note("box_feed_during_valve_oil_move", when, lines=[line], detail=f"Slot {slot} during {move}")
            return
        match = UNLOAD.search(line)
        if match:
            slot, pulled, moved = match.group(1), float(match.group(2)), match.group(3)
            if moved and moved != "unknown" and pulled > 0 and float(moved.split()[0]) < pulled * MOVED_SHARE:
                self._note("cancelled_part_dropped_moves", when, lines=[*self._context[-2:], line],
                           detail=f"Slot {slot}: pulled back {match.group(2)} mm, moved {moved}")
            return
        match = DROPPED.search(line)
        if match:
            self._note("cancelled_part_dropped_moves", when, lines=[line], detail=f"Slot {match.group(1)}")
            return
        match = STUCK.search(line)
        if match:
            self._note("unload_toolhead_sensor_stuck", when, lines=[*self._context[-3:], line],
                       detail=f"Slot {match.group(1)}")
            return
        match = CANCELLED_CHANGE.search(line)
        if match:
            self._note("cancelled_part_dropped_moves", when, lines=[line], repaired=True,
                       detail=f"Moves let through cancelled part {match.group(1)}")
            return
        if CLOCK_RESET.search(line):
            self._clock_resets = True

    # ------------------------------------------------------------------------------------------------ QIDI's Box replies
    def _box_reply(self, line: str, when: str | None) -> None:
        match = BOX_REPLY.search(line)
        if not match:
            return
        mcu, body = match.groups()
        name = REPLY_NAME.search(body)
        if not name:
            return
        fields = {key: int(value) for key, value in REPLY_FIELD.findall(body)}
        name = name.group(1)
        if name == "MCB_CONFIG_STEPPER" and "stepper_oid" in fields:
            self._autofeed_mcu = mcu
            oid = fields["stepper_oid"]
            if self._feeding.get(mcu) and not self._watched:
                before = self._clock.get((mcu, oid))
                self._note("autofeed_rearmed_during_feed", when, lines=[*self._context[-2:], line], raw=True,
                           detail=f"Box {_box_number(mcu)} slot stepper oid {oid}")
                if before is not None:
                    self._rearmed[(mcu, oid)] = (before, when or "")
            self._feeding[mcu] = False
        elif name == "last_step_clk" and "oid" in fields and "clk" in fields:
            self._autofeed_mcu = mcu
            self._active[mcu] = fields["oid"]
            self._clock_report(mcu, fields["oid"], fields["clk"], line, when)
        elif name == "STEP-set_last_step" and "next_s" in fields:
            self._feeding[mcu] = False
            oid = self._active.get(mcu)
            if oid is not None:
                self._clock_report(mcu, oid, fields["next_s"], line, when)

    def _clock_report(self, mcu: str, oid: int, clock: int, line: str, when: str | None) -> None:
        self._clock[(mcu, oid)] = clock
        rearmed = self._rearmed.get((mcu, oid))
        if rearmed is None or self._watched:
            return
        before, _at = rearmed
        freq = self.clock_freq.get(mcu, BOX_CLOCK_FREQ)
        offset = signed32(clock - before) / freq
        if abs(offset) < CLOCK_OFFSET_FAULT_S:
            return
        # One finding per re-arm, at the first clock that far off.
        del self._rearmed[(mcu, oid)]
        finding = self._note("box_step_clock_offset", when, lines=[line], raw=True,
                             detail=f"Box {_box_number(mcu)} slot stepper oid {oid}: {abs(offset):.1f} s "
                                    f"{'behind' if offset < 0 else 'ahead of'} Klipper's")
        finding.worst = max(finding.worst, abs(offset))

    # ------------------------------------------------------------------------------------------------ the result
    def result(self) -> list[dict]:
        self._session_end()
        found = []
        for sig in SIGNATURES.values():
            finding = self.findings.get(sig.id)
            if finding is None or not finding.count:
                continue
            if sig.id == "qidi_error":
                severity = "info"
            elif not finding.unrepaired:
                severity = "repaired"
            elif sig.fixed_in is not None and _at_least(self.installed, sig.fixed_in):
                severity = "fixed"
            else:
                severity = "fault"
            found.append({
                "id": sig.id,
                "title": sig.title,
                "severity": severity,
                "meaning": sig.meaning,
                "action": sig.action,
                "fixed_in": sig.fixed_in,
                "docs": f"{DOCS_URL}#{sig.docs}",
                "count": finding.count,
                "repaired": finding.count - finding.unrepaired if sig.id != "qidi_error" else 0,
                "first": finding.first,
                "last": finding.last,
                "details": finding.details,
                "evidence": finding.evidence,
            })
        order = {"fault": 0, "fixed": 1, "repaired": 2, "info": 3}
        return sorted(found, key=lambda item: order[item["severity"]])


def _version_key(version: str | None) -> tuple[int, ...] | None:
    if not version:
        return None
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def _at_least(version: str | None, minimum: str) -> bool:
    key, floor = _version_key(version), _version_key(minimum)
    return key is not None and floor is not None and key >= floor


def _box_number(mcu: str) -> str:
    return mcu.removeprefix("mcu_box") or mcu


def _day_before(when: str) -> str | None:
    try:
        parsed = time.strptime(when, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.mktime(parsed) - 86400))


# --------------------------------------------------------------------------------------------------------------------
# Files


def log_files(logs: Path) -> list[Path]:
    """klippy.log and its rotated copies, newest first."""
    files = [logs / "klippy.log"] if (logs / "klippy.log").is_file() else []
    rotated = [path for path in logs.glob("klippy.log.*") if path.is_file() and not path.name.endswith((".gz", ".zip"))]
    return files + sorted(rotated, key=_mtime, reverse=True)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _last_start(path: Path, size: int, budget: int) -> int | None:
    """Where the line of the last Klipper start in the newest `budget` bytes of `path` begins, found by reading back
    from the end; None when there is none."""
    marker = START.encode()
    floor = max(0, size - budget)
    end = size
    with path.open("rb") as handle:
        while end > floor:
            start = max(floor, end - 1024 * 1024)
            handle.seek(start)
            found = handle.read(end - start + len(marker)).rfind(marker)  # a match across the chunk boundary too
            if found >= 0:
                at = start + found
                handle.seek(max(0, at - 256))
                before = handle.read(at - max(0, at - 256))
                return max(0, at - 256) + before.rfind(b"\n") + 1
            end = start
    return None


def _plan(files: Iterable[Path], budget: int, since: str) -> list[tuple[Path, int, int]]:
    """(file, offset to read from, size) for the newest `budget` bytes across files given newest first, oldest first.
    A check since Klipper last started reads only the newest file, from that start."""
    plan = []
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        offset = max(0, size - budget)
        if since == "start":
            try:
                offset = _last_start(path, size, budget) or offset
            except OSError:
                pass
        plan.append((path, offset, size))
        budget -= size - offset
        if budget <= 0 or since == "start":
            break
    return list(reversed(plan))


def _lines(path: Path, offset: int) -> Iterator[bytes]:
    with path.open("rb") as handle:
        if offset:
            handle.seek(offset - 1)
            if handle.read(1) != b"\n":
                handle.readline()  # the rest of a line cut in two
        yield from handle


def _newest_timestamp(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            handle.seek(max(0, size - 65536))
            tail = handle.read()
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        match = TIMESTAMP.match(line)
        if match:
            return match.group(1).decode()
    return None


def check(files: Iterable[Path], *, since: str = "all", byte_budget: int = BYTE_BUDGET,
          time_budget: float = TIME_BUDGET, installed: str | None = None, clock=time.monotonic) -> dict:
    """Check the newest part of `files` (newest first) for every signature."""
    files = list(files)
    started = clock()
    plan = _plan(files, byte_budget, since)
    newest = _newest_timestamp(plan[-1][0]) if plan else None
    checker = Checker(since=since, newest=newest, installed=installed)
    read = []
    stopped = None
    for path, offset, size in plan:
        count = 0
        for number, line in enumerate(_lines(path, offset)):
            checker.feed(line)
            count += len(line)
            if number % 8192 == 0 and clock() - started > time_budget:
                stopped = "time"
                break
        read.append({"name": path.name, "size": size, "read_from": offset, "bytes_read": count})
        if stopped:
            break
    whole = all(item["read_from"] == 0 for item in read) and len(read) == len(files)
    findings = checker.result()
    return {
        "schema": SCHEMA,
        "checked": {
            "since": since,
            "files": read,
            "from": checker.first,
            "to": checker.last if stopped else (newest or checker.last),
            # Whether the whole period asked for was read: since a start or a day, or every log there is.
            "complete": stopped is None and (whole or (since == "start" and checker.started)
                                               or (since == "day" and checker.reached_cutoff)),
            "stopped": stopped,
            "elapsed": round(clock() - started, 2),
        },
        "installed": installed,
        "findings": findings,
    }


# --------------------------------------------------------------------------------------------------------------------
# The report


def report(result: dict, *, header: Iterable[str] = ()) -> str:
    """The result as text, to attach to an issue."""
    checked = result["checked"]
    out = ["Valve Oil diagnostics", *header, ""]
    files = ", ".join(f"{item['name']} ({item['bytes_read'] // 1024} KB of {item['size'] // 1024} KB)"
                      for item in checked["files"]) or "no logs"
    out.append(f"Checked: {files}")
    out.append(f"From {checked['from'] or 'unknown'} to {checked['to'] or 'unknown'}"
               + ("" if checked["complete"] else ", not the whole period")
               + (" (stopped at the time limit)" if checked["stopped"] == "time" else ""))
    out.append("")
    if not result["findings"]:
        out.append("No known fault signatures found.")
    for finding in result["findings"]:
        repaired = f", {finding['repaired']} repaired" if 0 < finding["repaired"] < finding["count"] else ""
        out.append(f"== {finding['title']} [{finding['severity']}] x{finding['count']}{repaired}, "
                   f"{finding['first'] or '?'} to {finding['last'] or '?'}")
        out.append(finding["meaning"])
        out.append("What to do: " + finding["action"])
        out.append("Docs: " + finding["docs"])
        for detail in finding["details"]:
            out.append("  - " + detail)
        if finding["evidence"]:
            out.append("  Log lines:")
            out.extend("    " + line for line in finding["evidence"])
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check klippy.log for Valve Oil's known fault signatures.")
    parser.add_argument("logs", nargs="*", type=Path,
                        help="log files, newest first, or a logs folder; default ~/printer_data/logs")
    parser.add_argument("--since", choices=SINCE, default="all",
                        help="all (the newest part within the budget), day (the last 24 hours of the log), or start "
                             "(since Klipper last started)")
    parser.add_argument("--budget-mb", type=int, default=BYTE_BUDGET // (1024 * 1024))
    parser.add_argument("--time-limit", type=float, default=TIME_BUDGET)
    parser.add_argument("--installed", help="the Valve Oil release installed now, to tell faults it fixes")
    parser.add_argument("--json", action="store_true", help="print the result as JSON instead of the report")
    parser.add_argument("--report", action="store_true", help="with --json, add the report to the result as report")
    args = parser.parse_args(argv)
    paths = args.logs or [Path.home() / "printer_data/logs"]
    files: list[Path] = []
    for path in paths:
        files.extend(log_files(path) if path.is_dir() else [path])
    if not files:
        print("No logs found.", file=sys.stderr)
        return 2
    result = check(files, since=args.since, byte_budget=args.budget_mb * 1024 * 1024, time_budget=args.time_limit,
                   installed=args.installed)
    if args.json and args.report:
        result["report"] = report(result)
    sys.stdout.write(json.dumps(result) + "\n" if args.json else report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
