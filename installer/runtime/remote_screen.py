from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .static_build import FLUIDD, VALVE_OIL_UI
from .errors import InstallerError
from .naming import earlier_marker


# The Fluidd Printer screen card connects through nginx to a package-owned screen service.
UNIT_OPERATION = "remote_screen_unit"
SERVER_OPERATION = "remote_screen_server"
NGINX_OPERATION = "remote_screen_nginx"
UNIT = "vo-remote-screen.service"
UNIT_FILE = f"/etc/systemd/system/{UNIT}"
SERVER_FILE = "/usr/local/lib/vo_remote_screen.py"
# The site operation also serves the package Fluidd build in place of QIDI's bundle, and Valve Oil UI beside it; the
# operation keeps its original ID.
NGINX_SITE = "/etc/nginx/sites-available/fluidd"
QIDI_FLUIDD_ROOT = "/home/qidi/fluidd"
NGINX_QIDI_ROOT = f"\n    root {QIDI_FLUIDD_ROOT};\n"
NGINX_BUILD_ROOT = f"\n    root {FLUIDD.destination};\n"
# Where releases before 26.11.22.2 installed the package Fluidd.
EARLIER_NGINX_BUILD_ROOT = "\n    root /home/qidi/tltg-fluidd;\n"
# The package Fluidd ships a gzip copy of each text asset, so nginx sends it instead of compressing every request.
NGINX_GZIP_STATIC = "    gzip_static on;\n"
SERVER_PAYLOAD = Path(__file__).resolve().parents[1] / "system/vo_remote_screen.py"
# Package-created files start with a marker line so later package versions replace earlier ones.
SERVER_MARKER = "# vo:remote-screen-server\n"
UNIT_MARKER = "# vo:remote-screen-unit\n"

# Restarting the unit without the server, before install or after restore, is a clean no-op.
UNIT_CONTENT = UNIT_MARKER + f"""\
# Serves the touchscreen to the Fluidd Printer screen card. Installed by Valve Oil.
[Unit]
Description=Valve Oil printer screen for Fluidd
After=network.target
ConditionPathExists={SERVER_FILE}

[Service]
Type=simple
User=qidi
SupplementaryGroups=video input
ExecStart=/home/qidi/moonraker-env/bin/python3 {SERVER_FILE}
Restart=on-failure
RestartSec=10
Nice=10
# Off Klipper's CPUs, where QIDI's tuning script used to put it by matching moonraker-env in its command line.
CPUAffinity=0
CPUQuota=60%
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
"""

NGINX_START = "    # vo:remote-screen\n"
NGINX_END = "    # /vo:remote-screen\n"
# The package's routes in the Fluidd site: the Printer screen card, the Add-ons settings, and the Valve Oil status
# service (status_service). Moonraker authorizes each connection: trusted client addresses, or the card's one-shot
# token.
NGINX_BLOCK = NGINX_START + """\
    location /vo-screen/ {
        set $vo_screen_token $arg_token;
        auth_request /vo-screen-auth;
        proxy_pass http://127.0.0.1:7130/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $http_host;
        proxy_buffering off;
        proxy_read_timeout 60s;
        access_log off;
    }

    location = /vo-screen-auth {
        internal;
        proxy_pass http://apiserver/access/user?token=$vo_screen_token;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Upgrade "";
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # Fluidd's Add-ons settings. Moonraker authorizes each request: trusted client addresses or Fluidd's login.
    location /vo-addons/ {
        auth_request /vo-addons-auth;
        proxy_pass http://127.0.0.1:7131/;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_read_timeout 150s;
        client_max_body_size 4k;
        access_log off;
    }

    location = /vo-addons-auth {
        internal;
        # The subrequest would otherwise keep the client's method, and POST /access/user creates a Moonraker user.
        proxy_method GET;
        proxy_pass http://apiserver/access/user?token=$arg_token;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # The Valve Oil section of Fluidd and Valve Oil UI, authorized as the Add-ons settings are.
    location /vo-status/ {
        auth_request /vo-status-auth;
        proxy_pass http://127.0.0.1:7133/;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_read_timeout 30s;
        client_max_body_size 1k;
        access_log off;
    }

    location = /vo-status-auth {
        internal;
        # The subrequest would otherwise keep the client's method, and POST /access/user creates a Moonraker user.
        proxy_method GET;
        proxy_pass http://apiserver/access/user?token=$arg_token;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # The same routes under the names releases before 26.11.22.2 used, for pages loaded before an update.
    location /tltg-screen/ {
        rewrite ^/tltg-screen/(.*)$ /vo-screen/$1 last;
    }

    location /tltg-addons/ {
        rewrite ^/tltg-addons/(.*)$ /vo-addons/$1 last;
    }

    location /tltg-status/ {
        rewrite ^/tltg-status/(.*)$ /vo-status/$1 last;
    }
""" + NGINX_END
NGINX_ANCHOR = "\n    location /webcam/ {\n"
NGINX_PREVIOUS = re.compile(
    "|".join(
        re.escape(start.rstrip("\n")) + r"\r?\n.*?" + re.escape(end.rstrip("\n")) + r"\r?\n"
        for start, end in ((NGINX_START, NGINX_END), (earlier_marker(NGINX_START), earlier_marker(NGINX_END)))
    ),
    re.DOTALL,
)

NGINX_UI_START = "    # vo:valve-oil-ui\n"
NGINX_UI_END = "    # /vo:valve-oil-ui\n"
# Valve Oil UI uses hash routing, so it needs no fallback. Its page and version file are never cached, so open tabs
# pick up an update.
NGINX_UI_BLOCK = NGINX_UI_START + f"""\
    location = /valve-oil {{
        # A relative redirect keeps the port and any proxy in front of the printer.
        absolute_redirect off;
        return 301 /valve-oil/;
    }}

    location = /valve-oil/index.html {{
        alias {VALVE_OIL_UI.destination}/index.html;
        add_header Cache-Control "no-store";
    }}

    location = /valve-oil/version.json {{
        alias {VALVE_OIL_UI.destination}/version.json;
        add_header Cache-Control "no-store";
    }}

    # /valve-oil/ itself is answered through index by the index.html location above.
    location ^~ /valve-oil/ {{
        alias {VALVE_OIL_UI.destination}/;
        index index.html;
    }}
""" + NGINX_UI_END
# The block and the blank line after it.
NGINX_UI_PREVIOUS = re.compile(
    "|".join(
        re.escape(start.rstrip("\n")) + r"\r?\n.*?" + re.escape(end.rstrip("\n")) + r"\r?\n(?:\r?\n)?"
        for start, end in ((NGINX_UI_START, NGINX_UI_END), (earlier_marker(NGINX_UI_START), earlier_marker(NGINX_UI_END)))
    ),
    re.DOTALL,
)


@lru_cache(maxsize=1)
def server_source() -> str:
    try:
        source = SERVER_PAYLOAD.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallerError(f"Printer screen service is missing from the package: {exc}") from exc
    if not source.startswith(SERVER_MARKER):
        raise InstallerError("Printer screen service payload lacks its package marker.")
    compile(source, "vo_remote_screen.py", "exec")
    return source


def _patch_created(current: str, desired: str, marker: str, name: str) -> str:
    if current == "" or current.startswith(marker):
        return desired
    raise InstallerError(f"Existing {name} is unrecognized and was preserved.")


def patch_server(current: str) -> str:
    return _patch_created(current, server_source(), SERVER_MARKER, SERVER_FILE)


def patch_unit(current: str) -> str:
    return _patch_created(current, UNIT_CONTENT, UNIT_MARKER, UNIT_FILE)


def serves_fluidd_build(text: str) -> bool:
    return any(NGINX_BUILD_ROOT.replace("\n", newline) in text for newline in ("\n", "\r\n"))


def serves_valve_oil_ui(text: str) -> bool:
    return NGINX_UI_START.rstrip("\n") in text


def patch_nginx_site(text: str, *, valve_oil_ui: bool = False) -> str:
    """Serve the package Fluidd and the Printer screen route, and Valve Oil UI only when valve_oil_ui is set."""
    # QIDI ships the site with CRLF line endings; changes use the file's own.
    newline = "\r\n" if "\r\n" in text else "\n"
    previous = NGINX_UI_PREVIOUS.findall(text)
    if len(previous) > 1:
        raise InstallerError("Fluidd nginx site does not match the reviewed QIDI source; preserving it.")
    if previous:
        text = text.replace(previous[0], "", 1)
    if "/valve-oil" in text:
        raise InstallerError("Fluidd nginx site does not match the reviewed QIDI source; preserving it.")
    text = _patch_fluidd_and_screen(text, newline)
    if valve_oil_ui:
        # Valve Oil UI goes just before the Printer screen route.
        start = NGINX_START.replace("\n", newline)
        text = text.replace(start, NGINX_UI_BLOCK.replace("\n", newline) + newline + start, 1)
    return text


def _patch_fluidd_and_screen(text: str, newline: str) -> str:
    # The route goes before QIDI's first webcam location and reuses its Moonraker upstream and upgrade map.
    qidi_root = NGINX_QIDI_ROOT.replace("\n", newline)
    build_root = NGINX_BUILD_ROOT.replace("\n", newline)
    # A site an earlier release patched serves the package Fluidd from its earlier folder.
    text = text.replace(EARLIER_NGINX_BUILD_ROOT.replace("\n", newline), build_root)
    if text.count(qidi_root) + text.count(build_root) != 1:
        raise InstallerError("Fluidd nginx site does not match the reviewed QIDI source; preserving it.")
    served = build_root + NGINX_GZIP_STATIC.replace("\n", newline)
    if served not in text:
        text = text.replace(qidi_root, build_root, 1).replace(build_root, served, 1)
    block = NGINX_BLOCK.replace("\n", newline)
    previous = NGINX_PREVIOUS.findall(text)
    if len(previous) == 1:
        return text.replace(previous[0], block, 1)
    anchor = NGINX_ANCHOR.replace("\n", newline)
    if (
        not previous
        and text.count(anchor) == 1
        and "/vo-screen" not in text
        and "proxy_pass http://apiserver/websocket;" in text
        and "$connection_upgrade" in text
    ):
        return text.replace(anchor, newline + block + anchor, 1)
    raise InstallerError("Fluidd nginx site does not match the reviewed QIDI source; preserving it.")
