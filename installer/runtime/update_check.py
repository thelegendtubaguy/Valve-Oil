"""What the update check finds waiting for a printer, how it publishes it, and the work an enrolled check does besides
installing a release: starting the waiting QIDI firmware update, and bringing boards to their pinned firmware.

Discovery changes nothing and stops nothing. It reads the latest release (auto_update.py), the installed state, the
firmware update's plan (firmware_update.firmware_update_plan), and, for each board, the version Klipper reports through
Moonraker against the image pinned for the printer's target firmware and what the last read of that board found
(board_firmware's admission record). A board is read again, which needs Klipper and the touchscreen stopped, only on
an enrolled idle printer, only when it reports a version it wasn't read at, and at most once per version and release
when the attempt doesn't bring it to its pinned image (CHECK_FILE); the next install reads it again. A board found
unadmitted keeps its warning and is not read again until it reports another version or a release admits what was found.
"""
from __future__ import annotations

import json
import shutil
import time
import urllib.request
from dataclasses import dataclass, field, replace

from . import board_firmware, firmware_update, valve_oil_status
from .auto_update import installed_release, newer_release
from .board_firmware import (
    CURRENT,
    HELD,
    NEEDS_IMAGE,
    UNADMITTED_STATES,
    UNREAD,
    BoardAdmission,
    BoardStatus,
    classify,
    load_board_admission,
)
from .board_images import load_board_manifest, pinned_board_image
from .box_firmware import Box, discover_boxes, held_boxes, usb_path_for_serial
from .errors import InstallerError
from .firmware import detect_firmware_version_best_effort, detect_supported_firmware
from .fs_atomic import atomic_delete, atomic_write_text
from .manifest import load_manifest
from .valve_oil_status import Blocker, PendingItem

# The boards the check last tried to bring to their pinned image, by version and release; see the module docstring.
CHECK_FILE = ".tltg_update_check.json"
# What a board needs: nothing; its pinned image, known to be admitted; a read first (it reports a version it wasn't
# read at); or QIDI's firmware update first (the firmware update flashes it).
NONE, FLASH, READ, WAIT = "none", "flash", "read", "wait"
DONE = ("current", "flashed")


@dataclass(frozen=True)
class BoardFinding:
    status: BoardStatus  # as the admission record holds it: what is known about the board now
    kind: str  # "thr", "mcu", or "box"
    reported: str | None  # the version Klipper reports
    pinned: str | None
    need: str
    box: Box | None = None

    @property
    def id(self) -> str:
        return self.status.board


@dataclass(frozen=True)
class Findings:
    enrolled: bool
    manifest: object
    printer: object | None  # PrinterSpec; None on firmware this release doesn't support
    firmware: str | None
    installed_release: str | None
    latest_version: str | None
    release_waiting: bool  # an enrolled check installs the latest release
    newer_release: bool  # the latest release's version is newer than the installed one
    release_item: PendingItem | None
    plan: firmware_update.FirmwareUpdatePlan | None
    boards: tuple[BoardFinding, ...] = ()
    boxes: tuple[Box, ...] = ()
    printer_blockers: tuple[Blocker, ...] = ()
    recovery_required: bool = False
    recovery: Blocker | None = None  # what the recovery needs, saying what to run, when the check can tell
    attempts: dict = field(default_factory=dict)
    board_manifests: dict = field(default_factory=dict)
    pinned_images: dict = field(default_factory=dict)


def installed(paths, *, enrolled: bool) -> bool:
    """Whether there is anything to check: Valve Oil's state, its board admission (both survive or outlive a QIDI
    firmware package's config cleanup in different ways), or enrollment. Uninstall removes all three."""
    try:
        state_file = load_manifest(paths.installer_root / "package.yaml").state_file
    except (InstallerError, ValueError):
        state_file = "config/tltg_optimized_state.yaml"
    return enrolled or (paths.printer_data_root / state_file).exists() or board_firmware.admission_path(paths).exists()


def discover(paths, *, enrolled: bool, latest_version: str | None, reconciliation_required: bool,
             release_changed: bool, urlopen=urllib.request.urlopen, recovery_required: bool = False,
             resolve_usb=usb_path_for_serial, disk_usage=shutil.disk_usage, **_seams) -> Findings:
    manifest = load_manifest(paths.installer_root / "package.yaml")
    try:
        printer, firmware = detect_supported_firmware(paths.firmware_manifest_path, manifest)
    except InstallerError:
        printer, firmware = None, detect_firmware_version_best_effort(paths.firmware_manifest_path)
    installed_version = installed_release(paths, manifest)
    bundle = manifest.package.version
    newer = newer_release(latest_version, installed_version or bundle)
    if newer:
        item = PendingItem(kind=valve_oil_status.RELEASE, version=latest_version, current=installed_version)
    elif reconciliation_required:
        # Valve Oil is no longer installed as this release installs it, such as after QIDI's package cleaned the config:
        # this release is installed again. Marked, so readers don't take it for a release the printer already runs.
        item = PendingItem(kind=valve_oil_status.RELEASE, version=bundle, current=installed_version, reinstall=True)
    elif enrolled and release_changed:
        item = PendingItem(kind=valve_oil_status.RELEASE, version=latest_version, current=installed_version)
    else:
        item = None
    plan = None
    boards, boxes, manifests, pinned = (), (), {}, {}
    if printer is not None:
        try:
            plan = firmware_update.firmware_update_plan(paths, manifest=manifest, urlopen=urlopen, disk_usage=disk_usage)
        except (OSError, InstallerError):
            plan = None
        boards, boxes, manifests, pinned = _assess_boards(paths, manifest=manifest, printer=printer, firmware=firmware,
                                                          urlopen=urlopen, resolve_usb=resolve_usb)
    recovery = firmware_update.recovery_blocker(paths)
    return Findings(
        enrolled=enrolled, manifest=manifest, printer=printer, firmware=firmware, installed_release=installed_version,
        latest_version=latest_version, release_waiting=release_changed or reconciliation_required or newer,
        newer_release=newer,
        release_item=item, plan=plan, boards=boards, boxes=boxes, printer_blockers=firmware_update.printer_blockers(paths, urlopen=urlopen),
        recovery_required=recovery_required or recovery is not None, recovery=recovery, attempts=_load_attempts(paths),
        board_manifests=manifests, pinned_images=pinned)


# ---- boards, from what Klipper reports --------------------------------------------------------------------------------


def _assess_boards(paths, *, manifest, printer, firmware, urlopen, resolve_usb):
    """Each configured board against its pinned image, without reading any board."""
    boxes = tuple(discover_boxes(paths, resolve_usb=resolve_usb))
    names = {"thr": "mcu THR", "mcu": "mcu", **{f"box{box.number}": f"mcu {box.name}" for box in boxes}}
    kinds = {name: "box" if name.startswith("box") else name for name in names}
    manifests = {kind: load_board_manifest(paths.bundle_root, kind) for kind in set(kinds.values())}
    pinned = {kind: pinned_board_image(paths.bundle_root, printer, kind) for kind in manifests}
    versions = board_firmware.reported_versions(paths, names, urlopen)
    previous = load_board_admission(paths)
    held = held_boxes(paths, manifest.package.version)
    findings = []
    for name, kind in kinds.items():
        version, image = versions[name], pinned[kind]
        prior = previous.board(name) if previous is not None else None
        box = next((box for box in boxes if f"box{box.number}" == name), None)
        if version is not None and version == image.version:
            status, need = BoardStatus(name, CURRENT, version=version, application=image.build,
                                       bootloader_sha256=prior.bootloader_sha256 if prior else None), NONE
        elif box is not None and (box.name, box.serial) in held:
            status, need = BoardStatus(name, HELD, version=version), NONE
        elif prior is not None and prior.state in UNADMITTED_STATES and version in (None, prior.version):
            # Found unadmitted before: what that read found, against this release, without reading it again.
            state, application = classify(kind, {"bootloader_sha256": prior.bootloader_sha256,
                                                 "application_sha256": prior.application_sha256,
                                                 "version": prior.version},
                                          board_manifest=manifests[kind], pinned=image, printer=printer,
                                          firmware=firmware)
            status = replace(prior, state=state, application=application)
            if state not in UNADMITTED_STATES:
                status = replace(status, report=None, issue_url=None)
            need = NONE if state in UNADMITTED_STATES else _flash_or_wait(status, printer, firmware, paths)
        elif version is None:
            # Klipper isn't ready or doesn't see the board: what was known stays.
            status, need = prior or BoardStatus(name, UNREAD, detail="Klipper doesn't report this board."), NONE
        elif prior is not None and prior.state == NEEDS_IMAGE and version == prior.version:
            status, need = prior, _flash_or_wait(prior, printer, firmware, paths)
        else:
            status = BoardStatus(name, UNREAD, version=version, detail=(
                f"It reports firmware {version}, not its pinned {image.version}; it is read before it is updated."))
            need = READ
        findings.append(BoardFinding(status=status, kind=kind, reported=version, pinned=image.version, need=need, box=box))
    # With the THR or mainboard not admitted, neither is flashed (board_firmware: today's mode); Boxes still are.
    if any(finding.status.state in UNADMITTED_STATES for finding in findings if finding.kind != "box"):
        findings = [replace(finding, need=NONE) if finding.kind != "box" else finding for finding in findings]
    return tuple(findings), boxes, manifests, pinned


def _flash_or_wait(status: BoardStatus, printer, firmware, paths) -> str:
    return FLASH if board_firmware.deliverable_now(status, printer, firmware, paths.bundle_root) else WAIT


def _load_attempts(paths) -> dict:
    try:
        value = json.loads((paths.printer_data_root / CHECK_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) and isinstance(value.get("boards"), dict) else {}


def _tried(found: Findings, board: BoardFinding) -> dict | None:
    """The check's last attempt at this board, when it was at this version and release and didn't bring it to its
    pinned image."""
    if found.attempts.get("release") != found.manifest.package.version:
        return None
    entry = found.attempts["boards"].get(board.id)
    if not isinstance(entry, dict) or entry.get("version") != board.reported or entry.get("outcome") in DONE:
        return None
    return entry


def _save_attempts(paths, found: Findings, outcomes: dict[str, tuple[str, str]]) -> None:
    boards = dict(found.attempts.get("boards", {})) if found.attempts.get("release") == found.manifest.package.version else {}
    reported = {board.id: board.reported for board in found.boards}
    for board, (outcome, detail) in outcomes.items():
        boards[board] = {"version": reported.get(board), "outcome": outcome, "detail": detail, "at": valve_oil_status.now()}
    try:
        atomic_write_text(paths.printer_data_root / CHECK_FILE,
                          json.dumps({"release": found.manifest.package.version, "boards": boards}, indent=2) + "\n",
                          mode=0o644)
    except OSError:
        pass


def remove_files(paths) -> None:
    """Uninstall."""
    try:
        atomic_delete(paths.printer_data_root / CHECK_FILE)
    except OSError:
        pass


# ---- publishing -------------------------------------------------------------------------------------------------------


def _merge(*groups) -> tuple[Blocker, ...]:
    seen, merged = set(), []
    for group in groups:
        for blocker in group:
            if blocker.code not in seen:
                seen.add(blocker.code)
                merged.append(blocker)
    return tuple(merged)


def _tried_blocker(entry: dict) -> Blocker:
    detail = entry.get("detail") or entry.get("outcome") or ""
    return Blocker.of("last_run_failed", "the last update check couldn't update it" + (f" ({detail})" if detail else "")
                      + "; the next install tries again")


def pending_items(found: Findings) -> tuple[PendingItem, ...]:
    """Everything waiting; why it hasn't run only on an enrolled printer (blocked_by is empty without enrollment)."""
    recovery = (found.recovery or Blocker.of("recovery_required"),) if found.recovery_required else ()
    common = _merge(found.printer_blockers, recovery)

    def blocked(*extra) -> tuple[Blocker, ...]:
        return _merge(common, *extra) if found.enrolled else ()

    items = []
    if found.release_item is not None:
        items.append(replace(found.release_item, blocked_by=blocked()))
    plan = found.plan
    if plan is not None and plan.needed:
        items.append(PendingItem(kind=valve_oil_status.FIRMWARE, version=plan.target, current=plan.installed,
                                 blocked_by=blocked(plan.blockers)))
    for board in found.boards:
        if board.need not in (FLASH, READ):
            continue
        entry = _tried(found, board)
        items.append(PendingItem(kind=valve_oil_status.BOARD, board=board.id, version=board.pinned,
                                 current=board.reported, blocked_by=blocked((_tried_blocker(entry),) if entry else ())))
    return tuple(items)


def status_boards(found: Findings) -> tuple[valve_oil_status.BoardStatus, ...]:
    checked = valve_oil_status.now()
    boards = []
    for board in found.boards:
        status = board.status
        waiting = board.need == WAIT
        note = (board_firmware.waiting_detail(found.printer) if waiting else
                board_firmware.box_note(status, found.printer) if board.kind == "box" else None)
        boards.append(valve_oil_status.BoardStatus(
            id=board.id, state=status.state if status.state in valve_oil_status.BOARD_STATES else valve_oil_status.UNKNOWN,
            firmware=board.reported or status.version, build=status.application, pinned=board.pinned,
            bootloader=status.bootloader_sha256, issue_url=status.issue_url, report=status.report,
            detail=note or (None if status.waiting_for_firmware_update else status.detail), checked_at=checked,
            waiting_for_firmware_update=waiting))
    return tuple(boards)


def _printer_info(found: Findings) -> valve_oil_status.PrinterInfo:
    printer = found.printer
    return valve_oil_status.PrinterInfo(
        id=getattr(printer, "id", None), name=getattr(printer, "display_name", None), firmware=found.firmware,
        target_firmware=printer.target_firmware.version if printer is not None else None)


def publish(paths, found: Findings, *, urlopen=urllib.request.urlopen) -> None:
    """Write what the check found to the status file, replacing what the last check found; drop Boxes no longer in
    box.cfg, with their unsupported-board warnings."""
    from .box_firmware import prune_unsupported

    valve_oil_status.record_check(paths, enrolled=found.enrolled, printer=_printer_info(found),
                                  installed_release=found.installed_release, pending=pending_items(found),
                                  boards=status_boards(found) if found.printer is not None else None)
    configured = {board.id for board in found.boards}
    gone = [board.id for board in valve_oil_status.load_status(paths).boards
            if board.id.startswith("box") and found.printer is not None and board.id not in configured]
    if gone:
        valve_oil_status.record_boards(paths, (), remove=gone)
    prune_unsupported(paths, found.boxes)
    if found.printer is not None:
        _refresh_closed_loop(paths, found, urlopen=urlopen)


def publish_failed(paths, *, enrolled: bool, message: str) -> None:
    """A check that couldn't find out what is waiting keeps the last check's list."""
    try:
        manifest = load_manifest(paths.installer_root / "package.yaml")
        printer, firmware = detect_supported_firmware(paths.firmware_manifest_path, manifest)
        info = valve_oil_status.PrinterInfo(id=printer.id, name=printer.display_name, firmware=firmware,
                                            target_firmware=printer.target_firmware.version)
    except (InstallerError, ValueError):
        manifest, info = None, valve_oil_status.PrinterInfo(
            firmware=detect_firmware_version_best_effort(paths.firmware_manifest_path))
    valve_oil_status.record_check(paths, enrolled=enrolled, printer=info,
                                  installed_release=installed_release(paths, manifest) if manifest else None,
                                  pending=(), failed=message)


def mark_failed(paths, kind: str, message: str, *, board: str | None = None) -> None:
    """Add "the last update failed" to what is still waiting of `kind` (one board's, with `board`)."""
    blocker = Blocker.of("last_run_failed", f"the last update failed: {message.rstrip('.')}")

    def change(status):
        return replace(status, pending=tuple(
            replace(item, blocked_by=_merge(item.blocked_by, (blocker,)))
            if item.kind == kind and (board is None or item.board == board) else item
            for item in status.pending))

    valve_oil_status.update_status(paths, change)


def _refresh_closed_loop(paths, found: Findings, *, urlopen) -> None:
    """The Max 4's closed-loop drivers in the status, from the versions Klipper reports (nothing is sent to them).
    Both on the target package's version: current, which also ends a "held" a firmware update recorded once they
    were updated from the touchscreen's firmware page. On another version with the printer already on the target
    firmware: held, since only a firmware update sends them an image. While a firmware update is still to come they
    are left out until it has run."""
    from .closed_loop_firmware import reported_versions

    printer = found.printer
    target = printer.target_firmware.board_version("closed_loop")
    entry = next((board for board in valve_oil_status.load_status(paths).boards if board.id == "closed_loop"), None)
    if not target or (entry is not None and entry.state == valve_oil_status.CURRENT):
        return
    versions = reported_versions(paths.moonraker_url, urlopen=urlopen)
    if not versions or not all(versions):
        return
    if all(version == target for version in versions):
        board = valve_oil_status.BoardStatus(id="closed_loop", state=valve_oil_status.CURRENT, firmware=target,
                                             pinned=target, checked_at=valve_oil_status.now())
        valve_oil_status.record_boards(paths, (
            board if entry is None else replace(entry, state=valve_oil_status.CURRENT, firmware=target, detail=None,
                                                checked_at=valve_oil_status.now()),))
    elif entry is None and found.firmware == printer.target_firmware.version:
        valve_oil_status.record_boards(paths, (valve_oil_status.BoardStatus(
            id="closed_loop", state=valve_oil_status.HELD, firmware=", ".join(sorted(set(versions))), pinned=target,
            checked_at=valve_oil_status.now()),))


# ---- applying, on an enrolled idle printer whose release is current ---------------------------------------------------


def apply(paths, found: Findings, *, reporter, environ, urlopen, run, start_firmware=None, deliver=None,
          read_session=None, sleep=time.sleep, clock=time.monotonic, resolve_usb=usb_path_for_serial,
          **_seams) -> str | None:
    """Start the waiting firmware update, or else bring the boards that need it to their pinned images. Returns what it
    did ("firmware-update-started", "boards-updated", "boards-not-updated"), or None when nothing was waiting."""
    if found.recovery_required or found.printer is None:
        return None
    plan = found.plan
    if plan is not None and plan.needed and not plan.blockers:
        start_firmware = start_firmware or firmware_update.start_firmware_update
        # The run continues on its own and reports through the status file; the check doesn't wait for it.
        result = start_firmware(paths=paths, reporter=reporter, manifest=found.manifest, kind="automatic",
                                environ=environ, input_stream=None, follow=False, urlopen=urlopen, run=run)
        if result.state in (firmware_update.RUNNING, firmware_update.SUCCEEDED, firmware_update.FAILED):
            return "firmware-update-started"
    return _update_boards(paths, found, reporter=reporter, environ=environ, urlopen=urlopen, run=run,
                          deliver=deliver or board_firmware.deliver_board_firmware,
                          read_session=read_session or board_firmware.read_session, sleep=sleep, clock=clock,
                          resolve_usb=resolve_usb)


def _update_boards(paths, found: Findings, *, reporter, environ, urlopen, run, deliver, read_session, sleep, clock,
                   resolve_usb) -> str | None:
    todo = [board for board in found.boards if board.need in (FLASH, READ) and _tried(found, board) is None]
    if not todo:
        return None
    from .thr_firmware import ThrFirmwareError

    printer, firmware = found.printer, found.firmware
    reporter.line("Update check: bringing " + ", ".join(board.status.label for board in todo)
                  + " to the firmware pinned for this release.")
    record = valve_oil_status.begin_run(paths, kind="automatic", step="boards", step_label="Updating the board firmware")
    statuses = {board.id: board.status for board in found.boards}
    outcomes: dict[str, tuple[str, str]] = {}
    seams = {"run": run, "urlopen": urlopen, "sleep": sleep, "clock": clock, "resolve_usb": resolve_usb}
    try:
        to_read = [board for board in todo if board.need == READ]
        if to_read:
            try:
                reports = read_session(paths, reporter, [(board.id, board.kind, board.box) for board in to_read],
                                       environ=environ, input_stream=None, **seams)
            except ThrFirmwareError as exc:
                # The printer wasn't idle and ready: nothing was stopped, and the next check tries again.
                reporter.line("The boards were not read: " + exc.message)
                valve_oil_status.finish_run(paths, record.id, state=valve_oil_status.FAILED, message=exc.message)
                return "boards-not-updated"
            for board in to_read:
                report_path, report, problem = reports[board.id]
                if report is None:
                    statuses[board.id] = BoardStatus(board.id, UNREAD, version=board.reported, detail=problem)
                    outcomes[board.id] = ("unread", problem or "")
                    continue
                state, application = classify(board.kind, report, board_manifest=found.board_manifests[board.kind],
                                              pinned=found.pinned_images[board.kind], printer=printer, firmware=firmware)
                statuses[board.id] = BoardStatus(
                    board.id, state, version=report.get("version") or board.reported, application=application,
                    bootloader_sha256=report.get("bootloader_sha256"),
                    application_sha256=report.get("application_sha256"),
                    report=str(report_path) if state in UNADMITTED_STATES else None)
        admission = BoardAdmission(package_version=found.manifest.package.version, printer=printer.id,
                                   firmware=firmware, target_firmware=printer.target_firmware.version,
                                   new_install=False, boards=tuple(statuses[board.id] for board in found.boards))
        admission = board_firmware.with_issue_links(paths, admission, printer=printer, box_count=len(found.boxes))
        delivery = deliver(paths=paths, reporter=reporter, manifest=found.manifest, printer=printer,
                           admission=admission, environ=environ, input_stream=None, **seams)
    except InstallerError as exc:
        # A service that couldn't be stopped or started again, or a write that couldn't be verified, whose rescue
        # record stops every installer operation until rescue.
        for board in todo:
            outcomes.setdefault(board.id, ("failed", exc.message))
        _save_attempts(paths, found, outcomes)
        valve_oil_status.finish_run(paths, record.id, state=valve_oil_status.FAILED, message=exc.message)
        for board in todo:
            mark_failed(paths, valve_oil_status.BOARD, exc.message, board=board.id)
        raise
    results = {result.board: result for result in delivery.results}
    for board in todo:
        result = results.get(board.id)
        outcomes.setdefault(board.id, (result.outcome, result.detail) if result else ("skipped", ""))
    _save_attempts(paths, found, outcomes)
    valve_oil_status.record_boards(paths, _with_pinned(valve_oil_status.boards_from_admission(
        delivery.admission, checked_at=valve_oil_status.now()), found))
    # Found unadmitted (a warning now), or waiting for the firmware update: no longer work for the check.
    settled = {board for board, (outcome, _) in outcomes.items() if outcome in DONE + ("not_admitted", "waiting")}
    left = {board: detail or outcome for board, (outcome, detail) in outcomes.items() if board not in settled}
    done = {board for board, (outcome, _) in outcomes.items() if outcome in DONE}

    def change(status):
        pending = []
        for item in status.pending:
            if item.kind == valve_oil_status.BOARD and item.board in settled:
                continue
            if item.kind == valve_oil_status.BOARD and item.board in left:
                item = replace(item, blocked_by=_merge(item.blocked_by, (_tried_blocker({"detail": left[item.board]}),)))
            pending.append(item)
        return replace(status, pending=tuple(pending))

    valve_oil_status.update_status(paths, change)
    message = "; ".join(f"{_label(board)}: {outcome}" + (f" ({detail})" if detail and outcome not in DONE else "")
                        for board, (outcome, detail) in outcomes.items())
    valve_oil_status.finish_run(paths, record.id, state=valve_oil_status.SUCCEEDED if not left else valve_oil_status.FAILED,
                                message=message)
    reporter.line("Update check: " + message + ".")
    return "boards-updated" if done else "boards-not-updated"


def _label(board: str) -> str:
    return BoardStatus(board, UNREAD).label


def _with_pinned(boards, found: Findings):
    pinned = {board.id: board.pinned for board in found.boards}
    return tuple(replace(board, pinned=pinned.get(board.id)) for board in boards)
