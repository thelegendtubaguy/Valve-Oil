from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .errors import InstallerError


# Fluidd's Add-ons settings reach a package-owned service through the Fluidd nginx site (see remote_screen.NGINX_BLOCK).
# The service runs addons.sh for each change, which outlives a restart of the service.
UNIT_OPERATION = "addons_service_unit"
SERVER_OPERATION = "addons_service_server"
UNIT = "tltg-addons.service"
UNIT_FILE = f"/etc/systemd/system/{UNIT}"
SERVER_FILE = "/usr/local/lib/tltg_addons.py"
SERVER_PAYLOAD = Path(__file__).resolve().parents[1] / "system/tltg_addons.py"
LISTEN_PORT = 7131

# Package-created files start with a marker line so later package versions replace earlier ones.
SERVER_MARKER = "# tltg-optimized:addons-server\n"
UNIT_MARKER = "# tltg-optimized:addons-unit\n"

# Restarting the unit without the server, before install or after restore, is a clean no-op. The jobs it starts need
# sudo and write system files, so the unit has no sandboxing; KillMode=process lets a running job finish when the
# service restarts.
UNIT_CONTENT = UNIT_MARKER + f"""\
# Installs and removes Valve Oil add-ons for Fluidd's Settings page. Installed by Valve Oil.
[Unit]
Description=Valve Oil add-ons for Fluidd
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
        raise InstallerError(f"Add-ons service is missing from the package: {exc}") from exc
    if not source.startswith(SERVER_MARKER):
        raise InstallerError("Add-ons service payload lacks its package marker.")
    compile(source, "tltg_addons.py", "exec")
    return source


def _patch_created(current: str, desired: str, marker: str, name: str) -> str:
    if current == "" or current.startswith(marker):
        return desired
    raise InstallerError(f"Existing {name} is unrecognized and was preserved.")


def patch_server(current: str) -> str:
    return _patch_created(current, server_source(), SERVER_MARKER, SERVER_FILE)


def patch_unit(current: str) -> str:
    return _patch_created(current, UNIT_CONTENT, UNIT_MARKER, UNIT_FILE)
