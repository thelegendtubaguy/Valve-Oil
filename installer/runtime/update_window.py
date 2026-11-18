"""When automatic updates may run, the timezone that is read in, and keeping the chamber light off through them.

The settings file in printer_data (outside config/, which QIDI's firmware package empties) holds the update window, whole
hours from `start` up to `end` that wrap past midnight when `end` is earlier, and the IANA timezone they are read in.
Without a window an enrolled check acts at any hour, as before windows existed. The printers keep their clocks in UTC,
so an install that finds no timezone saved looks one up from the printer's public address (TIMEZONE_LOOKUPS); Valve Oil
UI and Fluidd save the browser's instead when the operator sets a window, and auto-update.sh --timezone saves the one
given. Without a timezone the window is read in UTC. The status service (installer/system/tltg_status.py) reads and
writes the same file.

The light hold: an automatic run that is about to restart Klipper, install, or start a firmware update while the
chamber light is off writes LIGHTS_HOLD_FILE, and until it expires tltg_update_lights (installer/klipper/tltg/
update_lights.py) turns the light off again whenever Klipper starts, as the stock `[output_pin caselight]` starts it on.
It expires on its own (LIGHTS_HOLD_SECONDS) because a QIDI firmware update and the reboot an install schedules end after
the run that started them.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from . import safety
from .errors import PrinterStateError
from .fs_atomic import atomic_delete, atomic_write_text

SETTINGS_FILE = ".tltg_optimized_auto_update_settings.json"
LIGHTS_HOLD_FILE = ".tltg_update_lights_hold.json"
# A QIDI firmware update with its board flashes and the reboot after it take well under this.
LIGHTS_HOLD_SECONDS = 3 * 3600
SOURCES = ("lookup", "browser", "operator")
# Each answers the printer's public address with its IANA timezone: ipapi.co as a bare name, worldtimeapi.org as JSON.
TIMEZONE_LOOKUPS = ("https://ipapi.co/timezone/", "https://worldtimeapi.org/api/ip")
LOOKUP_TIMEOUT_SECONDS = 5
_ZONE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+){0,2}$")
_WINDOW_ARGUMENT = re.compile(r"^(\d{1,2})(?::00)?-(\d{1,2})(?::00)?$")


class UpdateWindowError(ValueError):
    pass


@dataclass(frozen=True)
class Window:
    start: int
    end: int

    def contains(self, hour: int) -> bool:
        if self.start < self.end:
            return self.start <= hour < self.end
        return hour >= self.start or hour < self.end

    def label(self) -> str:
        return f"{self.start:02d}:00–{self.end:02d}:00"


@dataclass(frozen=True)
class Settings:
    window: Window | None = None
    timezone: str | None = None
    timezone_source: str | None = None

    def zone_label(self) -> str:
        return self.timezone if self.timezone and zone(self.timezone) is not None else "UTC"

    def in_window(self, now: datetime | None = None) -> bool:
        if self.window is None:
            return True
        moment = (now or datetime.now(dt_timezone.utc)).astimezone(zone(self.timezone) or dt_timezone.utc)
        return self.window.contains(moment.hour)

    def window_label(self) -> str:
        return "any time" if self.window is None else f"{self.window.label()} {self.zone_label()}"


def zone(name: str | None):
    """The tzinfo for an IANA name the printer's zone database knows, or None."""
    if not isinstance(name, str) or not _ZONE_NAME.match(name):
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # ZoneInfoNotFoundError, ValueError, or no tzdata at all
        return None


def window_from(value) -> Window | None:
    """{"start": h, "end": h} as saved or posted; None for null. Raises UpdateWindowError for anything else."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise UpdateWindowError('Expected {"start": <hour>, "end": <hour>} or null.')
    start, end = value["start"], value["end"]
    if any(isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23 for hour in (start, end)):
        raise UpdateWindowError("The window's hours must be whole hours from 0 to 23.")
    if start == end:
        raise UpdateWindowError("The window must start and end at different hours.")
    return Window(start, end)


def parse_window_argument(text: str) -> Window | None:
    """auto-update.sh --window: "any", or start-end hours such as 2-5 or 22:00-04:00."""
    if text.strip().lower() == "any":
        return None
    match = _WINDOW_ARGUMENT.match(text.strip())
    if not match:
        raise UpdateWindowError("Give the window as start-end hours, such as 2-5 or 22-4, or as any.")
    return window_from({"start": int(match.group(1)), "end": int(match.group(2))})


def settings_path(paths) -> Path:
    return paths.printer_data_root / SETTINGS_FILE


def load_settings(paths) -> Settings:
    path = settings_path(paths)
    try:
        if path.is_symlink():
            return Settings()
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(payload, dict):
        return Settings()
    try:
        window = window_from(payload.get("window"))
    except UpdateWindowError:
        window = None
    name = payload.get("timezone")
    name = name if zone(name) is not None else None
    source = payload.get("timezone_source")
    return Settings(window=window, timezone=name, timezone_source=source if name and source in SOURCES else None)


def save_settings(paths, settings: Settings) -> None:
    payload = {
        "window": None if settings.window is None else {"start": settings.window.start, "end": settings.window.end},
        "timezone": settings.timezone,
        "timezone_source": settings.timezone_source,
    }
    atomic_write_text(settings_path(paths), json.dumps(payload, sort_keys=True, indent=2) + "\n", mode=0o644)


def set_window(paths, window: Window | None) -> Settings:
    current = load_settings(paths)
    settings = Settings(window=window, timezone=current.timezone, timezone_source=current.timezone_source)
    save_settings(paths, settings)
    return settings


def set_timezone(paths, name: str, *, source: str) -> Settings:
    if zone(name) is None:
        raise UpdateWindowError(f"{name} isn't a timezone this printer knows; use an IANA name such as America/Chicago.")
    current = load_settings(paths)
    settings = Settings(window=current.window, timezone=name, timezone_source=source)
    save_settings(paths, settings)
    return settings


def remove_settings(paths) -> None:
    for path in (settings_path(paths), lights_hold_path(paths)):
        try:
            atomic_delete(path)
        except OSError:
            pass


# ---- the timezone, looked up at install ---------------------------------------------------------------------------


def lookup_timezone(*, urlopen=urllib.request.urlopen) -> str | None:
    """The printer's timezone as a geolocation service places its public address, or None when none answers with one
    the printer knows."""
    for url in TIMEZONE_LOOKUPS:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "valve-oil-installer"})
            with urlopen(request, timeout=LOOKUP_TIMEOUT_SECONDS) as response:
                body = response.read()[:4096].decode("utf-8", errors="replace").strip()
        except Exception:  # a lookup that fails in any way leaves the timezone unset; it never fails the install
            continue
        name = body
        if body.startswith("{"):
            try:
                payload = json.loads(body)
            except ValueError:
                continue
            name = payload.get("timezone") if isinstance(payload, dict) else None
        if zone(name) is not None:
            return name
    return None


def ensure_timezone(paths, *, reporter, urlopen=urllib.request.urlopen) -> None:
    """At install: look the timezone up when none is saved. One saved by any means is kept."""
    if load_settings(paths).timezone is not None:
        return
    name = lookup_timezone(urlopen=urlopen)
    if name is None:
        reporter.line("Couldn't look up this printer's timezone; an update window is read in UTC until one is set.")
        return
    try:
        set_timezone(paths, name, source="lookup")
    except (OSError, UpdateWindowError):
        return
    reporter.line(f"Timezone for the update window: {name}, from the printer's internet address.")


# ---- the chamber light ----------------------------------------------------------------------------------------------


def lights_hold_path(paths) -> Path:
    return paths.printer_data_root / LIGHTS_HOLD_FILE


def hold_lights_if_off(paths, *, urlopen=urllib.request.urlopen, clock=time.time) -> bool:
    """Before an automatic run changes the printer: when the chamber light is off, keep it off through the Klipper
    starts that follow. Returns whether it wrote the hold. A light that is on, or can't be read, is left to Klipper."""
    try:
        status = safety.query_objects(paths.moonraker_url, ("output_pin caselight",), timeout=5, urlopen=urlopen)
        value = status["output_pin caselight"]["value"]
    except (PrinterStateError, KeyError, TypeError):
        return False
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != 0:
        return False
    try:
        atomic_write_text(lights_hold_path(paths), json.dumps({"until": int(clock()) + LIGHTS_HOLD_SECONDS}) + "\n",
                          mode=0o644)
    except OSError:
        return False
    return True


def release_lights(paths) -> None:
    """An automatic run that held the light and then changed nothing."""
    try:
        atomic_delete(lights_hold_path(paths))
    except OSError:
        pass
