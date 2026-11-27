# vo:qidi-block
"""Keeps the printer from reaching QIDI and the third-party services QIDI's software uses, all the time. Run as root by
vo-qidi-block.service.

QIDI's kernel has no packet filter (no netfilter, BPF, network namespaces, or policy routing), so nothing can block by
process; this blocks by destination:

- the names below, and the hosts QIDI's cloud gave the printer for MQTT, P2P, and FRP, resolve to 0.0.0.0 and :: from a
  block of /etc/hosts between this file's markers, so no lookup through the system resolver reaches them;
- the addresses below, and those the cloud gave as addresses, get prohibit routes, so connecting to them fails at once
  with "Permission denied", for every program, whatever names it used.

QIDI's frpc is also kept stopped. Valve Oil's Moonraker fixes keep QIDI's LAN-only mode on and load Moonraker's own
MQTT component in place of QIDI's, so QIDI's software doesn't try; this covers what it would still start.

Only public addresses are ever blocked, so the printer's network and the rest of the internet stay reachable. The block
and the routes are removed when the service stops, as uninstall stops it; the service starts before QIDI's app and
Moonraker at boot. It reads the servers QIDI's cloud gave the printer from Moonraker's database itself, so it works
before Moonraker is up and whichever app is on the screen.
"""
import ipaddress
import json
import os
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time

DATABASE = "/home/qidi/printer_data/database/moonraker-sql.db"
HOSTS = "/etc/hosts"
FRPC = "/root/Frp/frpc.json"
IP = "/usr/bin/ip"
ROUTE_PROTOCOL = "86"  # marks the routes this adds, so it only ever removes its own
BEGIN = "# vo:qidi-block begin\n"
END = "# vo:qidi-block end\n"
BLOCK_NOTE = ("# Valve Oil points these names nowhere so the printer doesn't reach QIDI or the services QIDI's software\n"
              "# uses. Removed when Valve Oil is uninstalled.\n")
CHECK_SECONDS = 5.0
REFRESH_SECONDS = 300.0
FRP_CHECK_SECONDS = 30.0  # frpc restarts 30 s after it stops
FRP_UNIT = "frpc.service"
SYSTEMCTL = "/bin/systemctl"

# The hosts the reviewed QIDI app builds, QIDI's Moonraker, and Moonraker's own components reach (qidi-platform.md).
NAMES = (
    # QIDI's cloud, downloads, and help
    "api.qidimaker.com", "api.qidimaker.com.cn", "www.qidimaker.com", "www.qidimaker.com.cn",
    "public-cdn.qidimaker.com", "aliyun.qidi3dprinter.com", "www.aliyun.qidi3dprinter.com",
    "www.aws.qidi3dprinter.com", "login.qidi3dprinter.com", "download_aliyun.qidi3dprinter.com",
    "download.apac.qidi3dprinter.com", "download.eea.qidi3dprinter.com", "download.na.qidi3dprinter.com",
    "download.others.qidi3dprinter.com", "wiki.qidi3d.com",
    # QIDI's MQTT broker and P2P server as QIDI's cloud names them to linked printers (also read from the database)
    "us-mqtt.qidimaker.com", "peershare.qidimaker.com",
    # Third parties QIDI's app reaches: Peergine (P2P), the printer's public address and location, connection checks
    "u.peergine.com", "api.ipify.org", "ip-api.com", "www.google.com", "www.baidu.com",
    # Moonraker's announcements and SimplyPrint
    "arksine.github.io", "api.simplyprint.io", "ws.simplyprint.io", "testws.simplyprint.io",
)
# The MQTT broker QIDI's Moonraker connects to when the cloud hasn't given it one, QIDI's app's time server, and an
# address the app holds. Never the public DNS servers the app pings (1.1.1.1, 8.8.8.8, 223.5.5.5, 119.29.29.29):
# QIDI's DHCP hook writes them to /etc/resolv.conf after the network's own servers, and on a network whose DHCP
# names no DNS server they are the printer's only ones.
ADDRESSES = ("8.149.135.149", "203.107.6.88", "183.232.231.100")
# Where QIDI's cloud names the servers it gave the printer.
CLOUD_KEYS = (("mqtt", "ip"), ("p2p", "connect"), ("p2p", "peer"))
NAME = re.compile(r"^(?=.{1,253}$)[a-z0-9_]([a-z0-9_-]{0,62})(\.[a-z0-9_-]{1,63})+$")


def log(message):
    print(f"vo_qidi_block: {message}", flush=True)


# ---- What to block --------------------------------------------------------------------------------------------------

def _host(value):
    """The host in a server value such as "host", "host:port", "scheme://host:port/path", or "[v6]:port"."""
    value = str(value or "").strip().lower()
    if "://" in value:
        value = value.split("://", 1)[1]
    value = value.split("/", 1)[0].split("@")[-1]
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


def _sort_host(value, names, addresses):
    """Adds a server's host to names or addresses; private, local, and malformed ones are never blocked."""
    host = _host(value)
    if not host:
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if NAME.match(host) and not host.endswith((".local", ".lan", ".home.arpa")):
            names.add(host)
        return
    if address.is_global:
        addresses.add(str(address))


def read_settings(database=DATABASE):
    """QIDI's config table rows this needs, or None when the database can't be read."""
    if not os.path.exists(database):
        return None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        rows = connection.execute(
            "SELECT section, key, value FROM config WHERE "
            "(section = 'mqtt' AND key = 'ip') OR (section = 'p2p' AND key IN ('connect', 'peer'))").fetchall()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return {(section, key): value for section, key, value in rows}


def frp_server(path=FRPC):
    try:
        with open(path, encoding="utf-8") as file:
            return str(json.load(file).get("serverAddr") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def wanted(settings, frp=""):
    """(names, addresses) to block: the lists, and the servers QIDI's cloud gave the printer."""
    names, addresses = set(NAMES), set(ADDRESSES)
    for key in CLOUD_KEYS:
        _sort_host((settings or {}).get(key), names, addresses)
    _sort_host(frp, names, addresses)
    return tuple(sorted(names)), tuple(sorted(addresses, key=ipaddress.ip_address))


# ---- /etc/hosts -----------------------------------------------------------------------------------------------------

def without_block(text):
    """The hosts file without this file's block."""
    lines = text.splitlines(keepends=True)
    kept, inside = [], False
    for line in lines:
        if line == BEGIN or line.rstrip("\n") == BEGIN.rstrip("\n"):
            inside = True
        elif inside and line.rstrip("\n") == END.rstrip("\n"):
            inside = False
        elif not inside:
            kept.append(line)
    return "".join(kept)


def with_block(text, names):
    text = without_block(text)
    if not names:
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    entries = "".join(f"0.0.0.0 {name}\n:: {name}\n" for name in names)
    return text + BEGIN + BLOCK_NOTE + entries + END


def write_hosts(names, path=HOSTS):
    try:
        with open(path, encoding="utf-8") as file:
            current = file.read()
    except FileNotFoundError:
        current = ""
    desired = with_block(current, names)
    if desired == current:
        return False
    temporary = os.path.join(os.path.dirname(path), ".hosts.vo-qidi-block")
    with open(temporary, "w", encoding="utf-8") as file:
        file.write(desired)
        file.flush()
        os.fchmod(file.fileno(), 0o644)
        os.fsync(file.fileno())
    os.replace(temporary, path)
    return True


# ---- Routes ---------------------------------------------------------------------------------------------------------

def _ip(*args):
    return subprocess.run([IP, *args], capture_output=True, text=True, timeout=10)


def our_routes(run=_ip):
    """The addresses this service has prohibit routes for."""
    routes = set()
    for family in ("-4", "-6"):
        result = run(family, "route", "show", "proto", ROUTE_PROTOCOL)
        for line in (result.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "prohibit":
                try:
                    routes.add(str(ipaddress.ip_network(parts[1], strict=False).network_address))
                except ValueError:
                    pass
    return routes


def set_routes(addresses, run=_ip):
    current = our_routes(run)
    for address in sorted(set(addresses) - current):
        family = "-6" if ipaddress.ip_address(address).version == 6 else "-4"
        result = run(family, "route", "replace", "prohibit", address, "proto", ROUTE_PROTOCOL)
        if result.returncode != 0:
            log(f"couldn't block {address}: {(result.stderr or '').strip()}")
    for address in sorted(current - set(addresses)):
        family = "-6" if ipaddress.ip_address(address).version == 6 else "-4"
        run(family, "route", "del", "prohibit", address, "proto", ROUTE_PROTOCOL)


# ---- QIDI's FRP client ----------------------------------------------------------------------------------------------

def _systemctl(*args):
    return subprocess.run([SYSTEMCTL, *args], capture_output=True, text=True, timeout=30)


def stop_frp(run=_systemctl):
    """Stops QIDI's frpc while it runs or waits to restart; True when it was stopped."""
    state = (run("is-active", FRP_UNIT).stdout or "").strip()
    if state not in ("active", "activating", "reloading"):
        return False
    run("stop", FRP_UNIT)
    log(f"stopped QIDI's frpc ({state})")
    return True


# ---- The service ----------------------------------------------------------------------------------------------------

def apply(names, addresses, *, hosts=HOSTS, run=_ip):
    changed = write_hosts(names, hosts)
    set_routes(addresses, run)
    return changed


def _notify_ready():
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return
    if path.startswith("@"):
        path = "\0" + path[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        try:
            sock.sendto(b"READY=1", path)
        except OSError:
            pass


def _stamp(*paths):
    stamps = []
    for path in paths:
        try:
            status = os.stat(path)
            stamps.append((status.st_mtime_ns, status.st_size, status.st_ino))
        except OSError:
            stamps.append(None)
    return tuple(stamps)


def run(*, database=DATABASE, hosts=HOSTS, frpc=FRPC, ip=_ip, systemctl=_systemctl, check_seconds=CHECK_SECONDS,
        refresh_seconds=REFRESH_SECONDS):
    stopping = []

    def stop(signum, frame):
        stopping.append(signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    current, stamp, refreshed, ready, frp_checked = None, None, None, False, None
    try:
        while not stopping:
            now = time.monotonic()
            new_stamp = _stamp(database, frpc)
            due = refreshed is None or now - refreshed >= refresh_seconds
            if new_stamp != stamp or due:
                settings = read_settings(database)
                if settings is not None or current is None:  # an unreadable database keeps the servers it named
                    desired = wanted(settings, frp_server(frpc))
                    if desired != current or due:
                        if current is None:
                            log("QIDI and third-party services blocked")
                        apply(*desired, hosts=hosts, run=ip)
                        current, refreshed = desired, now
                    if settings is not None:
                        stamp = new_stamp
            if frp_checked is None or now - frp_checked >= FRP_CHECK_SECONDS:
                stop_frp(systemctl)
                frp_checked = now
            if not ready:  # QIDI's app and Moonraker start once the first check is done
                _notify_ready()
                ready = True
            time.sleep(check_seconds)
    finally:
        apply((), (), hosts=hosts, run=ip)
        log("stopped: nothing blocked")
    return 0


def main(argv):
    if argv[1:] == ["clear"]:
        apply((), ())
        return 0
    if len(argv) > 1:
        log("usage: vo_qidi_block.py [clear]")
        return 2
    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
