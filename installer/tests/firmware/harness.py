"""What the board firmware flow tests share: the host a flow runs on, a stand-in for QIDI's images, and a printer's
home folder."""
from __future__ import annotations

import hashlib
import io
import itertools
import json
import os
import shutil
import signal
import subprocess

from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.reporter import create_reporter
from installer.runtime.sudo import PUBLIC_DEFAULT_SUDO_PASSWORD
from installer.tests.helpers import REPO_ROOT, build_env, temp_path


class Host:
    """The host as a board flow sees it through `run`: sudo, and the services that own the boards' serial ports.

    `states` maps each service to "active" or "inactive"; a service left out isn't installed. Any other command goes to
    the subclass's `tool`, which answers for the board tools. `fail_start` names a service that won't start.

    sudo takes `password`. Once it has accepted one, it keeps credentials for the terminal, as a printer's sudo does:
    `-v` from the terminal then passes whatever password it is given, while `-k -v` and a command in its own session
    check the password.
    """

    def __init__(self, states, *, fail_start=None, password=PUBLIC_DEFAULT_SUDO_PASSWORD):
        self.states = dict(states)
        self.original = dict(states)
        self.fail_start = fail_start
        self.password = password
        self.terminal_credentials = False
        self.stops = self.service_changes = self.tool_runs = 0

    def run(self, command, **kwargs):
        command = list(command)

        def result(code=0, output=""):
            return subprocess.CompletedProcess(command, code, stdout=output, stderr="")

        if command[:4] == ["sudo", "-S", "-p", ""]:
            command = command[4:]
            from_terminal = not kwargs.get("start_new_session")
            cached = from_terminal and self.terminal_credentials and command[0] != "-k"
            if not cached and kwargs.get("input", "").rstrip("\n") != self.password:
                return result(1)
            self.terminal_credentials |= from_terminal
        if command in (["-v"], ["-k", "-v"]):
            return result()
        if command[:2] == ["systemctl", "show"]:
            if command[2] not in self.states:
                return result(1, "LoadState=not-found\nActiveState=inactive\n")
            return result(output="LoadState=loaded\nActiveState=" + self.states[command[2]] + "\n")
        if command[0] == "systemctl" and command[1] in ("stop", "start"):
            self.service_changes += 1
            self.stops += command[1] == "stop"
            if command[1:] == ["start", self.fail_start]:
                return result(1)
            self.states[command[2]] = "inactive" if command[1] == "stop" else "active"
            return result()
        self.tool_runs += 1
        return self.tool(command, **kwargs)

    def tool(self, command, **kwargs):
        raise AssertionError("Unexpected external operation: " + str(command))

    def stopped_by_caller(self):
        """A firmware update has stopped every service before the flow runs, and Moonraker went down with Klipper."""
        self.states = self.original = dict.fromkeys(self.states, "inactive")

        def offline(*args, **kwargs):
            raise OSError("Moonraker is stopped")

        self.urlopen = offline


def seams(printer, **more):
    """What a flow is given in place of the host: the fake's commands and Moonraker, no real sleep, and a clock that
    moves ten seconds each time it is read, so a wait for Klipper ends without waiting."""
    return {"run": printer.run, "urlopen": printer.urlopen, "sleep": lambda seconds: None,
            "clock": itertools.count(0, 10).__next__, **more}


def terminal_goes_away(reporter):
    """A callback for the moment a fake has a board in hand: the SSH session closes and Ctrl-C is pressed. Returns it
    with the list it adds to on each call: whether the tool got its own session, and what a hang-up does then."""
    seen = []

    def hang_up(kwargs):
        seen.append((kwargs.get("start_new_session"), signal.getsignal(signal.SIGHUP)))
        reporter.stream.close()  # the SSH session is gone
        os.kill(os.getpid(), signal.SIGINT)  # and Ctrl-C was pressed

    return hang_up, seen


def superseded_image(stock):
    """An earlier build of this project's fix."""
    return b"\x01" + stock[1:]


def predecessor_image(stock):
    """QIDI's image of an earlier version, shorter than the stock image as the mainboard's 02.01.01.11 is."""
    return b"\x02" + stock[1:-224]


def synthetic_manifest(name):
    """The board manifest firmware/<name>, pinned to a stand-in for QIDI's image, which the repository doesn't hold.

    The stand-in is zeros except where the manifest's patches expect QIDI's bytes, so the project's patcher and the
    tools' hash checks run on it as they do on the real image. The manifest admits one earlier build of the fix
    (superseded_image), one QIDI predecessor where the real manifest names any (predecessor_image), and, where it
    lists bootloaders, only the returned one. Returns (manifest, stock image, bootloader).
    """
    manifest = json.loads((REPO_ROOT / "firmware" / name).read_text())
    spec = next(manifest[board] for board in ("box", "mcu", "thr") if board in manifest)
    patches = [(int(entry["offset"], 0), entry) for entry in spec["patches"]]
    stock = bytearray(spec["size"])
    for offset, entry in patches:
        if "before" in entry:
            value = bytes.fromhex(entry["before"])
            stock[offset:offset + len(value)] = value
    patched = bytearray(stock)
    for offset, entry in patches:
        value = bytes.fromhex(entry["after"])
        if "before_sha256" in entry:
            entry["before_sha256"] = hashlib.sha256(stock[offset:offset + len(value)]).hexdigest()
        patched[offset:offset + len(value)] = value
    stock = bytes(stock)
    spec["stock_sha256"] = hashlib.sha256(stock).hexdigest()
    spec["patched_sha256"] = hashlib.sha256(patched).hexdigest()
    if spec.get("superseded"):
        spec["superseded"] = [{**spec["superseded"][0], "sha256": hashlib.sha256(superseded_image(stock)).hexdigest()}]
    if spec.get("predecessors"):
        earlier = predecessor_image(stock)
        spec["predecessors"] = [{"version": spec["predecessors"][0]["version"], "size": len(earlier),
                                 "sha256": hashlib.sha256(earlier).hexdigest()}]
    boot = bytes(range(256)) * 128
    if "bootloaders" in manifest:
        manifest["bootloaders"] = [{"sha256": hashlib.sha256(boot).hexdigest(), "identity_reply": "0112"}]
    return manifest, stock, boot


def qidi_firmware(version="01.01.07.01", soc_prefix="QD_MAX4_SOC_"):
    """QIDI's firmware_manifest.json for firmware `version`; the SOC file's prefix says which printer it is."""
    return {"SOC": {"file": f"{soc_prefix}{version}_20261016_Release_NA", "version": version}}


def printer_home(prefix, *, config, firmware=None, bundle=None):
    """A printer's home folder for a board flow. Returns (paths, reporter); the reporter writes to a StringIO.

    config: file name under printer_data/config -> its content.
    firmware: QIDI's firmware_manifest.json (qidi_firmware); None for a flow that never reads it.
    bundle: path in a bundle of the test's own -> text, for a flow given stand-in board tools and a synthetic
    manifest; the bundle also gets the repository's package.yaml. None runs the repository as the bundle.

    The home also holds the klippy-env Python that the board tools run under.
    """
    root = temp_path(prefix)
    data = root / "printer_data"
    (data / "config").mkdir(parents=True)
    for name, content in config.items():
        (data / "config" / name).write_bytes(content if isinstance(content, bytes) else content.encode())
    if firmware is not None:
        (data / "firmware_manifest.json").write_text(json.dumps(firmware))
    (root / "klippy-env/bin").mkdir(parents=True)
    (root / "klippy-env/bin/python").write_text("")
    bundle_root = REPO_ROOT
    if bundle is not None:
        bundle_root = root / "bundle"
        (bundle_root / "installer").mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "installer/package.yaml", bundle_root / "installer/package.yaml")
        for path, text in bundle.items():
            (bundle_root / path).parent.mkdir(parents=True, exist_ok=True)
            (bundle_root / path).write_text(text)
    paths = resolve_runtime_paths(bundle_root=bundle_root, environ=build_env(
        data, moonraker_url="http://printer.invalid/printer/objects/query"))
    return paths, create_reporter(io.StringIO(), prefer_plain=True, bundle_root=REPO_ROOT, environ={})
