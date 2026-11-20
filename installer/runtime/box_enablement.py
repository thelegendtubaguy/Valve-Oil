from __future__ import annotations

import json
import re
import shlex
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from . import klipper_cfg, messages, safety
from .errors import ActivePrintError, InstallerError, PrinterStateError
from .fs_atomic import atomic_delete, atomic_write_text
from .firmware import detect_firmware, supported_printer
from .manifest import load_manifest
from .models import RuntimePaths
from .process_restart import ProcessRestartError, read_printer_info

VALUE_T_RE = re.compile(r"^value_t(?P<tool>\d+)$")
SAVED_VARIABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_SAVED_VARIABLE_VALUE_LENGTH = 96
RUNTIME_STATE_FILE = "config/tltg_optimized_runtime_state.json"
SAVED_VARIABLE_VERIFY_MARKER = ".tltg_optimized_saved_variables_verify_pending"
UrlOpenFn = Callable[..., object]
# QIDI's start-up macro (INIT_GLOBALS) runs its Box check and RFID read 5 s after Klipper is ready and holds Klipper's
# G-code queue until they finish, over a minute with one Box and spools loaded (issue 331). A SAVE_VARIABLE sent
# meanwhile stays queued in Moonraker and runs afterwards, so a reply that is slow to come is waited for in the saved
# variables.
SAVE_VARIABLE_REPLY_SECONDS = 10
QUEUED_SAVE_SECONDS = 600
QUEUED_SAVE_POLL_SECONDS = 2


class SavedVariablePersistenceError(InstallerError):
    """A live Klipper saved-variable operation could not be verified."""


def saved_variable_verify_marker_path(paths: RuntimePaths) -> Path:
    return paths.printer_data_root / SAVED_VARIABLE_VERIFY_MARKER


def write_saved_variable_verify_marker(paths: RuntimePaths, values: Mapping[str, str]) -> None:
    if not values:
        return
    _validate_saved_values(values)
    atomic_write_text(
        saved_variable_verify_marker_path(paths),
        json.dumps({"schema_version": 1, "values": dict(sorted(values.items()))}, sort_keys=True) + "\n",
        mode=0o600,
        force_mode=True,
    )


def verify_pending_saved_variable_expectations(
    paths: RuntimePaths,
    *,
    clear: bool = True,
    urlopen: UrlOpenFn = urllib.request.urlopen,
    earlier_run_reporter=None,
) -> bool:
    """Check that the values an install saved are still Klipper's. In the run that saved them a mismatch fails it.

    earlier_run_reporter: the check is of an earlier run's values. Klipper's values may have changed since, by the
    operator or the touchscreen, and that run already reported a loss, so a mismatch or an unreadable marker is
    reported here and the marker dropped; keeping it would stop every later install and update. Klipper not
    answering still fails the check."""
    path = saved_variable_verify_marker_path(paths)
    if not path.exists():
        return False
    if paths.restart_marker_path.exists():
        return False
    try:
        values = _read_saved_variable_marker(path)
    except SavedVariablePersistenceError as exc:
        return _drop_earlier_run_marker(path, exc, reporter=earlier_run_reporter, clear=clear)
    live = read_live_saved_variables(paths, urlopen=urlopen)
    try:
        _check_saved_values(live, values)
    except SavedVariablePersistenceError as exc:
        return _drop_earlier_run_marker(path, exc, reporter=earlier_run_reporter, clear=clear)
    if clear:
        atomic_delete(path)
    return True


def _read_saved_variable_marker(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        values = raw["values"] if isinstance(raw, dict) and raw.get("schema_version") == 1 else None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SavedVariablePersistenceError("Saved-variable verification marker is invalid.") from exc
    if not isinstance(values, dict) or not values or any(
        not isinstance(name, str) or not isinstance(value, str)
        for name, value in values.items()
    ):
        raise SavedVariablePersistenceError("Saved-variable verification marker is invalid.")
    _validate_saved_values(values)
    return values


def _drop_earlier_run_marker(path: Path, exc: SavedVariablePersistenceError, *, reporter, clear: bool) -> bool:
    if reporter is None:
        raise exc
    reporter.line(messages.SAVED_VARIABLES_CHANGED_SINCE_INSTALL)
    if hasattr(reporter, "debug"):
        reporter.debug(event="saved_variable.marker.dropped", message=exc.message)
    if clear:
        atomic_delete(path)
    return False


@dataclass(frozen=True)
class BoxEnablementOpportunity:
    saved_variables_path: Path
    box_count: int
    enable_box: int


@dataclass(frozen=True)
class RequiredToolSlotGap:
    tool: int
    variable: str
    current: str
    expected: str


@dataclass(frozen=True)
class RequiredToolSlotOpportunity:
    saved_variables_path: Path
    box_count: int
    gaps: tuple[RequiredToolSlotGap, ...]


def maybe_initialize_filament_retention(
    *,
    paths: RuntimePaths,
    reporter,
    journal=None,
    written: dict[str, str] | None = None,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    variables = with_earlier_names(read_live_saved_variables(paths, urlopen=urlopen))
    if "vo_keep_loaded_between_prints" in variables:
        reporter.debug(event="filament_retention_default.preserved")
        return False
    saved = save_live_variables(
        paths,
        {"vo_keep_loaded_between_prints": "1"},
        missing_only=frozenset({"vo_keep_loaded_between_prints"}),
        reporter=reporter,
        urlopen=urlopen,
    )
    if written is not None:
        written.update(saved)
    if not saved:
        reporter.debug(event="filament_retention_default.preserved")
        return False
    reporter.line(messages.FILAMENT_RETENTION_DEFAULT_ENABLED)
    return True


# Release defaults for tltg_box_feed_motion, saved as a set only when no motion
# setting exists. A setting saved as None (QIDI's value) counts as present.
# The Max 4 approaches the toolhead sensor at the Plus 5's 25 mm/s.
BOX_MOTION_DEFAULTS = {
    "vo_box_load_fast_length": "950",
    "vo_box_load_fast_speed_1": "85",
    "vo_box_load_fast_speed_2": "65",
    "vo_box_load_approach_speed": "25",
    "vo_box_unload_speed_1": "65",
    "vo_box_unload_speed_2": "85",
    "vo_box_unload_extruder_length": "55",
}
# The Plus 5's Box code loads with one fast move. QIDI's 720 mm stopped about
# 125 mm short of the toolhead sensor, leaving the rest to its 25 mm/s fallback.
PLUS5_BOX_MOTION_DEFAULTS = {
    "vo_box_load_fast_length": "850",
}
# No Smart 5 Box load has been traced yet, so it keeps QIDI's values.
BOX_MOTION_DEFAULTS_BY_PRINTER = {
    "max4": BOX_MOTION_DEFAULTS,
    "plus5": PLUS5_BOX_MOTION_DEFAULTS,
    "smart5": {},
}
# Settings earlier releases saved as defaults, in groups. Update moves a group
# the operator left at those values to the current defaults; a group with any
# setting changed is the operator's.
PREVIOUS_BOX_MOTION_DEFAULTS = {
    "max4": (
        {"vo_box_load_fast_speed_1": "120", "vo_box_load_fast_speed_2": "100"},
        {"vo_box_unload_speed_1": "115", "vo_box_unload_speed_2": "120"},
        {"vo_box_unload_extruder_length": "50"},
        {"vo_box_load_approach_speed": "15"},
    ),
    "plus5": (),
    "smart5": (),
}
BOX_MOTION_SETTINGS = frozenset(BOX_MOTION_DEFAULTS) | {
    "vo_box_load_accel",
    "vo_box_unload_accel",
    "vo_box_load_fast_speed",
    "vo_box_unload_speed",
    "vo_box_hub_speed",
    "vo_box_park_speed",
}


def _box_motion_printer(paths: RuntimePaths) -> str:
    """The detected printer's id; the Max 4 when it can't be read."""
    try:
        manifest = load_manifest(paths.installer_root / "package.yaml")
        printer = supported_printer(manifest, detect_firmware(paths.firmware_manifest_path))
    except Exception:
        return "max4"
    return printer.id if printer is not None and printer.id in BOX_MOTION_DEFAULTS_BY_PRINTER else "max4"


def maybe_initialize_box_motion(
    *,
    paths: RuntimePaths,
    reporter,
    journal=None,
    written: dict[str, str] | None = None,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    printer = _box_motion_printer(paths)
    if with_earlier_names(read_live_saved_variable_names(paths, urlopen=urlopen)) & BOX_MOTION_SETTINGS:
        return _update_previous_box_motion_defaults(
            paths=paths, printer=printer, reporter=reporter, written=written, urlopen=urlopen
        )
    defaults = BOX_MOTION_DEFAULTS_BY_PRINTER[printer]
    saved = save_live_variables(
        paths,
        defaults,
        missing_only=frozenset(defaults),
        reporter=reporter,
        urlopen=urlopen,
    )
    if written is not None:
        written.update(saved)
    if not saved:
        reporter.debug(event="box_motion_defaults.preserved")
        return False
    reporter.line(messages.BOX_MOTION_DEFAULTS_SAVED)
    return True


def _update_previous_box_motion_defaults(*, paths, printer, reporter, written, urlopen) -> bool:
    variables = with_earlier_names(read_live_saved_variables(paths, urlopen=urlopen))
    defaults = BOX_MOTION_DEFAULTS_BY_PRINTER[printer]
    updated = {
        name: defaults[name]
        for group in PREVIOUS_BOX_MOTION_DEFAULTS[printer]
        if all(variables.get(name) == value for name, value in group.items())
        for name in group
    }
    saved = save_live_variables(paths, updated, reporter=reporter, urlopen=urlopen)
    if written is not None:
        written.update(saved)
    if not saved:
        reporter.debug(event="box_motion_defaults.preserved")
        return False
    reporter.line(messages.BOX_MOTION_PREVIOUS_DEFAULTS_UPDATED)
    return True


def maybe_enable_box(
    *,
    paths: RuntimePaths,
    reporter,
    new_install: bool,
    journal=None,
    written: dict[str, str] | None = None,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    """A new install that finds QIDI Boxes turns Box support on without asking; later runs leave it as the operator
    set it. enable_box is QIDI's per-print "use the Box" flag, which every print start rewrites, so setting it on every
    run would undo an external-spool choice made for prints started from Fluidd or a slicer."""
    if not new_install:
        reporter.debug(event="box_enablement.skipped", reason="existing_install")
        return False
    opportunity = detect_box_enablement_opportunity(paths, urlopen=urlopen)
    if opportunity is None:
        reporter.debug(event="box_enablement.skipped")
        return False
    reporter.debug(
        event="box_enablement.detected",
        box_count=opportunity.box_count,
        enable_box=opportunity.enable_box,
        saved_variables_path=opportunity.saved_variables_path,
    )
    saved = save_live_variables(paths, {"enable_box": "1"}, reporter=reporter, urlopen=urlopen)
    if written is not None:
        written.update(saved)
    reporter.line(messages.ENABLE_QIDI_BOX_ENABLED)
    return True

def maybe_write_required_tool_slot_variables(
    *,
    paths: RuntimePaths,
    reporter,
    journal=None,
    written: dict[str, str] | None = None,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    opportunity = detect_required_tool_slot_opportunity(paths, urlopen=urlopen)
    if opportunity is None:
        reporter.debug(event="required_tool_slots.skipped")
        return False
    reporter.debug(
        event="required_tool_slots.detected",
        box_count=opportunity.box_count,
        gaps=len(opportunity.gaps),
        saved_variables_path=opportunity.saved_variables_path,
    )
    saved = save_live_variables(
        paths,
        {gap.variable: _quote_saved_string(gap.expected) for gap in opportunity.gaps},
        missing_or_empty=frozenset(gap.variable for gap in opportunity.gaps),
        reporter=reporter,
        urlopen=urlopen,
    )
    if written is not None:
        written.update(saved)
    if not saved:
        reporter.debug(event="required_tool_slots.preserved_after_live_recheck")
        return False
    reporter.line(
        messages.REQUIRED_TOOL_SLOT_MAPPINGS_WRITTEN.format(count=len(saved))
    )
    return True


def maybe_repair_saved_variables(
    *,
    paths: RuntimePaths,
    reporter,
    written: dict[str, str] | None = None,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    """Repair unattended defaults only after the caller has confirmed idleness."""
    changed = maybe_initialize_filament_retention(
        paths=paths, reporter=reporter, written=written, urlopen=urlopen
    )
    changed = maybe_initialize_box_motion(
        paths=paths, reporter=reporter, written=written, urlopen=urlopen
    ) or changed
    return maybe_write_required_tool_slot_variables(
        paths=paths, reporter=reporter, written=written, urlopen=urlopen
    ) or changed


def maybe_reconcile_tool_slots_after_box_count_change(
    *,
    paths: RuntimePaths,
    reporter,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> bool:
    try:
        safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen)
        variables = read_live_saved_variables(paths, urlopen=urlopen)
    except ActivePrintError:
        reporter.line(messages.REQUIRED_TOOL_SLOT_RECONCILE_SKIPPED_ACTIVE_PRINT)
        return False
    except (PrinterStateError, SavedVariablePersistenceError):
        reporter.line(messages.REQUIRED_TOOL_SLOT_RECONCILE_SKIPPED_UNKNOWN_STATE)
        return False

    if not _box_stack_available(paths, urlopen=urlopen):
        reporter.debug(event="required_tool_slots.reconcile_skipped", reason="not_configured")
        return False
    box_count = _resolve_live_int(variables, "box_count", default=0)
    gaps = collect_required_tool_slot_gaps_from_variables(variables)
    # A matching count is only an observation, never proof that an older release
    # initialized every required mapping.
    state = _read_runtime_state(paths)
    observed_count = _coerce_optional_int(state.get("last_observed_box_count"))
    if not gaps and observed_count == box_count:
        reporter.debug(event="required_tool_slots.reconcile_observed", box_count=box_count, gaps=0)
        return False
    if gaps:
        saved = save_live_variables(
            paths,
            {gap.variable: _quote_saved_string(gap.expected) for gap in gaps},
            missing_or_empty=frozenset(gap.variable for gap in gaps),
            reporter=reporter,
            urlopen=urlopen,
        )
        if not saved:
            reporter.debug(event="required_tool_slots.reconcile_preserved_after_live_recheck")
            return False
        reporter.line(
            messages.REQUIRED_TOOL_SLOT_RECONCILED.format(box_count=box_count, count=len(saved))
        )
    else:
        reporter.debug(event="required_tool_slots.reconcile_observed", box_count=box_count, gaps=0)
    _write_runtime_state(paths, {**state, "last_observed_box_count": box_count})
    return bool(gaps)


def detect_box_enablement_opportunity(
    paths: RuntimePaths, *, urlopen: UrlOpenFn = urllib.request.urlopen
) -> BoxEnablementOpportunity | None:
    if not _box_stack_available(paths, urlopen=urlopen):
        return None
    variables = read_live_saved_variables(paths, urlopen=urlopen)
    box_count_value = _resolve_live_int(variables, "box_count", default=0)
    enable_box_value = _resolve_live_int(variables, "enable_box", default=0)
    if box_count_value <= 0 or enable_box_value != 0:
        return None
    return BoxEnablementOpportunity(
        saved_variables_path=paths.config_root / "saved_variables.cfg",
        box_count=box_count_value,
        enable_box=enable_box_value,
    )


def detect_required_tool_slot_opportunity(
    paths: RuntimePaths, *, urlopen: UrlOpenFn = urllib.request.urlopen
) -> RequiredToolSlotOpportunity | None:
    if not _box_stack_available(paths, urlopen=urlopen):
        return None
    variables = read_live_saved_variables(paths, urlopen=urlopen)
    box_count = _resolve_live_int(variables, "box_count", default=0)
    gaps = collect_required_tool_slot_gaps_from_variables(variables)
    if not gaps:
        return None
    return RequiredToolSlotOpportunity(
        saved_variables_path=paths.config_root / "saved_variables.cfg",
        box_count=box_count,
        gaps=gaps,
    )


def with_earlier_names(variables):
    """Adds each tltg_ variable under its vo_ name when that isn't saved, as Klipper does when it next starts.

    Saved variables were named tltg_ before 26.11.18.2. The installer saves defaults before Klipper restarts with the
    module that copies them (save_variables_fast), so a setting saved only under its tltg_ name is the operator's.
    """
    earlier = {name: "vo_" + name[len("tltg_"):] for name in variables if name.startswith("tltg_")}
    if isinstance(variables, Mapping):
        return {**{new: variables[old] for old, new in earlier.items()}, **variables}
    return frozenset(variables) | frozenset(earlier.values())


def read_live_saved_variables(
    paths: RuntimePaths, *, urlopen: UrlOpenFn = urllib.request.urlopen
) -> dict[str, str]:
    variables: dict[str, str] = {}
    for name, value in _read_live_saved_variables_raw(paths, urlopen=urlopen).items():
        if isinstance(name, str) and isinstance(value, (str, int, float, bool)):
            variables[name] = str(value)
    return variables


def read_live_saved_variable_names(
    paths: RuntimePaths, *, urlopen: UrlOpenFn = urllib.request.urlopen
) -> frozenset[str]:
    """Return every saved variable name, including those saved as None."""
    return frozenset(
        name for name in _read_live_saved_variables_raw(paths, urlopen=urlopen)
        if isinstance(name, str)
    )


def _read_live_saved_variables_raw(
    paths: RuntimePaths, *, urlopen: UrlOpenFn
) -> Mapping:
    try:
        _, state = read_printer_info(paths.moonraker_url, urlopen=urlopen)
    except ProcessRestartError as exc:
        raise SavedVariablePersistenceError("Klipper saved-variable runtime is unavailable.") from exc
    if state != "ready":
        raise SavedVariablePersistenceError("Klipper saved-variable runtime is not ready.")
    request_url = _moonraker_objects_url(paths.moonraker_url, "save_variables")
    try:
        with urlopen(request_url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        raw = payload["result"]["status"]["save_variables"]["variables"]
    except (OSError, urllib.error.URLError, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SavedVariablePersistenceError("Could not read Klipper saved variables.") from exc
    if not isinstance(raw, Mapping):
        raise SavedVariablePersistenceError("Moonraker returned invalid saved variables.")
    return raw


def save_live_variables(
    paths: RuntimePaths,
    values: Mapping[str, str],
    *,
    missing_only: frozenset[str] = frozenset(),
    missing_or_empty: frozenset[str] = frozenset(),
    reporter=None,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> dict[str, str]:
    if not values:
        return {}
    if missing_only & missing_or_empty or not (missing_only | missing_or_empty) <= values.keys():
        raise ValueError("Saved-variable eligibility is invalid.")
    _validate_saved_values(values)
    # Read immediately before writing so values are sourced from Klipper memory,
    # not a potentially stale saved_variables.cfg snapshot.
    live = read_live_saved_variables(paths, urlopen=urlopen)
    pending = {}
    for name, value in values.items():
        current = live.get(name)
        if name in missing_only:
            if current is not None:
                continue
        elif name in missing_or_empty:
            if _normalize_saved_string(current or ""):
                continue
        elif _normalize_saved_string(current or "") == _normalize_saved_string(value):
            continue
        pending[name] = value
    for name, value in pending.items():
        if not _post_save_variable(paths, name, value, urlopen=urlopen):
            _wait_for_queued_save(paths, name, value, reporter=reporter, urlopen=urlopen)
    if pending:
        _verify_saved_values(paths, pending, urlopen=urlopen)
    return pending


def _post_save_variable(paths: RuntimePaths, name: str, value: str, *, urlopen: UrlOpenFn) -> bool:
    """Send SAVE_VARIABLE; False when Moonraker took it but Klipper hasn't run it yet."""
    request = urllib.request.Request(
        _moonraker_gcode_url(paths.moonraker_url),
        # Preserve the Python literal through Klipper's shell-style argument parser.
        data=json.dumps({"script": f"SAVE_VARIABLE VARIABLE={name} VALUE={shlex.quote(value)}"}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=SAVE_VARIABLE_REPLY_SECONDS) as response:
            response.read()
    except socket.timeout:
        # Sent, with no reply yet. A connection that timed out is a URLError, below.
        return False
    except (OSError, urllib.error.URLError, ValueError) as exc:
        raise SavedVariablePersistenceError(f"Could not save Klipper variable {name}.") from exc
    return True


def _wait_for_queued_save(paths: RuntimePaths, name: str, value: str, *, reporter, urlopen: UrlOpenFn) -> None:
    """Wait for Klipper to run a queued SAVE_VARIABLE, without sending it again."""
    if reporter is not None:
        reporter.debug(event="saved_variable.queued", name=name)
        reporter.line(messages.SAVED_VARIABLE_WAITING_FOR_KLIPPER)
    deadline = time.monotonic() + QUEUED_SAVE_SECONDS
    while True:
        live = read_live_saved_variables(paths, urlopen=urlopen)
        if _normalize_saved_string(live.get(name, "")) == _normalize_saved_string(value):
            return
        if time.monotonic() >= deadline:
            raise SavedVariablePersistenceError(
                f"Klipper was still busy after {QUEUED_SAVE_SECONDS // 60} minutes and did not save {name}."
            )
        time.sleep(QUEUED_SAVE_POLL_SECONDS)


def _verify_saved_values(paths: RuntimePaths, values: Mapping[str, str], *, urlopen: UrlOpenFn) -> None:
    _check_saved_values(read_live_saved_variables(paths, urlopen=urlopen), values)


def _check_saved_values(live: Mapping[str, str], values: Mapping[str, str]) -> None:
    if any(
        _normalize_saved_string(live.get(name, "")) != _normalize_saved_string(value)
        for name, value in values.items()
    ):
        raise SavedVariablePersistenceError("Klipper did not retain saved-variable updates.")
    # `SAVE_VARIABLE` is Klipper's persistence operation. A subsequent live
    # query confirms the command completed; a verified replacement process
    # confirms the same values survive the source-patch restart path.


def _validate_saved_values(values: Mapping[str, str]) -> None:
    for name, value in values.items():
        if not SAVED_VARIABLE_NAME_RE.fullmatch(name):
            raise SavedVariablePersistenceError("Saved-variable name is invalid.")
        if not isinstance(value, str) or not value or len(value) > MAX_SAVED_VARIABLE_VALUE_LENGTH:
            raise SavedVariablePersistenceError("Saved-variable value is invalid.")
        # 0/1 flags, quoted slot names, and whole-number Box motion settings.
        if (
            value not in {"0", "1"}
            and not re.fullmatch(r"'(?:slot(?:[0-9]|1[0-5]))'", value)
            and not re.fullmatch(r"[1-9][0-9]{0,3}", value)
        ):
            raise SavedVariablePersistenceError("Saved-variable value is not authorized.")


def _moonraker_objects_url(moonraker_url: str, object_name: str) -> str:
    parts = urllib.parse.urlsplit(moonraker_url)
    prefix = parts.path.removesuffix("/printer/objects/query")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"{prefix}/printer/objects/query", object_name, ""))


def _moonraker_gcode_url(moonraker_url: str) -> str:
    parts = urllib.parse.urlsplit(moonraker_url)
    prefix = parts.path.removesuffix("/printer/objects/query")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"{prefix}/printer/gcode/script", "", ""))


def collect_required_tool_slot_gaps_from_variables(
    variables: Mapping[str, str],
) -> tuple[RequiredToolSlotGap, ...]:
    box_count = _resolve_live_int(variables, "box_count", default=0)
    gaps = []
    for tool in range(_required_tool_count(box_count)):
        variable = f"value_t{tool}"
        current = _normalize_saved_string(variables.get(variable, ""))
        if not current:
            gaps.append(RequiredToolSlotGap(tool, variable, "<missing>" if variable not in variables else "<empty>", f"slot{tool}"))
    return tuple(gaps)


# Text helpers remain for offline configuration analysis and legacy tests; live
# installer paths intentionally use the Moonraker helpers above.
def collect_required_tool_slot_gaps(text: str) -> tuple[RequiredToolSlotGap, ...]:
    variables = _variables_from_text(text)
    return collect_required_tool_slot_gaps_from_variables(variables)


def _variables_from_text(text: str) -> dict[str, str]:
    section = klipper_cfg.resolve_unique_section(text, "Variables")
    values = {}
    for line in text.splitlines(keepends=True)[section.header_index + 1 : section.end_index]:
        parsed = klipper_cfg.parse_option_line(line)
        if parsed is not None:
            values[parsed.key] = parsed.value
    return values


def _required_tool_count(box_count: int) -> int:
    return max(0, min(16, box_count * 4))


def _box_extras_configured(path: Path) -> bool:
    try:
        return klipper_cfg.has_section(klipper_cfg.read_text(path), "box_extras")
    except OSError:
        return False


def _box_stack_available(paths: RuntimePaths, *, urlopen: UrlOpenFn) -> bool:
    # Legacy USB Boxes are configured in box.cfg. The flow controller that
    # registers T_MANAGER always loads its Box objects, so only a live Box count
    # proves an attached Box there; this also covers RS-485 Boxes without box.cfg.
    if _box_extras_configured(paths.config_root / "box.cfg"):
        return True
    request_url = _moonraker_objects_url(
        paths.moonraker_url, "gcode=commands&multi_color_controller=box_count&box485=box_count"
    )
    try:
        with urlopen(request_url, timeout=10) as response:
            status = json.loads(response.read().decode("utf-8"))["result"]["status"]
        if "T_MANAGER" not in status["gcode"]["commands"]:
            return False
        counts = (
            status.get(name, {}).get("box_count", 0) for name in ("multi_color_controller", "box485")
        )
        return any((_coerce_optional_int(count) or 0) > 0 for count in counts)
    except (OSError, urllib.error.URLError, KeyError, TypeError, ValueError, AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _read_runtime_state(paths: RuntimePaths) -> dict[str, object]:
    path = paths.printer_data_root / RUNTIME_STATE_FILE
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_runtime_state(paths: RuntimePaths, state: dict[str, object]) -> None:
    from .fs_atomic import atomic_write_text

    path = paths.printer_data_root / RUNTIME_STATE_FILE
    atomic_write_text(path, json.dumps(state, sort_keys=True, indent=2) + "\n", mode=0o644)


def _resolve_live_int(variables: Mapping[str, str], name: str, *, default: int) -> int:
    if name not in variables:
        return default
    return _coerce_saved_int(variables[name])


def _coerce_optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _coerce_saved_int(value: str) -> int:
    normalized = _normalize_saved_string(value).lower()
    if normalized == "true":
        return 1
    if normalized in {"false", "none", ""}:
        return 0
    return int(normalized)


def _normalize_saved_string(value: str) -> str:
    return value.strip().strip("'\"")


def _quote_saved_string(value: str) -> str:
    return f"'{value}'"
