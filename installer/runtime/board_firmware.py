"""Every board's firmware: admit the THR, the mainboard, and each USB Box, and bring them to their pinned images.

Admission reads each board through its transfer tool's read-only snapshot (Klipper, the touchscreen, and the update
timer stop for the reads; nothing is reset or programmed) and compares the bootloader and application with what this
release admits. A new installation reads every board and is refused before any change when one is not admitted. An
existing installation reads only boards that are not already on their pinned image, and one with a THR or mainboard
this release doesn't admit is updated in today's mode: no block on QIDI's package updates and no THR or mainboard
firmware. A USB Box this release doesn't admit is left alone and does not stop firmware for the rest of the printer.
Each board this release doesn't admit gets a printer warning with a prefilled printer-support issue link.

The result is kept in printer_data (outside the config folder QIDI's packages clean) so the update check and the
status service can show it without reading the boards again.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .board_guard import critical_window, detached
from .board_images import deliverable_on, load_board_manifest, pinned_board_image
from .box_firmware import (
    BACKUP_DIRECTORY as BOX_BACKUP_DIRECTORY,
    SERVICES,
    clear_stale_holds,
    discover_boxes,
    flash_box_firmware_after_install,
    held_boxes,
    usb_path_for_serial,
)
from .errors import InstallerError
from .fs_atomic import atomic_delete, atomic_write_text
from .mainboard_firmware import BACKUP_DIRECTORY as MAINBOARD_BACKUP_DIRECTORY, mainboard_serial, run_mainboard_firmware
from .naming import REPOSITORY_URL
from .path_safety import ensure_runtime_path_has_no_symlink_components
from .safety import BOX_RECOVERY_MARKER, MAINBOARD_RECOVERY_MARKER, THR_RECOVERY_MARKER
from .sudo import authenticate_sudo, run_sudo
from .thr_firmware import (
    BACKUP_DIRECTORY as THR_BACKUP_DIRECTORY,
    FLASHED as THR_FLASHED,
    ThrFirmwareError,
    flash_thr_firmware,
    wait_for_thr_printer_ready,
)

ADMISSION_FILE = ".tltg_board_admission.json"
SCHEMA_VERSION = 1
SUBJECT = "reading the boards"

# Board states.
CURRENT = "current"  # on the image pinned for the target firmware
NEEDS_IMAGE = "needs_image"  # admitted, and not yet on its pinned image
HELD = "held"  # a USB Box the operator restored to QIDI's firmware, kept there until the next release
UNRECOGNIZED_FIRMWARE = "unrecognized_firmware"  # admitted bootloader, application this release doesn't know
UNRECOGNIZED_BOOTLOADER = "unrecognized_bootloader"
UNREAD = "unread"  # could not be read this run; `detail` says why
ADMITTED_STATES = frozenset({CURRENT, NEEDS_IMAGE, HELD})
UNADMITTED_STATES = frozenset({UNRECOGNIZED_FIRMWARE, UNRECOGNIZED_BOOTLOADER})

# `application` values: the manifest's "patched", "stock", or "superseded" image; "qidi" for one of the manifest's
# predecessors, QIDI's image of an earlier board version (by its hash; flashed once the printer runs the target
# firmware); "unrecognized"; or None when the board was not read.
QIDI_APPLICATION = "qidi"

# Printer warnings read these from Klipper's log folder; tltg_box_firmware_box<N>.json is the Box flow's own.
LOG_REPORTS = {"thr": "tltg_thr_firmware.json", "mcu": "tltg_mainboard_firmware.json"}
BOX_LOG_REPORT = re.compile(r"^tltg_box_firmware_box(\d+)\.json$")
# The input shaper calibration saved when a run last flashed the THR; printer warnings ask for a new calibration while
# the saved one is still this.
THR_SHAPER_MARKER = "tltg_input_shaper_thr_flash.json"

ISSUE_FORM = REPOSITORY_URL + "/issues/new"
ISSUE_TEMPLATE = "printer-support.yml"
# GitHub refuses longer request URLs; fields are shortened to fit.
MAX_ISSUE_URL = 7000
# The keys of a board's shareable report that go into the printer warning's copy in Klipper's log folder, which the
# operator attaches themselves. Snapshots never record MCU unique IDs; only these keys are passed on regardless.
SHAREABLE_REPORT_KEYS = (
    "application", "application_sha256", "application_padding", "application_region_sha256",
    "application_region_bytes", "bootloader_sha256", "bootloader_page_sha256", "supported_bootloader", "version",
    "build_versions", "constants", "id_registers",
)
# The issue link carries only the board's identity: hashes, sizes, and the MCU model. The firmware's own version and
# build strings are left out: a firmware someone built themselves can carry the build machine's name in them.
LINK_REPORT_KEYS = (
    "application", "application_sha256", "application_padding", "application_region_sha256",
    "application_region_bytes", "bootloader_sha256", "bootloader_page_sha256", "supported_bootloader", "id_registers",
)
LINK_CONSTANTS = ("MCU", "CLOCK_FREQ", "SERIAL_BAUD")
# A QIDI version or file name, as the printer details carry them from QIDI's firmware manifest.
_PLAIN = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


NOT_CONNECTED = ("{} is listed in box.cfg but is not connected. Connect it, or remove it in the touchscreen's Box "
                 "setup, then run the installer again.")


class BoardAdmissionError(InstallerError):
    pass


@dataclass(frozen=True)
class BoardStatus:
    board: str  # "thr", "mcu", or "box<N>"
    state: str
    version: str | None = None  # what the board reports running
    application: str | None = None
    bootloader_sha256: str | None = None
    application_sha256: str | None = None
    report: str | None = None  # the shareable report of the read that found it unadmitted
    issue_url: str | None = None
    detail: str | None = None
    # NEEDS_IMAGE, and the last delivery left it for the QIDI firmware update to come first (waiting_detail).
    waiting_for_firmware_update: bool = False

    @property
    def kind(self) -> str:
        return "box" if self.board.startswith("box") else self.board

    @property
    def label(self) -> str:
        return {"thr": "THR", "mcu": "Mainboard"}.get(self.board) or "Box " + self.board[3:]

    @property
    def admitted(self) -> bool:
        return self.state in ADMITTED_STATES


@dataclass(frozen=True)
class BoardAdmission:
    package_version: str
    printer: str  # printer id
    firmware: str  # QIDI firmware installed when the boards were read
    target_firmware: str
    new_install: bool
    boards: tuple[BoardStatus, ...]

    def board(self, name: str) -> BoardStatus | None:
        return next((status for status in self.boards if status.board == name), None)

    @property
    def admitted(self) -> bool:
        """Every board is admitted, as a new installation requires."""
        return all(status.admitted for status in self.boards)

    @property
    def delivery_admitted(self) -> bool:
        """The THR and the mainboard are admitted: QIDI's package updates are blocked and firmware is delivered.

        A USB Box this release doesn't admit is left alone and does not stop firmware for the rest of the printer.
        """
        return all(status.admitted for status in self.boards if status.kind != "box")

    @property
    def unadmitted(self) -> tuple[BoardStatus, ...]:
        return tuple(status for status in self.boards if status.state in UNADMITTED_STATES)

    def to_json(self) -> str:
        payload = {"schema_version": SCHEMA_VERSION, **asdict(self)}
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "BoardAdmission":
        raw = json.loads(text)
        if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unknown board admission record")
        fields = set(BoardStatus.__dataclass_fields__)
        boards = tuple(BoardStatus(**{key: value for key, value in entry.items() if key in fields})
                       for entry in raw["boards"])
        return cls(package_version=raw["package_version"], printer=raw["printer"], firmware=raw["firmware"],
                   target_firmware=raw["target_firmware"], new_install=bool(raw["new_install"]), boards=boards)


@dataclass(frozen=True)
class BoardResult:
    board: str
    outcome: str  # current, flashed, fell_back, held, waiting, skipped, not_admitted, or dry_run
    detail: str = ""


@dataclass(frozen=True)
class BoardDelivery:
    results: tuple[BoardResult, ...]
    admission: BoardAdmission


def admission_path(paths) -> Path:
    return paths.printer_data_root / ADMISSION_FILE


def load_board_admission(paths) -> BoardAdmission | None:
    """The last recorded admission; None when there is none or it can't be read."""
    path = admission_path(paths)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return BoardAdmission.from_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def existing_installation(paths, manifest) -> bool:
    """Whether Valve Oil is installed on this printer now, so an install updates it rather than starting a new one.

    The one rule for new and existing installs. It counts what install writes and uninstall removes:
    - the installed state in the config folder;
    - this admission record, in printer_data, which QIDI's packages leave when they empty the config folder;
    - the automatic-update enrollment marker, also outside the config folder, so an enrolled printer that a QIDI
      update emptied is still updated.
    The installer's backup zips do not count: uninstall keeps them, and a printer installed again after an uninstall is
    held to the new-install rule.

    What a wrong answer costs: a printer counted new when it isn't (installed by a release before the admission record,
    not enrolled, and its config emptied by a QIDI update, so Valve Oil isn't running on it) is refused when a board
    isn't admitted, with the support link, and keeps working as stock. A printer counted existing when it isn't would
    be installed in today's mode with a board this release doesn't admit; only these three files can cause that, and
    uninstall removes all of them.
    """
    from .auto_update import enrollment_path

    return any(path.exists() for path in (
        paths.printer_data_root / manifest.state_file, admission_path(paths), enrollment_path(paths)))


# ---- reading -------------------------------------------------------------------------------------------------------


def reported_versions(paths, names, urlopen) -> dict[str, str | None]:
    parts = urllib.parse.urlsplit(paths.moonraker_url)
    url = urllib.parse.urlunsplit(parts._replace(
        query="&".join(urllib.parse.quote(name, safe="") + "=mcu_version" for name in names.values())))
    try:
        with urlopen(url, timeout=5) as response:
            status = json.load(response)["result"]["status"]
        return {board: (status.get(name) or {}).get("mcu_version") for board, name in names.items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return dict.fromkeys(names)


def _admitted_bootloaders(kind: str, board_manifest: dict) -> frozenset[str]:
    if kind == "thr":
        from scripts import thr_bootloaders
        return frozenset(thr_bootloaders.ADMITTED_BOOTLOADERS)
    return frozenset(entry["sha256"] for entry in board_manifest["bootloaders"])


def classify(kind: str, report: dict, *, board_manifest: dict, pinned, printer, firmware: str) -> tuple[str, str]:
    """(state, application) of a board from its snapshot report, against this release's manifests.

    Admission is by hash only: the bootloader and the application must each be an image the manifest names. What the
    board reports as its version is never evidence, so an image the manifest doesn't name is unrecognized.
    """
    from scripts.build_box_firmware import PREDECESSOR, board_spec, known_images

    spec = board_spec(board_manifest)
    if report.get("bootloader_sha256") not in _admitted_bootloaders(kind, board_manifest):
        return UNRECOGNIZED_BOOTLOADER, "unrecognized"
    # A predecessor (QIDI's image of an earlier version, by its exact hash) is QIDI's image too.
    known = {sha256: QIDI_APPLICATION if entry["identity"] == PREDECESSOR else entry["identity"]
             for sha256, entry in known_images(spec).items()}
    application = known.get(report.get("application_sha256"))
    if application is None:
        return UNRECOGNIZED_FIRMWARE, "unrecognized"
    return (CURRENT if report.get("application_sha256") == pinned.sha256 else NEEDS_IMAGE), application


def read_boards(
    *, paths, reporter, manifest, printer, firmware, new_install, dry_run=False, environ=None, input_stream=None,
    run=None, urlopen=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic, resolve_usb=usb_path_for_serial,
) -> BoardAdmission:
    """Admit every board without changing any firmware.

    A new installation reads every board. An existing one reads only boards that don't report their pinned image;
    Boxes the operator restored are held. A board an earlier read found unadmitted is read again when the operator runs
    the installer, since it may have been replaced by one reporting the same version (Boxes are told apart by name
    only); an install another installer run started (an automatic update, update now, or a firmware update, which set
    LOCK_HELD_ENV) instead checks that earlier read against this release without stopping Klipper, so an unsupported
    board doesn't stop the printer every hour. Boards that can't be read (dry run, Klipper not ready) keep their last
    recorded state, or are UNREAD.
    """
    from .auto_update import LOCK_HELD_ENV

    run = subprocess.run if run is None else run
    env = {} if environ is None else environ
    automatic = env.get(LOCK_HELD_ENV) == "1"
    boxes = discover_boxes(paths, resolve_usb=resolve_usb)
    names = {"thr": "mcu THR", "mcu": "mcu", **{f"box{box.number}": f"mcu {box.name}" for box in boxes}}
    kinds = {name: "box" if name.startswith("box") else name for name in names}
    manifests = {kind: load_board_manifest(paths.bundle_root, kind) for kind in set(kinds.values())}
    pinned = {kind: pinned_board_image(paths.bundle_root, printer, kind) for kind in manifests}
    versions = reported_versions(paths, names, urlopen)
    previous = None if new_install else load_board_admission(paths)
    not_ready = None
    if previous is not None and not dry_run and not any(versions.values()):
        # Klipper reports no board yet, as when a firmware update's install step has just started it: wait for it
        # instead of stopping it again to read boards that it would report on their pinned images.
        try:
            wait_for_thr_printer_ready(paths, urlopen=urlopen, sleep=sleep, clock=clock, reporter=reporter,
                                       subject=SUBJECT)
        except ThrFirmwareError as exc:
            not_ready = exc.message  # what a read would stop on too, after the same wait
        else:
            versions = reported_versions(paths, names, urlopen)
    release = manifest.package.version
    held = held_boxes(paths, release)
    statuses: dict[str, BoardStatus] = {}
    to_read = []
    for name, kind in kinds.items():
        version = versions[name]
        prior = previous.board(name) if previous is not None else None
        box = next((box for box in boxes if f"box{box.number}" == name), None)
        if box is not None and box.usb is None:
            # Klipper can't start with it missing either, so this, not Klipper, is what to tell the operator.
            statuses[name] = (prior if prior is not None and version in (None, prior.version) else
                              BoardStatus(name, UNREAD, version=version, detail=NOT_CONNECTED.format(BoardStatus(name, UNREAD).label)))
        elif new_install:
            to_read.append(name)
        elif version is not None and version == pinned[kind].version:
            # Counted current by the version Klipper reports, without stopping Klipper to read it. That is acceptable
            # because nothing is flashed on it: a current board is never programmed, and every flash reads the board
            # first and checks its bootloader and application by hash. It includes a Plus 5 mainboard on QIDI's
            # pinned stock image, whose bootloader an existing install then never reads; it is never flashed while
            # it reports that version, and once a release pins another version for it, it no longer matches here
            # and is read before anything is done to it.
            statuses[name] = BoardStatus(name, CURRENT, version=version, application=pinned[kind].build,
                                         bootloader_sha256=prior.bootloader_sha256 if prior else None)
        elif box is not None and (box.name, box.serial) in held:
            statuses[name] = BoardStatus(name, HELD, version=version)
        elif automatic and prior is not None and prior.state in UNADMITTED_STATES and version in (None, prior.version):
            # Found unadmitted before: check what that read found against this release instead of reading again.
            state, application = classify(kind, {"bootloader_sha256": prior.bootloader_sha256,
                                                 "application_sha256": prior.application_sha256,
                                                 "version": prior.version},
                                          board_manifest=manifests[kind], pinned=pinned[kind], printer=printer,
                                          firmware=firmware)
            statuses[name] = replace(prior, state=state, application=application)
            if state not in UNADMITTED_STATES:
                statuses[name] = replace(statuses[name], report=None, issue_url=None)
        else:
            to_read.append(name)

    unread_reason = None
    if to_read and dry_run:
        unread_reason = "dry run"
    elif to_read and not_ready is not None:
        unread_reason = not_ready
    elif to_read:
        try:
            reports = read_session(paths, reporter, [(name, kinds[name], next(
                (box for box in boxes if f"box{box.number}" == name), None)) for name in to_read],
                environ=env, input_stream=input_stream, run=run, urlopen=urlopen, sleep=sleep, clock=clock,
                resolve_usb=resolve_usb)
        except ThrFirmwareError as exc:
            unread_reason = exc.message
        else:
            for name in to_read:
                report_path, report, problem = reports[name]
                if report is None:
                    statuses[name] = BoardStatus(name, UNREAD, version=versions[name], detail=problem)
                    continue
                kind = kinds[name]
                state, application = classify(kind, report, board_manifest=manifests[kind], pinned=pinned[kind],
                                              printer=printer, firmware=firmware)
                box = next((box for box in boxes if f"box{box.number}" == name), None)
                if state == NEEDS_IMAGE and box is not None and (box.name, box.serial) in held:
                    state = HELD
                statuses[name] = BoardStatus(
                    name, state, version=report.get("version") or versions[name], application=application,
                    bootloader_sha256=report.get("bootloader_sha256"),
                    application_sha256=report.get("application_sha256"),
                    report=str(report_path) if state in UNADMITTED_STATES else None)
    if unread_reason is not None:
        for name in to_read:
            prior = previous.board(name) if previous is not None else None
            statuses[name] = (prior if prior is not None and versions[name] in (None, prior.version)
                              else BoardStatus(name, UNREAD, version=versions[name], detail=unread_reason))

    admission = BoardAdmission(package_version=release, printer=printer.id, firmware=firmware,
                               target_firmware=printer.target_firmware.version, new_install=new_install,
                               boards=tuple(statuses[name] for name in names))
    return with_issue_links(paths, admission, printer=printer, box_count=len(boxes))


def read_session(paths, reporter, boards, *, environ, input_stream, run, urlopen, sleep, clock, resolve_usb):
    """Read each board's snapshot with its tool, with Klipper, the touchscreen, and the update timer stopped.

    Returns board name -> (report path, report, problem); report is None when the board could not be read. Raises
    ThrFirmwareError before stopping anything when the printer isn't idle and ready.
    """
    python = paths.printer_data_root.parent / "klippy-env/bin/python"
    tools = {kind: paths.bundle_root / "scripts" / name for kind, name in
             (("thr", "flash_thr_firmware.py"), ("mcu", "flash_mcu_firmware.py"), ("box", "flash_box_firmware.py"))}
    if not python.is_file() or any(not tool.is_file() or tool.is_symlink() for tool in tools.values()):
        raise ThrFirmwareError("Reading the boards needs the packaged board tools and the existing klippy-env Python.")
    roots = {"thr": paths.printer_data_root / THR_BACKUP_DIRECTORY,
             "mcu": paths.printer_data_root / MAINBOARD_BACKUP_DIRECTORY,
             "box": paths.printer_data_root / BOX_BACKUP_DIRECTORY}
    for root in roots.values():
        ensure_runtime_path_has_no_symlink_components(printer_data_root=paths.printer_data_root, target=root)
    for marker in (THR_RECOVERY_MARKER, BOX_RECOVERY_MARKER, MAINBOARD_RECOVERY_MARKER):
        if (paths.printer_data_root / marker).exists():
            raise ThrFirmwareError("A board firmware operation did not finish: " + str(paths.printer_data_root / marker))
    wait_for_thr_printer_ready(paths, urlopen=urlopen, sleep=sleep, clock=clock, reporter=reporter, subject=SUBJECT)
    states = {}
    for service in SERVICES:
        result = run(["systemctl", "show", service, "--property=LoadState,ActiveState"], capture_output=True, text=True,
                     timeout=5)
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if service.endswith(".timer") and values.get("LoadState") == "not-found":
            continue
        if result.returncode or values.get("LoadState") != "loaded" or values.get("ActiveState") not in {"active", "inactive"}:
            raise ThrFirmwareError("Cannot establish serial service ownership: " + service)
        states[service] = values["ActiveState"]
    try:
        password = authenticate_sudo(run=run, environ=environ, reporter=reporter, input_stream=input_stream)
    except InstallerError as exc:
        raise ThrFirmwareError(exc.message) from exc
    wait_for_thr_printer_ready(paths, urlopen=urlopen, sleep=sleep, clock=clock, reporter=reporter, subject=SUBJECT)

    # From the first stop to the services' restart the terminal's hangup and Ctrl-C can't stop the work (board_guard).
    session_run = detached(run)

    def service_action(action, service):
        return not run_sudo(["systemctl", action, service], run=session_run, password=password, timeout=60).returncode

    reporter.line("Reading the THR, mainboard, and USB Box firmware without changing it. Klipper and the touchscreen "
                  "stop for a few minutes.")
    reports = {}
    with critical_window(reporter) as reporter:
        try:
            _read_each(paths, reporter, boards, states=states, service_action=service_action, run=session_run,
                       roots=roots, tools=tools, python=python, resolve_usb=resolve_usb, reports=reports)
        finally:
            # Every service is started even when one fails, or the read itself failed.
            failed = [service for service in SERVICES if states.get(service) == "active"
                      and not service_action("start", service)]
            if failed:
                raise BoardAdmissionError("Could not start " + ", ".join(failed) + " after reading the boards. No "
                                          "board firmware was changed; start them with: sudo systemctl start "
                                          + " ".join(failed))
    if states.get("klipper.service") == "active":
        try:
            wait_for_thr_printer_ready(paths, urlopen=urlopen, sleep=sleep, clock=clock, subject=SUBJECT)
        except ThrFirmwareError as exc:
            reporter.line("Klipper is not ready again after reading the boards: " + exc.message)
    return reports


def _read_each(paths, reporter, boards, *, states, service_action, run, roots, tools, python, resolve_usb, reports):
    """Stop the services and read each board into `reports`; the caller starts the services again."""
    box_session = None
    for service in reversed(SERVICES):
        if states.get(service) == "active" and not service_action("stop", service):
            raise BoardAdmissionError("Could not stop " + service + " to read the boards. No board firmware "
                                      "was changed.")
    for name, kind, box in boards:
        root = roots[kind]
        root.mkdir(mode=0o700, exist_ok=True)
        arguments = []
        if kind == "thr":
            session = Path(tempfile.mkdtemp(prefix="thr-", dir=root))
        elif kind == "mcu":
            session = Path(tempfile.mkdtemp(prefix="mainboard-", dir=root))
            usb = resolve_usb(mainboard_serial(paths))
            arguments = ["--usb", usb]
            atomic_write_text(session / "mainboard.json", json.dumps({"usb": usb}) + "\n", mode=0o600)
        else:
            box_session = box_session or Path(tempfile.mkdtemp(prefix="box-", dir=root))
            session = box_session / box.name
            session.mkdir(mode=0o700)
            usb = box.usb
            arguments = ["--usb", usb]
            atomic_write_text(session / "box.json", json.dumps({"box": box.name, "usb": usb, "serial": box.serial})
                              + "\n", mode=0o600)
        label = BoardStatus(name, UNREAD).label
        if arguments and arguments[1] is None:
            reports[name] = (None, None, label + " is not connected.")
            continue
        reporter.line(f"{label}: reading its bootloader and firmware...")
        before = session / "before"
        try:
            result = run([str(python), str(tools[kind]), "snapshot", *arguments, "--output-dir", str(before),
                          "--printer-idle-confirmed"], capture_output=True, text=True, timeout=960)
        except (OSError, subprocess.SubprocessError) as exc:
            reports[name] = (None, None, f"{label} could not be read ({type(exc).__name__}); see {session}.")
            continue
        atomic_write_text(session / "snapshot.stdout", result.stdout or "", mode=0o600)
        atomic_write_text(session / "snapshot.stderr", result.stderr or "", mode=0o600)
        try:
            report = json.loads((before / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = None
        if not isinstance(report, dict):
            reports[name] = (None, None, f"{label} could not be read; see {session}.")
        else:
            reports[name] = (before / "report.json", report, None)


# ---- refusal, links, and warnings ----------------------------------------------------------------------------------


def shareable_report(report: dict) -> dict:
    return {key: report[key] for key in SHAREABLE_REPORT_KEYS if key in report}


def _read_report(status: BoardStatus) -> dict | None:
    if not status.report:
        return None
    try:
        report = json.loads(Path(status.report).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return shareable_report(report) if isinstance(report, dict) else None


def link_report(status: BoardStatus) -> dict | None:
    """What of the board's report goes into the issue link (LINK_REPORT_KEYS)."""
    report = _read_report(status)
    if report is None:
        return None
    constants = report.get("constants") if isinstance(report.get("constants"), dict) else {}
    linked = {key: report[key] for key in LINK_REPORT_KEYS if key in report}
    linked["constants"] = {key: constants[key] for key in LINK_CONSTANTS if key in constants}
    return linked


def describe(status: BoardStatus) -> str:
    # The board's identity is its hashes; the version string it reports is not shown or linked.
    if status.state == UNRECOGNIZED_BOOTLOADER:
        return f"{status.label}: its bootloader is not supported yet (bootloader SHA-256 {status.bootloader_sha256})."
    if status.state == UNRECOGNIZED_FIRMWARE:
        return f"{status.label}: its firmware is not recognized (application SHA-256 {status.application_sha256})."
    if status.state == UNREAD:
        return f"{status.label}: could not be read. {status.detail or ''}".strip()
    return f"{status.label}: {status.state.replace('_', ' ')}."


def printer_details(paths) -> str:
    """The printer-support form's "Printer details", from files only, as docs/troubleshooting.md's block prints them.

    Only values of known shape go in: versions, QIDI's file names, counts, and on/off. Nothing that can carry a
    hostname, user name, address, or network name: the kernel's build string and free text are left out.
    """
    lines = []
    state = paths.config_root / "tltg_optimized_state.yaml"
    try:
        text = state.read_text(encoding="utf-8")
        version = re.search(r"^  version: *'?([0-9.]+)", text, re.M).group(1)
        installed = re.search(r"^installed_at: *'?([0-9T:.+Z -]+?)'?$", text, re.M).group(1)
        lines.append(f"Valve Oil: {version} installed {installed}")
    except (OSError, AttributeError):
        lines.append("Valve Oil: not installed")
    try:
        for name, part in json.loads(paths.firmware_manifest_path.read_text(encoding="utf-8")).items():
            fields = (name, part.get("version"), part.get("file"))
            if all(isinstance(field, str) and _PLAIN.match(field) for field in fields):
                lines.append("{}: {} ({})".format(*fields))
    except (OSError, ValueError, AttributeError) as exc:
        lines.append("Firmware manifest: unreadable " + type(exc).__name__)
    try:
        box_cfg = (paths.config_root / "box.cfg").read_text(encoding="utf-8", errors="replace")
        lines.append("USB Boxes in box.cfg: " + str(len(re.findall(r"^\[mcu mcu_box", box_cfg, re.M))))
    except OSError:
        lines.append("USB Boxes in box.cfg: no box.cfg")
    release = os.uname().release
    lines.append("Kernel: " + (release if re.match(r"^[A-Za-z0-9._+-]{1,80}$", release) else "unrecognized"))
    enrolled = paths.printer_data_root / ".tltg_optimized_auto_update_enrolled"
    lines.append("Auto-updates: " + ("on" if enrolled.is_file() else "off"))
    try:
        lines.append("zram: " + ("on" if "zram" in Path("/proc/swaps").read_text() else "off"))
    except OSError:
        lines.append("zram: unknown")
    lines.append("THR fix backups: " + ("yes" if (paths.printer_data_root / THR_BACKUP_DIRECTORY).is_dir() else "no"))
    return "\n".join(lines)


def issue_link(*, printer_name: str, firmware: str, box_count: int, details: str, output: str,
               report: dict | None, report_path: str | None) -> tuple[str, bool]:
    """A link to the printer-support form with its fields filled in by ID, and whether a field had to be shortened."""
    report_text = json.dumps(report, separators=(",", ":")) if report else ""
    fields = {
        "template": ISSUE_TEMPLATE,
        "title": f"[Support]: {printer_name} {firmware}",
        "printer": printer_name,
        "firmware": firmware,
        "printer_details": details,
        "installer_output": output,
        "board_report": report_text,
    }
    if box_count:
        fields["box"] = "One USB Box" if box_count == 1 else "More than one USB Box"

    def build():
        return ISSUE_FORM + "?" + urllib.parse.urlencode(fields, quote_via=urllib.parse.quote)

    shortened = False
    for field in ("installer_output", "printer_details", "board_report"):
        if len(build()) <= MAX_ISSUE_URL:
            break
        shortened = True
        if field == "board_report":
            fields[field] = f"(too long for a link; attach {report_path or 'the report.json the installer named'})"
            continue
        text = fields[field]
        while text and len(build()) > MAX_ISSUE_URL:
            # A character takes up to nine characters of the link once percent-encoded.
            text = text[: max(0, len(text) - max(32, (len(build()) - MAX_ISSUE_URL) // 9))]
            fields[field] = text + "\n(shortened to fit in a link)"
    return build(), shortened


def with_issue_links(paths, admission: BoardAdmission, *, printer, box_count: int) -> BoardAdmission:
    if not admission.unadmitted:
        return admission
    details = printer_details(paths)
    boards = []
    for status in admission.boards:
        if status.state in UNADMITTED_STATES:
            output = (f"Valve Oil {admission.package_version} on {printer.display_name} firmware {admission.firmware}:\n"
                      + describe(status))
            report_path = (os.path.relpath(status.report, paths.printer_data_root.parent) if status.report else None)
            url, _ = issue_link(printer_name=printer.display_name, firmware=admission.firmware, box_count=box_count,
                                details=details, output=output, report=link_report(status), report_path=report_path)
            status = replace(status, issue_url=url)
        boards.append(status)
    return replace(admission, boards=tuple(boards))


def refuse_unadmitted_new_install(admission: BoardAdmission, reporter) -> None:
    """Stop a new installation, before any change, on a board this release can't admit or couldn't read."""
    if not admission.new_install or admission.admitted:
        return
    unread = [status for status in admission.boards if status.state == UNREAD]
    for status in admission.unadmitted:
        reporter.line(describe(status) + " Nothing was installed and the board was not changed.")
        if status.report:
            reporter.line(f"Its shareable report is {status.report}. Keep the .bin files beside it private.")
        reporter.line("To get it supported, open this prefilled support issue: " + (status.issue_url or ISSUE_FORM))
    for status in unread:
        reporter.line(describe(status))
    if admission.unadmitted:
        raise BoardAdmissionError("This release of Valve Oil doesn't support a board on this printer yet, so nothing "
                                  "was installed.")
    if any(status.detail == NOT_CONNECTED.format(status.label) for status in unread):
        raise BoardAdmissionError("A USB Box listed in box.cfg is not connected, so nothing was installed. Connect it, "
                                  "or remove it in the touchscreen's Box setup, then run the installer again.")
    raise BoardAdmissionError("Valve Oil reads the THR, mainboard, and USB Boxes before a new install and could not "
                              "read them all, so nothing was installed. Run the installer again once Klipper is ready "
                              "and the printer is idle.")


def _log_dir(paths) -> Path:
    return paths.printer_data_root / "logs"


def _log_report_name(status: BoardStatus) -> str:
    return LOG_REPORTS.get(status.board) or f"tltg_box_firmware_box{status.board[3:]}.json"


def box_note(status: BoardStatus, printer) -> str | None:
    target = printer.target_firmware.board_version("box")
    if status.kind == "box" and status.state == UNRECOGNIZED_BOOTLOADER and status.version not in (None, target):
        return (f"QIDI firmware {printer.target_firmware.version} carries Box firmware {target}; update this Box from "
                "the touchscreen's firmware page.")
    return None


def publish_warnings(paths, admission: BoardAdmission, *, printer) -> None:
    """Leave each unadmitted board's shareable report, with its issue link, in Klipper's log folder for the printer
    warnings, and remove reports of boards that are admitted now or no longer configured."""
    logs = _log_dir(paths)
    try:
        names = set(os.listdir(logs))
    except OSError:
        return
    wanted = {}
    for status in admission.boards:
        if status.state in UNADMITTED_STATES:
            report = _read_report(status) or {}
            payload = {**report, "board": status.label, "issue_url": status.issue_url}
            note = box_note(status, printer)
            if note:
                payload["note"] = note
            wanted[_log_report_name(status)] = payload
    # Kept: reports of boards not read this run, and of Boxes still waiting for the Box flow, which removes its own
    # report once the Box has the Valve Oil firmware.
    keep = {_log_report_name(status) for status in admission.boards
            if status.state == UNREAD or (status.kind == "box" and status.state == NEEDS_IMAGE)}
    for name in names:
        if (name in LOG_REPORTS.values() or BOX_LOG_REPORT.match(name)) and name not in wanted and name not in keep:
            try:
                (logs / name).unlink()
            except OSError:
                pass
    for name, payload in wanted.items():
        target = logs / name
        try:
            ensure_runtime_path_has_no_symlink_components(printer_data_root=paths.printer_data_root, target=target)
            atomic_write_text(target, json.dumps(payload, indent=2) + "\n", mode=0o644)
        except (OSError, InstallerError):
            pass


def record_board_admission(paths, admission: BoardAdmission, *, printer) -> None:
    """Keep the admission for the update check and the status service, and publish its printer warnings."""
    target = admission_path(paths)
    ensure_runtime_path_has_no_symlink_components(printer_data_root=paths.printer_data_root, target=target)
    atomic_write_text(target, admission.to_json(), mode=0o644)
    publish_warnings(paths, admission, printer=printer)
    _settle_shaper_marker(paths)


# ---- delivery ------------------------------------------------------------------------------------------------------


def _saved_shaper(paths) -> dict[str, str]:
    """The input shaper frequencies printer.cfg's SAVE_CONFIG block holds."""
    try:
        text = (paths.config_root / "printer.cfg").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values, section = {}, None
    for line in text.splitlines():
        if not line.startswith("#*#"):
            continue
        body = line[3:].strip()
        if body.startswith("["):
            section = body.strip("[]").strip()
            continue
        if section == "input_shaper" and "=" in body:
            key, value = (part.strip() for part in body.split("=", 1))
            if key in ("shaper_freq_x", "shaper_freq_y"):
                values[key] = value
    return values


def _mark_thr_flashed(paths) -> None:
    target = _log_dir(paths) / THR_SHAPER_MARKER
    try:
        ensure_runtime_path_has_no_symlink_components(printer_data_root=paths.printer_data_root, target=target)
        target.parent.mkdir(exist_ok=True)
        atomic_write_text(target, json.dumps(_saved_shaper(paths), indent=2) + "\n", mode=0o644)
    except (OSError, InstallerError):
        pass


def _settle_shaper_marker(paths) -> None:
    """Drop the THR flash marker once printer.cfg holds another calibration."""
    target = _log_dir(paths) / THR_SHAPER_MARKER
    try:
        recorded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    saved = _saved_shaper(paths)
    if saved and saved != recorded:
        try:
            atomic_delete(target)
        except OSError:
            pass


def waiting_detail(printer) -> str:
    """Why a board that needs its pinned image is left as it is for now."""
    return f"will be updated after the QIDI firmware update to {printer.target_firmware.version}"


def deliverable_now(status: BoardStatus, printer, firmware: str, bundle_root: Path) -> bool:
    """Whether a board that needs its pinned image can get it with QIDI firmware `firmware` installed. A board on QIDI's
    image of another version gets it only when the manifest knows that image (a predecessor, by its hash) and the
    printer runs the target firmware."""
    from scripts.build_box_firmware import board_spec, predecessor_hashes

    from_predecessor = status.application == QIDI_APPLICATION
    if from_predecessor and status.application_sha256 not in predecessor_hashes(
            board_spec(load_board_manifest(bundle_root, status.kind))):
        return False
    return deliverable_on(printer, status.kind, firmware, bundle_root, from_predecessor=from_predecessor)


def deliver_board_firmware(
    *, paths, reporter, manifest, printer, admission: BoardAdmission, dry_run=False, environ=None, input_stream=None,
    services_stopped=False, run=None, urlopen=urllib.request.urlopen, sleep=time.sleep, clock=time.monotonic,
    resolve_usb=usb_path_for_serial, usb_state=None,
) -> BoardDelivery:
    """Bring each board that needs it to the image pinned for the printer's target firmware, and record the result.

    Installs call this after the configuration is installed; a firmware update calls it with services_stopped=True after
    stopping Klipper, Moonraker, and the touchscreen itself. A board already on its pinned image is not touched. A stop
    before any write leaves the board as it was and is reported in its result; a write that can't be verified raises
    with that board's rescue record kept, so every installer operation stays blocked until rescue.
    """
    from .firmware import detect_firmware_version_best_effort

    env = {} if environ is None else environ
    seams = {"run": run, "urlopen": urlopen, "sleep": sleep, "clock": clock}
    # A firmware update calls this after installing QIDI's package, so the installed firmware is read again.
    firmware = detect_firmware_version_best_effort(paths.firmware_manifest_path) or admission.firmware
    results: list[BoardResult] = []
    statuses = {status.board: status for status in admission.boards}
    if dry_run:
        for status in admission.boards:
            if status.state == CURRENT:
                action = "is on its pinned image; nothing to do"
            elif status.state == UNREAD:
                action = "would be read, then flashed if it needs its pinned image"
            elif status.state == NEEDS_IMAGE and (admission.delivery_admitted or status.kind == "box"):
                action = "would get its pinned image"
            else:
                action = "would be left as it is"
            reporter.line(f"Board firmware dry-run: {status.label} {action}. No board or service was touched.")
            results.append(BoardResult(status.board, "dry_run", action))
        return BoardDelivery(tuple(results), admission)

    clear_stale_holds(paths, manifest.package.version)
    record_board_admission(paths, admission, printer=printer)
    if not admission.delivery_admitted:
        reporter.line("This release doesn't support every board on this printer yet, so Valve Oil leaves QIDI's "
                      "firmware updates to the touchscreen and doesn't change the THR or mainboard firmware.")
        for status in admission.boards:
            if status.state in UNADMITTED_STATES:
                reporter.line(describe(status) + " To get it supported, open: " + (status.issue_url or ISSUE_FORM))

    def blocked(marker: str) -> bool:
        return (paths.printer_data_root / marker).exists()

    for board, marker in (("thr", THR_RECOVERY_MARKER), ("mcu", MAINBOARD_RECOVERY_MARKER)):
        status = statuses[board]
        pinned = pinned_board_image(paths.bundle_root, printer, board)
        if status.state == CURRENT:
            results.append(BoardResult(board, "current"))
            continue
        if status.state != NEEDS_IMAGE:
            results.append(BoardResult(board, "not_admitted" if status.state in UNADMITTED_STATES else "skipped",
                                       describe(status)))
            continue
        if not admission.delivery_admitted:
            results.append(BoardResult(board, "skipped", "another board is not supported yet"))
            continue
        if not deliverable_now(status, printer, firmware, paths.bundle_root):
            detail = waiting_detail(printer)
            reporter.line(f"{status.label}: {detail}. Its firmware is not changed now.")
            results.append(BoardResult(board, "waiting", detail))
            continue
        try:
            if board == "thr":
                outcome = flash_thr_firmware(paths=paths, reporter=reporter, input_stream=input_stream, environ=env,
                                             pinned=pinned, services_stopped=services_stopped, **seams)
            else:
                outcomes: dict = {}
                run_mainboard_firmware(paths=paths, reporter=reporter, action="install", input_stream=input_stream,
                                       environ=env, confirmed=True, services_stopped=services_stopped, pinned=pinned,
                                       outcomes=outcomes, resolve_usb=resolve_usb, usb_state=usb_state, **seams)
                outcome = outcomes.get("mcu", ("skipped", ""))[0]
        except InstallerError as exc:
            if blocked(marker):
                raise
            reporter.line(f"{status.label} firmware not updated: {exc.message}")
            results.append(BoardResult(board, "skipped", exc.message))
            continue
        results.append(BoardResult(board, outcome, waiting_detail(printer) if outcome == "waiting" else ""))
        if outcome in ("current", "flashed"):
            statuses[board] = replace(status, state=CURRENT, application=pinned.build, version=pinned.version)
        if board == "thr" and outcome == THR_FLASHED:
            _mark_thr_flashed(paths)

    boxes = [name for name, status in statuses.items() if status.kind == "box" and status.state == NEEDS_IMAGE
             and (status.application != QIDI_APPLICATION or deliverable_now(status, printer, firmware, paths.bundle_root))]
    for name, status in statuses.items():
        if status.kind == "box" and name not in boxes:
            if status.state in UNADMITTED_STATES and admission.delivery_admitted:
                # Left alone, read this run or found so by an earlier read; the printer warning carries the same.
                reporter.line(f"{describe(status)} It keeps its firmware; its report is {_log_report_name(status)} in "
                              "Fluidd's Logs. To get it supported, open: " + (status.issue_url or ISSUE_FORM))
            results.append(BoardResult(name, "held" if status.state == HELD else
                                       "current" if status.state == CURRENT else
                                       "not_admitted" if status.state in UNADMITTED_STATES else
                                       "waiting" if status.state == NEEDS_IMAGE else "skipped",
                                       "" if status.state in (HELD, CURRENT) else
                                       waiting_detail(printer) if status.state == NEEDS_IMAGE else describe(status)))
    if boxes:
        outcomes = {}
        # Stops before any write are reported there and leave the install successful; an unverified write raises.
        flash_box_firmware_after_install(
            paths=paths, reporter=reporter, input_stream=input_stream, environ=env,
            boxes=[int(name[3:]) for name in boxes], services_stopped=services_stopped,
            firmware=pinned_board_image(paths.bundle_root, printer, "box").firmware, outcomes=outcomes,
            resolve_usb=resolve_usb, usb_state=usb_state, **seams)
        configured = {box.name: f"box{box.number}" for box in discover_boxes(paths, resolve_usb=resolve_usb)}
        for name in boxes:
            outcome, detail = next(((kind, text) for box_name, (kind, text) in outcomes.items()
                                    if configured.get(box_name) == name), ("skipped", "Box firmware not updated"))
            results.append(BoardResult(name, outcome, waiting_detail(printer) if outcome == "waiting" else detail))
            if outcome in ("current", "flashed"):
                pinned = pinned_board_image(paths.bundle_root, printer, "box")
                statuses[name] = replace(statuses[name], state=CURRENT, application=pinned.build,
                                         version=pinned.version)

    # Every board has a result: the ones left for the QIDI firmware update say so, and no other does.
    waiting = {result.board: result.detail for result in results if result.outcome == "waiting"}
    for name, status in statuses.items():
        if name in waiting or status.waiting_for_firmware_update:
            statuses[name] = replace(status, waiting_for_firmware_update=name in waiting,
                                     detail=waiting.get(name))
    admission = replace(admission, boards=tuple(statuses[status.board] for status in admission.boards))
    record_board_admission(paths, admission, printer=printer)
    return BoardDelivery(tuple(results), admission)


def board_preflight(*, paths, reporter, manifest, dry_run=False, environ=None, input_stream=None, **seams):
    """Read and admit the boards before an install changes anything; refuse a new installation with a board this
    release doesn't admit. Returns (printer, admission)."""
    from .firmware import detect_supported_firmware

    printer, firmware = detect_supported_firmware(paths.firmware_manifest_path, manifest)
    new_install = not existing_installation(paths, manifest)
    admission = read_boards(paths=paths, reporter=reporter, manifest=manifest, printer=printer, firmware=firmware,
                            new_install=new_install, dry_run=dry_run, environ=environ, input_stream=input_stream,
                            **seams)
    if dry_run:
        if new_install:
            reporter.line("Board admission dry-run: a new install reads the THR, mainboard, and USB Boxes first and "
                          "is refused if this release doesn't support one. No board or service was touched.")
        return printer, admission
    refuse_unadmitted_new_install(admission, reporter)
    return printer, admission
