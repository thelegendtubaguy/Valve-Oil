from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .errors import InstallerError


# The Valve Oil section of Fluidd and Valve Oil UI reach a package-owned status and settings service through the Fluidd
# nginx site (see remote_screen.NGINX_BLOCK). It reads the pending-work status file (valve_oil_status), switches the
# automatic-update enrollment, and starts "update now" runs, which outlive a restart of the service.
UNIT_OPERATION = "status_service_unit"
SERVER_OPERATION = "status_service_server"
UNIT = "tltg-status.service"
UNIT_FILE = f"/etc/systemd/system/{UNIT}"
SERVER_FILE = "/usr/local/lib/tltg_status.py"
SERVER_PAYLOAD = Path(__file__).resolve().parents[1] / "system/tltg_status.py"
LISTEN_PORT = 7133

# Package-created files start with a marker line so later package versions replace earlier ones.
SERVER_MARKER = "# tltg-optimized:status-server\n"
UNIT_MARKER = "# tltg-optimized:status-unit\n"

# Restarting the unit without the server, before install or after restore, is a clean no-op. Like the add-ons
# service, the runs it starts use sudo and write system files, so the unit has no sandboxing; KillMode=process lets a
# running update finish when the service restarts, as the update's own install restarts it.
UNIT_CONTENT = UNIT_MARKER + f"""\
# Reports Valve Oil's pending updates and switches automatic updates for Fluidd and Valve Oil UI. Installed by Valve Oil.
[Unit]
Description=Valve Oil status and settings
After=network.target
ConditionPathExists={SERVER_FILE}

[Service]
Type=simple
User=qidi
ExecStart=/usr/bin/python3 -I {SERVER_FILE}
Restart=on-failure
RestartSec=10
KillMode=process
Nice=10
CPUAffinity=0

[Install]
WantedBy=multi-user.target
"""


@lru_cache(maxsize=1)
def server_source() -> str:
    try:
        source = SERVER_PAYLOAD.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallerError(f"Status service is missing from the package: {exc}") from exc
    if not source.startswith(SERVER_MARKER):
        raise InstallerError("Status service payload lacks its package marker.")
    compile(source, "tltg_status.py", "exec")
    return source


def _patch_created(current: str, desired: str, marker: str, name: str) -> str:
    if current == "" or current.startswith(marker):
        return desired
    raise InstallerError(f"Existing {name} is unrecognized and was preserved.")


def patch_server(current: str) -> str:
    return _patch_created(current, server_source(), SERVER_MARKER, SERVER_FILE)


def patch_unit(current: str) -> str:
    return _patch_created(current, UNIT_CONTENT, UNIT_MARKER, UNIT_FILE)
