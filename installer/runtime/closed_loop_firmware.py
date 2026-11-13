"""The Max 4's closed-loop X/Y motor drivers during a QIDI firmware update (design D13).

The drivers are updated with QIDI's own Klipper command and image, as QIDI's updater does, while the old Klipper still
runs and before QIDI's package is installed: `UPGRADE_MOTOR STEPPER=<x|y> FILE=<image>` (QIDI's `upgrade_motor`
module) silences the bus, tells the driver to enter its loader, switches the mainboard to a passthrough at 115200 baud
and sends the image with YMODEM, then waits for the driver to answer again. The G-code returns after about 4 s and
the transfer runs on in Klipper's timers; its progress is `closed_loop <axis>.upgrade_status`.

QIDI's module leaves gaps this code closes (from running QIDI's modules against a simulated mainboard and driver):
it calls any status reply after a transfer a success, so success here also needs the driver to report the target
version; it checks nothing about the file, so the image is checked here, by exact size and SHA-256, immediately before
each send; its hardware gate passes a driver it knows nothing about, so a driver is sent an image only when it reported
QIDI's previous version, hardware "9.1", and an idle state before the first transfer (an unknown driver is retried only
when this run recorded it as admitted); and nothing stops other traffic on the bus, so the caller first stops
Moonraker and the touchscreen and talks to Klipper only through its API socket, and `M84` turns the motors off.
`UPGRADE_MOTOR_V13`, for the other driver family, is never sent.

A transfer run to its end was checked on a Max 4 (both drivers, about 51 s each). What a power cut partway through
one does to a driver could not be established: the loader the update never overwrites keeps a flag in flash and clears
it once a transfer completes, which points to a retry being possible, but that is inference.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import time
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path

AXES = ("x", "y")
HARDWARE = "9.1"  # what the admitted drivers report: 0x80 | encoder direction << 4 | 1, not a board identity
MAX_TRANSFERS = 3  # per driver per run
START_SECONDS = 6.0  # after the G-code's reply, for the transfer to show as in progress
PREPARE_SECONDS = 25.0  # for a session Klipper shows as started (address, upgrading) to reach in progress
STALL_SECONDS = 25.0  # without progress; QIDI's own timeouts (15-20 s) end a stalled transfer first
AWAIT_SECONDS = 70.0  # waiting for the driver to answer after the image was sent
VERIFY_SECONDS = 45.0  # after QIDI's success, for the driver to report the new version
SETTLE_SECONDS = 45.0  # after Klipper starts, for the drivers to report a version (at least 4 query cycles)
READY_SECONDS = 180.0  # for Klipper to be ready, as after a boot or a FIRMWARE_RESTART
POLL_SECONDS = 1.0


class KlipperError(Exception):
    pass


class KlipperClosed(KlipperError):
    """Klipper closed its API socket after the request was written, as it does when it restarts."""


class DriverRefused(Exception):
    """Klipper refused to start a driver's first transfer; nothing was sent to that driver."""

    def __init__(self, axis: str, message: str):
        super().__init__(message)
        self.axis, self.message = axis, message


class DriverUpdateFailed(Exception):
    """A driver could not be updated after it was sent an image; the bus was reset with FIRMWARE_RESTART."""

    def __init__(self, axis: str, message: str):
        super().__init__(message)
        self.axis, self.message = axis, message
        self.reset = False  # FIRMWARE_RESTART reached Klipper afterwards


@dataclass(frozen=True)
class Driver:
    axis: str
    addr: int | None = None
    version: str | None = None  # stepper_version
    hardware: str | None = None  # stepper_hd_version
    version_ok: bool = False
    state: str | None = None  # stepper_state
    upgrading: bool = False  # upgrade_status.is_upgrading
    status: str | None = None  # upgrade_status.status
    percentage: float = 0.0

    def describe(self) -> str:
        return (f"{self.axis.upper()} driver reports firmware {self.version or 'unknown'}, hardware "
                f"{self.hardware or 'unknown'}, state {self.state or 'unknown'}")


@dataclass
class ClosedLoopResult:
    changes: bool  # QIDI's target package carries another driver version than the installed firmware's
    target: str | None  # the driver version QIDI's target package carries
    outcome: str  # "unchanged", "current", "flashed", "not_admitted", or "failed"
    drivers: list[dict] = field(default_factory=list)  # each driver as last read
    flashed: list[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---- Klipper's API socket -------------------------------------------------------------------------------------------


def klipper_api_path(paths, *, run=subprocess.run) -> Path:
    """The socket the running Klipper serves its API on: its `-a` argument, else Moonraker's `klippy_uds_address`,
    else Moonraker's default."""
    try:
        pid = run(["systemctl", "show", "klipper.service", "--property=MainPID", "--value"], capture_output=True,
                  text=True, timeout=10).stdout.strip()
        arguments = Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0") if pid and pid != "0" else []
    except (OSError, ValueError, subprocess.SubprocessError):
        arguments = []
    for index, argument in enumerate(arguments):
        text = argument.decode(errors="replace")
        if text in ("-a", "--api-server") and index + 1 < len(arguments):
            return Path(arguments[index + 1].decode(errors="replace"))
        if text.startswith("--api-server="):
            return Path(text.split("=", 1)[1])
    try:
        conf = (paths.config_root / "moonraker.conf").read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^\s*klippy_uds_address\s*[:=]\s*(\S+)", conf, re.M)
        if match:
            return Path(os.path.expanduser(match.group(1)))
    except OSError:
        pass
    return paths.printer_data_root / "comms/klippy.sock"


class KlipperAPI:
    """Klipper's API socket, as Moonraker talks to it: one JSON request at a time, each ended by 0x03. Console
    output Klipper sends meanwhile is kept, so a refusal can be read."""

    def __init__(self, path: Path, *, timeout: float = 10.0):
        self.path, self.timeout = Path(path), timeout
        self.sock = None
        self.buffer = b""
        self.next_id = 0
        self.output: list[str] = []

    def connect(self) -> None:
        self.close()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(str(self.path))
        except OSError:
            sock.close()
            raise
        self.sock, self.buffer = sock, b""
        self.call("gcode/subscribe_output", {"response_template": {"tltg": "output"}})

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def call(self, method: str, params: dict | None = None, *, timeout: float | None = None):
        if self.sock is None:
            raise KlipperError("not connected to Klipper")
        self.next_id += 1
        request_id = self.next_id
        written = False
        try:
            self.sock.sendall(json.dumps({"id": request_id, "method": method, "params": params or {}}).encode()
                              + b"\x03")
            written = True
            deadline = time.monotonic() + (timeout or self.timeout)
            while True:
                while b"\x03" not in self.buffer:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise KlipperError(f"Klipper didn't answer {method}")
                    self.sock.settimeout(remaining)
                    data = self.sock.recv(65536)
                    if not data:
                        self.close()
                        raise KlipperClosed("Klipper closed its API socket")
                    self.buffer += data
                raw, self.buffer = self.buffer.split(b"\x03", 1)
                message = json.loads(raw)
                if message.get("id") == request_id:
                    if "error" in message:
                        error = message["error"]
                        raise KlipperError(error.get("message") if isinstance(error, dict) else str(error))
                    return message.get("result")
                params = message.get("params")
                if isinstance(params, dict) and isinstance(params.get("response"), str):
                    self.output.append(params["response"])
        except (OSError, ValueError) as exc:
            self.close()
            if written and isinstance(exc, ConnectionError):
                raise KlipperClosed(f"Klipper closed its API socket: {exc}") from exc
            raise KlipperError(f"Klipper's API socket failed: {exc}") from exc

    def state(self) -> str | None:
        try:
            return (self.call("info") or {}).get("state")
        except KlipperError:
            return None

    def query(self, *names: str) -> dict:
        result = self.call("objects/query", {"objects": {name: None for name in names}})
        return (result or {}).get("status") or {}

    def gcode(self, script: str, *, timeout: float = 60.0) -> None:
        self.call("gcode/script", {"script": script}, timeout=timeout)

    def wait_ready(self, *, sleep, clock, seconds: float = READY_SECONDS) -> bool:
        deadline = clock() + seconds
        while True:
            if self.sock is None:
                try:
                    self.connect()
                except (OSError, KlipperError):
                    pass
            if self.sock is not None and self.state() == "ready":
                return True
            if clock() >= deadline:
                return False
            sleep(1.0)

    def firmware_restart(self, *, sleep, clock, seconds: float = 60.0) -> bool:
        """Send FIRMWARE_RESTART, which resets the mainboard and so takes it out of the driver passthrough whatever
        happened, reconnecting first when the socket was lost; then wait for Klipper to be ready. Returns whether
        Klipper took the command: it answered, or closed its socket after it as it restarted. A refusal, a timeout,
        or no socket is tried again (a second reset is harmless) until `seconds` have passed."""
        deadline = clock() + seconds
        sent = False
        while True:
            if self.sock is None:
                try:
                    self.connect()
                except (OSError, KlipperError):
                    pass
            if self.sock is not None:
                try:
                    self.gcode("FIRMWARE_RESTART", timeout=15)
                    sent = True
                except KlipperClosed:
                    sent = True
                except KlipperError:
                    pass
            if sent or clock() >= deadline:
                break
            sleep(1.0)
        self.close()
        if sent:
            sleep(2.0)
            self.wait_ready(sleep=sleep, clock=clock)
        return sent


# ---- reading the printer --------------------------------------------------------------------------------------------


def idle_blocker(api: KlipperAPI) -> str | None:
    """Why the drivers can't be updated now, as a status-file blocker code, or None: Klipper must be ready, with no
    print running or paused, no command running, and every heater target 0."""
    if api.state() != "ready":
        return "printer_state_unknown"
    try:
        status = api.query("print_stats", "pause_resume", "idle_timeout", "heaters")
        if status["print_stats"]["state"] in ("printing", "paused") or (status.get("pause_resume") or {}).get(
                "is_paused"):
            return "printing"
        if (status.get("idle_timeout") or {}).get("state") == "Printing":
            return "klipper_busy"
        # Klipper has no heaters object on a machine without heaters.
        heaters = (status.get("heaters") or {}).get("available_heaters") or []
        targets = api.query(*heaters) if heaters else {}
        if any(float((targets.get(name) or {}).get("target") or 0) != 0 for name in heaters):
            return "heaters_on"
    except (KlipperError, KeyError, TypeError, ValueError):
        return "printer_state_unknown"
    return None


def read_drivers(api: KlipperAPI) -> tuple[dict[str, Driver], tuple[int, ...]]:
    status = api.query(*(f"closed_loop {axis}" for axis in AXES), "cl_interface")
    drivers = {}
    for axis in AXES:
        raw = status.get(f"closed_loop {axis}") or {}
        upgrade = raw.get("upgrade_status") or {}
        drivers[axis] = Driver(
            axis=axis, addr=raw.get("stepper_addr") if isinstance(raw.get("stepper_addr"), int) else None,
            version=_text(raw.get("stepper_version")), hardware=_text(raw.get("stepper_hd_version")),
            version_ok=raw.get("version_ok") is True, state=_text(raw.get("stepper_state")),
            upgrading=upgrade.get("is_upgrading") is True, status=_text(upgrade.get("status")),
            percentage=float(upgrade.get("percentage") or 0))
    upgrading = (status.get("cl_interface") or {}).get("upgrading_addrs") or ()
    return drivers, tuple(addr for addr in upgrading if isinstance(addr, int))


def reported_versions(moonraker_url: str, *, urlopen) -> tuple[str | None, ...] | None:
    """The drivers' versions as Klipper reports them through Moonraker (read only, for the status), or None when they
    can't be read."""
    parts = urllib.parse.urlsplit(moonraker_url)
    prefix = parts.path.removesuffix("/printer/objects/query")
    query = "&".join(urllib.parse.quote(f"closed_loop {axis}") + "=stepper_version" for axis in AXES)
    url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, f"{prefix}/printer/objects/query", query, ""))
    try:
        with urlopen(url, timeout=10) as response:
            status = json.loads(response.read().decode("utf-8"))["result"]["status"]
        return tuple(_text((status.get(f"closed_loop {axis}") or {}).get("stepper_version")) for axis in AXES)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def verify_image(path: Path, *, size: int, sha256: str) -> None:
    """The image is a regular, readable file of exactly the pinned size and SHA-256, at a path that goes into a G-code
    line as it is, or this raises: QIDI's module sends whatever it is given, and a path it can't read stops Klipper
    with the bus left in upgrade mode."""
    if not str(path).isprintable() or any(character.isspace() or character in ";#\"'*" for character in str(path)):
        raise ValueError(f"the driver image's path {str(path)!r} can't go into a G-code command")
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size != size:
        raise ValueError(f"the driver image {path.name} is not the pinned file")
    with open(path, "rb") as handle:
        if hashlib.sha256(handle.read()).hexdigest() != sha256:
            raise ValueError(f"the driver image {path.name} doesn't match its pinned SHA-256")


# ---- the update ---------------------------------------------------------------------------------------------------


def update_drivers(*, api: KlipperAPI, image: Path, size: int, sha256: str, target: str, previous: tuple[str, ...],
                   state: dict, attempt: str, save, reporter, sleep, clock) -> ClosedLoopResult:
    """Bring both drivers to `target`, X then Y, from a version in `previous`.

    `state` is kept in the pending record and saved with `save()` before every send: which drivers were admitted and
    what they reported, how many transfers each had in this attempt of the run, which are verified. A resumed run
    passes it back. Returns "not_admitted" (nothing sent to either driver) when a driver is not exactly as expected.
    Raises DriverUpdateFailed when a driver that was sent an image can't be verified, or anything goes wrong once one
    was, after FIRMWARE_RESTART was sent to reset the bus (`reset` says whether Klipper took it); the other driver is
    not touched. Before anything was sent, a problem is raised as it is.
    """
    try:
        return _update(api=api, image=image, size=size, sha256=sha256, target=target, previous=previous, state=state,
                       attempt=attempt, save=save, reporter=reporter, sleep=sleep, clock=clock)
    except DriverUpdateFailed as failed:
        failed.reset = api.firmware_restart(sleep=sleep, clock=clock)
        raise
    except Exception as exc:  # noqa: BLE001 - once a driver was sent an image, every way out resets the bus
        if not state.get("sent"):
            raise
        axis = (state.get("sending") or {}).get("axis") or state.get("axis") or "?"
        failed = DriverUpdateFailed(axis, str(getattr(exc, "message", None) or exc) or type(exc).__name__)
        failed.reset = api.firmware_restart(sleep=sleep, clock=clock)
        raise failed from exc


def _update(*, api, image, size, sha256, target, previous, state, attempt, save, reporter, sleep, clock):
    result = ClosedLoopResult(changes=True, target=target, outcome="current")
    # Right after Klipper starts a healthy driver reports no version for a few seconds: wait for one before deciding.
    drivers, upgrading = _settled(api, AXES, sleep=sleep, clock=clock)
    result.drivers = [asdict(driver) for driver in drivers.values()]
    if state.get("attempt") != attempt:
        state.update(attempt=attempt, transfers={})
    admitted = state.get("admitted")
    if admitted is None:
        # First look, before anything is sent: both drivers exactly as expected, or neither is touched.
        admitted, refusals = {}, []
        for axis, driver in drivers.items():
            if driver.version == target:
                continue
            if (driver.version in previous and driver.hardware == HARDWARE and driver.version_ok
                    and driver.state == "idle" and not driver.upgrading and not upgrading):
                admitted[axis] = {"version": driver.version, "hardware": driver.hardware, "addr": driver.addr}
            else:
                refusals.append(driver.describe())
        if refusals:
            result.outcome = "not_admitted"
            result.detail = "; ".join(refusals)
            return result
        state.update(admitted=admitted, verified=[], sent=False)
        save()
    verified = state.setdefault("verified", [])
    for axis in AXES:
        if axis not in admitted or axis in verified:
            continue
        driver = drivers[axis]
        if driver.version == target and driver.version_ok and driver.state == "idle":
            verified.append(axis)  # a resumed run finds it already done
            save()
            continue
        if driver.version not in (*previous, None) or (driver.version is not None and driver.hardware != HARDWARE):
            if not state.get("sent"):
                result.outcome, result.detail = "not_admitted", driver.describe()
                return result
            # After a transfer this run started, a driver reporting anything else is left for an operator.
            raise DriverUpdateFailed(axis, driver.describe() + ", which this update didn't expect")
        state["axis"] = axis
        try:
            _flash(api, axis, admitted[axis], image=image, size=size, sha256=sha256, target=target, state=state,
                   save=save, reporter=reporter, sleep=sleep, clock=clock)
        except DriverRefused as refused:
            # Nothing reached this driver; it and any driver after it keep their firmware.
            result.outcome = "flashed" if verified else "not_admitted"
            result.detail = refused.message
            result.drivers = [asdict(driver) for driver in read_drivers(api)[0].values()]
            return result
        verified.append(axis)
        result.flashed.append(axis)
        save()
        drivers, _ = read_drivers(api)
    result.drivers = [asdict(driver) for driver in read_drivers(api)[0].values()]
    result.outcome = "flashed" if result.flashed or state.get("sent") else "current"
    return result


def _settle_seconds(api) -> float:
    """SETTLE_SECONDS, or four of the drivers' configured query cycles when that is longer."""
    try:
        settings = (api.call("objects/query", {"objects": {"configfile": ["settings"]}}) or {})["status"]
        cycles = [float(settings["configfile"]["settings"].get(f"closed_loop {axis}", {}).get("query_cycle") or 0)
                  for axis in AXES]
        return max(SETTLE_SECONDS, 4 * max(cycles) + 5)
    except (KlipperError, KeyError, TypeError, ValueError, AttributeError):
        return SETTLE_SECONDS


def _settled(api, axes, *, sleep, clock):
    """The drivers once each of `axes` reports a version, or as they are after the settle time: only then does a
    driver without a version count as silent."""
    deadline = None
    while True:
        drivers, upgrading = read_drivers(api)
        if all(drivers[axis].version is not None for axis in axes):
            return drivers, upgrading
        deadline = deadline if deadline is not None else clock() + _settle_seconds(api)
        if clock() >= deadline:
            return drivers, upgrading
        sleep(POLL_SECONDS)


def _flash(api, axis, identity, *, image, size, sha256, target, state, save, reporter, sleep, clock) -> None:
    """Up to MAX_TRANSFERS transfers of the image to one driver, until it is verified. A problem before a send is raised
    as it is (update_drivers decides what it means); a failed transfer is followed by the next, or DriverUpdateFailed."""
    transfers = state.setdefault("transfers", {})
    reason = ""
    retry = False
    while transfers.get(axis, 0) < MAX_TRANSFERS:
        if api.state() != "ready" and not api.wait_ready(sleep=sleep, clock=clock):
            raise KlipperError("Klipper didn't become ready")
        if retry:
            # A transfer that looked failed may still have taken; don't send the image again to a driver on target.
            driver = _settled(api, (axis,), sleep=sleep, clock=clock)[0][axis]
            if driver.version == target and driver.version_ok and driver.state == "idle":
                state.pop("sending", None)
                save()
                reporter.line(f"  The {axis.upper()} motor driver runs {target}.")
                return
        verify_image(image, size=size, sha256=sha256)
        drivers, upgrading = read_drivers(api)
        if upgrading:
            raise KlipperError("a driver update was already running in Klipper")
        if not state.get("motors_off"):
            # The driver resets into its loader with the motor outputs off; nothing may hold or move X/Y meanwhile.
            api.gcode("M84")
            state["motors_off"] = True
        touched = state.setdefault("touched", [])
        # A transfer cut short by a power loss left `sending` behind: that driver may be partly written.
        if (state.get("sending") or {}).get("axis") == axis and axis not in touched:
            touched.append(axis)
        transfers[axis] = transfers.get(axis, 0) + 1
        state.update(sent=True, sending={"axis": axis, "reported": asdict(drivers[axis]), "identity": identity})
        save()
        reporter.line(f"  Updating the {axis.upper()} motor driver to {target} (transfer {transfers[axis]} of "
                      f"{MAX_TRANSFERS})...")
        output = len(api.output)
        try:
            api.gcode(f"UPGRADE_MOTOR STEPPER={axis} FILE={image}", timeout=60)
            outcome, reason = _watch(api, axis, identity.get("addr"), target, sleep=sleep, clock=clock)
        except KlipperError as exc:
            # No answer, or a lost socket: the command may have started a transfer, so it counts as one.
            outcome, reason = "failed", str(exc)
        if outcome == "refused":
            transfers[axis] -= 1
            refusal = " ".join(api.output[output:]).strip() or reason
            reason = f"Klipper refused the update: {refusal}"
            if axis not in touched:
                state["sent"] = bool(touched)
                state.pop("sending", None)
                save()
                raise DriverRefused(axis, f"The {axis.upper()} motor driver was not updated: {reason}")
            break
        if axis not in touched:
            touched.append(axis)
        if outcome == "success":
            state.pop("sending", None)
            save()
            reporter.line(f"  The {axis.upper()} motor driver runs {target}.")
            return
        reporter.line(f"  The {axis.upper()} motor driver update didn't finish: {reason}.")
        retry = True
        # The next transfer needs QIDI's session over and Klipper ready; a reset clears whatever is left.
        if not _session_over(api, sleep=sleep, clock=clock) or api.state() != "ready":
            if not api.firmware_restart(sleep=sleep, clock=clock):
                reason = f"{reason}; Klipper didn't take FIRMWARE_RESTART"
                break
        save()
    raise DriverUpdateFailed(axis, f"the {axis.upper()} motor driver could not be updated ({reason})")


def _session_over(api, *, sleep, clock, seconds: float = 30.0) -> bool:
    deadline = clock() + seconds
    while True:
        try:
            if not read_drivers(api)[1]:
                return True
        except KlipperError:
            return False
        if clock() >= deadline:
            return False
        sleep(POLL_SECONDS)


def _watch(api, axis, addr, target, *, sleep, clock) -> tuple[str, str]:
    """("success" | "refused" | "failed", why). Only a transfer seen in progress in this attempt counts, as QIDI's
    status keeps "success" and "timeout" from earlier ones; success needs the driver to report `target`. "refused"
    needs every read to show no session at all; one Klipper shows as started (the driver's address on the bus's
    upgrading list, the driver upgrading) is watched as a transfer and can only fail."""
    started = clock()
    seen = session = False
    best, last_change, waiting_since = -1.0, clock(), None
    while True:
        if api.state() != "ready":
            return "failed", "Klipper stopped during the transfer"
        drivers, upgrading = read_drivers(api)
        driver = drivers[axis]
        if not seen:
            if driver.status == "in_progress" and (addr is None or addr in upgrading):
                seen, last_change = True, clock()
            else:
                session = session or bool(upgrading) or driver.upgrading or driver.state == "upgrading"
                if session and clock() - started > PREPARE_SECONDS:
                    return "failed", "the transfer started but never showed progress"
                if not session and clock() - started > START_SECONDS:
                    return "refused", "the transfer didn't start"
                sleep(0.25)
                continue
        if driver.status == "success" and not driver.upgrading:
            break
        if driver.status not in ("in_progress", "await_motor_status") or not driver.upgrading:
            return "failed", f"Klipper reported {driver.status}"
        if driver.percentage > best:
            best, last_change = driver.percentage, clock()
        if driver.status == "await_motor_status":
            waiting_since = waiting_since or clock()
            if clock() - waiting_since > AWAIT_SECONDS:
                return "failed", "the driver didn't answer after the transfer"
        elif clock() - last_change > STALL_SECONDS:
            return "failed", f"no progress at {best:.0f}%"
        sleep(POLL_SECONDS)
    deadline = clock() + VERIFY_SECONDS
    while True:
        driver = read_drivers(api)[0][axis]
        if driver.version == target and driver.version_ok and driver.state == "idle":
            return "success", ""
        if clock() >= deadline:
            return "failed", f"after the transfer the driver reports {driver.version or 'no firmware version'}"
        sleep(POLL_SECONDS)


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None
