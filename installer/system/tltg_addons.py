# tltg-optimized:addons-server
"""Valve Oil add-ons service for Fluidd.

Lists the package's optional add-ons and installs, updates, or removes them by running the bundled addons.sh, one job
at a time. Jobs run in their own session and record their result in files, so they finish and are reported even if
this service restarts. nginx authorizes every request through Moonraker before it reaches this loopback-only server.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 7131
BUNDLE = Path("/home/qidi/tltg-optimized-macros")
ADDONS_SH = BUNDLE / "addons.sh"
MANIFEST = BUNDLE / "installer/package.yaml"
HOME = Path("/home/qidi")
ADDONS_ROOT = HOME / "tltg-addons"
UNIT_DIR = Path("/etc/systemd/system")
STATE_DIR = Path("/home/qidi/.cache/tltg-addons")
JOB_FILE = STATE_DIR / "job.json"
JOB_LOG = STATE_DIR / "job.log"
JOB_RC = STATE_DIR / "job.rc"
MOONRAKER_PRINT_STATS = "http://127.0.0.1:7125/printer/objects/query?print_stats=state"
LIST_TIMEOUT = 120.0
LOG_LINES = 200
ACTIONS = ("install", "update", "remove")
ADDON_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
# POSTs must carry this header. A web page from another origin can't add it without a CORS preflight, which this
# server never approves, so it can't make a browser on a trusted address start a job.
ACTION_HEADER = ("X-Valve-Oil", "addons")

OCTOEVERYWHERE_STATUS = "http://127.0.0.1:7132/"
OCTOEVERYWHERE_LINK_URL = "https://octoeverywhere.com/getstarted?printerid={printer_id}"
OCTOEVERYWHERE_SHORTCODE = "https://octoeverywhere.com/api/shortcode/create"
OCTOEVERYWHERE_CODE_PAGE = "https://octoeverywhere.com/code"


def log(message: str) -> None:
    print(message, flush=True)


class Catalog:
    """Caches `addons.sh list --json`, which starts the installer runtime and takes several seconds on the printer.

    The list is loaded when the service starts and reloaded in the background when the package or anything the list
    depends on changes on disk; requests get the current list meanwhile. A finished job reloads it before its result is
    reported, so Fluidd never shows a finished job beside its add-on's earlier state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()
        self._entries: list[dict] | None = None
        self._signature: tuple = ()
        self._refreshing = False
        self.error: str | None = None
        threading.Thread(target=self.reload, daemon=True).start()

    def entries(self) -> list[dict]:
        if self._entries is None:
            self.reload()
        elif self._signature != _signature() and not self._refreshing:
            self._refreshing = True
            threading.Thread(target=self.reload, daemon=True).start()
        with self._lock:
            return [dict(entry) for entry in self._entries or []]

    def reload(self, *, force: bool = False) -> None:
        with self._load_lock:
            signature = _signature()
            if not force and self._entries is not None and signature == self._signature:
                self._refreshing = False
                return
            try:
                result = subprocess.run(
                    [str(ADDONS_SH), "list", "--json"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=LIST_TIMEOUT,
                    check=False,
                )
                if result.returncode != 0:
                    raise RuntimeError((result.stderr or result.stdout).strip() or "addons.sh list failed")
                entries = json.loads(result.stdout)["addons"]
                error = None
            except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
                log(f"Add-on list failed: {exc}")
                entries = None
                error = "Valve Oil's add-on list could not be read."
            with self._lock:
                if entries is not None or self._entries is None:
                    self._entries = entries or []
                self.error = error
                self._signature = signature
                self._refreshing = False


def _signature() -> tuple:
    # What `addons.sh list` reads: the package manifest, the add-on directories, their unit files, and installs made
    # outside Valve Oil in the home directory.
    return tuple(_mtime(path) for path in (MANIFEST, ADDONS_ROOT, *ADDONS_ROOT.glob("*/.tltg-addon.json"), UNIT_DIR, HOME))


class Jobs:
    def __init__(self, catalog: Catalog) -> None:
        self._lock = threading.Lock()
        self._catalog = catalog
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        job = self._read()
        if job and job.get("state") == "running":
            threading.Thread(target=self._watch, args=(job["id"], job.get("pid")), daemon=True).start()

    def current(self) -> dict | None:
        job = self._read()
        if job is None:
            return None
        job["log"] = _tail(JOB_LOG, LOG_LINES)
        job["message"] = next((line for line in reversed(job["log"]) if line.strip()), "")
        job.pop("pid", None)
        return job

    def busy(self) -> bool:
        job = self._read()
        return bool(job and job.get("state") == "running")

    def start(self, addon: str, action: str) -> dict:
        with self._lock:
            if self.busy():
                raise Conflict("Another add-on change is still running.")
            job_id = uuid.uuid4().hex
            JOB_RC.unlink(missing_ok=True)
            with JOB_LOG.open("w", encoding="utf-8") as output:
                process = subprocess.Popen(
                    ["/bin/sh", "-c", '"$@"; echo $? > "$TLTG_ADDONS_RC"', "sh", str(ADDONS_SH), action, addon, "--plain", "--yes"],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env={**os.environ, "TLTG_ADDONS_RC": str(JOB_RC), "PYTHONUNBUFFERED": "1"},
                )
            job = {
                "id": job_id,
                "addon": addon,
                "action": action,
                "state": "running",
                "started": time.time(),
                "finished": None,
                "pid": process.pid,
            }
            self._write(job)
            threading.Thread(target=self._wait, args=(job_id, process), daemon=True).start()
            log(f"Started {action} {addon}")
            return self.current()

    def _wait(self, job_id: str, process: subprocess.Popen) -> None:
        process.wait()
        self._finish(job_id)

    def _watch(self, job_id: str, pid: int | None) -> None:
        # A job started before this service restarted isn't our child; poll until its process is gone.
        while pid and _alive(pid):
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
            job["finished"] = time.time()
            if code is None:
                with JOB_LOG.open("a", encoding="utf-8") as output:
                    output.write("The add-on change stopped before it finished.\n")
            self._catalog.reload(force=True)
            self._write(job)
            log(f"Finished {job['action']} {job['addon']}: {job['state']}")

    def _read(self) -> dict | None:
        try:
            data = json.loads(JOB_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write(self, job: dict) -> None:
        tmp = JOB_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(job), encoding="utf-8")
        tmp.replace(JOB_FILE)


class Conflict(Exception):
    pass


class RequestError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def octoeverywhere_status() -> dict:
    try:
        data = _get_json(OCTOEVERYWHERE_STATUS, timeout=2.0)
        printer_id = str(data["PluginId"])
    except (OSError, ValueError, KeyError, TypeError):
        return {"unavailable": "OctoEverywhere isn't answering yet. It can take a minute after starting."}
    return {
        "connected": bool(data.get("IsConnectedToOctoEverywhere")),
        "printer_connected": bool(data.get("IsConnectedToPrinter")),
        "linked": bool(data.get("IsAccountLinked")),
        "printer_id": printer_id,
        "link_url": OCTOEVERYWHERE_LINK_URL.format(printer_id=printer_id),
    }


def octoeverywhere_link_code() -> dict:
    status = octoeverywhere_status()
    if "printer_id" not in status:
        raise RequestError(HTTPStatus.BAD_GATEWAY, status["unavailable"])
    body = json.dumps({"Type": 1, "PrinterId": status["printer_id"]}).encode("utf-8")
    # OctoEverywhere's CDN refuses Python's default User-Agent.
    headers = {"Content-Type": "application/json", "User-Agent": "Valve-Oil-addons"}
    request = urllib.request.Request(OCTOEVERYWHERE_SHORTCODE, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10.0) as response:
            result = json.load(response)["Result"]
        code = str(result["Code"])
        expires = int(result["ValidForSeconds"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log(f"OctoEverywhere link code failed: {exc}")
        raise RequestError(HTTPStatus.BAD_GATEWAY, "OctoEverywhere didn't return a link code. Use Link account instead.") from exc
    if not code or expires <= 0:
        raise RequestError(HTTPStatus.BAD_GATEWAY, "OctoEverywhere didn't return a link code. Use Link account instead.")
    return {"code": code, "url": OCTOEVERYWHERE_CODE_PAGE, "expires_in": expires}


STATUS_PROBES = {"octoeverywhere": octoeverywhere_status}
EXTRA_ACTIONS = {("octoeverywhere", "link-code"): octoeverywhere_link_code}


def printer_busy() -> bool:
    try:
        state = _get_json(MOONRAKER_PRINT_STATS, timeout=2.0)["result"]["status"]["print_stats"]["state"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return state in {"printing", "paused"}


class Handler(BaseHTTPRequestHandler):
    server_version = "tltg-addons"
    catalog: Catalog
    jobs: Jobs

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler's signature
        pass

    def do_GET(self) -> None:
        if self._route() != ("addons",):
            self._send(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        entries = self.catalog.entries()
        for entry in entries:
            probe = STATUS_PROBES.get(entry["id"])
            installed = entry.get("state") in {"installed", "update_available"}
            entry["status"] = probe() if probe and installed else None
        body = {
            "busy": self.jobs.busy(),
            "printer_busy": printer_busy(),
            "addons": entries,
            "job": self.jobs.current(),
        }
        if self.catalog.error:
            body["error"] = self.catalog.error
        self._send(HTTPStatus.OK, body)

    def do_POST(self) -> None:
        try:
            self._check_post()
            route = self._route()
            if len(route) != 3 or route[0] != "addons" or not ADDON_ID.match(route[1]):
                raise RequestError(HTTPStatus.NOT_FOUND, "Not found.")
            addon, action = route[1], route[2]
            extra = EXTRA_ACTIONS.get((addon, action))
            if extra is not None:
                self._send(HTTPStatus.OK, extra())
                return
            if action not in ACTIONS:
                raise RequestError(HTTPStatus.NOT_FOUND, "Not found.")
            if not any(entry["id"] == addon for entry in self.catalog.entries()):
                raise RequestError(HTTPStatus.NOT_FOUND, f"Unknown add-on: {addon}")
            if action != "remove" and printer_busy():
                raise Conflict("Add-ons can be changed only while the printer is not printing.")
            self._send(HTTPStatus.ACCEPTED, {"job": self.jobs.start(addon, action)})
        except Conflict as exc:
            self._send(HTTPStatus.CONFLICT, {"error": str(exc)})
        except RequestError as exc:
            self._send(exc.status, {"error": str(exc)})

    def _check_post(self) -> None:
        name, value = ACTION_HEADER
        if self.headers.get(name) != value:
            raise RequestError(HTTPStatus.FORBIDDEN, f"Missing {name} header.")
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise RequestError(HTTPStatus.BAD_REQUEST, "Expected a JSON request.")
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and (not host or origin.split("://", 1)[-1] != host):
            raise RequestError(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed.")
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4096:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Request too large.")
        if length:
            self.rfile.read(length)

    def _route(self) -> tuple[str, ...]:
        # nginx strips its /tltg-addons/ prefix; the API lives under api/.
        path = self.path.split("?", 1)[0].strip("/").split("/")
        if not path or path[0] != "api":
            return ()
        return tuple(part for part in path[1:] if part)

    def _send(self, status: HTTPStatus, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _get_json(url: str, *, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def _tail(path: Path, lines: int) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> None:
    catalog = Catalog()
    Handler.catalog = catalog
    Handler.jobs = Jobs(catalog)
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    server.daemon_threads = True
    log(f"Listening on {LISTEN_HOST}:{LISTEN_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
