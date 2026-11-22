from __future__ import annotations

import base64
import configparser
import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .errors import InstallerError
from .models import AddonSpec, AddonsSpec, SystemKernelModuleSpec, SystemWheelSpec
from .naming import has_marker
from .sudo import run_sudo


# Optional add-ons live in their own directories under ROOT, each with a systemd unit. A Python add-on gets its own
# environment built from wheels the package ships; a program the package doesn't ship is downloaded, and checked
# against the sha256 the manifest pins, when the operator installs it. Nothing outside ROOT and the unit file changes: no system packages, no
# configuration under printer_data/config, and no Moonraker update manager entries. The one exception is an add-on
# that needs a kernel module QIDI's kernel lacks: the package's build of the module and the script that loads it go in a
# root-owned directory under SYSTEM_ROOT, since root loads them.
ROOT = "/home/qidi/vo-addons"
# Releases up to 26.11.18.1 installed add-ons here. 26.11.18.2 moved ROOT without moving them; move_earlier_addons
# moves them now.
EARLIER_ROOT = "/home/qidi/tltg-addons"
SYSTEM_ROOT = "/usr/local/lib/vo-addons"
# Where releases before 26.11.22.1 put add-on kernel modules; move_earlier_addons moves them with their add-on.
EARLIER_SYSTEM_ROOT = "/usr/local/lib/tltg-addons"
MODULE_LOADER = "load-modules.sh"
OSRELEASE = "/proc/sys/kernel/osrelease"
KERNEL_VERSION = "/proc/sys/kernel/version"
UNIT_DIR = "/etc/systemd/system"
SYSTEM_PYTHON = "/usr/bin/python3"
# Marks a package-owned add-on directory and records what it was built from.
MARKER = ".vo-addon.json"
# The same two markers as releases before 26.11.22.1 named them. Both are read; an update or move_earlier_addons
# replaces them.
EARLIER_MARKER = ".tltg-addon.json"
EARLIER_DATA_MARKER = ".tltg-addon-data"
# Marks a package-owned add-on data directory, which updates keep and removal deletes.
DATA_MARKER = ".vo-addon-data"
STAGING_SUFFIX = ".vo-staging"
PREVIOUS_SUFFIX = ".vo-previous"
# A started service must stay active this long to count as running.
# A started service counts as running once it has stayed up this long without systemd restarting it. OctoEverywhere
# takes about 7 s on the printer to load and fail on a bad configuration, and Restart=always brings it straight back.
START_SETTLE_SECONDS = 20.0
START_TIMEOUT_SECONDS = 45.0
MAX_SOURCE_ENTRIES = 8192
MAX_SOURCE_BYTES = 128 * 1024 * 1024
DOWNLOAD_NAME = ".download"
DOWNLOAD_TIMEOUT_SECONDS = 60.0
BEFORE_REMOVE_TIMEOUT_SECONDS = 15.0
# Files the package adds to an add-on's source come from here.
PAYLOAD_DIR = Path(__file__).resolve().parents[1] / "system"

NOT_INSTALLED = "not_installed"
INSTALLED = "installed"
UPDATE_AVAILABLE = "update_available"
FOREIGN = "foreign"

RunFn = Callable[..., subprocess.CompletedProcess]
UrlOpenFn = Callable[..., Any]


class AddonError(InstallerError):
    pass


@dataclass(frozen=True)
class AddonDefinition:
    id: str
    name: str
    description: str
    homepage: str
    service: str
    # Paths that mean the add-on was installed some other way; the package leaves those installs alone.
    foreign_paths: tuple[str, ...]
    render_unit: Callable[["AddonPaths"], str]
    # Settings the package owns in the add-on's configuration files: relative path -> section -> key -> value.
    settings: dict[str, dict[str, dict[str, str]]]
    # Directories in the data directory the add-on needs before it starts.
    data_dirs: tuple[str, ...] = ()
    # Settings written only when missing, so the operator's own values stay: same shape as settings.
    defaults: dict[str, dict[str, dict[str, str]]] | None = None
    # Package files added to the add-on's source: path under app -> file in PAYLOAD_DIR.
    files: dict[str, str] | None = None
    # Add-ons in the same group can't be installed together.
    exclusive_group: str | None = None
    # Where the add-on keeps its server and account link, for relinking: (config file, section, server key, token key).
    link_settings: tuple[str, str, str, str] | None = None
    # Where the operator finds the add-on's log when it doesn't keep running.
    log_hint: str = "its log in Fluidd"
    # A command run, without waiting long or minding its result, before a removal stops the service.
    before_remove: Callable[["AddonPaths"], list[str]] | None = None
    # A downloaded Debian package's files the add-on uses: path in the package -> path under app.
    deb_members: dict[str, str] | None = None

    @property
    def unit(self) -> str:
        return f"{self.service}.service"

    @property
    def unit_marker(self) -> str:
        return f"# vo:addon-{self.id}\n"


@dataclass(frozen=True)
class AddonPaths:
    code: str
    data: str
    # Root-owned: the add-on's kernel modules and their loader.
    system: str

    @property
    def app(self) -> str:
        return f"{self.code}/app"

    @property
    def env(self) -> str:
        return f"{self.code}/env"

    @property
    def python(self) -> str:
        return f"{self.env}/bin/python3"


def addon_paths(addon_id: str, base: str = ROOT, system_root: str = SYSTEM_ROOT) -> AddonPaths:
    return AddonPaths(code=f"{base}/{addon_id}", data=f"{base}/{addon_id}-data", system=f"{system_root}/{addon_id}")


def _data_marked(data: Path) -> bool:
    """Whether a data directory carries the package's data marker, under its name now or an earlier release's."""
    return (data / DATA_MARKER).is_file() or (data / EARLIER_DATA_MARKER).is_file()


# OctoEverywhere's own installer also installs system packages, replaces NTP, sets the clock, and adds a Moonraker
# update-manager section, which turns on Moonraker's update panel and its system package upgrades. None of that is
# needed here: the plugin runs from its own environment, keeps its configuration outside printer_data/config so
# firmware updates keep it, and is told not to write moonraker.conf. Its parent directory has no Fluidd or Mainsail
# folder, so it doesn't inject its script into either Fluidd.
OCTOEVERYWHERE_STATUS_PORT = 7132
# OctoEverywhere and Obico both relay Fluidd and the camera and watch prints; running both doubles the load for nothing.
REMOTE_ACCESS = "remote-access"
OCTOEVERYWHERE_LOG_DIR = "/home/qidi/printer_data/logs"
OCTOEVERYWHERE_MOONRAKER_CONF = "/home/qidi/printer_data/config/moonraker.conf"


def _octoeverywhere_unit(paths: AddonPaths) -> str:
    args = {
        "ServiceName": "octoeverywhere",
        "VirtualEnvPath": paths.env,
        "RepoRootFolder": paths.app,
        "LocalFileStoragePath": f"{paths.data}/store",
        "ConfigFolder": f"{paths.data}/config",
        "LogFolder": OCTOEVERYWHERE_LOG_DIR,
        "IsCompanion": False,
        "MoonrakerConfigFile": OCTOEVERYWHERE_MOONRAKER_CONF,
        "DisableMoonrakerConfigFileWrites": True,
    }
    encoded = base64.b64encode(json.dumps(args, separators=(",", ":")).encode("utf-8")).decode("ascii")
    return OCTOEVERYWHERE.unit_marker + f"""\
# OctoEverywhere, installed by Valve Oil. Remove it from Fluidd's Settings or with addons.sh remove octoeverywhere.
[Unit]
Description=OctoEverywhere (Valve Oil add-on)
After=network-online.target moonraker.service
Wants=network-online.target
ConditionPathExists={paths.code}/{MARKER}

[Service]
Type=simple
User=qidi
WorkingDirectory={paths.app}
Environment="MALLOC_TRIM_THRESHOLD_=65536"
Environment="MALLOC_ARENA_MAX=2"
ExecStart={paths.python} -m moonraker_octoeverywhere "{encoded}"
Restart=always
RestartSec=10
# Off Klipper's CPUs and behind it for CPU time.
Nice=10
CPUAffinity=0 3

[Install]
WantedBy=multi-user.target
"""


OCTOEVERYWHERE = AddonDefinition(
    id="octoeverywhere",
    name="OctoEverywhere",
    description="Remote access to Fluidd, print notifications, and AI failure detection from anywhere.",
    homepage="https://octoeverywhere.com",
    service="octoeverywhere",
    foreign_paths=("/home/qidi/octoeverywhere", "/home/qidi/octoeverywhere-env"),
    render_unit=_octoeverywhere_unit,
    exclusive_group=REMOTE_ACCESS,
    # The plugin refuses to start without its config and storage directories.
    data_dirs=("config", "store"),
    settings={
        "config/octoeverywhere.conf": {
            # The plugin's status API, which the add-ons service reads for Fluidd; loopback only.
            "general": {
                "local_web_api_port": str(OCTOEVERYWHERE_STATUS_PORT),
                "local_web_api_bind_ip": "127.0.0.1",
            },
            # Remote access opens the Fluidd nginx serves on port 80.
            "relay": {"frontend_port": "80", "frontend_type_hint": "Fluidd"},
        },
    },
)


# Obico's own installer also installs system packages, adds a Moonraker update-manager section, and keeps its
# configuration in printer_data/config; the plugin itself adds an include to printer.cfg. Here it runs from its own
# environment through Valve Oil's launcher (installer/system/vo_obico.py), which stops the printer.cfg change, picks
# the plugin's Janus build for QIDI's Debian, and reports the link code for Fluidd.
OBICO_CLOUD = "https://app.obico.io"
OBICO_CONFIG = "moonraker-obico.cfg"
OBICO_STATUS = "vo-status.json"
OBICO_LOG = "/home/qidi/printer_data/logs/moonraker-obico.log"
OBICO_LAUNCHER = "vo_obico.py"


def _obico_unit(paths: AddonPaths) -> str:
    return OBICO.unit_marker + f"""\
# Obico, installed by Valve Oil. Remove it from Fluidd's Settings or with addons.sh remove obico.
[Unit]
Description=Obico (Valve Oil add-on)
After=network-online.target moonraker.service
Wants=network-online.target
ConditionPathExists={paths.code}/{MARKER}

[Service]
Type=simple
User=qidi
WorkingDirectory={paths.app}
Environment="VO_OBICO_STATUS={paths.data}/{OBICO_STATUS}"
Environment="MALLOC_TRIM_THRESHOLD_=65536"
Environment="MALLOC_ARENA_MAX=2"
ExecStart={paths.python} -m {OBICO_LAUNCHER.removesuffix(".py")} -c {paths.data}/{OBICO_CONFIG}
Restart=always
RestartSec=10
# Off Klipper's CPUs and behind it for CPU time.
Nice=10
CPUAffinity=0 3

[Install]
WantedBy=multi-user.target
"""


OBICO = AddonDefinition(
    id="obico",
    name="Obico",
    description="Remote access to Fluidd and the camera, print notifications, and AI failure detection, through Obico's cloud or your own Obico server.",
    homepage="https://www.obico.io",
    # Moonraker lists moonraker-obico among the services Fluidd's power menu may control.
    service="moonraker-obico",
    foreign_paths=("/home/qidi/moonraker-obico", "/home/qidi/moonraker-obico-env"),
    render_unit=_obico_unit,
    settings={
        OBICO_CONFIG: {
            "moonraker": {"host": "127.0.0.1", "port": "7125"},
            "logging": {"path": OBICO_LOG},
            # Remote access opens the Fluidd nginx serves on port 80.
            "tunnel": {"dest_host": "127.0.0.1", "dest_port": "80"},
        },
    },
    defaults={
        OBICO_CONFIG: {
            "server": {"url": OBICO_CLOUD},
            "webcam": {"disable_video_streaming": "False"},
            "logging": {"level": "INFO"},
        },
    },
    files={OBICO_LAUNCHER: "vo_obico.py"},
    exclusive_group=REMOTE_ACCESS,
    link_settings=(OBICO_CONFIG, "server", "url", "auth_token"),
)


# Tailscale's packages need a TUN device, netfilter, and policy routing, none of which QIDI's kernel has, and they
# install system-wide as root. Here tailscaled runs as the qidi user with its userspace network stack, which needs none
# of them: it hands connections arriving from the tailnet to the printer's own services over loopback, and changes
# no routes, firewall rules, or DNS settings. The printer itself can't open connections to other tailnet devices.
TAILSCALE_SOCKET = "tailscaled.sock"
# Tailscale's usual port, so routers that already forward or map it keep giving direct connections.
TAILSCALE_PORT = 41641


def _tailscale_unit(paths: AddonPaths) -> str:
    return TAILSCALE.unit_marker + f"""\
# Tailscale, installed by Valve Oil. Remove it from Fluidd's Settings or with addons.sh remove tailscale.
[Unit]
Description=Tailscale (Valve Oil add-on)
After=network-online.target
Wants=network-online.target
ConditionPathExists={paths.code}/{MARKER}

[Service]
Type=simple
User=qidi
WorkingDirectory={paths.data}
ExecStart={paths.app}/tailscaled --tun=userspace-networking --statedir={paths.data} --socket={paths.data}/{TAILSCALE_SOCKET} --port={TAILSCALE_PORT}
Restart=always
RestartSec=10
# Off Klipper's CPUs and behind it for CPU time.
Nice=10
CPUAffinity=0 3

[Install]
WantedBy=multi-user.target
"""


TAILSCALE = AddonDefinition(
    id="tailscale",
    name="Tailscale",
    description="Reach Fluidd, the camera, and SSH from your own devices anywhere, over your private Tailscale network.",
    homepage="https://tailscale.com",
    # Tailscale's own packages use the same unit name, so their install shows as one made outside Valve Oil.
    service="tailscaled",
    foreign_paths=("/usr/sbin/tailscaled", "/var/lib/tailscale"),
    render_unit=_tailscale_unit,
    settings={},
    log_hint="`journalctl -u tailscaled` over SSH",
    # Removal deletes the printer's node key, so tell the tailnet the key is finished with first.
    before_remove=lambda paths: [f"{paths.app}/tailscale", f"--socket={paths.data}/{TAILSCALE_SOCKET}", "logout"],
)

# ZeroTier's agent has no userspace network stack: it needs a TUN device, which QIDI's kernel doesn't have, so the
# package loads its own build of the tun module, made for QIDI's kernel, before ZeroTier starts. zerotier-one then runs
# as the qidi user with only the capabilities it needs to create and configure its network interface, keeping its
# identity, networks, and local API token in its data directory. ZeroTier's own package installs system-wide as root.
ZEROTIER_PORT = 9993


def _zerotier_unit(paths: AddonPaths) -> str:
    return ZEROTIER.unit_marker + f"""\
# ZeroTier, installed by Valve Oil. Remove it from Fluidd's Settings or with addons.sh remove zerotier.
[Unit]
Description=ZeroTier (Valve Oil add-on)
After=network-online.target
Wants=network-online.target
ConditionPathExists={paths.code}/{MARKER}

[Service]
Type=simple
User=qidi
WorkingDirectory={paths.data}
# As root: the tun module, on the kernel builds it is made for only.
ExecStartPre=+{paths.system}/{MODULE_LOADER}
# -U: run as this user instead of insisting on root.
ExecStart={paths.app}/zerotier-one -U -p{ZEROTIER_PORT} {paths.data}
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW
NoNewPrivileges=yes
Restart=always
RestartSec=10
# Off Klipper's CPUs and behind it for CPU time.
Nice=10
CPUAffinity=0 3

[Install]
WantedBy=multi-user.target
"""


ZEROTIER = AddonDefinition(
    id="zerotier",
    name="ZeroTier",
    description="Reach Fluidd, the camera, and SSH from your own devices anywhere, over your ZeroTier network.",
    homepage="https://www.zerotier.com",
    # ZeroTier's own package uses the same unit name, so its install shows as one made outside Valve Oil.
    service="zerotier-one",
    foreign_paths=("/usr/sbin/zerotier-one", "/var/lib/zerotier-one"),
    render_unit=_zerotier_unit,
    settings={},
    log_hint="`journalctl -u zerotier-one` over SSH",
    # zerotier-cli and zerotier-idtool are the same program under other names; it is run as `zerotier-one -q`.
    deb_members={"usr/sbin/zerotier-one": "zerotier-one"},
)

DEFINITIONS: dict[str, AddonDefinition] = {
    OCTOEVERYWHERE.id: OCTOEVERYWHERE,
    OBICO.id: OBICO,
    TAILSCALE.id: TAILSCALE,
    ZEROTIER.id: ZEROTIER,
}


# ---------------------------------------------------------------------------------------------------------------------
# Status


def list_addons(spec: AddonsSpec | None, *, root: Path = Path("/"), run: RunFn = subprocess.run) -> list[dict[str, Any]]:
    """Describe each catalog add-on: what the package offers, what is installed, and its service."""
    result = []
    for addon in spec.catalog if spec else ():
        definition = DEFINITIONS.get(addon.id)
        if definition is None:
            continue
        marker = read_marker(addon.id, root=root)
        state = addon_state(definition, addon, marker, root=root)
        installed = state in {INSTALLED, UPDATE_AVAILABLE}
        blockers = [] if installed else [other.name for other in _blockers(definition, root=root)]
        result.append(
            {
                "id": addon.id,
                "name": definition.name,
                "description": definition.description,
                "homepage": definition.homepage,
                "available_version": addon.version,
                "installed_version": marker.get("version") if marker else None,
                "state": state,
                "service": {"name": definition.service, "active": service_active(definition.unit, run=run)} if installed else None,
                # Other add-ons that must be removed before this one can be installed.
                "blocked_by": blockers,
            }
        )
    return result


def addon_state(definition: AddonDefinition, addon: AddonSpec, marker: dict[str, Any] | None, *, root: Path) -> str:
    if marker is not None:
        return INSTALLED if marker.get("build") == build_id(addon, definition) else UPDATE_AVAILABLE
    if _foreign_install(definition, root=root):
        return FOREIGN
    return NOT_INSTALLED


def build_id(addon: AddonSpec, definition: AddonDefinition) -> str:
    """Identify the add-on's pinned files and the package's files for it, independently of their order."""
    digests = sorted([addon.source.sha256, *(item.sha256 for item in (*addon.wheels, *addon.sdists, *addon.kernel_modules))])
    digests += sorted(hashlib.sha256(f"{name}\n".encode("utf-8") + data).hexdigest() for name, data in _package_files(definition).items())
    if addon.kernel_modules:
        digests.append(hashlib.sha256(render_module_loader(addon).encode("utf-8")).hexdigest())
    return hashlib.sha256(f"{addon.id}\n{addon.version}\n".encode("utf-8") + "\n".join(digests).encode("ascii")).hexdigest()


def _package_files(definition: AddonDefinition) -> dict[str, bytes]:
    files = {}
    for relative, payload in (definition.files or {}).items():
        try:
            files[relative] = (PAYLOAD_DIR / payload).read_bytes()
        except OSError as exc:
            raise AddonError(f"{payload} is missing from the package.") from exc
    return files


def render_module_loader(addon: AddonSpec) -> str:
    """The root-run script that loads the add-on's kernel modules, on the kernel builds they are made for only."""
    directory = addon_paths(addon.id).system
    names = " ".join(module.name for module in addon.kernel_modules)
    builds = " | ".join(f"'{addon.kernel_release} {version}'" for version in addon.kernel_versions)
    return f"""#!/bin/sh
# Loads the kernel modules the {addon.id} add-on needs, built for QIDI's kernel. Installed by Valve Oil.
set -u
case "$(cat {OSRELEASE}) $(cat {KERNEL_VERSION})" in
    {builds}) ;;
    *)
        echo "Kernel $(cat {OSRELEASE}) $(cat {KERNEL_VERSION}) is not a build Valve Oil's {names} module is made for." >&2
        exit 1
        ;;
esac
loaded=0
for module in {names}; do
    grep -q "^$module " /proc/modules && continue
    insmod "{directory}/$module.ko" || exit 1
    loaded=1
done
# udev gives a new device its permissions, such as /dev/net/tun's 0666, just after the module loads.
[ "$loaded" = 0 ] || udevadm settle --timeout=10
"""


def kernel_supported(addon: AddonSpec, *, root: Path) -> bool:
    """Whether the running kernel is one of the builds the add-on's kernel modules are made for."""
    try:
        release = _map(root, OSRELEASE).read_text(encoding="utf-8").strip()
        version = _map(root, KERNEL_VERSION).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return release == addon.kernel_release and version in addon.kernel_versions


def read_marker(addon_id: str, *, root: Path, base: str = ROOT) -> dict[str, Any] | None:
    for name in (MARKER, EARLIER_MARKER):
        path = _map(root, f"{addon_paths(addon_id, base).code}/{name}")
        try:
            if path.is_symlink() or not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and data.get("id") == addon_id else None
    return None


def service_active(unit: str, *, run: RunFn) -> str:
    try:
        result = run(["systemctl", "is-active", unit], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
    except OSError:
        return "unknown"
    state = (result.stdout or "").strip().splitlines()
    return state[0] if state else "unknown"


def _blockers(definition: AddonDefinition, *, root: Path) -> list[AddonDefinition]:
    """Add-ons in the same exclusive group that are installed, by the package or otherwise."""
    if definition.exclusive_group is None:
        return []
    return [
        other
        for other in DEFINITIONS.values()
        if other.id != definition.id
        and other.exclusive_group == definition.exclusive_group
        and (read_marker(other.id, root=root) is not None or unit_owned(other, root=root) or _foreign_install(other, root=root))
    ]


def _foreign_install(definition: AddonDefinition, *, root: Path) -> bool:
    if any(_map(root, path).exists() for path in definition.foreign_paths):
        return True
    unit = _map(root, f"{UNIT_DIR}/{definition.unit}")
    if unit.is_symlink():
        return True
    try:
        return unit.is_file() and not has_marker(unit.read_text(encoding="utf-8"), definition.unit_marker)
    except OSError:
        return True


# ---------------------------------------------------------------------------------------------------------------------
# Install, update, and remove


def install_addon(
    addon_id: str,
    *,
    spec: AddonsSpec | None,
    installer_root: Path,
    root: Path = Path("/"),
    sudo_password: str | None,
    run: RunFn = subprocess.run,
    status: Callable[[str], None] = lambda message: None,
    sleep: Callable[[float], None] = time.sleep,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> str:
    """Install the pinned add-on, or bring an installed one to the pinned build. Returns the resulting state."""
    definition, addon = _resolve(addon_id, spec)
    marker = read_marker(addon_id, root=root)
    state = addon_state(definition, addon, marker, root=root)
    if state == FOREIGN:
        raise AddonError(f"{definition.name} was installed outside Valve Oil; remove that install first.")
    blockers = _blockers(definition, root=root) if marker is None else []
    if blockers:
        names = " and ".join(other.name for other in blockers)
        raise AddonError(f"Remove {names} first; {definition.name} and {names} can't be installed together.")
    if state == INSTALLED and _current(definition, addon, root=root):
        status(f"{definition.name} {addon.version} is already installed.")
        _ensure_data(definition, root=root)
        _start(definition, root=root, sudo_password=sudo_password, run=run)
        return INSTALLED

    paths = addon_paths(addon_id)
    code = _map(root, paths.code)
    staging = _map(root, paths.code + STAGING_SUFFIX)
    previous = _map(root, paths.code + PREVIOUS_SUFFIX)
    _verify_artifacts(spec, addon, installer_root)
    if addon.kernel_modules and not kernel_supported(addon, root=root):
        raise AddonError(
            f"{definition.name} needs a kernel module Valve Oil builds for QIDI's kernel, and this printer's kernel isn't a "
            "build it is made for; nothing was installed."
        )
    if state == INSTALLED:
        # Same build, missing or edited unit or kernel modules: rewrite those only.
        status(f"Repairing the {definition.name} service")
        _ensure_data(definition, root=root)
        _install_system_files(addon, installer_root=installer_root, root=root, sudo_password=sudo_password, run=run)
        _install_unit(definition, root=root, sudo_password=sudo_password, run=run)
        _start(definition, root=root, sudo_password=sudo_password, run=run, wait=True, sleep=sleep)
        return INSTALLED

    status(f"Preparing {definition.name} {addon.version}")
    _remove_tree(staging, root=root)
    _remove_tree(previous, root=root)
    staging.mkdir(parents=True)
    if addon.download:
        status(f"Downloading {definition.name} {addon.version}")
        archive = staging / DOWNLOAD_NAME
        try:
            _download(addon.source, archive, urlopen=urlopen)
            if definition.deb_members:
                _extract_deb(archive, staging / "app", definition.deb_members)
            else:
                _extract_source(archive, staging / "app")
        except AddonError:
            _remove_tree(staging, root=root)
            raise
        archive.unlink()
    else:
        _extract_source(installer_root / addon.source.archive, staging / "app")
    for relative, data in _package_files(definition).items():
        target = staging / "app" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if addon.wheels:
        status("Creating its Python environment")
        _checked(run, [SYSTEM_PYTHON, "-m", "venv", "--without-pip", str(staging / "env")], "Could not create the add-on's Python environment.")
        status(f"Installing {len(addon.wheels)} Python packages")
        pip = installer_root / spec.pip_wheel.archive
        _checked(
            run,
            [
                str(staging / "env/bin/python3"), f"{pip}/pip", "install", "--quiet", "--no-index", "--no-deps",
                "--no-compile", "--no-cache-dir", "--disable-pip-version-check", "--no-warn-script-location",
                *(str(installer_root / wheel.archive) for wheel in addon.wheels),
            ],
            "Could not install the add-on's Python packages.",
        )
        if addon.sdists:
            status(f"Building {len(addon.sdists)} Python packages")
            # Pure-Python sources, built offline with the setuptools from the wheels above.
            _checked(
                run,
                [
                    str(staging / "env/bin/python3"), f"{pip}/pip", "install", "--quiet", "--no-index", "--no-deps",
                    "--no-build-isolation", "--no-cache-dir", "--disable-pip-version-check", "--no-warn-script-location",
                    *(str(installer_root / sdist.archive) for sdist in addon.sdists),
                ],
                "Could not build the add-on's Python packages.",
            )
    _write_json(staging / MARKER, {"id": addon_id, "version": addon.version, "build": build_id(addon, definition)})
    _ensure_data(definition, root=root)

    status(f"Starting {definition.name}")
    if marker is not None:
        _systemctl(["stop", definition.unit], root=root, sudo_password=sudo_password, run=run, check=False)
        code.rename(previous)
    staging.rename(code)
    try:
        _install_system_files(addon, installer_root=installer_root, root=root, sudo_password=sudo_password, run=run)
        _install_unit(definition, root=root, sudo_password=sudo_password, run=run)
        _start(definition, root=root, sudo_password=sudo_password, run=run, wait=True, sleep=sleep)
    except AddonError:
        _systemctl(["stop", definition.unit], root=root, sudo_password=sudo_password, run=run, check=False)
        _remove_tree(code, root=root)
        if previous.exists():
            status(f"Putting back {definition.name} {marker.get('version') if marker else ''}".rstrip())
            previous.rename(code)
            _systemctl(["start", definition.unit], root=root, sudo_password=sudo_password, run=run, check=False)
        else:
            _remove_unit(definition, root=root, sudo_password=sudo_password, run=run)
            _remove_system_files(definition, root=root, sudo_password=sudo_password, run=run)
        raise
    _remove_tree(previous, root=root)
    status(f"{definition.name} {addon.version} is installed and running.")
    return INSTALLED


def remove_addon(
    addon_id: str,
    *,
    root: Path = Path("/"),
    sudo_password: str | None,
    run: RunFn = subprocess.run,
    status: Callable[[str], None] = lambda message: None,
) -> bool:
    """Remove a package-owned add-on with its data. Returns False when there was nothing to remove."""
    definition = DEFINITIONS.get(addon_id)
    if definition is None:
        raise AddonError(f"Unknown add-on: {addon_id}")
    paths = addon_paths(addon_id)
    owned_unit = unit_owned(definition, root=root)
    marker = read_marker(addon_id, root=root)
    if marker is None and not owned_unit:
        if _foreign_install(definition, root=root):
            raise AddonError(f"{definition.name} was installed outside Valve Oil; Valve Oil leaves it alone.")
        return False
    status(f"Removing {definition.name}")
    if marker is not None and definition.before_remove is not None:
        try:
            run(definition.before_remove(paths), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=BEFORE_REMOVE_TIMEOUT_SECONDS, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    if owned_unit:
        _systemctl(["disable", "--now", definition.unit], root=root, sudo_password=sudo_password, run=run, check=False)
        _remove_unit(definition, root=root, sudo_password=sudo_password, run=run)
    _remove_system_files(definition, root=root, sudo_password=sudo_password, run=run)
    for path in (paths.code + STAGING_SUFFIX, paths.code + PREVIOUS_SUFFIX):
        _remove_tree(_map(root, path), root=root)
    if marker is not None:
        _remove_tree(_map(root, paths.code), root=root)
    data = _map(root, paths.data)
    if _data_marked(data):
        _remove_tree(data, root=root)
    _remove_earlier_copies(addon_id, root=root, data=True)
    _remove_root_if_empty(root=root)
    status(f"{definition.name} is removed.")
    return True


# A server address: http or https, a host name, IPv4 or bracketed IPv6 address, an optional port, and an optional
# path. Nothing that could end the configuration line or add another setting.
_SERVER_URL = re.compile(r"^https?://(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?(?:/[A-Za-z0-9._~%/-]*)?$")


def normalize_server_url(url: str) -> str:
    """The server address as the add-on stores it, or AddonError when it isn't an http or https server address."""
    url = url.strip()
    if len(url) > 200 or not _SERVER_URL.fullmatch(url):
        raise AddonError("Enter the server's address, such as http://192.168.1.10:3334 or https://obico.example.com.")
    return url.rstrip("/")


def relink_addon(
    addon_id: str,
    *,
    server: str | None = None,
    root: Path = Path("/"),
    sudo_password: str | None,
    run: RunFn = subprocess.run,
    status: Callable[[str], None] = lambda message: None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Forget the add-on's account link, optionally move it to another server, and restart it so it can be linked."""
    definition = DEFINITIONS.get(addon_id)
    if definition is None:
        raise AddonError(f"Unknown add-on: {addon_id}")
    if definition.link_settings is None:
        raise AddonError(f"{definition.name} is linked from its own site, not from Valve Oil.")
    if read_marker(addon_id, root=root) is None:
        raise AddonError(f"{definition.name} is not installed.")
    url = normalize_server_url(server) if server is not None else None
    relative, section, server_key, token_key = definition.link_settings
    status(f"Stopping {definition.name}")
    _systemctl(["stop", definition.unit], root=root, sudo_password=sudo_password, run=run, check=False)
    _ensure_data(definition, root=root)
    path = _map(root, addon_paths(addon_id).data) / relative
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(path.read_text(encoding="utf-8") if path.is_file() else "")
    except configparser.Error as exc:
        raise AddonError(f"{definition.name}'s settings can't be read: {exc}") from exc
    if not parser.has_section(section):
        parser.add_section(section)
    parser.remove_option(section, token_key)
    if url is not None:
        parser.set(section, server_key, url)
        status(f"Using the server {url}")
    buffer = io.StringIO()
    parser.write(buffer)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    status(f"Starting {definition.name}")
    _start(definition, root=root, sudo_password=sudo_password, run=run, wait=True, sleep=sleep)
    status(f"{definition.name} is ready to link.")


def update_installed_addons(
    *,
    spec: AddonsSpec | None,
    installer_root: Path,
    root: Path = Path("/"),
    sudo_password: str | None,
    run: RunFn = subprocess.run,
    status: Callable[[str], None] = lambda message: None,
    sleep: Callable[[float], None] = time.sleep,
    urlopen: UrlOpenFn = urllib.request.urlopen,
) -> list[str]:
    """Bring every installed add-on to the package's pinned build. Returns failure messages; never raises for one add-on."""
    failures = []
    for addon in spec.catalog if spec else ():
        definition = DEFINITIONS.get(addon.id)
        marker = read_marker(addon.id, root=root)
        if definition is None or marker is None:
            continue
        if addon_state(definition, addon, marker, root=root) == INSTALLED and _current(definition, addon, root=root):
            continue
        try:
            install_addon(addon.id, spec=spec, installer_root=installer_root, root=root, sudo_password=sudo_password, run=run, status=status, sleep=sleep, urlopen=urlopen)
        except (AddonError, OSError) as exc:
            failures.append(f"{definition.name}: {exc}")
    return failures


def remove_all_addons(
    *,
    root: Path = Path("/"),
    sudo_password: str | None,
    run: RunFn = subprocess.run,
    status: Callable[[str], None] = lambda message: None,
) -> list[str]:
    """Remove every package-owned add-on, as uninstall does. Returns failure messages."""
    failures = []
    for addon_id, definition in DEFINITIONS.items():
        try:
            remove_addon(addon_id, root=root, sudo_password=sudo_password, run=run, status=status)
        except (AddonError, OSError) as exc:
            failures.append(f"{definition.name}: {exc}")
    return failures


def installed_addon_ids(*, root: Path = Path("/")) -> list[str]:
    return [addon_id for addon_id in DEFINITIONS if read_marker(addon_id, root=root) is not None]


def earlier_addon_ids(*, root: Path = Path("/")) -> list[str]:
    """Add-ons with a package-owned program or data directory still under EARLIER_ROOT."""
    return [
        addon_id
        for addon_id in DEFINITIONS
        if read_marker(addon_id, root=root, base=EARLIER_ROOT) is not None
        or _data_marked(_map(root, addon_paths(addon_id, EARLIER_ROOT).data))
    ]


def move_earlier_addons(
    *,
    root: Path = Path("/"),
    sudo_password: str | None,
    run: RunFn = subprocess.run,
    status: Callable[[str], None] = lambda message: None,
) -> list[str]:
    """Move add-ons installed under EARLIER_ROOT into ROOT, with their data, and point their services there.

    Each directory moves with a rename while the service is stopped, so an interrupted move is finished by the next
    call. When the same add-on was installed again under ROOT, that install is the one its service runs: the earlier
    program directory is deleted and its data stays until the add-on is removed. Returns failure messages.
    """
    failures = []
    for addon_id in earlier_addon_ids(root=root):
        definition = DEFINITIONS[addon_id]
        earlier, paths = addon_paths(addon_id, EARLIER_ROOT), addon_paths(addon_id)
        moves = [
            (_map(root, earlier.code), _map(root, paths.code))
            if read_marker(addon_id, root=root, base=EARLIER_ROOT) is not None
            else None,
            (_map(root, earlier.data), _map(root, paths.data))
            if _data_marked(_map(root, earlier.data))
            else None,
        ]
        moves = [(source, target) for source, target in filter(None, moves) if not target.exists() and not target.is_symlink()]
        try:
            if moves:
                status(f"Moving {definition.name} to {ROOT}")
                if unit_owned(definition, root=root):
                    _systemctl(["stop", definition.unit], root=root, sudo_password=sudo_password, run=run, check=False)
                base = _map(root, ROOT)
                if base.is_symlink():
                    raise AddonError(f"Refusing to use a symlinked add-ons directory: {ROOT}")
                base.mkdir(parents=True, exist_ok=True)
                for source, target in moves:
                    source.rename(target)
                    for earlier_name, name in ((EARLIER_MARKER, MARKER), (EARLIER_DATA_MARKER, DATA_MARKER)):
                        if (target / earlier_name).is_file() and not (target / name).exists():
                            (target / earlier_name).rename(target / name)
                _move_earlier_system_files(addon_id, root=root, sudo_password=sudo_password, run=run)
            if read_marker(addon_id, root=root) is not None and unit_owned(definition, root=root) and not _unit_current(definition, root=root):
                _install_unit(definition, root=root, sudo_password=sudo_password, run=run)
                _systemctl(["start", definition.unit], root=root, sudo_password=sudo_password, run=run, check=False)
            _remove_earlier_copies(addon_id, root=root, data=False)
        except (AddonError, OSError) as exc:
            failures.append(f"{definition.name}: {exc}")
    _remove_earlier_root_if_empty(root=root)
    return failures


def _resolve(addon_id: str, spec: AddonsSpec | None) -> tuple[AddonDefinition, AddonSpec]:
    definition = DEFINITIONS.get(addon_id)
    addon = spec.find(addon_id) if spec else None
    if definition is None or addon is None:
        raise AddonError(f"Unknown add-on: {addon_id}")
    return definition, addon


def _verify_artifacts(spec: AddonsSpec, addon: AddonSpec, installer_root: Path) -> None:
    bundled = [*addon.wheels, *addon.sdists]
    if addon.wheels:
        bundled.append(spec.pip_wheel)
    if not addon.download:
        bundled.append(addon.source)
    for artifact in (*bundled, *addon.kernel_modules):
        _verify_artifact(artifact, installer_root)


def _download(artifact: SystemWheelSpec, destination: Path, *, urlopen: UrlOpenFn) -> None:
    """Fetch an add-on's source to destination, keeping it only if it is the file the manifest pins."""
    name = PurePosixPath(artifact.archive).name
    digest = hashlib.sha256()
    size = 0
    try:
        request = urllib.request.Request(artifact.url, headers={"User-Agent": "Valve-Oil-addons"})
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, destination.open("wb") as handle:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                size += len(chunk)
                if size > MAX_SOURCE_BYTES:
                    raise AddonError(f"{name} is larger than an add-on download may be.")
                digest.update(chunk)
                handle.write(chunk)
    except (OSError, ValueError) as exc:
        raise AddonError(f"Could not download {name} from {artifact.url}. Check the printer's internet connection. ({exc})") from exc
    if digest.hexdigest() != artifact.sha256:
        raise AddonError(f"The downloaded {name} is not the file this Valve Oil release pins; nothing was installed.")


def _verify_artifact(artifact: SystemWheelSpec | SystemKernelModuleSpec, installer_root: Path) -> None:
    path = installer_root / artifact.archive
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AddonError(f"{PurePosixPath(artifact.archive).name} is missing from the package.") from exc
    if digest.hexdigest() != artifact.sha256:
        raise AddonError(f"{PurePosixPath(artifact.archive).name} does not match the package manifest.")


def _extract_source(archive: Path, destination: Path) -> None:
    """Extract a release archive's single top-level directory into destination, refusing anything but plain files."""
    destination.mkdir(parents=True)
    links: list[tuple[Path, Path]] = []
    try:
        with tarfile.open(archive, mode="r:gz") as tar:
            members = tar.getmembers()
            if len(members) > MAX_SOURCE_ENTRIES or sum(member.size for member in members) > MAX_SOURCE_BYTES:
                raise AddonError("The add-on source archive is too large.")
            for member in members:
                parts = PurePosixPath(member.name).parts
                if member.name.startswith("/") or any(part in {"", ".", ".."} for part in parts) or "\\" in member.name:
                    raise AddonError(f"Unsafe path in the add-on source archive: {member.name}")
                if member.isdir() or member.type == tarfile.XGLTYPE or member.type == tarfile.XHDTYPE:
                    continue
                relative = PurePosixPath(*parts[1:])
                if not relative.parts:
                    continue
                if member.issym() and "/" not in member.linkname and member.linkname not in {"", ".", ".."}:
                    # Shared-library names, such as Obico's Janus plugins, link to a file beside them; they become
                    # copies of it.
                    target = destination.joinpath(*relative.parts)
                    links.append((target, target.with_name(member.linkname)))
                    continue
                if not member.isfile():
                    # Other links and special files aren't needed.
                    continue
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise AddonError(f"Unreadable entry in the add-on source archive: {member.name}")
                with source, target.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
        for target, source in links:
            if source.is_file() and not source.is_symlink() and not target.exists():
                shutil.copy2(source, target)
    except (tarfile.TarError, OSError) as exc:
        raise AddonError(f"Could not extract the add-on source: {exc}") from exc


def _extract_deb(archive: Path, destination: Path, members: dict[str, str]) -> None:
    """Extract the named files from a Debian package's data archive into destination; each must be a plain file."""
    destination.mkdir(parents=True)
    try:
        data = _deb_data(archive)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            found: set[str] = set()
            entries = 0
            for member in tar:
                entries += 1
                if entries > MAX_SOURCE_ENTRIES:
                    raise AddonError("The add-on package is too large.")
                name = member.name.removeprefix("./")
                if name not in members:
                    continue
                if not member.isfile() or member.size > MAX_SOURCE_BYTES:
                    raise AddonError(f"{name} in the add-on package is not a plain file.")
                target = destination / members[name]
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise AddonError(f"Unreadable entry in the add-on package: {name}")
                with source, target.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
                found.add(name)
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        raise AddonError(f"Could not extract the add-on package: {exc}") from exc
    missing = sorted(set(members) - found)
    if missing:
        raise AddonError(f"The add-on package has no {', '.join(missing)}.")


def _deb_data(archive: Path) -> bytes:
    """A Debian package's data.tar member: the package is an ar archive of 60-byte headers, each followed by its member
    padded to an even length."""
    with archive.open("rb") as handle:
        if handle.read(8) != b"!<arch>\n":
            raise AddonError("The add-on package is not a Debian package.")
        while True:
            header = handle.read(60)
            if len(header) < 60:
                raise AddonError("The add-on package has no data archive.")
            if header[58:60] != b"`\n":
                raise AddonError("The add-on package is damaged.")
            name = header[:16].decode("ascii", "replace").strip().rstrip("/")
            size = int(header[48:58].decode("ascii").strip())
            if size > MAX_SOURCE_BYTES:
                raise AddonError("The add-on package is too large.")
            if name.startswith("data.tar"):
                data = handle.read(size)
                if len(data) != size:
                    raise AddonError("The add-on package is truncated.")
                return data
            handle.seek(size + size % 2, io.SEEK_CUR)


def _ensure_data(definition: AddonDefinition, *, root: Path) -> None:
    data = _map(root, addon_paths(definition.id).data)
    if data.is_symlink():
        raise AddonError(f"Refusing to use a symlinked data directory: {addon_paths(definition.id).data}")
    data.mkdir(parents=True, exist_ok=True)
    (data / DATA_MARKER).write_text(f"{definition.id}\n", encoding="utf-8")
    (data / EARLIER_DATA_MARKER).unlink(missing_ok=True)
    for directory in definition.data_dirs:
        (data / directory).mkdir(parents=True, exist_ok=True)
    for relative in {*definition.settings, *(definition.defaults or {})}:
        sections = definition.settings.get(relative, {})
        defaults = (definition.defaults or {}).get(relative, {})
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        try:
            parser.read_string(current)
        except configparser.Error:
            # The plugin rewrites a configuration it can't parse with its defaults; start from the package settings.
            parser = configparser.ConfigParser(interpolation=None)
            parser.optionxform = str
            current = ""
        changed = current == ""
        for section, values in defaults.items():
            if not parser.has_section(section):
                parser.add_section(section)
            for key, value in values.items():
                if not parser.has_option(section, key):
                    parser.set(section, key, value)
                    changed = True
        for section, values in sections.items():
            if not parser.has_section(section):
                parser.add_section(section)
            for key, value in values.items():
                if parser.get(section, key, fallback=None) != value:
                    parser.set(section, key, value)
                    changed = True
        if changed:
            buffer = io.StringIO()
            parser.write(buffer)
            path.write_text(buffer.getvalue(), encoding="utf-8")


def unit_owned(definition: AddonDefinition, *, root: Path) -> bool:
    unit = _map(root, f"{UNIT_DIR}/{definition.unit}")
    try:
        return not unit.is_symlink() and unit.is_file() and has_marker(unit.read_text(encoding="utf-8"), definition.unit_marker)
    except OSError:
        return False


def _current(definition: AddonDefinition, addon: AddonSpec, *, root: Path) -> bool:
    """Whether the installed unit, and any kernel modules and their loader, are the ones this build installs."""
    return _unit_current(definition, root=root) and _system_current(addon, root=root)


def _system_current(addon: AddonSpec, *, root: Path) -> bool:
    if not addon.kernel_modules:
        return True
    directory = _map(root, addon_paths(addon.id).system)
    try:
        if (directory / MODULE_LOADER).read_text(encoding="utf-8") != render_module_loader(addon):
            return False
        return all(hashlib.sha256((directory / f"{module.name}.ko").read_bytes()).hexdigest() == module.sha256 for module in addon.kernel_modules)
    except OSError:
        return False


def _install_system_files(addon: AddonSpec, *, installer_root: Path, root: Path, sudo_password: str | None, run: RunFn) -> None:
    """Install the add-on's kernel modules and their loader, root-owned, where only root can change what root loads."""
    if not addon.kernel_modules:
        return
    paths = addon_paths(addon.id)
    files = {f"{module.name}.ko": ((installer_root / module.archive).read_bytes(), "0644") for module in addon.kernel_modules}
    files[MODULE_LOADER] = (render_module_loader(addon).encode("utf-8"), "0755")
    if _fake(root):
        directory = _map(root, paths.system)
        directory.mkdir(parents=True, exist_ok=True)
        for name, (data, mode) in files.items():
            (directory / name).write_bytes(data)
            (directory / name).chmod(int(mode, 8))
        return
    staged = _map(root, paths.code) / ".system"
    staged.mkdir(exist_ok=True)
    try:
        _privileged(["install", "-d", "-o", "root", "-g", "root", "-m", "0755", SYSTEM_ROOT, paths.system], "Could not install the add-on's kernel modules.", sudo_password=sudo_password, run=run)
        for name, (data, mode) in files.items():
            (staged / name).write_bytes(data)
            _privileged(["install", "-o", "root", "-g", "root", "-m", mode, str(staged / name), f"{paths.system}/{name}"], "Could not install the add-on's kernel modules.", sudo_password=sudo_password, run=run)
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def _remove_system_files(definition: AddonDefinition, *, root: Path, sudo_password: str | None, run: RunFn) -> None:
    """Unload the add-on's kernel modules when nothing uses them, and delete them with their loader."""
    for system_root in (SYSTEM_ROOT, EARLIER_SYSTEM_ROOT):
        _remove_system_dir(definition, system_root, root=root, sudo_password=sudo_password, run=run)


def _remove_system_dir(definition: AddonDefinition, system_root: str, *, root: Path, sudo_password: str | None, run: RunFn) -> None:
    paths = addon_paths(definition.id, system_root=system_root)
    directory = _map(root, paths.system)
    if not directory.is_dir() or directory.is_symlink():
        return
    modules = sorted(path.stem for path in directory.glob("*.ko"))
    if _fake(root):
        shutil.rmtree(directory)
    else:
        for name in modules:
            if re.fullmatch(r"[a-z0-9_]+", name):
                # Refused while ZeroTier, or anything else, still holds the device; the module then stays until reboot.
                run_sudo(["rmmod", name], run=run, password=sudo_password or "")
        _privileged(["rm", "-rf", paths.system], "Could not remove the add-on's kernel modules.", sudo_password=sudo_password, run=run)
        run_sudo(["rmdir", system_root], run=run, password=sudo_password or "")


def _unit_current(definition: AddonDefinition, *, root: Path) -> bool:
    unit = _map(root, f"{UNIT_DIR}/{definition.unit}")
    try:
        return not unit.is_symlink() and unit.read_text(encoding="utf-8") == definition.render_unit(addon_paths(definition.id))
    except OSError:
        return False


def _install_unit(definition: AddonDefinition, *, root: Path, sudo_password: str | None, run: RunFn) -> None:
    path = f"{UNIT_DIR}/{definition.unit}"
    content = definition.render_unit(addon_paths(definition.id))
    if _fake(root):
        target = _map(root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    else:
        staged = _map(root, addon_paths(definition.id).code) / f".{definition.unit}"
        staged.write_text(content, encoding="utf-8")
        try:
            _privileged(["install", "-m", "0644", str(staged), path], "Could not install the add-on's service.", sudo_password=sudo_password, run=run)
        finally:
            staged.unlink(missing_ok=True)
    _systemctl(["daemon-reload"], root=root, sudo_password=sudo_password, run=run)
    _systemctl(["enable", definition.unit], root=root, sudo_password=sudo_password, run=run)


def _remove_unit(definition: AddonDefinition, *, root: Path, sudo_password: str | None, run: RunFn) -> None:
    if not unit_owned(definition, root=root):
        return
    path = f"{UNIT_DIR}/{definition.unit}"
    if _fake(root):
        _map(root, path).unlink()
    else:
        _privileged(["rm", "-f", path], "Could not remove the add-on's service.", sudo_password=sudo_password, run=run)
    _systemctl(["daemon-reload"], root=root, sudo_password=sudo_password, run=run, check=False)


def _start(
    definition: AddonDefinition,
    *,
    root: Path,
    sudo_password: str | None,
    run: RunFn,
    wait: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    _systemctl(["restart" if wait else "start", definition.unit], root=root, sudo_password=sudo_password, run=run)
    if not wait:
        return
    # Restart=always brings a crashing service back, so it has to stay active, without systemd restarting it, for a while
    # to count as running.
    elapsed = 0.0
    active_for = 0.0
    restarts = None
    while elapsed < START_TIMEOUT_SECONDS:
        sleep(1.0)
        elapsed += 1.0
        state, count = _service_health(definition.unit, run=run)
        if state != "active" or (restarts is not None and count != restarts):
            active_for = 0.0
        else:
            active_for += 1.0
        restarts = count
        if active_for >= START_SETTLE_SECONDS:
            return
    raise AddonError(f"{definition.name} did not keep running after it started; see {definition.log_hint}.")


def _service_health(unit: str, *, run: RunFn) -> tuple[str, str]:
    try:
        result = run(
            ["systemctl", "show", unit, "--property=ActiveState", "--property=NRestarts"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown", ""
    values = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
    return values.get("ActiveState", "unknown"), values.get("NRestarts", "")


def _systemctl(args: list[str], *, root: Path, sudo_password: str | None, run: RunFn, check: bool = True) -> None:
    command = ["systemctl", *args]
    if _fake(root):
        result = run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        result = run_sudo(command, run=run, password=sudo_password or "")
    if check and result.returncode != 0:
        raise AddonError(f"systemctl {' '.join(args)} failed.")


def _privileged(command: list[str], message: str, *, sudo_password: str | None, run: RunFn) -> None:
    if run_sudo(command, run=run, password=sudo_password or "").returncode != 0:
        raise AddonError(message)


def _checked(run: RunFn, command: list[str], message: str) -> None:
    result = run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    if result.returncode != 0:
        detail = (result.stdout or "").strip().splitlines()
        raise AddonError(message + (f" {detail[-1]}" if detail else ""))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _remove_tree(path: Path, *, root: Path) -> None:
    """Delete a directory under the add-ons root; anything else is refused."""
    base = _map(root, ROOT)
    if path.parent != base:
        raise AddonError(f"Refusing to remove {path}: outside {ROOT}.")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _remove_root_if_empty(*, root: Path) -> None:
    base = _map(root, ROOT)
    try:
        base.rmdir()
    except OSError:
        pass
    _remove_earlier_root_if_empty(root=root)


def _move_earlier_system_files(addon_id: str, *, root: Path, sudo_password: str | None, run: RunFn) -> None:
    """Move the add-on's root-owned kernel modules and loader from EARLIER_SYSTEM_ROOT, pointing the loader at their
    new place, so the add-on keeps starting even before an update rebuilds it."""
    earlier, paths = addon_paths(addon_id, system_root=EARLIER_SYSTEM_ROOT), addon_paths(addon_id)
    source, target = _map(root, earlier.system), _map(root, paths.system)
    if source.is_symlink() or not source.is_dir() or target.exists():
        return
    try:
        loader = (source / MODULE_LOADER).read_text(encoding="utf-8").replace(earlier.system, paths.system)
    except OSError:
        loader = None
    if _fake(root):
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        if loader is not None:
            (target / MODULE_LOADER).write_text(loader, encoding="utf-8")
        with suppress(OSError):
            source.parent.rmdir()
        return
    message = "Could not move the add-on's kernel modules."
    _privileged(["install", "-d", "-o", "root", "-g", "root", "-m", "0755", SYSTEM_ROOT], message, sudo_password=sudo_password, run=run)
    _privileged(["mv", "-T", earlier.system, paths.system], message, sudo_password=sudo_password, run=run)
    if loader is not None:
        staged = _map(root, addon_paths(addon_id).code) / f".{MODULE_LOADER}"
        staged.write_text(loader, encoding="utf-8")
        try:
            _privileged(["install", "-o", "root", "-g", "root", "-m", "0755", str(staged), f"{paths.system}/{MODULE_LOADER}"], message, sudo_password=sudo_password, run=run)
        finally:
            staged.unlink(missing_ok=True)
    run_sudo(["rmdir", EARLIER_SYSTEM_ROOT], run=run, password=sudo_password or "")


def _remove_earlier_copies(addon_id: str, *, root: Path, data: bool) -> None:
    """Delete the add-on's package-owned directories under EARLIER_ROOT: its program, its staging and previous builds,
    and, with data, its data directory."""
    earlier = addon_paths(addon_id, EARLIER_ROOT)
    base = _map(root, EARLIER_ROOT)
    if base.is_symlink() or not base.is_dir():
        return
    targets = [_map(root, earlier.code + STAGING_SUFFIX), _map(root, earlier.code + PREVIOUS_SUFFIX)]
    if read_marker(addon_id, root=root, base=EARLIER_ROOT) is not None:
        targets.append(_map(root, earlier.code))
    if data and _data_marked(_map(root, earlier.data)):
        targets.append(_map(root, earlier.data))
    for path in targets:
        if path.parent != base:
            raise AddonError(f"Refusing to remove {path}: outside {EARLIER_ROOT}.")
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)


def _remove_earlier_root_if_empty(*, root: Path) -> None:
    base = _map(root, EARLIER_ROOT)
    if base.is_symlink():
        return
    try:
        base.rmdir()
    except OSError:
        pass


def _fake(root: Path) -> bool:
    return root != Path("/")


def _map(root: Path, absolute: str) -> Path:
    return Path(absolute) if not _fake(root) else root / absolute.lstrip("/")
