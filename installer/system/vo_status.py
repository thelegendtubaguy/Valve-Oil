# vo:status-server
"""Valve Oil status and settings service for Fluidd and Valve Oil UI.

Reports the installed Valve Oil release, the printer's QIDI firmware, each board's firmware, and the work waiting for
the printer, from the pending-work status file the update check and update runs write, and switches automatic updates
on or off by writing or removing only the enrollment marker, and saves the update window and its timezone. "Update now" starts the installer's update-now run in its
own session; it records its result in files, so it finishes and is reported even if this service restarts. nginx
authorizes every request through Moonraker before it reaches this loopback-only server.

API, under nginx's /vo-status/ prefix (schema 1; see STATUS_SCHEMA):
  GET  api/status       the status
  POST api/auto-update  any of {"enabled": true|false, "window": {"start": <hour>, "end": <hour>} | null,
                        "timezone": "<IANA name>"}; answers with the status. A timezone posted here is the browser's.
  POST api/update-now   {}; answers 202 with {"job": ...}, or 409 with {"error", "blocked_by"} when update_now.allowed
                        is false
  GET  api/support-bundle  a zip of what a support issue needs (see support_bundle), named in Content-Disposition;
                        409 while another is being made
  GET  api/diagnostics  the last Diagnostics check (see Diagnostics)
  POST api/diagnostics  {"since": "all" | "day" | "start"}; starts a check of the printer's logs for Valve Oil's known
                        fault signatures and answers 202 with the check, or 409 while one runs
POSTs need the X-Valve-Oil: status header and a JSON body, and a same-origin Origin when a browser sends one.

The status: {schema, updated_at, release: {installed, channel, commit}, printer: {id, name, firmware, target_firmware},
auto_update: {enabled, scheduled, window: {start, end} | null, timezone, timezone_source, in_window}, check: {at, result, message, next_at}, pending: [{kind, version, current, board,
reinstall, blocked_by: [{code, message}]}], boards: [{id, state, name, firmware, build, pinned, bootloader, report,
detail, checked_at, issue_url, waiting_for_firmware_update}], run: {id, kind, state, step, step_label, started_at,
finished_at, message, resumed, firmware_update, can_continue} | null, job: {id, state, started_at, finished_at, log,
message, run_id} | null, busy, printer_busy, update_now: {allowed, blocked_by: [{code, message}]},
support: {issue_url}}.
- release.channel and release.commit: "release" or "dev", and the commit, of the bundle that installed the installed
  release (its RELEASE_METADATA.yaml); null when the bundle in ~/valve-oil is for another version or doesn't say. A
  dev build's update check fails until a release publishes the archive it looks for, so the UIs don't report that.
- auto_update.window: the whole hours an enrolled check installs in, from start up to end, wrapping past midnight when
  end is earlier; null for any time. timezone: the IANA zone it is read in (null: UTC), and timezone_source how it was
  set ("lookup" at install from the printer's internet address, "browser", or "operator" over SSH). in_window: whether
  now is inside it (true without one). installer/runtime/update_window.py owns the file.
- check.next_at: when the timer runs the next check (ISO 8601 UTC), or null when it isn't scheduled or can't be read.
- pending[].reinstall: true for a release item that installs the installed release again, because its installed state
  is missing or has drifted (as after QIDI's firmware package emptied the config folder); version is then the
  installed release. A release item no newer than the installed release is otherwise left out, as it is installed.
- pending[].blocked_by: why an enrolled printer's update hasn't run; empty while automatic updates are off. The
  printer's state (printing, heaters_on, printer_state_unknown) is read live when Moonraker answers, and
  outside_window from the saved window and the clock; the rest is what the last check found.
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
- support.issue_url: this project's bug report form, with the printer, its details (printer_details), and how Valve
  Oil was installed filled in, for the support bundle to be attached to.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone as dt_timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATUS_SCHEMA = 1
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 7133
HOME = Path("/home/qidi")
PRINTER_DATA = HOME / "printer_data"
# Written by installer/runtime/valve_oil_status.py.
STATUS_FILE = PRINTER_DATA / ".vo_status.json"
# Written by installer/runtime/valve_oil_status.py (ProgressPublisher): the steps of the install running now or last.
PROGRESS_FILE = PRINTER_DATA / ".vo_progress.json"
# installer/runtime/board_firmware.py: the last install's read of every board.
ADMISSION_FILE = PRINTER_DATA / ".vo_board_admission.json"
# installer/runtime/auto_update.py: present means automatic updates are on.
ENROLLMENT_FILE = PRINTER_DATA / ".vo_auto_update_enrolled"
AUTO_UPDATE_TIMER = Path("/etc/systemd/system/vo-auto-update.timer")
# installer/runtime/update_window.py: the update window and the timezone it is read in.
UPDATE_SETTINGS_FILE = PRINTER_DATA / ".vo_auto_update_settings.json"
TIMEZONE_SOURCES = ("lookup", "browser", "operator")
ZONE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}$")
MACRO_GLOBALS = PRINTER_DATA / "config/vo-macros/globals.cfg"
FIRMWARE_MANIFEST = HOME / "update/firmware_manifest.json"
BUNDLE = HOME / "valve-oil"
# scripts/build_installer_bundle.py: the bundle's version, channel ("release" or "dev"), and commit.
RELEASE_METADATA = BUNDLE / "RELEASE_METADATA.yaml"
UPDATE_NOW = (sys.executable or "/usr/bin/python3", "-I", "-S", str(BUNDLE / "installer/runtime/bootstrap.py"), "update-now", "--plain")
# installer/runtime/update_now.py: the run records this job id in the status file's run.
JOB_ENV = "VO_STATUS_JOB"
# installer/runtime/locking.py and safety.py: an installer run holds the lock; these records mean recovery comes first,
# and say what to run, as installer/runtime/firmware_update.py RECOVERY_STEPS and SENTINEL_STEP do.
INSTALLER_LOCK = PRINTER_DATA / ".vo_installer.lock"
RECOVERY_FILES = {
    PRINTER_DATA / ".vo_mainboard_firmware_pending.json":
        "a mainboard firmware write wasn't verified; run ./mainboard-firmware.sh rescue first",
    PRINTER_DATA / ".vo_box_firmware_pending.json":
        "a Box firmware write wasn't verified; run ./box-firmware.sh rescue first",
    PRINTER_DATA / ".vo_thr_firmware_pending.json":
        "a THR firmware flash didn't finish; turn the printer off and on, or run ./install.sh, and Valve Oil finishes it",
    PRINTER_DATA / ".vo_recovery_required":
        "an earlier install's recovery didn't complete; restore a backup with ./restore.sh first",
}
# installer/runtime/firmware_update.py: the QIDI firmware update's pending record.
FIRMWARE_UPDATE_RECORD = PRINTER_DATA / ".vo_firmware_update_pending.json"
PROC_LOCKS = Path("/proc/locks")
TIMER_OBJECT = "/org/freedesktop/systemd1/unit/vo_2dauto_2dupdate_2etimer"
NEXT_CHECK_CACHE_SECONDS = 30.0
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
STATE_DIR = HOME / ".cache/valve-oil/status"
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
# The support issue: the bug report form (.github/ISSUE_TEMPLATE/bug-report.yml), whose printer choices these are.
SUPPORT_ISSUE_TEMPLATE = "bug-report.yml"
SUPPORT_ISSUE_PRINTERS = ("QIDI Max 4", "QIDI Plus 5", "QIDI Smart 5")
SUPPORT_ISSUE_LOGS = "Drag in the valve-oil-support zip file that Valve Oil's settings downloaded."
INSTALL_SOURCES = {"release": "Release install command from the README", "dev": "Dev prerelease"}
# The support bundle: the newest part of each log, as a long-running printer's klippy.log reaches tens of megabytes.
SUPPORT_LOG_BYTES = 16 * 1024 * 1024
LOGS = PRINTER_DATA / "logs"
BOX_CFG = PRINTER_DATA / "config/box.cfg"
CACHE = HOME / ".cache/valve-oil"
FIRMWARE_UPDATE_RESULT = PRINTER_DATA / ".vo_firmware_update_result.json"
# The Diagnostics check: installer/runtime/diagnostics.py from the bundle, at the lowest priority, with its own time
# limit; this service stops one that outlives it by DIAGNOSTICS_GRACE seconds.
DIAGNOSTICS = (sys.executable or "/usr/bin/python3", "-I", str(BUNDLE / "installer/runtime/diagnostics.py"))
DIAGNOSTICS_SINCE = ("all", "day", "start")
DIAGNOSTICS_TIME_LIMIT = 90
DIAGNOSTICS_GRACE = 60
DIAGNOSTICS_FILE = STATE_DIR / "diagnostics.json"
DIAGNOSTICS_REPORT = STATE_DIR / "diagnostics.txt"
MAX_DIAGNOSTICS_BYTES = 2 * 1024 * 1024
# Only values of known shape go in printer details: versions, QIDI's file names, and counts.
PLAIN = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
# POSTs must carry this header. A web page from another origin can't add it without a CORS preflight, which this
# server never approves, so it can't make a browser on a trusted address change a setting or start an update.
ACTION_HEADER = ("X-Valve-Oil", "status")
PENDING_KINDS = ("release", "firmware", "board")
BOARD_STATES = ("current", "needs_image", "unrecognized_firmware", "unrecognized_bootloader", "held", "unknown")
BOARD_NAMES = {"thr": "THR", "mcu": "Mainboard", "closed_loop": "Closed-loop drivers"}
RUN_STATES = ("running", "succeeded", "failed", "interrupted")
PROGRESS_STATES = (*RUN_STATES, "handed_off")
PROGRESS_ORIGINS = ("ssh", "usb", "automatic", "firmware_update")
MAX_PROGRESS_STEPS = 40
# installer/runtime/run_log.py: a run's log in printer_data/logs, which Moonraker serves.
RUN_LOG_NAME = re.compile(r"^valve-oil-[a-z-]+-\d{8}-\d{6}(-\d+)?\.log$")
# installer/runtime/valve_oil_status.py BLOCKERS: the printer's state, which this service reads live.
LIVE_BLOCKERS = {
    "printing": "a print is running or paused",
    "heaters_on": "a heater is set to a temperature",
    "printer_state_unknown": "Klipper didn't report whether the printer is idle",
    "klipper_not_ready": "Klipper isn't ready; fix the error Fluidd shows, or run the Valve Oil installer",
}
OUTSIDE_WINDOW = "it's outside the update window"
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


class Diagnostics:
    """The Diagnostics check this service ran last: one at a time, its result kept in DIAGNOSTICS_FILE and its report,
    which the support bundle carries, in DIAGNOSTICS_REPORT.

    The check: {state: "idle" | "running" | "done" | "failed", since, started_at, finished_at, error, result, report}.
    result is diagnostics.py's (its schema 1), report the text to attach to an issue, with the printer details first.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        check = self._read()
        if check.get("state") == "running":
            # This service restarted during a check, which stopped with it.
            self._write({**check, "state": "failed", "finished_at": _now(), "error": "The check was interrupted."})

    def current(self) -> dict:
        check = self._read()
        if not check:
            return {"state": "idle", "since": None, "started_at": None, "finished_at": None, "error": None,
                    "result": None, "report": None}
        return check

    def start(self, since: str, status: dict) -> dict:
        if since not in DIAGNOSTICS_SINCE:
            raise RequestError(HTTPStatus.BAD_REQUEST, 'Expected {"since": "all"}, "day", or "start".')
        if not Path(DIAGNOSTICS[-1]).is_file():
            raise RequestError(HTTPStatus.CONFLICT, UPDATE_NOW_BLOCKERS["installer_missing"] + ".")
        with self._lock:
            if self._read().get("state") == "running":
                raise RequestError(HTTPStatus.CONFLICT, "A check is already running.")
            check = {"state": "running", "since": since, "started_at": _now(), "finished_at": None, "error": None,
                     "result": None, "report": None}
            self._write(check)
        threading.Thread(target=self._run, args=(since, status), daemon=True).start()
        log(f"Started a Diagnostics check ({since})")
        return check

    def _run(self, since: str, status: dict) -> None:
        check = self._read()
        try:
            result = run_diagnostics(since, status["release"]["installed"])
            report = "Printer details:\n" + printer_details(status) + "\n\n" + result.pop("report")
            check.update(state="done", result=result, report=report)
            DIAGNOSTICS_REPORT.write_text(report, encoding="utf-8")
            log(f"Diagnostics check found {len(result['findings'])} signatures")
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            log(f"Diagnostics check failed: {exc}")
            reason = _text(str(exc))
            check.update(state="failed", error=f"The check could not be run: {reason}" if reason else
                         "The check could not be run.")
        check["finished_at"] = _now()
        with self._lock:
            self._write(check)

    def _read(self) -> dict:
        data = _read_json(DIAGNOSTICS_FILE, limit=MAX_DIAGNOSTICS_BYTES + 65536)
        return data if isinstance(data, dict) and data.get("state") in ("running", "done", "failed") else {}

    def _write(self, check: dict) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DIAGNOSTICS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(check), encoding="utf-8")
        tmp.replace(DIAGNOSTICS_FILE)


def run_diagnostics(since: str, installed: str | None) -> dict:
    """Run the bundle's checker on the printer's logs at the lowest CPU and disk priority, returning its result with its
    report."""
    low = [*(["nice", "-n", "19"] if shutil.which("nice") else []), *(["ionice", "-c", "3"] if shutil.which("ionice") else [])]
    command = [*low, *DIAGNOSTICS, "--json", "--report", "--since", since, "--time-limit", str(DIAGNOSTICS_TIME_LIMIT),
               *(["--installed", installed] if installed and PLAIN.match(installed) else []), str(LOGS)]
    done = subprocess.run(command, cwd=str(BUNDLE), stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=DIAGNOSTICS_TIME_LIMIT + DIAGNOSTICS_GRACE, check=False)
    if done.returncode != 0 or len(done.stdout) > MAX_DIAGNOSTICS_BYTES:
        raise ValueError((done.stderr.decode("utf-8", "replace").strip().splitlines() or ["the checker failed"])[-1])
    result = json.loads(done.stdout)
    if not isinstance(result, dict) or result.get("schema") != 1 or not isinstance(result.get("report"), str):
        raise ValueError("the checker's result wasn't one this service reads")
    return result


def start_update_now(output, job_id: str) -> subprocess.Popen:
    # The fixed command line runs in its own session, so it outlives this request and a restart of this service; the
    # shell only records its exit status.
    return subprocess.Popen(
        ["/bin/sh", "-c", '"$@"; echo $? > "$VO_STATUS_RC"', "sh", *UPDATE_NOW],
        cwd=str(BUNDLE),
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "VO_STATUS_RC": str(JOB_RC), JOB_ENV: job_id, "PYTHONUNBUFFERED": "1"},
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


def zone(name):
    """The tzinfo for an IANA name the printer knows, or None (update_window.zone)."""
    if not isinstance(name, str) or not ZONE_NAME.match(name):
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return None


def window_from(value) -> dict | None:
    """A posted or saved window, {"start", "end"} in whole hours that differ, or None; ValueError for anything else."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise ValueError('Expected "window" to be {"start": <hour>, "end": <hour>} or null.')
    hours = (value["start"], value["end"])
    if any(isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23 for hour in hours):
        raise ValueError("The window's hours must be whole hours from 0 to 23.")
    if hours[0] == hours[1]:
        raise ValueError("The window must start and end at different hours.")
    return {"start": hours[0], "end": hours[1]}


def update_settings() -> dict:
    data = _read_json(UPDATE_SETTINGS_FILE, limit=MAX_BODY)
    data = data if isinstance(data, dict) else {}
    try:
        window = window_from(data.get("window"))
    except ValueError:
        window = None
    name = data.get("timezone") if zone(data.get("timezone")) is not None else None
    source = data.get("timezone_source") if name and data.get("timezone_source") in TIMEZONE_SOURCES else None
    return {"window": window, "timezone": name, "timezone_source": source}


def save_update_settings(settings: dict) -> None:
    tmp = UPDATE_SETTINGS_FILE.with_name(f".{UPDATE_SETTINGS_FILE.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(settings, sort_keys=True, indent=2) + "\n")
        os.replace(tmp, UPDATE_SETTINGS_FILE)
    finally:
        tmp.unlink(missing_ok=True)


def in_window(settings: dict, now: datetime | None = None) -> bool:
    window = settings["window"]
    if window is None:
        return True
    hour = (now or datetime.now(dt_timezone.utc)).astimezone(zone(settings["timezone"]) or dt_timezone.utc).hour
    start, end = window["start"], window["end"]
    return start <= hour < end if start < end else hour >= start or hour < end


def window_blocker(settings: dict) -> dict:
    window = settings["window"]
    return {"code": "outside_window", "message": f"{OUTSIDE_WINDOW}, {window['start']:02d}:00–{window['end']:02d}:00 "
                                                 f"{settings['timezone'] or 'UTC'}"}


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
    settings = update_settings()
    outside = not in_window(settings)
    live = live_printer()
    busy = jobs.busy() or _run_active(_record(data.get("run")))
    pending = [item for item in map(_pending, _records(data.get("pending"))) if _still_waiting(item, installed)]
    for item in pending:
        item["blocked_by"] = _live_blockers(item["blocked_by"], live, enabled,
                                            window_blocker(settings) if outside else None)
    channel, commit = release_build(installed)
    body = {
        "schema": STATUS_SCHEMA,
        "updated_at": _text(data.get("updated_at")),
        "release": {"installed": installed, "channel": channel, "commit": commit},
        "printer": {
            "id": _text(printer.get("id")) or _text(admission.get("printer")),
            "name": _text(printer.get("name")),
            "firmware": _qidi_firmware() or _text(printer.get("firmware")),
            "target_firmware": _text(printer.get("target_firmware")) or _text(admission.get("target_firmware")),
        },
        "auto_update": {"enabled": enabled, "scheduled": AUTO_UPDATE_TIMER.is_file(), **settings,
                        "in_window": not outside},
        "check": {
            "at": _text(check.get("at")),
            "result": check.get("result") if check.get("result") in ("ok", "failed") else None,
            "message": _text(check.get("message")) or "",
            "next_at": next_check_at(),
        },
        "pending": pending,
        "boards": _boards(_records(data.get("boards")), admission, admitted_at),
        "run": _run(data.get("run")),
        "progress": _progress(),
        "job": jobs.current(data.get("run")),
        "busy": busy,
        "printer_busy": live is not None and "printing" in live,
        "update_now": _update_now(busy, live),
    }
    body["support"] = {"issue_url": support_issue_url(body)}
    return body


# ---- support -----------------------------------------------------------------------------------------------------------


def release_build(installed: str | None) -> tuple[str | None, str | None]:
    """The channel and short commit of the bundle that installed `installed`, from its RELEASE_METADATA.yaml."""
    fields = {}
    try:
        with RELEASE_METADATA.open("r", encoding="utf-8") as handle:
            for line in handle.read(4096).splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    fields[key.strip()] = value.strip().strip('"').strip("'")
    except (OSError, UnicodeDecodeError):
        return None, None
    if not installed or fields.get("package_version") != installed:
        return None, None
    channel = fields.get("channel") if fields.get("channel") in INSTALL_SOURCES else None
    commit = fields.get("git_short_sha") if COMMIT.match(fields.get("git_short_sha", "")) else None
    return channel, commit


def printer_details(status: dict) -> str:
    """The bug report's "Printer details", as docs/troubleshooting.md's block prints them, from files only. As
    installer/runtime/board_firmware.py printer_details does, only values of known shape go in: nothing that can carry
    a hostname, user name, address, or network name."""
    release = status["release"]
    if release["installed"]:
        build = (" (dev build" + (f" {release['commit']}" if release["commit"] else "") + ")") if release["channel"] == "dev" else ""
        lines = [f"Valve Oil: {release['installed']}{build}"]
    else:
        lines = ["Valve Oil: not installed"]
    manifest = _read_json(FIRMWARE_MANIFEST)
    if isinstance(manifest, dict):
        for name, part in manifest.items():
            fields = (name, *(part.get(key) for key in ("version", "file"))) if isinstance(part, dict) else ()
            if fields and all(isinstance(field, str) and PLAIN.match(field) for field in fields):
                lines.append("{}: {} ({})".format(*fields))
    else:
        lines.append("Firmware manifest: unreadable")
    try:
        box_cfg = BOX_CFG.read_text(encoding="utf-8", errors="replace")
        lines.append("USB Boxes in box.cfg: " + str(len(re.findall(r"^\[mcu mcu_box", box_cfg, re.M))))
    except OSError:
        lines.append("USB Boxes in box.cfg: no box.cfg")
    kernel = os.uname().release
    lines.append("Kernel: " + (kernel if re.match(r"^[A-Za-z0-9._+-]{1,80}$", kernel) else "unrecognized"))
    lines.append("Auto-updates: " + ("on" if status["auto_update"]["enabled"] else "off"))
    try:
        lines.append("zram: " + ("on" if "zram" in Path("/proc/swaps").read_text(encoding="utf-8") else "off"))
    except OSError:
        lines.append("zram: unknown")
    return "\n".join(lines)


def support_issue_url(status: dict) -> str:
    """The bug report form with what the printer knows filled in by field ID; printer details are left out of a link
    that would be too long to pass on."""
    fields = {"template": SUPPORT_ISSUE_TEMPLATE}
    if status["printer"]["name"] in SUPPORT_ISSUE_PRINTERS:
        fields["printer"] = status["printer"]["name"]
    if status["release"]["channel"] in INSTALL_SOURCES:
        fields["install_source"] = INSTALL_SOURCES[status["release"]["channel"]]
    fields["printer_details"] = printer_details(status)
    fields["logs"] = SUPPORT_ISSUE_LOGS
    url = ISSUE_URL_PREFIX + urllib.parse.urlencode(fields, quote_via=urllib.parse.quote)
    if len(url) > MAX_ISSUE_URL:
        del fields["printer_details"]
        url = ISSUE_URL_PREFIX + urllib.parse.urlencode(fields, quote_via=urllib.parse.quote)
    return url


def support_files() -> list[tuple[str, Path]]:
    """What a support issue needs, by its name in the bundle: Klipper's and Moonraker's logs, today's and the last
    rotated one, Valve Oil's Box link log, what Valve Oil's own runs wrote, and the last Diagnostics report."""
    files = []
    for name in ("klippy.log", "moonraker.log"):
        files.append((f"logs/{name}", LOGS / name))
        rotated = sorted(LOGS.glob(f"{name}.*"), key=_mtime)
        if rotated:
            files.append((f"logs/{rotated[-1].name}", rotated[-1]))
    files.append(("logs/vo_box_link.log", LOGS / "vo_box_link.log"))
    files += [
        ("valve-oil/update-now.log", JOB_LOG),
        ("valve-oil/addon-change.log", CACHE / "addons/job.log"),
        ("valve-oil/firmware-update.log", CACHE / "firmware-update/update.log"),
        ("valve-oil/qidi-package.log", CACHE / "firmware-update/qidi-package.log"),
        ("valve-oil/firmware-update-result.json", FIRMWARE_UPDATE_RESULT),
        ("valve-oil/diagnostics.txt", DIAGNOSTICS_REPORT),
        ("valve-oil/RELEASE_METADATA.yaml", RELEASE_METADATA),
        ("firmware_manifest.json", FIRMWARE_MANIFEST),
    ]
    return [(name, path) for name, path in files if path.is_file() and not path.is_symlink()]


def support_bundle(status: dict):
    """The support bundle as an unnamed temporary file, read from the start: the status and printer details this
    service reports, and the newest SUPPORT_LOG_BYTES of each file in support_files."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    handle = tempfile.TemporaryFile(dir=STATE_DIR)
    try:
        with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.writestr("printer-details.txt", printer_details(status) + "\n")
            archive.writestr("valve-oil-status.json", json.dumps(status, indent=2) + "\n")
            for name, path in support_files():
                _add_tail(archive, name, path)
        handle.seek(0)
    except BaseException:
        handle.close()
        raise
    return handle


def support_bundle_name(status: dict) -> str:
    printer = status["printer"]["id"] if PLAIN.match(status["printer"]["id"] or "") else "printer"
    return f"valve-oil-support-{printer}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.zip"


def _add_tail(archive: zipfile.ZipFile, name: str, path: Path) -> None:
    try:
        with path.open("rb") as source:
            size = os.fstat(source.fileno()).st_size
            info = zipfile.ZipInfo(name, time.localtime(os.fstat(source.fileno()).st_mtime)[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            with archive.open(info, "w") as target:
                if size > SUPPORT_LOG_BYTES:
                    source.seek(size - SUPPORT_LOG_BYTES)
                    source.readline()  # the rest of a line cut in two
                    target.write(f"[The start of this {size // (1024 * 1024)} MB file is left out.]\n".encode())
                shutil.copyfileobj(source, target, 1024 * 1024)
    except OSError as exc:
        log(f"Support bundle left out {name}: {exc}")


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


# ---- what the printer is doing now --------------------------------------------------------------------------------


def live_printer() -> tuple[str, ...] | None:
    """The printer-state blockers that hold now, as the installer's checks see them (firmware_update.printer_blockers):
    printing (or paused), heaters_on, klipper_not_ready, or printer_state_unknown; None when Moonraker can't be
    read."""
    try:
        status = _moonraker_objects(("print_stats", "webhooks", "heaters"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    state = _record(status.get("print_stats")).get("state")
    if not isinstance(state, str) or not state:
        # A Klipper halted on an error has no print state; nothing is updated onto it.
        return ("klipper_not_ready",) if _klipper_state() == "error" else ("printer_state_unknown",)
    if state in ("printing", "paused"):
        return ("printing",)
    if _record(status.get("webhooks")).get("state") != "ready":
        return ("klipper_not_ready",)
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


def _live_blockers(recorded: list[dict], live: tuple[str, ...] | None, enabled: bool,
                   outside: dict | None = None) -> list[dict]:
    """Why a waiting update hasn't run: nothing while automatic updates are off; otherwise what the check recorded,
    with the printer's state as it is now when Moonraker answers, and the update window as it is now."""
    if not enabled:
        return []
    recorded = [blocker for blocker in recorded if blocker["code"] != "outside_window"]
    window = [outside] if outside else []
    if live is None:
        return window + recorded
    kept = [blocker for blocker in recorded if blocker["code"] not in LIVE_BLOCKERS]
    return [{"code": code, "message": LIVE_BLOCKERS[code]} for code in live] + window + kept


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
                if path.exists() and not (recorded and path.name == ".vo_recovery_required")]
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
    # A manual install of the waiting release, or of a later one, ends the wait before the next check rewrites the
    # file. Installing the same release again doesn't change its version, so that install removes a reinstall item
    # itself.
    if item is None or item["reinstall"] or item["kind"] != "release" or not installed:
        return item is not None
    if item["version"] == installed:
        return False
    waiting, running = _version_key(item["version"]), _version_key(installed)
    return waiting is None or running is None or waiting > running


def _version_key(version: str | None) -> tuple[int, ...] | None:
    if not version or not re.fullmatch(r"\d+(?:\.\d+)*", version):
        return None
    return tuple(int(part) for part in version.split("."))


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


def _progress() -> dict | None:
    """The install running now, or the last one, step by step; a running one whose process is gone was interrupted."""
    data = _record(_read_json(PROGRESS_FILE, limit=MAX_STATUS_BYTES))
    if data.get("schema") != STATUS_SCHEMA or data.get("state") not in PROGRESS_STATES:
        return None
    steps = [step[:MAX_TEXT] for step in data.get("steps") or [] if isinstance(step, str) and step][:MAX_PROGRESS_STEPS] \
        if isinstance(data.get("steps"), list) else []
    index = data.get("index")
    state = data["state"]
    if state == "running" and not _run_active(data):
        state = "interrupted"
    log = _text(data.get("log"))
    return {
        "state": state,
        "origin": data.get("origin") if data.get("origin") in PROGRESS_ORIGINS else None,
        "steps": steps,
        "index": index if isinstance(index, int) and 0 <= index <= len(steps) else 0,
        "detail": _text(data.get("detail")),
        "from_version": _text(data.get("from_version")),
        "to_version": _text(data.get("to_version")),
        "started_at": _text(data.get("started_at")),
        "finished_at": _text(data.get("finished_at")),
        "log": log if log and RUN_LOG_NAME.match(log) else None,
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
    server_version = "vo-status"
    jobs: Jobs
    diagnostics: Diagnostics
    # One support bundle at a time: each reads tens of megabytes of logs.
    bundling = threading.Lock()

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
        elif route == "support-bundle":
            self._send_support_bundle()
        elif route == "diagnostics":
            self._send(HTTPStatus.OK, self.diagnostics.current())
        elif route in ("auto-update", "update-now"):
            self._send(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "Use POST."}, allow="POST")
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def do_POST(self) -> None:
        try:
            body = self._check_post()
            route = self._route()
            if route == "auto-update":
                self._auto_update(body)
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
            elif route == "diagnostics":
                if set(body) - {"since"}:
                    raise RequestError(HTTPStatus.BAD_REQUEST, 'Expected {"since": "all"}, "day", or "start".')
                self._send(HTTPStatus.ACCEPTED, self.diagnostics.start(body.get("since", "all"), status_body(self.jobs)))
            elif route in ("status", "support-bundle"):
                raise RequestError(HTTPStatus.METHOD_NOT_ALLOWED, "Use GET.")
            else:
                raise RequestError(HTTPStatus.NOT_FOUND, "Not found.")
        except Conflict as exc:
            self._send(HTTPStatus.CONFLICT, {"error": str(exc), "blocked_by": exc.blocked_by})
        except RequestError as exc:
            allow = {"status": "GET", "support-bundle": "GET"}.get(self._route(), "POST") if exc.status == HTTPStatus.METHOD_NOT_ALLOWED else None
            self._send(exc.status, {"error": str(exc)}, allow=allow)

    def _auto_update(self, body: dict) -> None:
        if not body or not set(body) <= {"enabled", "window", "timezone"}:
            raise RequestError(HTTPStatus.BAD_REQUEST,
                               'Expected any of "enabled", "window", and "timezone", such as {"enabled": true}.')
        if "enabled" in body and not isinstance(body["enabled"], bool):
            raise RequestError(HTTPStatus.BAD_REQUEST, 'Expected {"enabled": true} or {"enabled": false}.')
        settings = update_settings()
        try:
            if "window" in body:
                settings["window"] = window_from(body["window"])
        except ValueError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        if "timezone" in body:
            if zone(body["timezone"]) is None:
                raise RequestError(HTTPStatus.BAD_REQUEST, "The timezone isn't one this printer knows.")
            settings["timezone"], settings["timezone_source"] = body["timezone"], "browser"
        try:
            if "window" in body or "timezone" in body:
                save_update_settings(settings)
                log(f"Update window {settings['window']} in {settings['timezone'] or 'UTC'}")
            if "enabled" in body:
                set_auto_update(body["enabled"])
                log(f"Automatic updates {'on' if body['enabled'] else 'off'}")
        except OSError as exc:
            log(f"Automatic update settings failed: {exc}")
            raise RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "The automatic update setting could not be saved.") from exc

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
        # nginx strips its /vo-status/ prefix; the API is exactly api/<name>.
        parts = self.path.split("?", 1)[0].split("/")
        if len(parts) == 3 and parts[0] == "" and parts[1] == "api" and parts[2] in ("status", "auto-update", "update-now", "support-bundle",
                                                                                     "diagnostics"):
            return parts[2]
        return None

    def _send_support_bundle(self) -> None:
        if not self.bundling.acquire(blocking=False):
            self._send(HTTPStatus.CONFLICT, {"error": "A support bundle is already being made."})
            return
        try:
            status = status_body(self.jobs)
            try:
                bundle = support_bundle(status)
            except (OSError, zipfile.BadZipFile) as exc:
                log(f"Support bundle failed: {exc}")
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "The support bundle could not be made."})
                return
            with bundle:
                size = os.fstat(bundle.fileno()).st_size
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{support_bundle_name(status)}"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                shutil.copyfileobj(bundle, self.wfile, 1024 * 1024)
            log(f"Sent a support bundle of {size} bytes")
        finally:
            self.bundling.release()

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
    Handler.diagnostics = Diagnostics()
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    server.daemon_threads = True
    log(f"Listening on {LISTEN_HOST}:{LISTEN_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
