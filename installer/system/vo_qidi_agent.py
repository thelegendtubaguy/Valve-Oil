# vo:qidi-agent
"""Valve Oil's stand-in for what QIDI's touchscreen app does besides drawing the screen.

Installed by Valve Oil as /usr/local/lib/vo_qidi_agent.py with its helpers beside it (vo_qidi_*.py) and run by
vo-qidi-agent.service. Valve Oil patches those duties out of the reviewed app builds
(installer/runtime/qidiclient_firmware_popup.py), so they are done here whichever touchscreen app runs, or none:

- the Moonraker extension agent QIDI's components call, under the app's own name and QIDI's firmware version,
  answering the app's methods in its result shapes;
- the print state power-loss resume reads, written as the app writes it;
- resuming a print after the Klipper errors the app resumes from;
- saving box_count, and pausing a print when QIDI's AI service reports spaghetti;
- the error list, the print file list and statistics, the official filament list, and LAN discovery, through the
  helpers.

The app's QIDI cloud work, its P2P remote access, and its decryption of the cloud account for QIDI's MQTT component are
patched out of the app and done by nothing: Valve Oil keeps the printer off QIDI's servers.

APP_BUILDS, filled in at install, says how to tell the patched app and what it is called: {size: {"site": [file
offset, patched bytes as hex], "name": the app's agent name}}. With any other app on disk, as after a QIDI firmware
update until Valve Oil installs again, the app does its own duties, so this service connects nothing and checks again
every APP_CHECK_SECONDS.

It speaks JSON-RPC to Moonraker over Moonraker's Unix socket, each message ended by ETX (0x03).
"""
import asyncio
import importlib
import json
import logging
import os
import struct
import sys
import time

APP_BUILDS = None

HELPERS = ("vo_qidi_errors", "vo_qidi_files", "vo_qidi_lan")
SOCKET = "/home/qidi/printer_data/comms/moonraker.sock"
APP = "/home/qidi/QIDI_Client/bin/qidiclient"
FIRMWARE_MANIFEST = "/home/qidi/update/firmware_manifest.json"
PRINTER_DATA = "/home/qidi/printer_data"
SERIAL_BY_ID = "/dev/serial/by-id"
APP_CHECK_SECONDS = 30.0
RECONNECT_SECONDS = 2.0
CALL_TIMEOUT = 30.0
ETX = b"\x03"

LOG = logging.getLogger("vo_qidi_agent")


# ---- Printer facts --------------------------------------------------------------------------------------------------

def firmware_version(path=FIRMWARE_MANIFEST):
    """QIDI's SOC version, the version the app reports, or None."""
    try:
        with open(path, encoding="utf-8") as handle:
            version = json.load(handle)["SOC"]["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return version if isinstance(version, str) and version else None


def serial_number(cpuinfo="/proc/cpuinfo"):
    """The CPU serial number, upper-cased, which the app uses as the device's ID, as in its LAN announcements."""
    try:
        with open(cpuinfo, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                if key.strip() == "Serial":
                    return value.strip().upper()
    except OSError:
        pass
    return ""


def patched_app(app=APP, builds=None):
    """The APP_BUILDS entry of the touchscreen app when it is a reviewed build with Valve Oil's patch of its duties,
    else None."""
    builds = APP_BUILDS if builds is None else builds
    try:
        build = (builds or {}).get(str(os.stat(app).st_size))
        if build is None:
            return None
        offset, patched = build["site"]
        with open(app, "rb") as handle:
            data = os.pread(handle.fileno(), len(patched) // 2, offset)
    except (OSError, KeyError, TypeError, ValueError):
        return None
    return build if data == bytes.fromhex(patched) else None


def is_box(name):
    """Whether a /dev/serial/by-id name is a USB QIDI Box, as the app and Valve Oil's Box monitor tell."""
    prefix = "usb-Klipper_"
    return name.startswith(prefix) and (name[len(prefix):].startswith("02.03") or "QIDI_BOX_V" in name)


def usb_box_count(by_id=SERIAL_BY_ID):
    try:
        return sum(1 for name in os.listdir(by_id) if is_box(name))
    except OSError:
        return 0


# ---- Moonraker connection -------------------------------------------------------------------------------------------

class MoonrakerError(Exception):
    pass


class Connection:
    """One JSON-RPC connection to Moonraker's Unix socket: calls out, and requests and notifications in."""

    def __init__(self, reader, writer, *, on_request, on_notification):
        self._reader, self._writer = reader, writer
        self._on_request, self._on_notification = on_request, on_notification
        self._pending = {}
        self._next_id = 1
        self.closed = asyncio.get_event_loop().create_future()

    async def run(self):
        buffer = b""
        try:
            while True:
                chunk = await self._reader.read(65536)
                if not chunk:
                    break
                buffer += chunk
                while ETX in buffer:
                    raw, buffer = buffer.split(ETX, 1)
                    if raw.strip():
                        self._dispatch(raw)
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(MoonrakerError("Moonraker connection closed"))
            self._pending.clear()
            if not self.closed.done():
                self.closed.set_result(None)

    def _dispatch(self, raw):
        try:
            message = json.loads(raw)
        except ValueError:
            LOG.warning("unreadable message from Moonraker")
            return
        if not isinstance(message, dict):
            return
        if "method" in message:
            if "id" in message:
                asyncio.ensure_future(self._answer(message))
            else:
                try:
                    self._on_notification(message["method"], message.get("params"))
                except Exception:
                    LOG.exception("notification %s failed", message.get("method"))
            return
        future = self._pending.pop(message.get("id"), None)
        if future is None or future.done():
            return
        if "error" in message:
            error = message["error"] or {}
            future.set_exception(MoonrakerError(f"{error.get('code')} {error.get('message')}"))
        else:
            future.set_result(message.get("result"))

    async def _answer(self, message):
        reply = {"jsonrpc": "2.0", "id": message["id"]}
        try:
            reply["result"] = await self._on_request(message["method"], message.get("params") or {})
        except RpcError as exc:
            reply["error"] = {"code": exc.code, "message": exc.message}
        except Exception as exc:  # the app answers -32603 for a handler that fails
            LOG.exception("request %s failed", message.get("method"))
            reply["error"] = {"code": -32603, "message": f"Internal error: {exc}"}
        self._send(reply)

    def _send(self, message):
        if self._writer.is_closing():
            return
        self._writer.write(json.dumps(message, separators=(",", ":")).encode("utf-8") + ETX)

    async def call(self, method, params=None, timeout=CALL_TIMEOUT):
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        message = {"jsonrpc": "2.0", "method": method, "id": request_id}
        if params is not None:
            message["params"] = params
        self._send(message)
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)

    def close(self):
        self._writer.close()


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code} {message}")
        self.code, self.message = code, message


# ---- What the helpers and duties see --------------------------------------------------------------------------------

class Context:
    """What each duty uses: Moonraker calls, the merged Klipper status, events, and the printer's facts.

    A duty registers what it needs before the service connects: Klipper objects to subscribe to (subscribe), agent
    methods it answers (method), and callbacks for status updates, notifications, and each connection (on_status,
    on_notification, on_connected). status holds the latest value of every subscribed object, merged from Moonraker's
    updates; on_status callbacks get each update's changes. Calls made while Moonraker isn't connected raise
    MoonrakerError.
    """

    def __init__(self, name, *, printer_data=PRINTER_DATA, version=None, app=None):
        self.name = name
        self.app = app or {}  # the touchscreen app's APP_BUILDS entry
        self.printer_data = printer_data
        self.version = version if version is not None else (firmware_version() or "")
        self.serial_number = serial_number()
        self.status = {}
        self.klippy_state = None
        self.objects = {}
        self.methods = {}
        self._status_callbacks = []
        self._notification_callbacks = {}
        self._connected_callbacks = []
        self._tasks = []
        self.connection = None

    # Registration
    def subscribe(self, objects):
        for name, fields in objects.items():
            current = self.objects.get(name, [])
            if fields is None or current is None:
                self.objects[name] = None
            else:
                self.objects[name] = sorted(set(current) | set(fields))

    def method(self, name, handler, *, api_alias=True):
        self.methods[name] = handler
        if api_alias:
            self.methods["api." + name] = handler

    def on_status(self, callback):
        self._status_callbacks.append(callback)

    def on_notification(self, method, callback):
        self._notification_callbacks.setdefault(method, []).append(callback)

    def on_connected(self, callback):
        self._connected_callbacks.append(callback)

    def background(self, coroutine_function):
        """Run coroutine_function() for the life of the service, restarting it after an exception."""
        self._tasks.append(coroutine_function)

    # Use
    @property
    def connected(self):
        return self.connection is not None

    async def call(self, method, params=None, timeout=CALL_TIMEOUT):
        if self.connection is None:
            raise MoonrakerError("Moonraker is not connected")
        return await self.connection.call(method, params, timeout)

    async def gcode(self, script, timeout=CALL_TIMEOUT):
        return await self.call("printer.gcode.script", {"script": script}, timeout)

    async def send_event(self, event, data):
        """An agent event to every Moonraker client."""
        return await self.call("printer.connection.send_event_to_all", {"event": event, "data": data})

    async def config_get(self, section, key, default=None):
        """A value of QIDI's config table, as the app reads it. QIDI's Moonraker answers
        {"status": "success", "result": {"<section>.<key>": value}}."""
        reply = await self.call("server.database.config.select",
                                {"section": section, "key": key, "default": default})
        values = reply.get("result") if isinstance(reply, dict) else None
        if isinstance(values, dict):
            return values.get(f"{section}.{key}", default)
        return default

    async def config_set(self, section, key, value):
        """Write a value of QIDI's config table as a string, as the app does."""
        return await self.call("server.database.config.update", {"section": section, "key": key, "value": value})

    def print_state(self):
        return (self.status.get("print_stats") or {}).get("state")

    # The service's side
    def _status_update(self, changes):
        for name, fields in changes.items():
            if isinstance(fields, dict):
                self.status.setdefault(name, {}).update(fields)
            else:
                self.status[name] = fields
        for callback in self._status_callbacks:
            try:
                callback(changes)
            except Exception:
                LOG.exception("status callback failed")

    def _notification(self, method, params):
        if method == "notify_status_update" and params:
            self._status_update(params[0])
        elif method == "notify_klippy_ready":
            self.klippy_state = "ready"
            asyncio.ensure_future(self._subscribe())
        elif method in ("notify_klippy_shutdown", "notify_klippy_disconnected"):
            self.klippy_state = "shutdown" if method == "notify_klippy_shutdown" else "disconnected"
        for callback in self._notification_callbacks.get(method, ()):
            try:
                result = callback(params)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:
                LOG.exception("notification callback for %s failed", method)

    async def _request(self, method, params):
        handler = self.methods.get(method)
        if handler is None:
            raise RpcError(-32601, "Method not found: " + method)
        result = handler(params)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def _subscribe(self):
        if not self.objects:
            return
        try:
            result = await self.call("printer.objects.subscribe", {"objects": self.objects})
        except Exception as exc:
            LOG.info("subscribing to Klipper's status failed: %s", exc)
            return
        status = (result or {}).get("status") or {}
        self.status = {}
        self._status_update(status)

    async def _connected(self):
        try:
            info = await self.call("server.info")
            self.klippy_state = (info or {}).get("klippy_state")
        except Exception:
            self.klippy_state = None
        await self._subscribe()
        for callback in self._connected_callbacks:
            try:
                result = callback()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                LOG.exception("connected callback failed")


async def _connect_once(context, socket_path):
    reader, writer = await asyncio.open_unix_connection(socket_path, limit=1 << 22)
    connection = Connection(reader, writer, on_request=context._request, on_notification=context._notification)
    runner = asyncio.ensure_future(connection.run())
    try:
        await connection.call("server.connection.identify", {
            "client_name": context.name, "version": context.version, "type": "agent", "url": "local"})
        context.connection = connection
        LOG.info("connected to Moonraker as agent %s %s", context.name, context.version)
        await context._connected()
        await connection.closed
    finally:
        context.connection = None
        connection.close()
        runner.cancel()


async def _forever(coroutine_function, name):
    while True:
        try:
            await coroutine_function()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception("%s failed; starting it again", name)
        await asyncio.sleep(RECONNECT_SECONDS)


async def serve(context, *, socket_path=SOCKET, patched=lambda: patched_app() is not None):
    for task in context._tasks:
        asyncio.ensure_future(_forever(task, getattr(task, "__qualname__", "task")))
    while True:
        if not patched():
            LOG.info("the touchscreen app is not Valve Oil's patched build; leaving its duties to it")
            while not patched():
                await asyncio.sleep(APP_CHECK_SECONDS)
        try:
            await _connect_once(context, socket_path)
        except (OSError, MoonrakerError, asyncio.TimeoutError) as exc:
            LOG.info("Moonraker not reachable: %s", exc)
        await asyncio.sleep(RECONNECT_SECONDS)


# ---- Print state for power-loss resume ------------------------------------------------------------------------------

def _f32(value):
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def _dump(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, sort_keys=True)


def _round_half_away(value):
    value = _f32(value)
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


# The app's record, in the order it writes changed fields, with the value each takes when a print ends.
PRINT_STATE_FIELDS = (
    ("extrude_factor", 0.0), ("speed", 50.0), ("speed_factor", 0.0), ("current_layer", 0),
    ("absolute_coord", True), ("absolute_extrude", True), ("base_position", ""), ("gcode_position", ""),
    ("homing_position", ""), ("extruder_target", 0), ("heater_bed_target", 0), ("chamber_target", 0),
    ("cooling_fan_speed", 0.0), ("chamber_circulation_fan_speed", 0.0), ("auxiliary_cooling_fan_speed", 0.0),
    ("file_position", 0), ("plate_index", ""), ("exclude_objects", ""), ("all_objects", ""),
)
ALL_OBJECTS_LIMIT = 64
FILE_POSITION_STEP = 1024


def print_state_value(key, value):
    """A print-state field as the app writes it to QIDI's config table."""
    if key in ("extrude_factor", "speed", "speed_factor"):
        return "%f" % value
    if key in ("cooling_fan_speed", "chamber_circulation_fan_speed", "auxiliary_cooling_fan_speed"):
        return "%f" % value
    if key in ("absolute_coord", "absolute_extrude"):
        return "1" if value else "0"
    if key in ("current_layer", "extruder_target", "heater_bed_target", "chamber_target", "file_position"):
        return str(int(value))
    return value  # positions, plate index, and objects are kept as the strings written


class PrintStateRecorder:
    """Writes print_state.* to QIDI's config table while a print runs, as QIDI's app does.

    The app keeps the record it last wrote and the current one. Status fields fill the current record only while the
    print is printing, except the bed target and the absolute-coordinate flags, which fill in any state. Each status
    update carrying virtual_sdcard.file_position, once the file position has moved more than 1024 bytes past the
    record last written, writes every field that differs from it, one config update each, in PRINT_STATE_FIELDS
    order. Both records go back to the defaults when the print ends, is cancelled, fails, or goes to standby, so a
    field equal to its default is never written. QIDI's Moonraker clears the rows itself when a job starts or ends
    other than by an error.
    """

    OBJECTS = {
        "print_stats": ["state", "info"],
        "virtual_sdcard": ["file_position", "plate_index"],
        "gcode_move": ["extrude_factor", "speed_2", "speed_factor_2", "absolute_coordinates", "absolute_extrude",
                       "base_position", "gcode_position", "homing_position"],
        "extruder": ["target"],
        "heater_bed": ["target"],
        "heater_generic chamber": ["target"],
        "fan_generic cooling_fan": ["speed"],
        "fan_generic chamber_circulation_fan": ["speed"],
        "fan_generic auxiliary_cooling_fan": ["speed"],
        "exclude_object": ["excluded_objects", "objects"],
    }
    RESET_STATES = ("standby", "complete", "error", "cancelled")

    def __init__(self, context, *, write=None):
        self.context = context
        self.current = dict(PRINT_STATE_FIELDS)
        self.written = dict(PRINT_STATE_FIELDS)
        self.state = None
        self._write = write or self._config_update
        self._queue = asyncio.Queue()
        context.subscribe(self.OBJECTS)
        context.on_status(self.update)
        context.background(self._writer)

    def _reset(self):
        self.current = dict(PRINT_STATE_FIELDS)
        self.written = dict(PRINT_STATE_FIELDS)

    def update(self, changes):
        stats = changes.get("print_stats") or {}
        if "state" in stats and stats["state"] != self.state:
            self.state = stats["state"]
            if self.state in self.RESET_STATES:
                self._reset()
        printing = self.state == "printing"
        move = changes.get("gcode_move") or {}
        if "absolute_coordinates" in move:
            self.current["absolute_coord"] = bool(move["absolute_coordinates"])
        if "absolute_extrude" in move:
            self.current["absolute_extrude"] = bool(move["absolute_extrude"])
        bed = changes.get("heater_bed") or {}
        if "target" in bed:
            self.current["heater_bed_target"] = _round_half_away(bed["target"])
        if printing:
            self._fill(changes, move, stats)
        sdcard = changes.get("virtual_sdcard") or {}
        if "file_position" not in sdcard:
            return
        if printing:
            self.current["file_position"] = int(sdcard["file_position"])
        if self.current["file_position"] - self.written["file_position"] > FILE_POSITION_STEP:
            for key, _ in PRINT_STATE_FIELDS:
                if self.current[key] != self.written[key]:
                    self.written[key] = self.current[key]
                    self._queue.put_nowait((key, print_state_value(key, self.current[key])))

    def _fill(self, changes, move, stats):
        current = self.current
        if "extrude_factor" in move:
            current["extrude_factor"] = float(move["extrude_factor"])
        if "speed_2" in move:
            current["speed"] = float(int(move["speed_2"]))
        if "speed_factor_2" in move:
            current["speed_factor"] = float(move["speed_factor_2"])
        for field in ("base_position", "gcode_position", "homing_position"):
            if field in move:
                current[field] = _dump(move[field])
        info = stats.get("info")
        if isinstance(info, dict) and info.get("current_layer") is not None:
            current["current_layer"] = int(info["current_layer"])
        for name, key in (("extruder", "extruder_target"), ("heater_generic chamber", "chamber_target")):
            target = (changes.get(name) or {}).get("target")
            if target is not None:
                current[key] = _round_half_away(target)
        for name, key in (("fan_generic cooling_fan", "cooling_fan_speed"),
                          ("fan_generic chamber_circulation_fan", "chamber_circulation_fan_speed"),
                          ("fan_generic auxiliary_cooling_fan", "auxiliary_cooling_fan_speed")):
            speed = (changes.get(name) or {}).get("speed")
            if speed is not None:
                current[key] = _f32(speed)
        sdcard = changes.get("virtual_sdcard") or {}
        if "plate_index" in sdcard and sdcard["plate_index"] is not None:
            current["plate_index"] = str(sdcard["plate_index"])
        objects = changes.get("exclude_object") or {}
        if "excluded_objects" in objects:
            current["exclude_objects"] = _dump(objects["excluded_objects"])
        if "objects" in objects:
            if len(objects["objects"]) <= ALL_OBJECTS_LIMIT:
                current["all_objects"] = _dump(objects["objects"])
            else:
                LOG.info("Too many objects (%d), skipping all_objects", len(objects["objects"]))

    async def _config_update(self, key, value):
        await self.context.config_set("print_state", key, value)

    async def _writer(self):
        while True:
            key, value = await self._queue.get()
            try:
                await self._write(key, value)
            except Exception as exc:
                LOG.info("writing print_state.%s failed: %s", key, exc)


# ---- Resume after a Klipper error -----------------------------------------------------------------------------------

RESUME_ERRORS = ("Lost communication with MCU", "Heater extruder not heating at expected rate", "Move queue overflow")
RESUME_SETTLE_SECONDS = 40.0
RESUME_POLL_SECONDS = 1.0
# How long each wait may take before the resume is given up: Klipper stopping, and starting again.
RESUME_STOP_SECONDS = 60.0
RESUME_START_SECONDS = 300.0


class ErrorResume:
    """Restarts Klipper's firmware and resumes the print after the Klipper errors QIDI's app resumes from.

    When Klipper's state message changes to one naming one of RESUME_ERRORS while a print is printing or paused past
    its first layer, it waits for Klipper to have stopped (Moonraker's klippy_state shutdown or error; the app waits
    for its own "error" state), sends FIRMWARE_RESTART, waits 2 s and then for Klipper to be ready, waits
    RESUME_SETTLE_SECONDS more and for ready again, and resumes with printer.print.power_off_print, as the app does.
    A wait that runs past its limit gives the resume up, so Klipper left stopped or never ready again is left as it is.
    """

    def __init__(self, context, *, sleep=asyncio.sleep):
        self.context = context
        self.sleep = sleep
        self.message = None
        self.running = None
        self.layer = 0
        context.subscribe({"webhooks": ["state", "state_message"], "print_stats": ["state", "info"]})
        context.on_status(self.update)

    def update(self, changes):
        stats = changes.get("print_stats") or {}
        info = stats.get("info")
        if isinstance(info, dict) and info.get("current_layer") is not None:
            self.layer = int(info["current_layer"])
        webhooks = changes.get("webhooks") or {}
        message = webhooks.get("state_message")
        if message is None or message == self.message:
            return
        self.message = message
        printing = self.context.print_state() in ("printing", "paused")
        if not printing or not any(error in message for error in RESUME_ERRORS):
            return
        if self.layer <= 1:
            LOG.info("Klipper stopped during a print before layer 2; not resuming: %s", message)
            return
        if self.running is not None and not self.running.done():
            return
        LOG.info("Klipper stopped during a print at layer %d; resuming as QIDI's app does: %s", self.layer, message)
        self.running = asyncio.ensure_future(self.resume())

    async def _klippy_state(self):
        try:
            info = await self.context.call("server.info")
        except Exception:
            return None
        return (info or {}).get("klippy_state")

    async def _wait_for(self, states, limit):
        waited = 0.0
        while await self._klippy_state() not in states:
            if waited >= limit:
                return False
            await self.sleep(RESUME_POLL_SECONDS)
            waited += RESUME_POLL_SECONDS
        return True

    async def resume(self):
        if not await self._wait_for(("shutdown", "error"), RESUME_STOP_SECONDS):
            LOG.info("Klipper didn't stop; not resuming")
            return
        await self.context.gcode("FIRMWARE_RESTART")
        await self.sleep(2.0)
        if not await self._wait_for(("ready",), RESUME_START_SECONDS):
            LOG.info("Klipper wasn't ready again after FIRMWARE_RESTART; not resuming")
            return
        await self.sleep(RESUME_SETTLE_SECONDS)
        if not await self._wait_for(("ready",), RESUME_START_SECONDS):
            LOG.info("Klipper wasn't ready again after FIRMWARE_RESTART; not resuming")
            return
        await self.context.call("printer.print.power_off_print")
        LOG.info("print resumed after the Klipper error")


# ---- box_count ------------------------------------------------------------------------------------------------------

class BoxCount:
    """Saves box_count from the USB Boxes connected, as QIDI's patched app did: after connecting to Moonraker and
    each time Klipper becomes ready. Valve Oil's Box monitor writes box.cfg for a Box change and restarts Klipper
    when the printer is idle, so the count is saved with the box.cfg Klipper runs with."""

    def __init__(self, context, *, count=usb_box_count):
        self.context = context
        self.count = count
        context.subscribe({"save_variables": ["variables"]})
        context.on_connected(self.save)
        context.on_notification("notify_klippy_ready", lambda params: self.save())

    async def save(self):
        if not self.context.connected or self.context.klippy_state != "ready":
            return
        count = self.count()
        saved = ((self.context.status.get("save_variables") or {}).get("variables") or {}).get("box_count")
        if saved == count:
            return
        try:
            await self.context.gcode(f"SAVE_VARIABLE VARIABLE=box_count VALUE={count}")
        except Exception as exc:
            LOG.info("saving box_count failed: %s", exc)


# ---- AI pause -------------------------------------------------------------------------------------------------------

def ai_report(value):
    """QIDI's AI service's report in the qdc_ai_error_code saved variable, "<kind>=<0|1> status=<0|1> time=<ts>":
    (kind, status) or None."""
    if not isinstance(value, str):
        return None
    fields = dict(part.split("=", 1) for part in value.split() if "=" in part)
    kind = value.split("=", 1)[0].strip() if "=" in value else ""
    status = fields.get("status")
    return (kind, status == "1") if kind else None


class AiPause:
    """Pauses a print when QIDI's AI service reports spaghetti, once per print, as QIDI's app does."""

    def __init__(self, context):
        self.context = context
        self.value = None
        self.paused_this_print = False
        context.subscribe({"save_variables": ["variables"], "print_stats": ["state"]})
        context.on_status(self.update)

    def update(self, changes):
        stats = changes.get("print_stats") or {}
        if stats.get("state") in ("standby", "complete", "cancelled", "error"):
            self.paused_this_print = False
        variables = (changes.get("save_variables") or {}).get("variables")
        if not isinstance(variables, dict) or "qdc_ai_error_code" not in variables:
            return
        value = variables["qdc_ai_error_code"]
        if value == self.value:
            return
        self.value = value
        report = ai_report(value)
        if report is None or report != ("noodle", True) or self.paused_this_print:
            return
        if self.context.print_state() not in ("printing", "paused"):
            return
        self.paused_this_print = True
        LOG.info("QIDI's AI service reported spaghetti; pausing")
        asyncio.ensure_future(self.context.gcode("PAUSE W=1"))


# ---- Service --------------------------------------------------------------------------------------------------------

def build(context):
    PrintStateRecorder(context)
    ErrorResume(context)
    BoxCount(context)
    AiPause(context)
    for name in HELPERS:
        try:
            module = importlib.import_module(name)
        except ImportError as exc:
            LOG.warning("%s is missing: %s", name, exc)
            continue
        module.setup(context)
    return context


async def _wait_for_patched_app():
    while True:
        app = patched_app()
        if app is not None:
            return app
        LOG.info("the touchscreen app is not Valve Oil's patched build; leaving its duties to it")
        await asyncio.sleep(APP_CHECK_SECONDS)


def main():
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s", stream=sys.stdout)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    loop = asyncio.get_event_loop()
    app = loop.run_until_complete(_wait_for_patched_app())
    context = build(Context(app["name"], app=app))
    loop.run_until_complete(serve(context))
    return 0


if __name__ == "__main__":
    sys.exit(main())
