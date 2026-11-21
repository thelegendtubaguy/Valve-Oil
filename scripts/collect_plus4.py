#!/usr/bin/env python3
"""Read-only evidence collection for a QIDI Plus 4.

Run on the printer as `mks`, in an SSH session:

    curl -fsSLo /tmp/collect_plus4.py <raw URL of this file>
    python3 /tmp/collect_plus4.py

It reads files and asks Klipper, Moonraker, and the system for information. It changes nothing on the
printer: the only file it writes is one archive in /tmp, named in its last line. `sudo` is used once, to
read /root/xindi/version; it asks for the `mks` password (QIDI's default is `makerbase`), and the rest
runs without it if you skip that.

Hardware serial numbers in device names and box.cfg are replaced with REDACTED before anything is
written. Wi-Fi settings, account files, and tokens are never read: config.mksini, mobileraker.conf, and
moonraker secrets are listed by name only.

This file runs on the printer's Python 3.7, so it uses 3.7 syntax and library features only.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

HOME = os.path.expanduser("~")
CONFIG = os.path.join(HOME, "printer_data", "config")
LOGS = os.path.join(HOME, "printer_data", "logs")
MOONRAKER = "http://127.0.0.1:7125"

# Factory config files the 2.1.1 update package doesn't ship.
FACTORY_FILES = ("plr.cfg", "time_update.cfg", "timelapse.cfg", "KAMP_Settings.cfg")
FACTORY_DIRS = ("KAMP",)
# Listed by name and size only: they can hold Wi-Fi, account, or API settings.
NAME_ONLY = re.compile(r"(\.mksini|mobileraker|secret|token|credential|\.key$|\.pem$|webcam)", re.I)
SKIP_DIRS = {".git", "__pycache__", "node_modules"}
SERIAL = re.compile(r"(usb-[^\s/]*?[_-])([0-9A-Fa-f]{8,}|[0-9A-Z]{10,}|[0-9A-Za-z]{16,})(-if\d+)")
SERIAL_LINE = re.compile(r"^(\s*(?:serial|canbus_uuid)\s*[:=]\s*)(\S+)", re.M)


def redact(text):
    text = SERIAL.sub(r"\1REDACTED\3", text)
    return SERIAL_LINE.sub(lambda m: m.group(1) + (SERIAL.sub(r"\1REDACTED\3", m.group(2))
                                                    if "/dev/serial/by-id/" in m.group(2) else m.group(2)), text)


def run(*cmd, timeout=20):
    try:
        done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=timeout)
        return done.stdout
    except Exception as exc:  # noqa: BLE001 - every failure is reported, none stops the run
        return "unavailable: {} ({})\n".format(" ".join(cmd), exc.__class__.__name__)


def read(path, limit=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit) if limit else handle.read()
    except Exception as exc:  # noqa: BLE001
        return "unreadable: {} ({})\n".format(path, exc.__class__.__name__)


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_hashes(root):
    lines = []
    if not os.path.isdir(root):
        return "missing: {}\n".format(root)
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name.endswith(".pyc"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root)
            try:
                if os.path.islink(path):
                    lines.append("link {} -> {}".format(rel, os.readlink(path)))
                else:
                    lines.append("{}  {:>9}  {}".format(sha256(path), os.path.getsize(path), rel))
            except Exception as exc:  # noqa: BLE001
                lines.append("unreadable {} ({})".format(rel, exc.__class__.__name__))
    return "\n".join(lines) + "\n"


def moonraker(path):
    try:
        with urllib.request.urlopen(MOONRAKER + path, timeout=10) as reply:
            return json.dumps(json.load(reply).get("result"), indent=1, sort_keys=True) + "\n"
    except Exception as exc:  # noqa: BLE001
        return "no answer: {} ({})\n".format(path, exc.__class__.__name__)


def klippy_excerpt():
    path = os.path.join(LOGS, "klippy.log")
    wanted = re.compile(r"(Git version|CPU Info|Python:|Start printer at|Args:|Loaded MCU|MCU '[^']+' config|"
                        r"mcu '[^']+':|Configured MCU|Timer too close|Lost communication|shutdown|"
                        r"Unknown command|Error|Traceback)")
    try:
        lines = []
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if wanted.search(line):
                    lines.append(line)
                    if len(lines) > 4000:
                        del lines[:1000]
        return redact("".join(lines[-3000:]))
    except Exception as exc:  # noqa: BLE001
        return "unreadable: {} ({})\n".format(path, exc.__class__.__name__)


def xindi_version():
    path = "/root/xindi/version"
    if os.access(path, os.R_OK):
        return read(path)
    print("Reading the firmware version needs sudo. Enter the mks password, or press Ctrl+C to skip it.")
    try:
        return run("sudo", "cat", path, timeout=60)
    except KeyboardInterrupt:
        return "skipped\n"


def main():
    if sys.version_info < (3, 5):
        print("Needs Python 3.5 or later.")
        return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = tempfile.mkdtemp(prefix="plus4-collect-")
    out = os.path.join(work, "plus4-collect")
    os.makedirs(out)
    files = {}

    def put(name, text):
        files[name] = text
        print("  " + name)

    print("Collecting (read-only):")
    put("firmware/xindi_version.txt", xindi_version())
    put("firmware/dev_info.txt", read("/dev_info.txt", 400))
    put("firmware/dpkg_qidi.txt", run("dpkg-query", "-W", "-f",
                                      "${Package} ${Version} ${Status}\n", "QIDI-X-Plus4")
        + run("dpkg-query", "-W", "-f", "${Package} ${Version}\n"))
    put("system/os.txt", "".join([
        read("/etc/os-release"), "\n", run("uname", "-a"), run("python3", "--version"),
        run(os.path.join(HOME, "klippy-env/bin/python"), "--version"),
        run(os.path.join(HOME, "moonraker-env/bin/python"), "--version"),
        run("ldd", "--version").splitlines()[0] + "\n" if run("ldd", "--version") else "",
        "nproc: " + run("nproc"), read("/proc/device-tree/model", 200).replace("\x00", "") + "\n",
        read("/proc/device-tree/compatible", 400).replace("\x00", " ") + "\n"]))
    put("system/cpuinfo.txt", read("/proc/cpuinfo"))
    put("system/memory.txt", run("free", "-m") + read("/proc/meminfo", 2000) + read("/proc/swaps"))
    put("system/storage.txt", run("df", "-h") + run("lsblk") + run("mount"))
    put("system/services.txt", run("systemctl", "list-units", "--type=service", "--all", "--no-pager",
                                   "--plain")
        + run("systemctl", "list-unit-files", "--no-pager", "--plain")
        + run("systemctl", "list-timers", "--all", "--no-pager"))
    put("system/cpufreq.txt", run("bash", "-c", "grep -H . /sys/devices/system/cpu/cpu*/cpufreq/"
                                  "scaling_{governor,available_governors,min_freq,max_freq} 2>/dev/null"))
    put("system/usb.txt", redact(run("lsusb") + run("ls", "-l", "/dev/serial/by-id/")))
    put("system/processes.txt", run("ps", "-eo", "pid,user,ni,psr,pcpu,pmem,rss,comm", "--sort=-pcpu"))
    put("system/load.txt", read("/proc/loadavg") + run("uptime"))

    put("software/klipper_files.txt", tree_hashes(os.path.join(HOME, "klipper")))
    put("software/moonraker_files.txt", tree_hashes(os.path.join(HOME, "moonraker")))
    put("software/fluidd.txt", read(os.path.join(HOME, "fluidd", ".version")) + "\n"
        + tree_hashes(os.path.join(HOME, "fluidd")))
    put("software/home_files.txt", tree_hashes_top(HOME))
    put("software/git.txt", run("git", "-C", os.path.join(HOME, "klipper"), "describe", "--always", "--dirty",
                                "--tags")
        + run("git", "-C", os.path.join(HOME, "moonraker"), "describe", "--always", "--dirty", "--tags"))
    put("software/klippy_env.txt", run(os.path.join(HOME, "klippy-env/bin/python"), "-m", "pip", "freeze",
                                       timeout=60))
    put("software/moonraker_env.txt", run(os.path.join(HOME, "moonraker-env/bin/python"), "-m", "pip",
                                          "freeze", timeout=60))

    put("config/listing.txt", config_listing())
    for name in FACTORY_FILES + ("box.cfg",):
        path = os.path.join(CONFIG, name)
        if os.path.isfile(path):
            put("config/files/" + name, redact(read(path)))
    for name in FACTORY_DIRS:
        root = os.path.join(CONFIG, name)
        for base, _dirs, names in os.walk(root):
            for each in sorted(names):
                path = os.path.join(base, each)
                put("config/files/" + os.path.relpath(path, CONFIG), redact(read(path)))
    for index in range(5):
        path = os.path.join(HOME, "cfg_with_{}".format(index))
        if os.path.isfile(path):
            put("config/templates/cfg_with_{}".format(index), redact(read(path)))

    put("moonraker/server_info.json", moonraker("/server/info"))
    put("moonraker/printer_info.json", moonraker("/printer/info"))
    put("moonraker/objects_list.json", moonraker("/printer/objects/list"))
    put("moonraker/mcu_status.json", redact(moonraker(
        "/printer/objects/query?mcu&mcu%20U_1&system_stats&toolhead&configfile=warnings")))
    put("klipper/klippy_excerpt.log", klippy_excerpt())

    for name, text in files.items():
        path = os.path.join(out, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    archive = os.path.join("/tmp", "plus4-collect-{}.tar.gz".format(stamp))
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname="plus4-collect-{}".format(stamp))
    shutil.rmtree(work, ignore_errors=True)
    print("\nDone. Nothing on the printer was changed. Copy this file to your computer:")
    print(archive)
    return 0


def tree_hashes_top(root):
    """Files directly in the home folder (QIDI's tools and Box templates), not its subfolders."""
    lines = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if os.path.isfile(path) and not name.startswith(".") and not NAME_ONLY.search(name):
            try:
                lines.append("{}  {:>9}  {}".format(sha256(path), os.path.getsize(path), name))
            except Exception as exc:  # noqa: BLE001
                lines.append("unreadable {} ({})".format(name, exc.__class__.__name__))
        elif os.path.isdir(path):
            lines.append("dir {}".format(name))
    return "\n".join(lines) + "\n"


def config_listing():
    lines = []
    for base, dirs, names in os.walk(CONFIG):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, CONFIG)
            try:
                size = os.path.getsize(path)
                if NAME_ONLY.search(rel):
                    lines.append("{:>64}  {:>9}  {}".format("(name only)", size, rel))
                else:
                    lines.append("{}  {:>9}  {}".format(sha256(path), size, rel))
            except Exception as exc:  # noqa: BLE001
                lines.append("unreadable {} ({})".format(rel, exc.__class__.__name__))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
