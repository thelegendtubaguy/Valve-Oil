# tltg-optimized:status-server
"""Valve Oil status and settings service for Fluidd and Valve Oil UI.

Reports the installed Valve Oil release, the printer's QIDI firmware, each board's firmware, and the work waiting for
the printer, from the pending-work status file the update check and update runs write, and switches automatic updates
on or off by writing or removing only the enrollment marker. "Update now" starts the installer's update-now run in its
own session; it records its result in files, so it finishes and is reported even if this service restarts. nginx
authorizes every request through Moonraker before it reaches this loopback-only server.

API, under nginx's /tltg-status/ prefix (schema 1; see STATUS_SCHEMA):
  GET  api/status       the status
  POST api/auto-update  {"enabled": true|false}; answers with the status
  POST api/update-now   {}; answers 202 with {"job": ...}, or 409 with {"error", "blocked_by"} when update_now.allowed
                        is false
POSTs need the X-Valve-Oil: status header and a JSON body, and a same-origin Origin when a browser sends one.

The status: {schema, updated_at, release: {installed}, printer: {id, name, firmware, target_firmware},
auto_update: {enabled, scheduled}, check: {at, result, message, next_at}, pending: [{kind, version, current, board,
reinstall, blocked_by: [{code, message}]}], boards: [{id, state, name, firmware, build, pinned, bootloader, report,
detail, checked_at, issue_url, waiting_for_firmware_update}], run: {id, kind, state, step, step_label, started_at,
finished_at, message, resumed, firmware_update, can_continue} | null, job: {id, state, started_at, finished_at, log,
message, run_id} | null, busy, printer_busy, update_now: {allowed, blocked_by: [{code, message}]}}.
- check.next_at: when the timer runs the next check (ISO 8601 UTC), or null when it isn't scheduled or can't be read.
- pending[].reinstall: true for a release item that installs the installed release again, because its installed state
  is missing or has drifted (as after QIDI's firmware package emptied the config folder); version is then the
  installed release. A release item whose version is the installed release is otherwise left out, as it is installed.
- pending[].blocked_by: why an enrolled printer's update hasn't run; empty while automatic updates are off. The
  printer's state (printing, heaters_on, printer_state_unknown) is read live when Moonraker answers; the rest is what
  the last check found.
- boards[].waiting_for_firmware_update: a board that needs its pinned image and is left as it is until the QIDI
  firmware update is installed (detail: "will be updated after the QIDI firmware update to <version>").
- run.firmware_update: the run is a QIDI firmware update (or update now continuing one). run.can_continue: that
  update's pending record is kept, failed, and this release can carry it on, so Update now or ./install.sh continues
  it (update_now.blocked_by still says when something must run first, such as a board's rescue).
- job.run_id: the status run this update now started or continued, once it has begun; run is that run while it is the
  latest.
- update_now: whether POST api/update-now would start a run now, and if not, every reason, read live: run_in_progress
  (an update runs), installer_busy (another installer run holds its lock, such as the hourly check), printing,
  heaters_on, printer_state_unknown, recovery_required (its message says what to run), installer_missing.
- boards[].issue_url: a prefilled link to this project's support form; one too long to pass on is replaced by the bare
  form.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATUS_SCHEMA = 1
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 7133
HOME = Path("/home/qidi")
PRINTER_DATA = HOME / "printer_data"
# Written by installer/runtime/valve_oil_status.py.
STATUS_FILE = PRINTER_DATA / ".tltg_valve_oil_status.json"
# installer/runtime/board_firmware.py: the last install's read of every board.
ADMISSION_FILE = PRINTER_DATA / ".tltg_board_admission.json"
# installer/runtime/auto_update.py: present means automatic updates are on.
ENROLLMENT_FILE = PRINTER_DATA / ".tltg_optimized_auto_update_enrolled"
AUTO_UPDATE_TIMER = Path("/etc/systemd/system/tltg-optimized-auto-update.timer")
MACRO_GLOBALS = PRINTER_DATA / "config/tltg-optimized-macros/globals.cfg"
FIRMWARE_MANIFEST = HOME / "update/firmware_manifest.json"
BUNDLE = HOME / "tltg-optimized-macros"
UPDATE_NOW = (sys.executable or "/usr/bin/python3", "-I", "-S", str(BUNDLE / "installer/runtime/bootstrap.py"), "update-now", "--plain")
# installer/runtime/update_now.py: the run records this job id in the status file's run.
JOB_ENV = "TLTG_STATUS_JOB"
# installer/runtime/locking.py and safety.py: an installer run holds the lock; these records mean recovery comes first,
# and say what to run, as installer/runtime/firmware_update.py RECOVERY_STEPS and SENTINEL_STEP do.
INSTALLER_LOCK = PRINTER_DATA / ".tltg_optimized_installer.lock"
RECOVERY_FILES = {
    PRINTER_DATA / ".tltg_mainboard_firmware_pending.json":
        "a mainboard firmware write wasn't verified; run ./mainboard-firmware.sh rescue first",
    PRINTER_DATA / ".tltg_box_firmware_pending.json":
        "a Box firmware write wasn't verified; run ./box-firmware.sh rescue first",
    PRINTER_DATA / ".tltg_thr_firmware_pending.json":
        "a THR firmware operation didn't finish; recover the THR with the recovery files the installer named first",
    PRINTER_DATA / ".tltg_optimized_recovery_required":
        "an earlier install's recovery didn't complete; restore a backup with ./restore.sh first",
}
# installer/runtime/firmware_update.py: the QIDI firmware update's pending record.
FIRMWARE_UPDATE_RECORD = PRINTER_DATA / ".tltg_firmware_update_pending.json"
PROC_LOCKS = Path("/proc/locks")
TIMER_OBJECT = "/org/freedesktop/systemd1/unit/tltg_2doptimized_2dauto_2dupdate_2etimer"
NEXT_CHECK_CACHE_SECONDS = 30.0
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
STATE_DIR = HOME / ".cache/tltg-status"
JOB_FILE = STATE_DIR / "job.json"
JOB_LOG = STATE_DIR / "job.log"
JOB_RC = STATE_DIR / "job.rc"
MOONRAKER = "http://127.0.0.1:7125"
LOG_LINES = 200
MAX_STATUS_BYTES = 262144
MAX_BODY = 1024
MAX_TEXT = 500
# Prefilled support-issue links are kilobytes long; only links to this project's issue form are passed on.
ISSUE_URL_PREFIX = "https://github.com/thelegendtubaguy/valve-oil/issues/new?"
ISSUE_FORM_URL = ISSUE_URL_PREFIX + "template=printer-support.yml"
MAX_ISSUE_URL = 8000
# POSTs must carry this header. A web page from another origin can't add it without a CORS preflight, which this
# server never approves, so it can't make a browser on a trusted address change a setting or start an update.
ACTION_HEADER = ("X-Valve-Oil", "status")
PENDING_KINDS = ("release", "firmware", "board")
BOARD_STATES = ("current", "needs_image", "unrecognized_firmware", "unrecognized_bootloader", "held", "unknown")
BOARD_NAMES = {"thr": "THR", "mcu": "Mainboard", "closed_loop": "Closed-loop drivers"}
RUN_STATES = ("running", "succeeded", "failed", "interrupted")
# installer/runtime/valve_oil_status.py BLOCKERS: the printer's state, which this service reads live.
LIVE_BLOCKERS = {
    "printing": "a print is running or paused",
    "heaters_on": "a heater is set to a temperature",
    "printer_state_unknown": "Klipper didn't report whether the printer is idle",
}
UPDATE_NOW_BLOCKERS = {
    "run_in_progress": "another update is still running",
    "installer_busy": "another installer run is in progress",
    "installer_missing": "Valve Oil's installer is not in " + str(BUNDLE),
}


def log(message: str) -> None:
    print(message, flush=True)


class Conflict(Exception):
    def __init__(self, message: str, blocked_by: list[dict] | None = None) -> None:
        super().__init__(message)
        self.blocked_by = blocked_by or [{"code": "run_in_progress", "message": UPDATE_NOW_BLOCKERS["run_in_progress"]}]


class RequestError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


class Jobs:
    """The update-now run this service started last: one at a time, its output kept in JOB_LOG."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        job = self._read()
        if job and job.get("state") == "running":
            threading.Thread(target=self._watch, args=(job["id"], job.get("pid")), daemon=True).start()

    def current(self, run: dict | None = None) -> dict | None:
        """The last job; `run` is the status file's run, which names the job that started or continued it."""
        job = self._read()
        if job is None:
            return None
        run = _record(run)
        if run.get("job") == job.get("id") and _text(run.get("id")) and run["id"] != job.get("run_id"):
            with self._lock:
                job = self._read() or job
                if job.get("id") == run.get("job"):
                    job["run_id"] = run["id"]
                    self._write(job)
        lines = _tail(JOB_LOG, LOG_LINES)
        return {
            "id": job.get("id"),
            "state": job.get("state"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "log": lines,
            "message": next((line for line in reversed(lines) if line.strip()), ""),
            "run_id": _text(job.get("run_id")),
        }

    def busy(self) -> bool:
        job = self._read()
        return bool(job and job.get("state") == "running" and _alive(job.get("pid")))

    def start(self, status: dict) -> dict:
        with self._lock:
            if self.busy() or (status.get("run") or {}).get("state") == "running":
                raise Conflict("An update is already running.")
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            JOB_RC.unlink(missing_ok=True)
            job_id = uuid.uuid4().hex
            try:
                with JOB_LOG.open("w", encoding="utf-8") as output:
                    process = start_update_now(output, job_id)
            except OSError as exc:
                log(f"Update now failed to start: {exc}")
                raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "Valve Oil's installer could not be started.") from exc
            self._write({"id": job_id, "state": "running", "started_at": _now(), "finished_at": None, "pid": process.pid,
                         "run_id": None})
            threading.Thread(target=self._wait, args=(job_id, process), daemon=True).start()
            log("Started update now")
            return self.current()

    def _wait(self, job_id: str, process: subprocess.Popen) -> None:
        process.wait()
        self._finish(job_id)

    def _watch(self, job_id: str, pid: int | None) -> None:
        # A run started before this service restarted isn't our child; poll until its process is gone.
        while _alive(pid):
            time.sleep(1.0)
        self._finish(job_id)

    def _finish(self, job_id: str) -> None:
        with self._lock:
            job = self._read()
            if not job or job.get("id") != job_id:
                return
            try:
                code = int(JOB_RC.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                code = None
            job["state"] = "succeeded" if code == 0 else "failed"
            job["finished_at"] = _now()
            if code is None:
                with JOB_LOG.open("a", encoding="utf-8") as output:
                    output.write("The update stopped before it finished.\n")
            self._write(job)
            log(f"Finished update now: {job['state']}")

    def _read(self) -> dict | None:
        data = _read_json(JOB_FILE)
        return data if isinstance(data, dict) and isinstance(data.get("id"), str) else None

    def _write(self, job: dict) -> None:
        tmp = JOB_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(job), encoding="utf-8")
        tmp.replace(JOB_FILE)


def start_update_now(output, job_id: str) -> subprocess.Popen:
    # The fixed command line runs in its own session, so it outlives this request and a restart of this service; the
    # shell only records its exit status.
    return subprocess.Popen(
        ["/bin/sh", "-c", '"$@"; echo $? > "$TLTG_STATUS_RC"', "sh", *UPDATE_NOW],
        cwd=str(BUNDLE),
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "TLTG_STATUS_RC": str(JOB_RC), JOB_ENV: job_id, "PYTHONUNBUFFERED": "1"},
    )


def auto_update_enabled() -> bool:
    return ENROLLMENT_FILE.is_file() and not ENROLLMENT_FILE.is_symlink()


def set_auto_update(enabled: bool) -> None:
    """Write or remove the enrollment marker, as auto_update.py does; the check reads it at its next run."""
    if enabled:
        tmp = ENROLLMENT_FILE.with_name(f".{ENROLLMENT_FILE.name}.{os.getpid()}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("1\n")
            os.replace(tmp, ENROLLMENT_FILE)
        finally:
            tmp.unlink(missing_ok=True)
    else:
        ENROLLMENT_FILE.unlink(missing_ok=True)


def status_body(jobs: Jobs) -> dict:
    """The API's status: the status file, with what can change between checks read live."""
    data = _read_json(STATUS_FILE, limit=MAX_STATUS_BYTES)
    data = data if isinstance(data, dict) and data.get("schema") == STATUS_SCHEMA else {}
    release = _record(data.get("release"))
    installed = _installed_release() or _text(release.get("installed"))
    printer = _record(data.get("printer"))
    check = _record(data.get("check"))
    admission, admitted_at = _admission()
    enabled = auto_update_enabled()
    live = live_printer()
    busy = jobs.busy() or _run_active(_record(data.get("run")))
    pending = [item for item in map(_pending, _records(data.get("pending"))) if _still_waiting(item, installed)]
    for item in pending:
        item["blocked_by"] = _live_blockers(item["blocked_by"], live, enabled)
    return {
        "schema": STATUS_SCHEMA,
        "updated_at": _text(data.get("updated_at")),
        "release": {"installed": installed},
        "printer": {
            "id": _text(printer.get("id")) or _text(admission.get("printer")),
            "name": _text(printer.get("name")),
            "firmware": _qidi_firmware() or _text(printer.get("firmware")),
            "target_firmware": _text(printer.get("target_firmware")) or _text(admission.get("target_firmware")),
        },
        "auto_update": {"enabled": enabled, "scheduled": AUTO_UPDATE_TIMER.is_file()},
        "check": {
            "at": _text(check.get("at")),
            "result": check.get("result") if check.get("result") in ("ok", "failed") else None,
            "message": _text(check.get("message")) or "",
            "next_at": next_check_at(),
        },
        "pending": pending,
        "boards": _boards(_records(data.get("boards")), admission, admitted_at),
        "run": _run(data.get("run")),
        "job": jobs.current(data.get("run")),
        "busy": busy,
        "printer_busy": live is not None and "printing" in live,
        "update_now": _update_now(busy, live),
    }


# ---- what the printer is doing now --------------------------------------------------------------------------------


def live_printer() -> tuple[str, ...] | None:
    """The printer-state blockers that hold now, as the installer's idle check sees them (safety.ensure_printer_idle):
    printing (or paused), heaters_on, or printer_state_unknown; None when Moonraker can't be read."""
    try:
        status = _moonraker_objects(("print_stats", "webhooks", "heaters"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    state = _record(status.get("print_stats")).get("state")
    if not isinstance(state, str) or not state:
        # A Klipper halted on an error has no print state, can't be printing, and has shut its heaters off.
        return () if _klipper_state() == "error" else ("printer_state_unknown",)
    if state in ("printing", "paused"):
        return ("printing",)
    if _record(status.get("webhooks")).get("state") != "ready":
        return ()
    heaters = _record(status.get("heaters")).get("available_heaters")
    if not isinstance(heaters, list) or not all(isinstance(name, str) and name for name in heaters):
        return ("printer_state_unknown",)
    if not heaters:
        return ()
    try:
        targets = _moonraker_objects(tuple(heaters))
        heating = any(_finite(_record(targets.get(name)).get("target")) != 0 for name in heaters)
    except (OSError, ValueError, KeyError, TypeError):
        return ("printer_state_unknown",)
    return ("heaters_on",) if heating else ()


def _moonraker_objects(names: tuple[str, ...]) -> dict:
    query = "&".join(urllib.parse.quote(name, safe="") for name in names)
    with urllib.request.urlopen(f"{MOONRAKER}/printer/objects/query?{query}", timeout=2.0) as response:
        status = json.load(response)["result"]["status"]
    if not isinstance(status, dict):
        raise ValueError("status")
    return status


def _klipper_state() -> str | None:
    try:
        with urllib.request.urlopen(f"{MOONRAKER}/printer/info", timeout=2.0) as response:
            return _text(json.load(response)["result"]["state"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _finite(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        raise ValueError(value)
    return float(value)


def _live_blockers(recorded: list[dict], live: tuple[str, ...] | None, enabled: bool) -> list[dict]:
    """Why a waiting update hasn't run: nothing while automatic updates are off; otherwise what the check recorded,
    with the printer's state as it is now when Moonraker answers."""
    if not enabled:
        return []
    if live is None:
        return recorded
    kept = [blocker for blocker in recorded if blocker["code"] not in LIVE_BLOCKERS]
    return [{"code": code, "message": LIVE_BLOCKERS[code]} for code in live] + kept


def _update_now(busy: bool, live: tuple[str, ...] | None) -> dict:
    """Whether POST api/update-now starts a run now: every reason it would refuse, or the run would stop at once."""
    codes = []
    if busy:
        codes.append("run_in_progress")
    elif installer_lock_held():
        codes.append("installer_busy")
    codes.extend(live if live is not None else ("printer_state_unknown",))
    blocked = [{"code": code, "message": LIVE_BLOCKERS.get(code) or UPDATE_NOW_BLOCKERS[code]} for code in codes]
    # While a firmware update is recorded, update now continues it, and only a board's rescue has to come first
    # (installer/runtime/firmware_update.py resume_blocker); its installer step deals with an installer recovery.
    recorded = FIRMWARE_UPDATE_RECORD.exists()
    recovery = [message for path, message in RECOVERY_FILES.items()
                if path.exists() and not (recorded and path.name == ".tltg_optimized_recovery_required")]
    if recovery:
        blocked.append({"code": "recovery_required", "message": "; ".join(recovery)})
    if not (BUNDLE / "installer/runtime/bootstrap.py").is_file():
        blocked.append({"code": "installer_missing", "message": UPDATE_NOW_BLOCKERS["installer_missing"]})
    return {"allowed": not blocked, "blocked_by": blocked}


def installer_lock_held() -> bool:
    """Whether an installer run holds its lock, from the kernel's lock table, without taking the lock."""
    try:
        info = INSTALLER_LOCK.stat()
        table = PROC_LOCKS.read_text(encoding="utf-8")
    except OSError:
        return False
    device = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
    for line in table.splitlines():
        fields = line.split()
        if len(fields) > 5 and fields[1] == "FLOCK" and device in fields:
            return True
    return False


_next_check = {"until": 0.0, "value": None}


def next_check_at() -> str | None:
    """When the update check's timer next runs it, from systemd's timer properties, which need no privilege."""
    if not AUTO_UPDATE_TIMER.is_file():
        return None
    now = time.monotonic()
    if now < _next_check["until"]:
        return _next_check["value"]
    value = None
    realtime = read_timer_property("NextElapseUSecRealtime")
    if realtime:
        value = _iso(realtime / 1e6)
    else:
        monotonic = read_timer_property("NextElapseUSecMonotonic")
        if monotonic:
            value = _iso(time.time() + monotonic / 1e6 - time.clock_gettime(time.CLOCK_MONOTONIC))
    _next_check.update(until=now + NEXT_CHECK_CACHE_SECONDS, value=value)
    return value


def read_timer_property(name: str) -> int | None:
    """A uint64 property of the timer's org.freedesktop.systemd1.Timer interface; None when unset or unreadable."""
    try:
        result = subprocess.run(["busctl", "get-property", "org.freedesktop.systemd1", TIMER_OBJECT,
                                 "org.freedesktop.systemd1.Timer", name], capture_output=True, text=True, timeout=2.0)
        kind, value = result.stdout.split()
        number = int(value)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return number if result.returncode == 0 and kind == "t" and 0 < number < 2 ** 64 - 1 else None


def _iso(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _pending(item: dict) -> dict | None:
    if item.get("kind") not in PENDING_KINDS:
        return None
    return {
        "kind": item["kind"],
        "version": _text(item.get("version")),
        "current": _text(item.get("current")),
        "board": _text(item.get("board")),
        "reinstall": item["kind"] == "release" and item.get("reinstall") is True,
        "blocked_by": [
            {"code": _text(blocker.get("code")), "message": _text(blocker.get("message")) or _text(blocker.get("code"))}
            for blocker in _records(item.get("blocked_by"))
            if _text(blocker.get("code"))
        ],
    }


def _still_waiting(item: dict | None, installed: str | None) -> bool:
    # A manual install of the waiting release ends the wait before the next check rewrites the file. Installing the
    # same release again doesn't change its version, so that install removes a reinstall item itself.
    return item is not None and (item["reinstall"] or not (
        item["kind"] == "release" and installed and item["version"] == installed))


def _boards(recorded: list[dict], admission: dict, admitted_at: str | None) -> list[dict]:
    """The boards from whichever is newer: the status file's, or the last install's admission record."""
    recorded = [board for board in map(_board, recorded) if board]
    checked = max((board["checked_at"] for board in recorded if board["checked_at"]), default=None)
    if admitted_at is not None and (checked is None or admitted_at > checked):
        admitted = [board for board in (_board({
            "id": entry.get("board"), "state": entry.get("state"), "firmware": entry.get("version"),
            "build": entry.get("application"), "bootloader": entry.get("bootloader_sha256"),
            "issue_url": entry.get("issue_url"), "report": entry.get("report"), "detail": entry.get("detail"),
            "checked_at": admitted_at, "waiting_for_firmware_update": entry.get("waiting_for_firmware_update"),
        }) for entry in _records(admission.get("boards"))) if board]
        # An install reads the THR, the mainboard, and the Boxes. A board it doesn't read, the Max 4's closed-loop
        # drivers, stays as last recorded, ahead of the Boxes.
        kept = [board for board in recorded if board["id"] not in ("thr", "mcu") and not _is_box(board["id"])]
        boxes = [board for board in admitted if _is_box(board["id"])]
        return [board for board in admitted if not _is_box(board["id"])] + kept + boxes
    return recorded


def _is_box(board_id: str) -> bool:
    return board_id.startswith("box") and board_id[3:].isdigit()


def _board(board: dict) -> dict | None:
    board_id = _text(board.get("id"))
    if not board_id:
        return None
    state = board.get("state")
    result = {"id": board_id, "state": state if state in BOARD_STATES else "unknown"}
    for key in ("name", "firmware", "build", "pinned", "bootloader", "report", "detail", "checked_at"):
        result[key] = _text(board.get(key))
    url = board.get("issue_url")
    ours = isinstance(url, str) and url.startswith(ISSUE_URL_PREFIX)
    # A link too long to pass on still leads to the same form, without the filled-in report.
    result["issue_url"] = (url if len(url) <= MAX_ISSUE_URL else ISSUE_FORM_URL) if ours else None
    if not result["name"]:
        number = board_id[3:] if board_id.startswith("box") and board_id[3:].isdigit() else None
        result["name"] = f"Box {number}" if number else BOARD_NAMES.get(board_id, board_id)
    result["waiting_for_firmware_update"] = board.get("waiting_for_firmware_update") is True
    return result


def _admission() -> tuple[dict, str | None]:
    """The admission record and when it was written, or ({}, None)."""
    data = _read_json(ADMISSION_FILE, limit=MAX_STATUS_BYTES)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return {}, None
    try:
        written = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ADMISSION_FILE.stat().st_mtime))
    except OSError:
        return {}, None
    return data, written


def _run(run) -> dict | None:
    run = _record(run)
    if not _text(run.get("id")) or run.get("state") not in RUN_STATES:
        return None
    state = run["state"]
    if state == "running" and not _run_active(run):
        state = "interrupted"
    return {
        "id": run["id"],
        "kind": _text(run.get("kind")),
        "state": state,
        "step": _text(run.get("step")) or "",
        "step_label": _text(run.get("step_label")) or "",
        "started_at": _text(run.get("started_at")),
        "finished_at": _text(run.get("finished_at")),
        "message": _text(run.get("message")) or "",
        "resumed": run.get("resumed") is True,
        "firmware_update": _text(run.get("firmware_update")) is not None,
        "can_continue": _can_continue(_text(run.get("firmware_update"))),
    }


def _can_continue(record_id: str | None) -> bool:
    """Whether the firmware update `record_id` is kept, failed, for Update now or ./install.sh to continue: its pending
    record has this release's format, and no release found it couldn't carry it on (its "continuable")."""
    if record_id is None or FIRMWARE_UPDATE_RECORD.is_symlink():
        return False
    record = _read_json(FIRMWARE_UPDATE_RECORD)
    return (isinstance(record, dict) and record.get("schema") == 1 and record.get("id") == record_id
            and record.get("state") == "failed" and record.get("continuable") is not False)


def _run_active(run: dict) -> bool:
    if run.get("state") != "running":
        return False
    pid = run.get("pid")
    try:
        boot_id = BOOT_ID.read_text(encoding="utf-8").strip() or None
    except OSError:
        boot_id = None
    return run.get("boot_id") == boot_id and isinstance(pid, int) and _alive(pid)


def _installed_release() -> str | None:
    # The macros the installer put in Klipper's config carry the release; QIDI's firmware update deletes them.
    try:
        with MACRO_GLOBALS.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("variable_package_version:"):
                    return _text(line.split(":", 1)[1].strip().strip('"').strip("'"))
    except OSError:
        pass
    return None


def _qidi_firmware() -> str | None:
    data = _read_json(FIRMWARE_MANIFEST, limit=MAX_STATUS_BYTES)
    soc = data.get("SOC") if isinstance(data, dict) else None
    return _text(soc.get("version")) if isinstance(soc, dict) else None


class Handler(BaseHTTPRequestHandler):
    server_version = "tltg-status"
    jobs: Jobs

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler's signature
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # The page closed or reloaded before the response was written.

    def do_GET(self) -> None:
        route = self._route()
        if route == "status":
            self._send(HTTPStatus.OK, status_body(self.jobs))
        elif route in ("auto-update", "update-now"):
            self._send(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Use POST."}, allow="POST")
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def do_POST(self) -> None:
        try:
            body = self._check_post()
            route = self._route()
            if route == "auto-update":
                if set(body) != {"enabled"} or not isinstance(body["enabled"], bool):
                    raise RequestError(HTTPStatus.BAD_REQUEST, 'Expected {"enabled": true} or {"enabled": false}.')
                try:
                    set_auto_update(body["enabled"])
                except OSError as exc:
                    log(f"Automatic update switch failed: {exc}")
                    raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "The automatic update setting could not be saved.") from exc
                log(f"Automatic updates {'on' if body['enabled'] else 'off'}")
                self._send(HTTPStatus.OK, status_body(self.jobs))
            elif route == "update-now":
                if body:
                    raise RequestError(HTTPStatus.BAD_REQUEST, "Update now takes no options.")
                status = status_body(self.jobs)
                blocked = status["update_now"]["blocked_by"]
                if blocked:
                    raise Conflict("Update now can't start: " + "; ".join(item["message"] for item in blocked) + ".",
                                   blocked)
                self._send(HTTPStatus.ACCEPTED, {"job": self.jobs.start(status)})
            elif route == "status":
                raise RequestError(HTTPStatus.METHOD_NOT_ALLOWED, "Use GET.")
            else:
                raise RequestError(HTTPStatus.NOT_FOUND, "Not found.")
        except Conflict as exc:
            self._send(HTTPStatus.CONFLICT, {"error": str(exc), "blocked_by": exc.blocked_by})
        except RequestError as exc:
            allow = {"status": "GET"}.get(self._route(), "POST") if exc.status == HTTPStatus.METHOD_NOT_ALLOWED else None
            self._send(exc.status, {"error": str(exc)}, allow=allow)

    def _check_post(self) -> dict:
        name, value = ACTION_HEADER
        if self.headers.get(name) != value:
            raise RequestError(HTTPStatus.FORBIDDEN, f"Missing {name} header.")
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise RequestError(HTTPStatus.BAD_REQUEST, "Expected a JSON request.")
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and (not host or origin.split("://", 1)[-1] != host):
            raise RequestError(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed.")
        if self.headers.get("Transfer-Encoding"):
            raise RequestError(HTTPStatus.BAD_REQUEST, "Expected a JSON request.")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Expected a JSON request.") from exc
        if length < 0 or length > MAX_BODY:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Request too large.")
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
        except ValueError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Expected a JSON request.") from exc
        if not isinstance(body, dict):
            raise RequestError(HTTPStatus.BAD_REQUEST, "Expected a JSON object.")
        return body

    def _route(self) -> str | None:
        # nginx strips its /tltg-status/ prefix; the API is exactly api/<name>.
        parts = self.path.split("?", 1)[0].split("/")
        if len(parts) == 3 and parts[0] == "" and parts[1] == "api" and parts[2] in ("status", "auto-update", "update-now"):
            return parts[2]
        return None

    def _send(self, status: HTTPStatus, body: dict, *, allow: str | None = None) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        if allow:
            self.send_header("Allow", allow)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _read_json(path: Path, *, limit: int = MAX_STATUS_BYTES):
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        return None
    if len(raw) > limit:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _record(value) -> dict:
    return value if isinstance(value, dict) else {}


def _records(value) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value) -> str | None:
    return value[:MAX_TEXT] if isinstance(value, str) and value else None


def _tail(path: Path, lines: int) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> None:
    Handler.jobs = Jobs()
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    server.daemon_threads = True
    log(f"Listening on {LISTEN_HOST}:{LISTEN_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
