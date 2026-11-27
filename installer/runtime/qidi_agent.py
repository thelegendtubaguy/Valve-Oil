"""Valve Oil's stand-in for QIDI's touchscreen app's duties besides drawing the screen: the agent service and the USB
drive mounter, their units and udev rules.

Valve Oil's patch of the reviewed touchscreen app builds (qidiclient_firmware_popup.py) switches those duties off in
the app. installer/system/vo_qidi_agent.py and its helpers (vo_qidi_*.py) do them instead as vo-qidi-agent.service:
the Moonraker agent QIDI's components call, the print state power-loss resume reads, resuming after the Klipper errors
the app resumed from, box_count, the AI pause, the error list, and LAN discovery. The app's QIDI cloud work and P2P
remote access are patched out and done by nothing.
installer/system/vo_usb_mount.py mounts USB drives, started by Valve Oil's udev rule, and a rules file of the same name
as QIDI's makerbase-automount rule switches that mounter off. The Box monitor (box_config.py) keeps MCU_ID.cfg.
installer/system/vo_qidi_block.py, run as root by vo-qidi-block.service, keeps the printer from reaching QIDI and the
third-party services QIDI's software uses, whichever app is on disk.

Each installed file but the block carries the reviewed app builds it acts for, so with any other app on disk (after a
QIDI firmware update, until Valve Oil installs again) the app keeps its duties and these do nothing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .errors import InstallerError
from .qidiclient_firmware_popup import BUILDS

SYSTEM = Path(__file__).resolve().parents[1] / "system"
LIB = "/usr/local/lib"
DATA_LINE = "APP_BUILDS = None\n"

UNIT = "vo-qidi-agent.service"
UNIT_FILE = f"/etc/systemd/system/{UNIT}"
UNIT_OPERATION = "qidi_agent_unit"
UNIT_MARKER = "# vo:qidi-agent-unit\n"

USB_UNIT_FILE = "/etc/systemd/system/vo-usb-mount@.service"
USB_UNIT_OPERATION = "usb_mount_unit"
USB_UNIT_MARKER = "# vo:usb-mount-unit\n"
USB_RULE_FILE = "/etc/udev/rules.d/99-vo-usb-mount.rules"
USB_RULE_OPERATION = "usb_mount_rule"
USB_RULE_MARKER = "# vo:usb-mount-rule\n"
# A file in /etc/udev/rules.d replaces the one of the same name in /lib/udev/rules.d.
MAKERBASE_RULE_FILE = "/etc/udev/rules.d/60-usbmount.rules"
MAKERBASE_RULE_OPERATION = "usb_mount_makerbase_off"
MAKERBASE_RULE_MARKER = "# vo:usb-mount-makerbase-off\n"

BLOCK_UNIT = "vo-qidi-block.service"
BLOCK_UNIT_FILE = f"/etc/systemd/system/{BLOCK_UNIT}"
BLOCK_UNIT_OPERATION = "qidi_block_unit"
BLOCK_UNIT_MARKER = "# vo:qidi-block-unit\n"


@dataclass(frozen=True)
class Payload:
    operation: str
    name: str  # the file's name in installer/system and /usr/local/lib, without .py
    marker: str
    data_line: str | None = None  # the line the installer fills with the reviewed app builds

    @property
    def file(self) -> str:
        return f"{LIB}/{self.name}.py"

    @property
    def source(self) -> Path:
        return SYSTEM / f"{self.name}.py"


AGENT = Payload("qidi_agent", "vo_qidi_agent", "# vo:qidi-agent\n", DATA_LINE)
HELPERS = (
    Payload("qidi_agent_errors", "vo_qidi_errors", "# vo:qidi-errors\n"),
    Payload("qidi_agent_files", "vo_qidi_files", "# vo:qidi-files\n"),
    Payload("qidi_agent_lan", "vo_qidi_lan", "# vo:qidi-lan\n"),
)
USB_MOUNT = Payload("usb_mount", "vo_usb_mount", "# vo:usb-mount\n", DATA_LINE)
BLOCK = Payload("qidi_block", "vo_qidi_block", "# vo:qidi-block\n")
PAYLOADS = (AGENT, *HELPERS, USB_MOUNT, BLOCK)
PAYLOAD_BY_OPERATION = {payload.operation: payload for payload in PAYLOADS}

# The service is a Moonraker client like the app it stands in for, so it runs as qidi, off Klipper's CPUs, below the
# print-critical services, and within a memory ceiling.
UNIT_CONTENT = UNIT_MARKER + f"""\
# Does what QIDI's touchscreen app did besides drawing the screen. Installed by Valve Oil.
[Unit]
Description=Valve Oil stand-in for QIDI's touchscreen app duties
After=moonraker.service network.target
ConditionPathExists={AGENT.file}

[Service]
Type=simple
User=qidi
ExecStart=/usr/bin/python3 -I {AGENT.file}
Restart=always
RestartSec=5
Nice=10
CPUAffinity=0
MemoryMax=96M

[Install]
WantedBy=multi-user.target
"""

USB_UNIT_CONTENT = USB_UNIT_MARKER + f"""\
# Mounts or unmounts a USB drive at printer_data/gcodes/USB; started by Valve Oil's udev rule. Installed by Valve Oil.
[Unit]
Description=Valve Oil USB drive %i
ConditionPathExists={USB_MOUNT.file}

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I -S {USB_MOUNT.file} %i
"""

_START = 'RUN+="/bin/systemctl --no-block start vo-usb-mount@{action}-%k.service"'
USB_RULE_CONTENT = USB_RULE_MARKER + f"""\
# Mounts a USB drive's partition when it is added and unmounts it when it is removed. Installed by Valve Oil.
ACTION=="add", SUBSYSTEM=="block", ENV{{ID_BUS}}=="usb", ENV{{DEVTYPE}}=="partition", {_START.format(action="add")}
ACTION=="remove", SUBSYSTEM=="block", ENV{{ID_BUS}}=="usb", {_START.format(action="remove")}
"""

# Root, for /etc/hosts and routes. It is ready once its first check is done, before QIDI's app and Moonraker start, and
# stops after them; stopping removes what it blocked.
BLOCK_UNIT_CONTENT = BLOCK_UNIT_MARKER + f"""\
# Keeps the printer from reaching QIDI and the services QIDI's software uses. Installed by Valve Oil.
[Unit]
Description=Valve Oil block of QIDI's servers
After=local-fs.target
Before=moonraker.service qidi-client.service
ConditionPathExists={BLOCK.file}

[Service]
Type=notify
NotifyAccess=main
TimeoutStartSec=30
ExecStart=/usr/bin/python3 -I -S {BLOCK.file}
Restart=always
RestartSec=5
Nice=10
CPUAffinity=0
MemoryMax=32M

[Install]
WantedBy=multi-user.target
"""

MAKERBASE_RULE_CONTENT = MAKERBASE_RULE_MARKER + """\
# Replaces QIDI's makerbase-automount rule, which mounted every USB drive again on each add event, while Valve Oil
# mounts USB drives (99-vo-usb-mount.rules). Installed by Valve Oil; uninstall removes it and QIDI's rule applies again.
"""


def app_builds() -> dict[str, dict]:
    """For each reviewed app build, by file size: where its takeover patch is, as [file offset, patched bytes as
    hex], and the name it connects to Moonraker as."""
    return {str(build.size): {"site": [build.agent.offset, build.agent.patched.hex()], "name": build.model}
            for build in BUILDS.values()}


@lru_cache(maxsize=None)
def _bundled(name: str) -> str:
    payload = next(payload for payload in PAYLOADS if payload.name == name)
    try:
        source = payload.source.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallerError(f"{payload.source.name} is missing from the package: {exc}") from exc
    if not source.startswith(payload.marker):
        raise InstallerError(f"{payload.source.name} lacks its package marker.")
    if payload.data_line is not None and source.count(payload.data_line) != 1:
        raise InstallerError(f"{payload.source.name} lacks its data line.")
    return source


@lru_cache(maxsize=None)
def installed_source(name: str) -> str:
    """The installed file: the bundled one with the reviewed app builds in its data line."""
    payload = next(payload for payload in PAYLOADS if payload.name == name)
    source = _bundled(name)
    if payload.data_line is not None:
        source = source.replace(payload.data_line, f"APP_BUILDS = {json.dumps(app_builds(), sort_keys=True)}\n")
    compile(source, payload.file, "exec")
    return source


def _patch_created(current: str, desired: str, marker: str, name: str) -> str:
    if current == "" or current.startswith(marker):
        return desired
    raise InstallerError(f"Existing {name} is unrecognized and was preserved.")


def patcher(operation: str):
    """patch(current) for one of this module's files."""
    payload = PAYLOAD_BY_OPERATION.get(operation)
    if payload is not None:
        return lambda current: _patch_created(current, installed_source(payload.name), payload.marker, payload.file)
    content, marker, file = {
        UNIT_OPERATION: (UNIT_CONTENT, UNIT_MARKER, UNIT_FILE),
        USB_UNIT_OPERATION: (USB_UNIT_CONTENT, USB_UNIT_MARKER, USB_UNIT_FILE),
        USB_RULE_OPERATION: (USB_RULE_CONTENT, USB_RULE_MARKER, USB_RULE_FILE),
        MAKERBASE_RULE_OPERATION: (MAKERBASE_RULE_CONTENT, MAKERBASE_RULE_MARKER, MAKERBASE_RULE_FILE),
        BLOCK_UNIT_OPERATION: (BLOCK_UNIT_CONTENT, BLOCK_UNIT_MARKER, BLOCK_UNIT_FILE),
    }[operation]
    return lambda current: _patch_created(current, content, marker, file)


# Install order: the helpers before the service that imports them, the unit after its files, the mounter before its
# unit and rule, and makerbase's rule switched off last, once Valve Oil's mounter is in place.
OPERATIONS = (*(payload.operation for payload in HELPERS), AGENT.operation, UNIT_OPERATION,
              USB_MOUNT.operation, USB_UNIT_OPERATION, USB_RULE_OPERATION, MAKERBASE_RULE_OPERATION,
              BLOCK.operation, BLOCK_UNIT_OPERATION)


LABELS = {
    **{payload.operation: f"Valve Oil {payload.name}.py" for payload in PAYLOADS},
    UNIT_OPERATION: "Valve Oil touchscreen-app duties unit",
    USB_UNIT_OPERATION: "Valve Oil USB drive mount unit",
    USB_RULE_OPERATION: "Valve Oil USB drive udev rule",
    MAKERBASE_RULE_OPERATION: "QIDI makerbase-automount udev rule switched off",
    BLOCK_UNIT_OPERATION: "Valve Oil QIDI block unit",
}


def restart_service(operation: str) -> str | None:
    """Each service restarts for its own files; the mounter runs per udev event, and udev reads rules itself."""
    if operation in (BLOCK.operation, BLOCK_UNIT_OPERATION):
        return BLOCK_UNIT
    return UNIT if operation in (UNIT_OPERATION, AGENT.operation, *(payload.operation for payload in HELPERS)) else None


def service_unit(operation: str) -> str | None:
    """The unit an operation's file is: install enables it, uninstall stops and disables it."""
    return {UNIT_OPERATION: UNIT, BLOCK_UNIT_OPERATION: BLOCK_UNIT}.get(operation)


def file_for(operation: str) -> str:
    payload = PAYLOAD_BY_OPERATION.get(operation)
    if payload is not None:
        return payload.file
    return {UNIT_OPERATION: UNIT_FILE, USB_UNIT_OPERATION: USB_UNIT_FILE, USB_RULE_OPERATION: USB_RULE_FILE,
            MAKERBASE_RULE_OPERATION: MAKERBASE_RULE_FILE, BLOCK_UNIT_OPERATION: BLOCK_UNIT_FILE}[operation]


def payload_module(name: str):
    """A bundled payload as a module, for tests and the installer's own checks."""
    import importlib.util
    payload = next(payload for payload in PAYLOADS if payload.name == name)
    spec = importlib.util.spec_from_file_location(f"{name}_bundled", payload.source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
