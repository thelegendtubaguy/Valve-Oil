# tltg-optimized:addons-server
"""Valve Oil add-ons service for Fluidd.

Lists the package's optional add-ons and installs, updates, or removes them by running the bundled addons.sh, one job
at a time. Jobs run in their own session and record their result in files, so they finish and are reported even if
this service restarts. nginx authorizes every request through Moonraker before it reaches this loopback-only server.
"""

from __future__ import annotations

import configparser
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
ACTIONS = ("install", "update", "remove", "relink")
# Longest server address a relink accepts; addons.sh checks the address itself.
MAX_SERVER_URL = 200
ADDON_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
# POSTs must carry this header. A web page from another origin can't add it without a CORS preflight, which this
# server never approves, so it can't make a browser on a trusted address start a job.
ACTION_HEADER = ("X-Valve-Oil", "addons")

OCTOEVERYWHERE_STATUS = "http://127.0.0.1:7132/"
OCTOEVERYWHERE_LINK_URL = "https://octoeverywhere.com/getstarted?printerid={printer_id}"
OCTOEVERYWHERE_SHORTCODE = "https://octoeverywhere.com/api/shortcode/create"
OCTOEVERYWHERE_PRINTER_INFO = "https://octoeverywhere.com/api/printer/info"
OCTOEVERYWHERE_CODE_PAGE = "https://octoeverywhere.com/code"
# OctoEverywhere's CDN refuses Python's default User-Agent.
OCTOEVERYWHERE_HEADERS = {"Content-Type": "application/json", "User-Agent": "Valve-Oil-addons"}


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

    def start(self, addon: str, action: str, options: tuple[str, ...] = ()) -> dict:
        with self._lock:
            if self.busy():
                raise Conflict("Another add-on change is still running.")
            job_id = uuid.uuid4().hex
            JOB_RC.unlink(missing_ok=True)
            with JOB_LOG.open("w", encoding="utf-8") as output:
                process = subprocess.Popen(
                    ["/bin/sh", "-c", '"$@"; echo $? > "$TLTG_ADDONS_RC"', "sh", str(ADDONS_SH), action, addon, *options, "--plain", "--yes"],
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


class OctoEverywhereLink:
    """Whether OctoEverywhere's service has the printer on an account.

    The plugin learns whether it's linked only when it connects to OctoEverywhere, so it keeps saying it isn't after
    the account is linked. While it says so, this asks OctoEverywhere's service in the background, at most every
    CHECK_SECONDS, and status reads the last answer.
    """

    CHECK_SECONDS = 20.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._printer_id: str | None = None
        self._linked = False
        self._checked = 0.0
        self._checking = False

    def linked(self, printer_id: str) -> bool:
        with self._lock:
            if printer_id != self._printer_id:
                self._printer_id, self._linked, self._checked = printer_id, False, 0.0
            if not self._checking and time.monotonic() - self._checked >= self.CHECK_SECONDS:
                self._checking = True
                threading.Thread(target=self._check, args=(printer_id,), daemon=True).start()
            return self._linked

    def _check(self, printer_id: str) -> None:
        linked = None
        body = json.dumps({"Id": printer_id}).encode("utf-8")
        request = urllib.request.Request(OCTOEVERYWHERE_PRINTER_INFO, data=body, headers=OCTOEVERYWHERE_HEADERS, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10.0) as response:
                linked = json.load(response)["Result"]["HasOwners"] is True
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log(f"OctoEverywhere printer info failed: {exc}")
        with self._lock:
            if printer_id == self._printer_id:
                if linked is not None:
                    self._linked = linked
                self._checked = time.monotonic()
            self._checking = False


OCTOEVERYWHERE_LINK = OctoEverywhereLink()


def octoeverywhere_status() -> dict:
    try:
        data = _get_json(OCTOEVERYWHERE_STATUS, timeout=2.0)
        printer_id = str(data["PluginId"])
    except (OSError, ValueError, KeyError, TypeError):
        return {"unavailable": "OctoEverywhere isn't answering yet. It can take a minute after starting."}
    return {
        "connected": bool(data.get("IsConnectedToOctoEverywhere")),
        "printer_connected": bool(data.get("IsConnectedToPrinter")),
        "linked": bool(data.get("IsAccountLinked")) or OCTOEVERYWHERE_LINK.linked(printer_id),
        "printer_id": printer_id,
        "link_url": OCTOEVERYWHERE_LINK_URL.format(printer_id=printer_id),
    }


def octoeverywhere_link_code() -> dict:
    status = octoeverywhere_status()
    if "printer_id" not in status:
        raise RequestError(HTTPStatus.BAD_GATEWAY, status["unavailable"])
    body = json.dumps({"Type": 1, "PrinterId": status["printer_id"]}).encode("utf-8")
    request = urllib.request.Request(OCTOEVERYWHERE_SHORTCODE, data=body, headers=OCTOEVERYWHERE_HEADERS, method="POST")
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


OBICO_DATA = ADDONS_ROOT / "obico-data"
OBICO_CONFIG = OBICO_DATA / "moonraker-obico.cfg"
# Written by Valve Oil's Obico launcher while the plugin waits to be linked.
OBICO_STATUS = OBICO_DATA / "tltg-status.json"
OBICO_CLOUD = "https://app.obico.io"


def _obico_state() -> tuple[str, bool, dict]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(OBICO_CONFIG.read_text(encoding="utf-8"))
    except (OSError, configparser.Error):
        pass
    server = (parser.get("server", "url", fallback="") or OBICO_CLOUD).strip().rstrip("/")
    linked = bool(parser.get("server", "auth_token", fallback="").strip())
    try:
        status = json.loads(OBICO_STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        status = {}
    return server, linked, status if isinstance(status, dict) else {}


def obico_status() -> dict:
    server, linked, status = _obico_state()
    result = {"server": server, "cloud": server == OBICO_CLOUD, "linked": linked, "discovering": False}
    if not linked:
        # The code Obico's server gave the plugin while it waits to be linked; it changes while it waits.
        result["discovering"] = status.get("discovering") is True
        code = status.get("one_time_passcode")
        if result["discovering"] and isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9]{1,32}", code):
            result["link_code"] = code
    return result


TAILSCALE_CLI = ADDONS_ROOT / "tailscale/app/tailscale"
TAILSCALE_SOCKET = ADDONS_ROOT / "tailscale-data/tailscaled.sock"
# How long a login waits for Tailscale's server to hand tailscaled a new sign-in address.
TAILSCALE_LOGIN_WAIT = 12.0
TAILSCALE_UNAVAILABLE = "Tailscale isn't answering yet. It can take a few seconds after starting."


def _tailscale(*args: str, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(TAILSCALE_CLI), f"--socket={TAILSCALE_SOCKET}", *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
    )


def _tailscale_state() -> dict | None:
    try:
        data = json.loads(_tailscale("status", "--json", "--peers=false", timeout=5.0).stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("BackendState"), str) else None


def _tailscale_login_url(data: dict) -> str | None:
    url = data.get("AuthURL")
    return url if isinstance(url, str) and re.fullmatch(r"https?://[!-~]{1,300}", url) else None


def tailscale_status() -> dict:
    data = _tailscale_state()
    if data is None:
        return {"unavailable": TAILSCALE_UNAVAILABLE}
    state = data["BackendState"]
    node = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    tailnet = data.get("CurrentTailnet") if isinstance(data.get("CurrentTailnet"), dict) else {}
    result = {
        # NeedsLogin, NeedsMachineAuth (waiting for approval in the tailnet's admin console), Stopped, Starting, Running.
        "state": state,
        "connected": state == "Running",
        "name": str(node.get("DNSName") or "").rstrip("."),
        "addresses": [address for address in data.get("TailscaleIPs") or [] if isinstance(address, str)],
        "tailnet": str(tailnet.get("Name") or ""),
        "key_expiry": node.get("KeyExpiry") if isinstance(node.get("KeyExpiry"), str) else None,
        # What tailscaled reports wrong while connected; before that it only says it is stopped or starting.
        "problems": [problem for problem in data.get("Health") or [] if isinstance(problem, str)][:5] if state == "Running" else [],
    }
    if state == "NeedsLogin":
        result["login_url"] = _tailscale_login_url(data)
    return result


def tailscale_login() -> dict:
    """Connect the printer to a tailnet: a sign-in address for a printer that isn't logged in, or reconnect one that is."""
    data = _tailscale_state()
    if data is None:
        raise RequestError(HTTPStatus.BAD_GATEWAY, TAILSCALE_UNAVAILABLE)
    if data["BackendState"] != "NeedsLogin":
        # Logged in but stopped, as after `tailscale down`: bring it back up with its settings as they are.
        try:
            result = _tailscale("up", "--timeout=10s", timeout=15.0)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RequestError(HTTPStatus.BAD_GATEWAY, "Tailscale didn't connect.") from exc
        if result.returncode != 0:
            log(f"tailscale up failed: {result.stdout.strip()}")
            raise RequestError(HTTPStatus.BAD_GATEWAY, "Tailscale didn't connect. Run `tailscale up` over SSH to see why.")
        return tailscale_status()
    # `tailscale login` asks for a new sign-in address and then waits for the sign-in; the address stays valid after
    # it gives up, so it is left to time out while the panel shows the address.
    previous = _tailscale_login_url(data)
    try:
        process = subprocess.Popen(
            [str(TAILSCALE_CLI), f"--socket={TAILSCALE_SOCKET}", "login", "--timeout=30s"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise RequestError(HTTPStatus.BAD_GATEWAY, TAILSCALE_UNAVAILABLE) from exc
    threading.Thread(target=process.wait, daemon=True).start()
    deadline = time.monotonic() + TAILSCALE_LOGIN_WAIT
    while time.monotonic() < deadline:
        time.sleep(0.5)
        data = _tailscale_state()
        if data is None:
            continue
        if data["BackendState"] != "NeedsLogin" or _tailscale_login_url(data) not in {None, previous}:
            return tailscale_status()
    raise RequestError(HTTPStatus.BAD_GATEWAY, "Tailscale's server didn't answer. Check the printer's internet connection and try again.")


def tailscale_logout() -> dict:
    try:
        result = _tailscale("logout", timeout=20.0)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RequestError(HTTPStatus.BAD_GATEWAY, "Tailscale didn't log out.") from exc
    if result.returncode != 0:
        log(f"tailscale logout failed: {result.stdout.strip()}")
        raise RequestError(HTTPStatus.BAD_GATEWAY, "Tailscale didn't log out.")
    return tailscale_status()


STATUS_PROBES = {"octoeverywhere": octoeverywhere_status, "obico": obico_status, "tailscale": tailscale_status}
EXTRA_ACTIONS = {
    ("octoeverywhere", "link-code"): octoeverywhere_link_code,
    ("tailscale", "login"): tailscale_login,
    ("tailscale", "logout"): tailscale_logout,
}


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

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # The page closed or reloaded before the response was written.

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
            body = self._check_post()
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
            self._send(HTTPStatus.ACCEPTED, {"job": self.jobs.start(addon, action, _options(action, body))})
        except Conflict as exc:
            self._send(HTTPStatus.CONFLICT, {"error": str(exc)})
        except RequestError as exc:
            self._send(exc.status, {"error": str(exc)})

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
        length = int(self.headers.get("Content-Length") or 0)
        if length > 4096:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Request too large.")
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except ValueError as exc:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Expected a JSON request.") from exc
        if not isinstance(body, dict):
            raise RequestError(HTTPStatus.BAD_REQUEST, "Expected a JSON object.")
        return body

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


def _options(action: str, body: dict) -> tuple[str, ...]:
    """addons.sh options for a request: a relink may name the server to link to."""
    server = body.get("server")
    if server is None:
        return ()
    if action != "relink" or not isinstance(server, str) or not server.strip() or len(server) > MAX_SERVER_URL:
        raise RequestError(HTTPStatus.BAD_REQUEST, "Invalid server address.")
    return (f"--server={server.strip()}",)


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
