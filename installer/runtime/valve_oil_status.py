"""The pending-work status file: what Valve Oil has waiting for this printer, and why it hasn't run.

One small JSON file in printer_data, beside the automatic-update enrollment marker, outside both /tmp and the config
folder a QIDI firmware package wipes. The update check, the firmware-update run, and "update now" write it through this
module; Klipper's tltg_printer_warnings and the tltg-status service read it. Writes are atomic and serialized by a lock
file, so a reader never sees a partial file and two writers never lose each other's changes.

Readers must accept any `schema` they know and ignore keys they don't; a change that would mislead an older reader
raises SCHEMA. Every field may be missing or null in a file written before the field's writer ran.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .fs_atomic import atomic_write_text
from .models import RuntimePaths

SCHEMA = 1
STATUS_FILE = ".tltg_valve_oil_status.json"
LOCK_FILE = ".tltg_valve_oil_status.lock"
# Readers ignore a file larger than this.
MAX_BYTES = 262144
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")

# What is waiting.
RELEASE, FIRMWARE, BOARD = "release", "firmware", "board"
PENDING_KINDS = (RELEASE, FIRMWARE, BOARD)

# Why an enrolled printer's waiting work has not run, with the text readers show after "It hasn't run yet: ".
BLOCKERS = {
    "printing": "a print is running or paused",
    "heaters_on": "a heater is set to a temperature",
    "printer_state_unknown": "Klipper didn't report whether the printer is idle",
    "free_space": "there isn't enough free space on the printer",
    "mirror_unreachable": "the firmware download couldn't be reached",
    "package_unverified": "the firmware download didn't match its pinned checksum",
    "sudo_password": "the update needs sudo with QIDI's default password, as automatic updates do",
    "run_failed": "the update could not be started",
    "board_not_admitted": "a board's bootloader or firmware isn't supported yet",
    "recovery_required": ("an earlier installer or firmware operation needs recovery first; run the Valve Oil installer "
                          "to see what is needed"),
    "run_in_progress": "another update is still running",
    "last_run_failed": "the last update failed",
}

# Board identities and their display names; USB Boxes are box1 to box4.
BOARD_NAMES = {"thr": "THR", "mcu": "Mainboard", "closed_loop": "Closed-loop drivers"}
# A board's firmware against the image pinned for the printer's target firmware; the same names as the install's
# board admission record (board_firmware.py), whose "unread" is UNKNOWN here.
CURRENT = "current"  # on its pinned image
NEEDS_IMAGE = "needs_image"  # admitted, on QIDI's firmware or an earlier build; the next run flashes it
UNRECOGNIZED_FIRMWARE = "unrecognized_firmware"  # admitted bootloader, firmware this release doesn't recognize
UNRECOGNIZED_BOOTLOADER = "unrecognized_bootloader"  # bootloader this release doesn't admit; see issue_url
HELD = "held"  # a USB Box put back on QIDI's firmware by `box-firmware.sh restore`; kept there until the next release
UNKNOWN = "unknown"  # not read yet, or the read failed; see detail
BOARD_STATES = (CURRENT, NEEDS_IMAGE, UNRECOGNIZED_FIRMWARE, UNRECOGNIZED_BOOTLOADER, HELD, UNKNOWN)

# Update runs.
RUN_KINDS = ("update_now", "automatic", "install")
RUNNING, SUCCEEDED, FAILED, INTERRUPTED = "running", "succeeded", "failed", "interrupted"
RUN_STATES = (RUNNING, SUCCEEDED, FAILED, INTERRUPTED)


@dataclass(frozen=True)
class Blocker:
    code: str
    message: str = ""

    @classmethod
    def of(cls, code: str, message: str | None = None) -> Blocker:
        return cls(code=code, message=message or BLOCKERS.get(code, code))


@dataclass(frozen=True)
class PendingItem:
    kind: str  # RELEASE, FIRMWARE, or BOARD
    version: str | None = None  # the release, the QIDI firmware, or the board image it would install
    current: str | None = None  # what the printer or board has now
    board: str | None = None  # BOARD only: "thr", "mcu", or "box<N>"
    # RELEASE only: the installed release is installed again, because its installed state is missing or has drifted
    # (as after QIDI's package emptied the config folder); `version` is then the installed release.
    reinstall: bool = False
    # Empty when the work can run at the next check, or when automatic updates are off.
    blocked_by: tuple[Blocker, ...] = ()


@dataclass(frozen=True)
class BoardStatus:
    id: str  # "thr", "mcu", or "box<N>"
    state: str = UNKNOWN
    name: str = ""
    firmware: str | None = None  # the version the board reports, as known
    # The application as admission recognized it: "patched" (this project's build), "stock" (QIDI's image in the
    # firmware manifest), "superseded" (an earlier build of this project), "qidi" (QIDI's image of another version the
    # firmware's package carries), "unrecognized", or None when not read.
    build: str | None = None
    pinned: str | None = None  # the version of the image pinned for the target firmware
    bootloader: str | None = None  # the bootloader's SHA-256, as known
    issue_url: str | None = None  # the prefilled support-issue link for an unrecognized board
    report: str | None = None  # the shareable report of the read that found it unrecognized
    detail: str | None = None  # why an UNKNOWN board wasn't read, or when a waiting board is updated
    checked_at: str | None = None
    # NEEDS_IMAGE that the QIDI firmware update must come first for: its pinned image is for the target firmware's
    # Klipper (a THR or mainboard), or it runs QIDI's image of an earlier version. `detail` says which update.
    waiting_for_firmware_update: bool = False


@dataclass(frozen=True)
class PrinterInfo:
    id: str | None = None  # package.yaml printer id, such as "max4"
    name: str | None = None
    firmware: str | None = None  # the QIDI firmware it runs
    target_firmware: str | None = None  # the QIDI firmware the installed release brings it to


@dataclass(frozen=True)
class CheckInfo:
    at: str | None = None
    enrolled: bool | None = None  # enrollment as the check saw it; readers read the marker for the current value
    result: str | None = None  # "ok" or "failed"
    message: str = ""


@dataclass(frozen=True)
class RunInfo:
    id: str
    kind: str  # one of RUN_KINDS
    state: str  # one of RUN_STATES
    started_at: str
    step: str = ""
    step_label: str = ""
    finished_at: str | None = None
    message: str = ""
    resumed: bool = False
    # A RUNNING record whose process is gone, or that was written before the last boot, was interrupted.
    pid: int | None = None
    boot_id: str | None = None
    # The tltg-status service's "update now" job that started this run, or the run it continued (begin_run).
    job: str | None = None
    # The id of the QIDI firmware update's pending record (firmware_update.py) when this run carries one.
    firmware_update: str | None = None


@dataclass(frozen=True)
class ValveOilStatus:
    schema: int = SCHEMA
    updated_at: str | None = None
    installed_release: str | None = None
    printer: PrinterInfo = field(default_factory=PrinterInfo)
    check: CheckInfo = field(default_factory=CheckInfo)
    pending: tuple[PendingItem, ...] = ()
    boards: tuple[BoardStatus, ...] = ()
    run: RunInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "updated_at": self.updated_at,
            "release": {"installed": self.installed_release},
            "printer": _fields(self.printer),
            "check": _fields(self.check),
            "pending": [
                {**_fields(item), "blocked_by": [_fields(blocker) for blocker in item.blocked_by]} for item in self.pending
            ],
            "boards": [_fields(board) for board in self.boards],
            "run": _fields(self.run) if self.run else None,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ValveOilStatus:
        """Parse a status file's content; anything unreadable becomes an empty status."""
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return cls()
        release = data.get("release") if isinstance(data.get("release"), dict) else {}
        run = data.get("run")
        try:
            run_info = _build(RunInfo, run) if isinstance(run, dict) else None
        except (TypeError, ValueError):
            run_info = None
        return cls(
            updated_at=_text(data.get("updated_at")),
            installed_release=_text(release.get("installed")),
            printer=_build(PrinterInfo, data.get("printer")),
            check=_build(CheckInfo, data.get("check")),
            pending=tuple(_pending(item) for item in _items(data.get("pending")) if item.get("kind") in PENDING_KINDS),
            boards=tuple(_build(BoardStatus, item) for item in _items(data.get("boards")) if _text(item.get("id"))),
            run=run_info,
        )


def status_path(paths: RuntimePaths) -> Path:
    return paths.printer_data_root / STATUS_FILE


def load_status(paths: RuntimePaths) -> ValveOilStatus:
    """The current status; a missing, oversized, or unreadable file reads as empty."""
    try:
        path = status_path(paths)
        if path.stat().st_size > MAX_BYTES:
            return ValveOilStatus()
        return ValveOilStatus.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return ValveOilStatus()


def update_status(paths: RuntimePaths, change: Callable[[ValveOilStatus], ValveOilStatus]) -> ValveOilStatus:
    """Read, change, and atomically write the status under its lock; returns what was written."""
    with _locked(paths):
        status = replace(change(load_status(paths)), updated_at=now())
        atomic_write_text(status_path(paths), json.dumps(status.to_dict(), indent=2) + "\n", mode=0o644)
    return status


def record_check(
    paths: RuntimePaths,
    *,
    enrolled: bool,
    printer: PrinterInfo,
    installed_release: str | None,
    pending: Iterable[PendingItem],
    boards: Iterable[BoardStatus] | None = None,
    failed: str | None = None,
) -> ValveOilStatus:
    """Publish an update check's findings: everything waiting, replacing what the last check found.

    `boards` replaces the board list when given (merged by id with what an install recorded); `failed` records a
    check that couldn't finish, keeping the previous pending list.
    """
    pending = tuple(pending)

    def change(status: ValveOilStatus) -> ValveOilStatus:
        result = replace(
            status,
            printer=printer,
            installed_release=installed_release,
            check=CheckInfo(at=now(), enrolled=enrolled, result="failed" if failed else "ok", message=failed or ""),
        )
        if not failed:
            result = replace(result, pending=pending)
        if boards is not None:
            result = replace(result, boards=_merge_boards(status.boards, boards))
        return result

    return update_status(paths, change)


def boards_from_admission(admission: Any, *, checked_at: str | None = None) -> tuple[BoardStatus, ...]:
    """The boards of an install's admission record (board_firmware.BoardAdmission), as the status file shows them."""
    return tuple(
        BoardStatus(
            id=board.board,
            state=board.state if board.state in BOARD_STATES else UNKNOWN,
            name=_board_name(board.board),
            firmware=board.version,
            build=board.application,
            bootloader=board.bootloader_sha256,
            issue_url=board.issue_url,
            report=board.report,
            detail=board.detail,
            checked_at=checked_at,
            waiting_for_firmware_update=board.waiting_for_firmware_update,
        )
        for board in admission.boards
    )


def record_boards(paths: RuntimePaths, boards: Iterable[BoardStatus], *, remove: Iterable[str] = ()) -> ValveOilStatus:
    """Record what an install, a check, or a flash learned about some boards, keeping the others; `remove` drops
    boards no longer configured, such as a Box taken out of box.cfg."""
    boards = tuple(boards)
    gone = set(remove)
    return update_status(
        paths, lambda status: replace(status, boards=tuple(b for b in _merge_boards(status.boards, boards) if b.id not in gone))
    )


def clear_pending(paths: RuntimePaths, *, kind: str | None = None, board: str | None = None,
                  version: str | None = None) -> ValveOilStatus:
    """Drop waiting work that has been done: every item, every item of `kind`, one board's flash, or the items that
    would install `version`."""

    def keep(item: PendingItem) -> bool:
        if kind is not None and item.kind != kind:
            return True
        if version is not None and item.version != version:
            return True
        return board is not None and item.board != board

    return update_status(paths, lambda status: replace(status, pending=tuple(item for item in status.pending if keep(item))))


def begin_run(paths: RuntimePaths, *, kind: str, step: str = "", step_label: str = "", resumed: bool = False,
              job: str | None = None, firmware_update: str | None = None) -> RunInfo:
    """Record that an update run started in this process; replaces the previous run's result.

    A run that begins while another is still running was started by it, such as the firmware update an update now's
    install hands off to, and carries on its `job`.
    """
    if kind not in RUN_KINDS:
        raise ValueError(f"Unknown run kind: {kind}")
    started = {}

    def change(status: ValveOilStatus) -> ValveOilStatus:
        inherited = status.run.job if status.run is not None and run_active(status.run) else None
        started["run"] = RunInfo(
            id=uuid.uuid4().hex, kind=kind, state=RUNNING, started_at=now(), step=step, step_label=step_label,
            resumed=resumed, pid=os.getpid(), boot_id=current_boot_id(), job=job or inherited,
            firmware_update=firmware_update,
        )
        return replace(status, run=started["run"])

    update_status(paths, change)
    return started["run"]


def attach_job(paths: RuntimePaths, job: str) -> bool:
    """Link the run that is running now to an update now `job` that follows it; False when no run is running."""
    attached = []

    def change(status: ValveOilStatus) -> ValveOilStatus:
        if status.run is None or not run_active(status.run):
            return status
        attached.append(True)
        return replace(status, run=replace(status.run, job=job))

    update_status(paths, change)
    return bool(attached)


def set_run_step(paths: RuntimePaths, run_id: str, *, step: str, step_label: str) -> None:
    """Report the step a running update has reached, such as ("download", "Downloading QIDI firmware 01.01.07.01")."""
    update_status(paths, lambda status: _change_run(status, run_id, step=step, step_label=step_label))


def finish_run(paths: RuntimePaths, run_id: str, *, state: str, message: str = "") -> None:
    """Record a run's result: SUCCEEDED, FAILED, or INTERRUPTED (stopped where a later run resumes it)."""
    if state not in (SUCCEEDED, FAILED, INTERRUPTED):
        raise ValueError(f"A finished run can't be {state}.")
    update_status(paths, lambda status: _change_run(status, run_id, state=state, message=message, finished_at=now()))


def run_active(run: RunInfo | None) -> bool:
    """Whether a run recorded as running is still running on this boot."""
    if run is None or run.state != RUNNING:
        return False
    if run.boot_id != current_boot_id() or run.pid is None:
        return False
    try:
        os.kill(run.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def remove_status(paths: RuntimePaths) -> None:
    for name in (STATUS_FILE, LOCK_FILE):
        try:
            (paths.printer_data_root / name).unlink()
        except FileNotFoundError:
            pass


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def current_boot_id() -> str | None:
    try:
        return BOOT_ID.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


@contextmanager
def _locked(paths: RuntimePaths) -> Iterator[None]:
    paths.printer_data_root.mkdir(parents=True, exist_ok=True)
    with (paths.printer_data_root / LOCK_FILE).open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _change_run(status: ValveOilStatus, run_id: str, **changes: Any) -> ValveOilStatus:
    if status.run is None or status.run.id != run_id:
        return status
    return replace(status, run=replace(status.run, **changes))


def _merge_boards(current: Iterable[BoardStatus], updates: Iterable[BoardStatus]) -> tuple[BoardStatus, ...]:
    merged = {board.id: board for board in current}
    for board in updates:
        if board.state not in BOARD_STATES:
            raise ValueError(f"Unknown board state: {board.state}")
        merged[board.id] = replace(board, name=board.name or _board_name(board.id))
    return tuple(sorted(merged.values(), key=lambda board: _board_order(board.id)))


def _board_name(board_id: str) -> str:
    if board_id.startswith("box") and board_id[3:].isdigit():
        return f"Box {board_id[3:]}"
    return BOARD_NAMES.get(board_id, board_id)


def _board_order(board_id: str) -> tuple[int, int, str]:
    if board_id in BOARD_NAMES:
        return (list(BOARD_NAMES).index(board_id), 0, board_id)
    if board_id.startswith("box") and board_id[3:].isdigit():
        return (len(BOARD_NAMES), int(board_id[3:]), board_id)
    return (len(BOARD_NAMES) + 1, 0, board_id)


def _fields(value: Any) -> dict[str, Any]:
    return {name: getattr(value, name) for name in value.__dataclass_fields__ if name != "blocked_by"}


def _build(cls, data: Any):
    data = data if isinstance(data, dict) else {}
    return cls(**{name: data[name] for name in cls.__dataclass_fields__ if name in data})


def _pending(data: dict) -> PendingItem:
    blockers = tuple(
        Blocker.of(str(item.get("code")), _text(item.get("message"))) for item in _items(data.get("blocked_by")) if item.get("code")
    )
    item = _build(PendingItem, {key: value for key, value in data.items() if key != "blocked_by"})
    return replace(item, reinstall=item.reinstall is True, blocked_by=blockers)


def _items(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
