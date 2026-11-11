from __future__ import annotations

import base64
import configparser
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .errors import InstallerError
from .models import AddonSpec, AddonsSpec, SystemWheelSpec
from .sudo import run_sudo


# Optional add-ons live in their own directories under ROOT, each with its own Python environment built from wheels
# the package ships, and a systemd unit. Nothing outside ROOT and the unit file changes: no system packages, no
# configuration under printer_data/config, and no Moonraker update manager entries.
ROOT = "/home/qidi/tltg-addons"
UNIT_DIR = "/etc/systemd/system"
SYSTEM_PYTHON = "/usr/bin/python3"
# Marks a package-owned add-on directory and records what it was built from.
MARKER = ".tltg-addon.json"
# Marks a package-owned add-on data directory, which updates keep and removal deletes.
DATA_MARKER = ".tltg-addon-data"
STAGING_SUFFIX = ".tltg-staging"
PREVIOUS_SUFFIX = ".tltg-previous"
# A started service must stay active this long to count as running.
# A started service counts as running once it has stayed up this long without systemd restarting it. OctoEverywhere
# takes about 7 s on the printer to load and fail on a bad configuration, and Restart=always brings it straight back.
START_SETTLE_SECONDS = 20.0
START_TIMEOUT_SECONDS = 45.0
MAX_SOURCE_ENTRIES = 8192
MAX_SOURCE_BYTES = 128 * 1024 * 1024

NOT_INSTALLED = "not_installed"
INSTALLED = "installed"
UPDATE_AVAILABLE = "update_available"
FOREIGN = "foreign"

RunFn = Callable[..., subprocess.CompletedProcess]


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

    @property
    def unit(self) -> str:
        return f"{self.service}.service"

    @property
    def unit_marker(self) -> str:
        return f"# tltg-optimized:addon-{self.id}\n"


@dataclass(frozen=True)
class AddonPaths:
    code: str
    data: str

    @property
    def app(self) -> str:
        return f"{self.code}/app"

    @property
    def env(self) -> str:
        return f"{self.code}/env"

    @property
    def python(self) -> str:
        return f"{self.env}/bin/python3"


def addon_paths(addon_id: str) -> AddonPaths:
    return AddonPaths(code=f"{ROOT}/{addon_id}", data=f"{ROOT}/{addon_id}-data")


# OctoEverywhere's own installer also installs system packages, replaces NTP, sets the clock, and adds a Moonraker
# update-manager section, which turns on Moonraker's update panel and its system package upgrades. None of that is
# needed here: the plugin runs from its own environment, keeps its configuration outside printer_data/config so
# firmware updates keep it, and is told not to write moonraker.conf. Its parent directory has no Fluidd or Mainsail
# folder, so it doesn't inject its script into either Fluidd.
OCTOEVERYWHERE_STATUS_PORT = 7132
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

DEFINITIONS: dict[str, AddonDefinition] = {OCTOEVERYWHERE.id: OCTOEVERYWHERE}


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
            }
        )
    return result


def addon_state(definition: AddonDefinition, addon: AddonSpec, marker: dict[str, Any] | None, *, root: Path) -> str:
    if marker is not None:
        return INSTALLED if marker.get("build") == build_id(addon) else UPDATE_AVAILABLE
    if _foreign_install(definition, root=root):
        return FOREIGN
    return NOT_INSTALLED


def build_id(addon: AddonSpec) -> str:
    """Identify the add-on's pinned files independently of their order."""
    digests = sorted([addon.source.sha256, *(wheel.sha256 for wheel in addon.wheels)])
    return hashlib.sha256(f"{addon.id}\n{addon.version}\n".encode("utf-8") + "\n".join(digests).encode("ascii")).hexdigest()


def read_marker(addon_id: str, *, root: Path) -> dict[str, Any] | None:
    path = _map(root, f"{addon_paths(addon_id).code}/{MARKER}")
    try:
        if path.is_symlink() or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("id") == addon_id else None


def service_active(unit: str, *, run: RunFn) -> str:
    try:
        result = run(["systemctl", "is-active", unit], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False)
    except OSError:
        return "unknown"
    state = (result.stdout or "").strip().splitlines()
    return state[0] if state else "unknown"


def _foreign_install(definition: AddonDefinition, *, root: Path) -> bool:
    if any(_map(root, path).exists() for path in definition.foreign_paths):
        return True
    unit = _map(root, f"{UNIT_DIR}/{definition.unit}")
    if unit.is_symlink():
        return True
    try:
        return unit.is_file() and not unit.read_text(encoding="utf-8").startswith(definition.unit_marker)
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
) -> str:
    """Install the pinned add-on, or bring an installed one to the pinned build. Returns the resulting state."""
    definition, addon = _resolve(addon_id, spec)
    marker = read_marker(addon_id, root=root)
    state = addon_state(definition, addon, marker, root=root)
    if state == FOREIGN:
        raise AddonError(f"{definition.name} was installed outside Valve Oil; remove that install first.")
    if state == INSTALLED and _unit_current(definition, root=root):
        status(f"{definition.name} {addon.version} is already installed.")
        _ensure_data(definition, root=root)
        _start(definition, root=root, sudo_password=sudo_password, run=run)
        return INSTALLED

    paths = addon_paths(addon_id)
    code = _map(root, paths.code)
    staging = _map(root, paths.code + STAGING_SUFFIX)
    previous = _map(root, paths.code + PREVIOUS_SUFFIX)
    _verify_artifacts(spec, addon, installer_root)
    if state == INSTALLED:
        # Same build, missing or edited unit: rewrite the unit only.
        status(f"Repairing the {definition.name} service")
        _ensure_data(definition, root=root)
        _install_unit(definition, root=root, sudo_password=sudo_password, run=run)
        _start(definition, root=root, sudo_password=sudo_password, run=run, wait=True, sleep=sleep)
        return INSTALLED

    status(f"Preparing {definition.name} {addon.version}")
    _remove_tree(staging, root=root)
    _remove_tree(previous, root=root)
    staging.mkdir(parents=True)
    _extract_source(installer_root / addon.source.archive, staging / "app")
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
    _write_json(staging / MARKER, {"id": addon_id, "version": addon.version, "build": build_id(addon)})
    _ensure_data(definition, root=root)

    status(f"Starting {definition.name}")
    if marker is not None:
        _systemctl(["stop", definition.unit], root=root, sudo_password=sudo_password, run=run, check=False)
        code.rename(previous)
    staging.rename(code)
    try:
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
    if owned_unit:
        _systemctl(["disable", "--now", definition.unit], root=root, sudo_password=sudo_password, run=run, check=False)
        _remove_unit(definition, root=root, sudo_password=sudo_password, run=run)
    for path in (paths.code + STAGING_SUFFIX, paths.code + PREVIOUS_SUFFIX):
        _remove_tree(_map(root, path), root=root)
    if marker is not None:
        _remove_tree(_map(root, paths.code), root=root)
    data = _map(root, paths.data)
    if (data / DATA_MARKER).is_file():
        _remove_tree(data, root=root)
    _remove_root_if_empty(root=root)
    status(f"{definition.name} is removed.")
    return True


def update_installed_addons(
    *,
    spec: AddonsSpec | None,
    installer_root: Path,
    root: Path = Path("/"),
    sudo_password: str | None,
    run: RunFn = subprocess.run,
    status: Callable[[str], None] = lambda message: None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Bring every installed add-on to the package's pinned build. Returns failure messages; never raises for one add-on."""
    failures = []
    for addon in spec.catalog if spec else ():
        definition = DEFINITIONS.get(addon.id)
        marker = read_marker(addon.id, root=root)
        if definition is None or marker is None:
            continue
        if addon_state(definition, addon, marker, root=root) == INSTALLED and _unit_current(definition, root=root):
            continue
        try:
            install_addon(addon.id, spec=spec, installer_root=installer_root, root=root, sudo_password=sudo_password, run=run, status=status, sleep=sleep)
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


def _resolve(addon_id: str, spec: AddonsSpec | None) -> tuple[AddonDefinition, AddonSpec]:
    definition = DEFINITIONS.get(addon_id)
    addon = spec.find(addon_id) if spec else None
    if definition is None or addon is None:
        raise AddonError(f"Unknown add-on: {addon_id}")
    return definition, addon


def _verify_artifacts(spec: AddonsSpec, addon: AddonSpec, installer_root: Path) -> None:
    for artifact in (spec.pip_wheel, addon.source, *addon.wheels):
        _verify_artifact(artifact, installer_root)


def _verify_artifact(artifact: SystemWheelSpec, installer_root: Path) -> None:
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
                if not member.isfile():
                    # Release archives can carry symlinks; nothing the plugin runs needs them.
                    continue
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise AddonError(f"Unreadable entry in the add-on source archive: {member.name}")
                with source, target.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (tarfile.TarError, OSError) as exc:
        raise AddonError(f"Could not extract the add-on source: {exc}") from exc


def _ensure_data(definition: AddonDefinition, *, root: Path) -> None:
    data = _map(root, addon_paths(definition.id).data)
    if data.is_symlink():
        raise AddonError(f"Refusing to use a symlinked data directory: {addon_paths(definition.id).data}")
    data.mkdir(parents=True, exist_ok=True)
    (data / DATA_MARKER).write_text(f"{definition.id}\n", encoding="utf-8")
    for directory in definition.data_dirs:
        (data / directory).mkdir(parents=True, exist_ok=True)
    for relative, sections in definition.settings.items():
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
        return not unit.is_symlink() and unit.is_file() and unit.read_text(encoding="utf-8").startswith(definition.unit_marker)
    except OSError:
        return False


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
    raise AddonError(f"{definition.name} did not keep running after it started; see its log in Fluidd.")


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


def _fake(root: Path) -> bool:
    return root != Path("/")


def _map(root: Path, absolute: str) -> Path:
    return Path(absolute) if not _fake(root) else root / absolute.lstrip("/")
