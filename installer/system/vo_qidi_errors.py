# vo:qidi-errors
"""The error list QIDI's touchscreen app kept for Moonraker's clients, kept as the app kept it.

Installed by Valve Oil as /usr/local/lib/vo_qidi_errors.py beside vo_qidi_agent.py, which loads it. The app kept a list
of QIDI error entries in memory, answered the agent method get_all_error_list with it, and sent each addition and
removal as the agent event ui_assistant_message_update, which QIDI's Moonraker mqtt component forwarded to QIDI's cloud.
Valve Oil patches that duty out of the app, so this module does it from what Moonraker shows; Valve Oil keeps the
printer off QIDI's cloud, so the events reach only Moonraker's clients.

Entries are built as the app built them. Each code's definition is QIDI_Client/resource/qidi_client_error/<code>/0.json
on the printer: error_type and weight give error_type and error_weight, and error_popup is whether it has a
popup_stlye key. The texts are always its en_US ones. A code with message variants (n.json, n >= 1) takes the texts of
the first variant whose message is part of the raw text, and otherwise the raw text for both. The list holds one
entry per code, in the order added. Adding a code already listed refreshes its timestamp and text without sending
anything; removing one sends the stored entry. A code without a definition is logged and skipped, as the app does. The
list starts empty with the service, and nothing is sent for what a restart forgets.

Codes added and removed here, each on the signal and condition the app used:

- QDC_100_005_000_001, a Klipper warning: added with each console line starting "!! ". Only the app's popup removed
  it, so it stays until the service restarts.
- QDC_100_005_015_001, a Klipper error: added with Klipper's state message while Klipper's state is neither ready nor
  startup, removed when it is either.
- QDC_400_004_005_001, a USB drive low on space: added while printer_data/gcodes/USB is mounted with less than
  1024 MB free, removed when it isn't mounted or has more. Checked when the mount changes and when Moonraker reports
  a file change on the drive.
- QDC_200_001_010_001, QDC_300_001_010_001, and QDC_300_001_010_002: added while print_stats_manager's sub_status is
  ext_filament_exhausted, box_filament_exhausted, or auto_reloading_failed, removed otherwise.
- QDC_200_001_005_001, 002, and 003: added while the Box task (multi_color_controller.task) is paused on EXT_BITE,
  EXT_WIPE, or EXT_CUT, removed otherwise.
- QDC_300_002_005_001 to 012 and QDC_300_005_005_002: added while multi_color_controller.box_warning_info holds the
  warning's exact text, removed while it doesn't.
- QDC_100_005_005_001, new mainboard or THR firmware: added while a board's reported version doesn't start with the
  version in QIDI's firmware manifest, the comparison of Valve Oil's patched app. The app judged each board as its
  version arrived, so the later one decided; this judges both together.
- QDC_500_005_005_001 and 002, QIDI's AI service: added while a print is printing or paused and qdc_ai_error_code
  reports foreign or noodle with status=1, removed when the print completes or is cancelled. Valve Oil's agent sends
  the pause; the app's popup that also removed them has no counterpart here.

Codes left out:

- QDC_400_005_015_001, the printer service failing: the app added it after it couldn't reach Moonraker. This service
  is a Moonraker client and sends events through Moonraker, so it has nothing to send it through.
- QDC_400_004_010_001 and 002, QIDI's cloud MQTT and P2P connections: Valve Oil keeps the printer off QIDI's cloud,
  with QIDI's LAN-only mode on, where the app reported neither.
- QDC_100_005_005_002, 003, and 004: the closed-loop motor and buffer versions reach the app through its own checks,
  and Valve Oil's patch makes the app treat QIDI's online update as offering nothing, so 004 never shows.
- QDC_300_005_005_001, new Box firmware: the app compares each Box's version from its own device scan with the
  manifest's targets by Box generation. Valve Oil flashes its own Box images, and a comparison not taken from the app
  would send false notices to QIDI's cloud.
- The Plus 5 and Smart 5 definitions QDC_001_001_001_001 to QDC_013_001_001_001: no rule in the reviewed apps uses
  them.
"""
import asyncio
import json
import logging
import os
import time

LOG = logging.getLogger("vo_qidi_errors")

DEFINITIONS = "/home/qidi/QIDI_Client/resource/qidi_client_error"
FIRMWARE_MANIFEST = "/home/qidi/update/firmware_manifest.json"
MOUNTS = "/proc/mounts"
LANGUAGE = "en_US"
EVENT = "ui_assistant_message_update"

KLIPPER_WARNING = "QDC_100_005_000_001"
KLIPPER_ERROR = "QDC_100_005_015_001"
USB_LOW = "QDC_400_004_005_001"
BOARD_FIRMWARE = "QDC_100_005_005_001"
AI_FOREIGN = "QDC_500_005_005_001"
AI_NOODLE = "QDC_500_005_005_002"

SUB_STATUS_CODES = (
    ("ext_filament_exhausted", "QDC_200_001_010_001"),
    ("box_filament_exhausted", "QDC_300_001_010_001"),
    ("auto_reloading_failed", "QDC_300_001_010_002"),
)
BOX_TASK_CODES = (
    ("EXT_BITE", "QDC_200_001_005_001"),
    ("EXT_WIPE", "QDC_200_001_005_002"),
    ("EXT_CUT", "QDC_200_001_005_003"),
)
# The warning texts the app looks for in box_warning_info, as compiled into it.
BOX_WARNING_CODES = tuple(
    ("BOX %d temperature and humidity sensor abnormal, heating and drying functions have been turned off." % box,
     "QDC_300_002_005_%03d" % box) for box in range(1, 5)
) + tuple(
    ("BOX %d experienced a heating timeout during heating and has automatically turned off the heating. "
     "Please check if the lid is closed." % box, "QDC_300_002_005_%03d" % (box + 4)) for box in range(1, 5)
) + tuple(
    ("BOX %d experienced heating timeout during drying and has automatically turned off drying. "
     "Please check if the lid is closed." % box, "QDC_300_002_005_%03d" % (box + 8)) for box in range(1, 5)
) + (
    ("A new BOX has been detected, and the current number of connected BOX has reached its limit. "
     "Please click to manually reorder the BOX.", "QDC_300_005_005_002"),
)
# Klipper's MCU object for each board and its key in QIDI's firmware manifest.
BOARDS = (("mcu", "MCU"), ("mcu THR", "THR"))
AI_CODES = (("foreign", AI_FOREIGN), ("noodle", AI_NOODLE))

USB_LOW_MB = 1024.0
USB_POLL_SECONDS = 30.0
KLIPPY_POLL_SECONDS = 5.0


def _sorted(mapping):
    """A dict in the key order the app's JSON objects serialize in."""
    return {key: mapping[key] for key in sorted(mapping)}


def _english(translations):
    if isinstance(translations, dict):
        text = translations.get(LANGUAGE)
        if isinstance(text, str) and text:
            return text
    return None


class Definitions:
    """QIDI's error definitions, read from the printer's resource folder the first time each code is used."""

    def __init__(self, root=DEFINITIONS):
        self.root = root
        self._cache = {}

    def get(self, code):
        """(definition, variants) for a code, or None when the code isn't defined."""
        if code not in self._cache:
            self._cache[code] = self._read(code)
        return self._cache[code]

    def _read(self, code):
        folder = os.path.join(self.root, code)
        definition = self._json(os.path.join(folder, "0.json"))
        if not isinstance(definition, dict):
            return None
        variants = []
        try:
            names = os.listdir(folder)
        except OSError:
            names = []
        numbers = sorted(int(name[:-5]) for name in names if name.endswith(".json") and name[:-5].isdigit())
        for number in numbers:
            if number == 0:
                continue
            variant = self._json(os.path.join(folder, "%d.json" % number))
            if isinstance(variant, dict) and isinstance(variant.get("message"), str) and variant["message"]:
                variants.append(variant)
        return definition, variants

    @staticmethod
    def _json(path):
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError) as exc:
            if not isinstance(exc, FileNotFoundError):
                LOG.info("reading %s failed: %s", path, exc)
            return None


class ErrorList:
    """The app's error list: one entry per code, in the order added, each change sent as the app sent it."""

    def __init__(self, context, *, definitions=None, clock=time.time):
        self.context = context
        self.definitions = definitions or Definitions()
        self.clock = clock
        self.entries = []
        context.method("get_all_error_list", self.get_all, api_alias=False)

    def get_all(self, params=None):
        return {"event": "GetAll", "results": [dict(entry) for entry in self.entries]}

    def find(self, code):
        for entry in self.entries:
            if entry["error_code"] == code:
                return entry
        return None

    def add(self, code, raw=""):
        """List a code with the raw text that raised it; returns its entry, or None when the code isn't defined."""
        loaded = self.definitions.get(code)
        if loaded is None:
            LOG.info("no definition for %s; not listing it", code)
            return None
        definition, variants = loaded
        message = _english(definition.get("message_translation"))
        process = _english(definition.get("prossess_msg_translation"))
        for variant in variants:
            if variant["message"] in raw:
                message = _english(variant.get("message_translation"))
                process = _english(variant.get("prossess_msg_translation"))
                break
        message = raw if message is None else message
        process = raw if process is None else process
        timestamp = int(self.clock() * 1000)
        entry = self.find(code)
        if entry is not None:
            entry.update(error_message=message, prossess_message=process, timestamp=timestamp)
            return entry
        entry = _sorted({
            "error_code": code,
            "error_message": message,
            "error_popup": "popup_stlye" in definition,
            "error_type": _int(definition.get("error_type")),
            "error_weight": _int(definition.get("weight")),
            "prossess_message": process,
            "timestamp": timestamp,
        })
        self.entries.append(entry)
        LOG.info("listed %s: %s", code, message)
        self._send("add", entry)
        return entry

    def remove(self, code):
        entry = self.find(code)
        if entry is None:
            return None
        self.entries.remove(entry)
        LOG.info("cleared %s", code)
        self._send("remove", entry)
        return entry

    def set(self, code, listed, raw=""):
        """Add or remove a code by a condition the app judged both ways."""
        if listed:
            self.add(code, raw)
        else:
            self.remove(code)

    def _send(self, event, entry):
        asyncio.ensure_future(self._publish(event, dict(entry)))

    async def _publish(self, event, entry):
        try:
            await self.context.send_event(EVENT, {"event": event, "results": entry})
        except Exception as exc:
            LOG.info("sending %s %s failed: %s", event, entry["error_code"], exc)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ---- Klipper --------------------------------------------------------------------------------------------------------

class KlipperRules:
    """Klipper's warnings and errors, print_stats_manager's sub status, and the Box's task and warnings."""

    def __init__(self, context, errors):
        self.context = context
        self.errors = errors
        self.webhooks = {}
        context.subscribe({
            "webhooks": ["state", "state_message"],
            "print_stats_manager": ["sub_status"],
            "multi_color_controller": ["task", "box_warning_info"],
        })
        context.on_status(self.update)
        context.on_notification("notify_gcode_response", self.gcode_response)
        context.on_connected(self.query_klippy)
        context.background(self._watch_klippy)

    def gcode_response(self, params):
        for line in params or ():
            if isinstance(line, str) and line.startswith("!! "):
                self.errors.add(KLIPPER_WARNING, line)

    def update(self, changes):
        webhooks = changes.get("webhooks")
        if isinstance(webhooks, dict) and ("state" in webhooks or "state_message" in webhooks):
            self.klippy(webhooks)
        manager = changes.get("print_stats_manager")
        if isinstance(manager, dict) and "sub_status" in manager:
            for sub_status, code in SUB_STATUS_CODES:
                self.errors.set(code, manager["sub_status"] == sub_status)
        controller = changes.get("multi_color_controller")
        if not isinstance(controller, dict):
            return
        task = controller.get("task")
        if isinstance(task, dict):
            paused = task.get("is_paused") is True
            for step, code in BOX_TASK_CODES:
                self.errors.set(code, paused and task.get("current_step") == step)
        warnings = controller.get("box_warning_info")
        if isinstance(warnings, list):
            texts = [warning for warning in warnings if isinstance(warning, str)]
            for text, code in BOX_WARNING_CODES:
                self.errors.set(code, text in texts)

    def klippy(self, webhooks):
        previous = dict(self.webhooks)
        self.webhooks.update({key: webhooks[key] for key in ("state", "state_message") if key in webhooks})
        if self.webhooks == previous:
            return
        state = self.webhooks.get("state")
        if state is None:
            return
        message = self.webhooks.get("state_message")
        self.errors.set(KLIPPER_ERROR, state not in ("ready", "startup"), message if isinstance(message, str) else "")

    async def query_klippy(self):
        """Klipper's state from Moonraker, for the states a status subscription doesn't follow."""
        try:
            info = await self.context.call("printer.info")
        except Exception:
            return
        if isinstance(info, dict) and "state" in info:
            self.klippy({"state": info.get("state"), "state_message": info.get("state_message", "")})

    async def _watch_klippy(self):
        while True:
            await asyncio.sleep(KLIPPY_POLL_SECONDS)
            if self.context.connected and self.context.klippy_state != "ready":
                await self.query_klippy()


class BoardFirmwareRule:
    """New mainboard or THR firmware in QIDI's manifest, judged as Valve Oil's patched app judges it."""

    def __init__(self, context, errors, *, manifest=FIRMWARE_MANIFEST):
        self.context = context
        self.errors = errors
        self.manifest = manifest
        self.versions = {}
        context.subscribe({name: ["mcu_version"] for name, _ in BOARDS})
        context.on_status(self.update)

    def update(self, changes):
        changed = False
        for name, key in BOARDS:
            version = (changes.get(name) or {}).get("mcu_version")
            if isinstance(version, str) and version and self.versions.get(key) != version:
                self.versions[key] = version
                changed = True
        if not changed:
            return
        targets = self._targets()
        outdated = [key for key, version in self.versions.items()
                    if targets.get(key) and not version.startswith(targets[key])]
        self.errors.set(BOARD_FIRMWARE, bool(outdated))

    def _targets(self):
        try:
            with open(self.manifest, encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, ValueError):
            return {}
        targets = {}
        for _, key in BOARDS:
            version = (manifest.get(key) or {}).get("version") if isinstance(manifest, dict) else None
            if isinstance(version, str):
                targets[key] = version
        return targets


class AiRule:
    """QIDI's AI service's foreign-object and spaghetti reports during a print."""

    def __init__(self, context, errors):
        self.context = context
        self.errors = errors
        self.state = None
        context.subscribe({"save_variables": ["variables"], "print_stats": ["state"]})
        context.on_status(self.update)

    def update(self, changes):
        state = (changes.get("print_stats") or {}).get("state")
        if state is not None and state != self.state:
            self.state = state
            if state in ("complete", "cancelled"):
                for _, code in AI_CODES:
                    self.errors.remove(code)
        variables = (changes.get("save_variables") or {}).get("variables")
        if not isinstance(variables, dict):
            return
        value = variables.get("qdc_ai_error_code")
        if not isinstance(value, str) or "status=1" not in value:
            return
        if self.context.print_state() not in ("printing", "paused"):
            return
        kind = value.split("=", 1)[0].strip()
        for name, code in AI_CODES:
            if kind == name:
                self.errors.add(code)


# ---- Moonraker and the host -----------------------------------------------------------------------------------------

def mounted(path, mounts=MOUNTS):
    try:
        with open(mounts, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) > 1 and fields[1].replace("\\040", " ") == path:
                    return True
    except OSError:
        pass
    return False


def free_mb(path):
    """Free space for unprivileged users, in MB, as the app measured it; None when it can't be read."""
    try:
        stats = os.statvfs(path)
    except OSError:
        return None
    return stats.f_bsize * stats.f_bavail / 1024.0 / 1024.0


class UsbRule:
    """A USB drive at printer_data/gcodes/USB with under 1 GB free."""

    def __init__(self, context, errors, *, mounts=MOUNTS):
        self.context = context
        self.errors = errors
        self.mounts = mounts
        self.path = os.path.join(context.printer_data, "gcodes", "USB")
        self.mounted = None
        context.on_notification("notify_filelist_changed", self.file_change)
        context.on_connected(self.check)
        context.background(self._watch)

    def file_change(self, params):
        for change in params or ():
            item = (change or {}).get("item") if isinstance(change, dict) else None
            path = (item or {}).get("path") if isinstance(item, dict) else None
            if isinstance(path, str) and (path == "USB" or path.startswith("USB/")):
                self.check()
                return

    def check(self):
        self.mounted = mounted(self.path, self.mounts)
        if not self.mounted:
            self.errors.remove(USB_LOW)
            return
        free = free_mb(self.path)
        if free is None:
            return
        if free < USB_LOW_MB:
            self.errors.add(USB_LOW)
        elif free > USB_LOW_MB:
            self.errors.remove(USB_LOW)

    async def _watch(self):
        while True:
            await asyncio.sleep(USB_POLL_SECONDS)
            if self.context.connected and mounted(self.path, self.mounts) != self.mounted:
                self.check()


def setup(context, *, definitions=DEFINITIONS, manifest=FIRMWARE_MANIFEST, mounts=MOUNTS):
    errors = ErrorList(context, definitions=Definitions(definitions))
    KlipperRules(context, errors)
    BoardFirmwareRule(context, errors, manifest=manifest)
    AiRule(context, errors)
    UsbRule(context, errors, mounts=mounts)
    return errors
