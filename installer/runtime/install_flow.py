"""Every question first, one summary, then a run that needs no input.

An install has two steps that every front end drives:

- plan_install looks at the printer and returns an Overview: what it found (printer, QIDI firmware, the installed
  Valve Oil, Boxes, boards that get firmware) and the questions that apply. It changes nothing.
- the run (runner.run_install, then the board firmware) takes the Answers and asks nothing: Box support, tool-slot
  mappings, restarting Klipper, and the reboot after host tuning are not questions.

The terminal installer (ask_in_terminal) and the touchscreen (usb_install.py) both collect the answers and show the
summary before the run starts. A QIDI firmware update that the run starts installs Valve Oil in a process of its own;
the answers wait for it in a file (save_pending_answers) that the install it runs reads and removes.
"""
from __future__ import annotations

import json
import subprocess
import urllib.request
from dataclasses import dataclass, field

from . import messages, progress
from .auto_update import auto_update_enrolled
from .errors import OperationCancelled
from .fs_atomic import atomic_delete, atomic_write_text
from .interaction import prompt_yes

AUTO_UPDATES = "auto_updates"
PENDING_ANSWERS = ".vo_install_answers.json"  # in printer_data, for the install a firmware update runs

# The two kinds of run a front end offers on a printer: install or update with the settings it has, or change them.
INSTALL = "install"
CHANGE_SETTINGS = "change_settings"


@dataclass(frozen=True)
class Question:
    id: str
    title: str  # the touchscreen's heading and the terminal's question
    text: str  # what it means
    yes: str  # the answer that turns the feature on
    no: str
    instruction: str  # how to answer in the terminal


QUESTIONS = {
    AUTO_UPDATES: Question(
        AUTO_UPDATES,
        "Keep Valve Oil updated?",
        "The printer checks for a new release every hour and installs it by itself. It never updates during a "
        "print, while a print is paused, or while a heater is on. You can turn this off later.",
        "Yes, update automatically",
        "No, I'll update by hand",
        messages.AUTO_UPDATE_PROMPT_INSTRUCTION,
    ),
}


@dataclass(frozen=True)
class Overview:
    """What an install found before it changes anything, and what it asks."""

    printer_id: str
    printer_name: str
    firmware: str  # the installed QIDI firmware
    target_firmware: str  # the QIDI firmware this release brings the printer to
    firmware_update: bool  # QIDI's firmware update runs first
    package_version: str
    installed_version: str | None  # the Valve Oil installed now, None for a new install
    new_install: bool
    auto_updates_on: bool
    ai_detection_on: bool  # QIDI's AI detection service runs now; the install turns it off
    box_count: int | None  # QIDI Boxes in the saved variables; None when Klipper couldn't say
    box_support_on: bool | None
    legacy_manual_install: bool  # hand-copied configs that the install resets to QIDI's first
    boards: tuple[str, ...] = ()  # boards that get firmware: "Toolhead", "mainboard", "Box 1", ...
    questions: tuple[Question, ...] = ()
    community_supported: bool = False  # releases may ship untested on this printer (PrinterSpec.support)

    @property
    def turns_box_support_on(self) -> bool:
        return self.new_install and bool(self.box_count) and self.box_support_on is False


@dataclass(frozen=True)
class Answers:
    auto_updates: bool | None = None  # None leaves automatic updates as they are
    asked: tuple[str, ...] = field(default=())

    def to_json(self) -> dict:
        return {"auto_updates": self.auto_updates}

    @classmethod
    def from_json(cls, data) -> "Answers":
        if not isinstance(data, dict):
            return cls()
        value = data.get("auto_updates")
        return cls(auto_updates=value if isinstance(value, bool) else None)


def plan_install(paths, manifest, *, environ, admission=None, kind: str = INSTALL, run=subprocess.run,
                 urlopen=urllib.request.urlopen) -> Overview:
    """Look at the printer and return what the install found and asks. Changes nothing.

    kind CHANGE_SETTINGS asks about automatic updates whatever they are now; an install asks only while they are off.
    """
    from .board_firmware import NEEDS_IMAGE, existing_installation
    from .firmware import detect_supported_firmware
    from .legacy_manual_install import detect_legacy_manual_install
    from .state_file import load_installed_state
    from .system_optimizations import ai_detection_running

    printer, firmware = detect_supported_firmware(paths.firmware_manifest_path, manifest)
    target = printer.target_firmware
    installed_version = None
    state_path = paths.printer_data_root / manifest.state_file
    if state_path.exists():
        try:
            installed_version = load_installed_state(state_path).package_version
        except Exception:  # the run's own checks say what is wrong with it
            installed_version = None
    new_install = not existing_installation(paths, manifest)
    enrolled = auto_update_enrolled(paths)
    ai_on = ai_detection_running(manifest, paths=paths, environ=environ, run=run)
    box_count, box_on = _box_state(paths, urlopen=urlopen)
    boards = ()
    if admission is not None:
        boards = tuple(_board_name(status) for status in admission.boards if status.state == NEEDS_IMAGE
                       and (admission.delivery_admitted or status.kind == "box"))
    questions = []
    if kind == CHANGE_SETTINGS or not enrolled:
        questions.append(QUESTIONS[AUTO_UPDATES])
    return Overview(
        printer_id=printer.id,
        printer_name=printer.display_name,
        firmware=firmware,
        target_firmware=target.version,
        firmware_update=target.can_update_from(firmware),
        package_version=manifest.package.version,
        installed_version=installed_version,
        new_install=new_install,
        auto_updates_on=enrolled,
        ai_detection_on=ai_on,
        box_count=box_count,
        box_support_on=box_on,
        legacy_manual_install=detect_legacy_manual_install(paths=paths, manifest=manifest) is not None,
        boards=boards,
        questions=tuple(questions),
        community_supported=printer.community_supported,
    )


def _box_state(paths, *, urlopen) -> tuple[int | None, bool | None]:
    from .box_enablement import SavedVariablePersistenceError, read_live_saved_variables

    try:
        variables = read_live_saved_variables(paths, urlopen=urlopen)
    except (SavedVariablePersistenceError, OSError):
        return None, None
    try:
        count = int(str(variables.get("box_count", "0")).strip() or 0)
    except ValueError:
        count = 0
    try:
        enabled = int(str(variables.get("enable_box", "0")).strip() or 0) != 0
    except ValueError:
        enabled = False
    return count, enabled


def _board_name(status) -> str:
    return {"thr": "Toolhead", "mcu": "mainboard"}.get(status.board) or status.label


def summary_rows(overview: Overview, answers: Answers) -> list[tuple[str, str]]:
    """The summary's rows: every answer and what the run will do, in the touchscreen's words."""
    rows = [("Valve Oil", f"{overview.package_version} on {overview.printer_name}")]
    if overview.firmware_update:
        rows.append(("QIDI firmware", f"{overview.firmware} updated to {overview.target_firmware} first"))
    rows.append(("Automatic updates", "On" if _auto_updates_after(overview, answers) else "Off"))
    if overview.ai_detection_on:
        rows.append(("QIDI AI detection", "Turned off"))
    if overview.box_count:
        support = ("Box support turned on" if overview.turns_box_support_on else
                   "Box support on" if overview.box_support_on else "Box support off")
        rows.append(("QIDI Box", f"{overview.box_count} found, {support}"))
    if overview.boards:
        rows.append(("Board firmware", ", ".join(overview.boards)))
    if overview.legacy_manual_install:
        rows.append(("Hand-copied configs", "Backed up, then reset to QIDI's before the install"))
    return rows


def _auto_updates_after(overview: Overview, answers: Answers) -> bool:
    return overview.auto_updates_on if answers.auto_updates is None else answers.auto_updates


def ask_in_terminal(overview: Overview, *, reporter, input_stream) -> Answers:
    """Ask the questions, show the summary, and return the answers once the operator confirms it.

    Quitting at the summary raises OperationCancelled before anything has changed."""
    if overview.community_supported:
        reporter.line(messages.COMMUNITY_PRINTER_NOTICE.format(printer=overview.printer_name))
        reporter.line("")
    values: dict[str, bool] = {}
    for question in overview.questions:
        values[question.id] = prompt_yes(reporter=reporter, input_stream=input_stream, question=question.title,
                                         instruction=question.instruction)
        reporter.debug(event="install.answer", question=question.id, answer=values[question.id])
    answers = Answers(auto_updates=values.get(AUTO_UPDATES), asked=tuple(values))
    reporter.line("")
    reporter.line(messages.INSTALL_SUMMARY_HEADER)
    for label, value in summary_rows(overview, answers):
        reporter.line(f"  {label}: {value}")
    if overview.community_supported:
        reporter.line(f"  Printer support: {messages.COMMUNITY_PRINTER_SUMMARY}")
    if not prompt_yes(reporter=reporter, input_stream=input_stream, question=messages.INSTALL_SUMMARY_PROMPT,
                      instruction=messages.INSTALL_SUMMARY_PROMPT_INSTRUCTION):
        reporter.debug(event="install.summary.declined")
        reporter.line(messages.INSTALL_SUMMARY_DECLINED)
        raise OperationCancelled(messages.INSTALL_SUMMARY_DECLINED)
    reporter.debug(event="install.summary.confirmed")
    return answers


def install_steps(overview: Overview | None) -> list[str]:
    """The steps an install expects, in the order it runs them, for its progress (progress.py)."""
    steps = [progress.CHECK]
    if overview is not None and overview.firmware_update:
        steps.append(progress.QIDI_FIRMWARE.format(target=overview.target_firmware))
    steps += [progress.BACKUP, progress.CONFIGURATION, progress.SYSTEM_CHANGES, progress.RESTART_KLIPPER]
    boards = overview.boards if overview is not None else ()
    if any(board in ("Toolhead", "mainboard") for board in boards):
        steps.append(progress.BOARDS)
    boxes = [int(board.split()[-1]) for board in boards if board.startswith("Box ")]
    if boxes:
        steps.append(progress.box_firmware(boxes))
    steps.append(progress.FINISH)
    return steps


def save_pending_answers(paths, answers: Answers) -> None:
    """Keep the answers for the install a QIDI firmware update runs in a process of its own."""
    atomic_write_text(paths.printer_data_root / PENDING_ANSWERS, json.dumps(answers.to_json()) + "\n", mode=0o600)


def load_pending_answers(paths) -> Answers:
    path = paths.printer_data_root / PENDING_ANSWERS
    try:
        return Answers.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return Answers()


def clear_pending_answers(paths) -> None:
    path = paths.printer_data_root / PENDING_ANSWERS
    if path.exists() or path.is_symlink():
        atomic_delete(path)
