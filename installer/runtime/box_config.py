"""Valve Oil owns box.cfg for USB QIDI Boxes: the Box monitor, its udev rule and unit, and the firmware update's write.

Valve Oil's patch of the reviewed touchscreen app builds (qidiclient_firmware_popup.py) stops the app from writing
box.cfg and restarting Klipper whenever the Boxes it finds change. installer/system/vo_box_monitor.py takes that over:
a udev rule starts vo-box-monitor.service when a USB Box connects or disconnects, and the monitor writes box.cfg in
QIDI's layout, numbering each Box from a record so it keeps its slots on another port, and restarts Klipper only while
the printer is idle. A QIDI firmware update resets box.cfg; the update writes it again before its board step, so the
Boxes are flashed in the same run.
"""
from __future__ import annotations

import importlib.util
import json
from functools import lru_cache
from pathlib import Path

from .errors import InstallerError
from .qidiclient_firmware_popup import BUILDS

MONITOR_OPERATION = "box_monitor"
UNIT_OPERATION = "box_monitor_unit"
RULE_OPERATION = "box_monitor_rule"
UNIT = "vo-box-monitor.service"
UNIT_FILE = f"/etc/systemd/system/{UNIT}"
MONITOR_FILE = "/usr/local/lib/vo_box_monitor.py"
RULE_FILE = "/etc/udev/rules.d/99-vo-box-monitor.rules"
MONITOR_PAYLOAD = Path(__file__).resolve().parents[1] / "system/vo_box_monitor.py"
MONITOR_MARKER = "# vo:box-monitor\n"
UNIT_MARKER = "# vo:box-monitor-unit\n"
RULE_MARKER = "# vo:box-monitor-rule\n"
# The monitor's data line, which the installed copy carries the reviewed app builds in.
DATA_LINE = "APP_BUILDS = None\n"

# Runs once at boot, for a Box removed while the printer was off, and whenever the udev rule starts it. Starting it while
# it runs does nothing; it reads the Boxes again before it writes. It waits for installer runs and board operations,
# and while the printer is busy, so it has no time limit.
UNIT_CONTENT = UNIT_MARKER + f"""\
# Writes box.cfg for the USB QIDI Boxes connected and restarts Klipper for them when idle. Installed by Valve Oil.
[Unit]
Description=Valve Oil Box monitor
After=moonraker.service
ConditionPathExists={MONITOR_FILE}

[Service]
Type=simple
User=qidi
ExecStart=/usr/bin/python3 -I -S {MONITOR_FILE}
Nice=10

[Install]
WantedBy=multi-user.target
"""

# udev names a USB serial device's model from its USB product string. QIDI's touchscreen app counts a Klipper device
# as a Box by the same test.
_START = 'RUN+="/bin/systemctl --no-block start ' + UNIT + '"'
RULE_CONTENT = RULE_MARKER + f"""\
# Starts Valve Oil's Box monitor when a USB QIDI Box connects or disconnects. Installed by Valve Oil.
ACTION=="add|remove", SUBSYSTEM=="tty", ENV{{ID_VENDOR}}=="Klipper", ENV{{ID_MODEL}}=="02.03*", {_START}
ACTION=="add|remove", SUBSYSTEM=="tty", ENV{{ID_VENDOR}}=="Klipper", ENV{{ID_MODEL}}=="*QIDI_BOX_V*", {_START}
"""


def app_builds() -> dict[str, list]:
    """Where each reviewed app build has its Box detector patch: file size to (offset, patched bytes in hex)."""
    return {str(build.size): [build.detector.offset, build.detector.patched.hex()] for build in BUILDS.values()}


@lru_cache(maxsize=1)
def _payload() -> str:
    try:
        source = MONITOR_PAYLOAD.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallerError(f"The Box monitor is missing from the package: {exc}") from exc
    if not source.startswith(MONITOR_MARKER) or source.count(DATA_LINE) != 1:
        raise InstallerError("The Box monitor lacks its package marker or data line.")
    return source


def monitor_source() -> str:
    """The installed monitor: the bundled monitor with the reviewed app builds in it."""
    return _with_builds(json.dumps(app_builds(), sort_keys=True))


@lru_cache(maxsize=4)
def _with_builds(builds: str) -> str:
    source = _payload().replace(DATA_LINE, f"APP_BUILDS = {builds}\n")
    compile(source, "vo_box_monitor.py", "exec")
    return source


@lru_cache(maxsize=1)
def monitor():
    """The bundled monitor as a module, for its box.cfg writer."""
    spec = importlib.util.spec_from_file_location("vo_box_monitor", MONITOR_PAYLOAD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_created(current: str, desired: str, marker: str, name: str) -> str:
    if current == "" or current.startswith(marker):
        return desired
    raise InstallerError(f"Existing {name} is unrecognized and was preserved.")


def patch_monitor(current: str) -> str:
    return _patch_created(current, monitor_source(), MONITOR_MARKER, MONITOR_FILE)


def patch_unit(current: str) -> str:
    return _patch_created(current, UNIT_CONTENT, UNIT_MARKER, UNIT_FILE)


def patch_rule(current: str) -> str:
    return _patch_created(current, RULE_CONTENT, RULE_MARKER, RULE_FILE)


def seed_record(paths) -> None:
    """Record each Box's number from box.cfg, unless a record exists, before something resets box.cfg."""
    monitor().seed(str(paths.printer_data_root))


def write_for_connected(paths, *, by_id: str | None = None) -> int | None:
    """Write box.cfg for the USB Boxes connected, numbered from the record, and return how many it lists, or None when
    box.cfg is in another layout and was left alone. Klipper is not restarted."""
    module = monitor()
    boxes = module.connected(by_id or module.SERIAL_BY_ID)
    root = str(paths.printer_data_root)
    _, _, wanted = module.plan(root, boxes)
    if wanted is None:
        return None
    module.write_config(root, boxes)
    return min(len(boxes), module.MAX_BOXES)


def remove_files(paths) -> None:
    """The record, the waiting change, and the previous box.cfg go with the monitor at uninstall."""
    module = monitor()
    for name in (module.RECORD, module.STATE, module.PREVIOUS):
        try:
            (paths.printer_data_root / name).unlink()
        except FileNotFoundError:
            pass
