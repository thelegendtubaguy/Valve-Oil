from __future__ import annotations

import hashlib
import io
import atexit
import socket
import tempfile
import threading
import json
import os
import shlex
import shutil
import stat
import subprocess
import unittest
import urllib.error
import urllib.parse
import zipfile
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from installer.runtime import board_firmware, firmware_update, qidi_package, safety, update_screen, valve_oil_status
from installer.runtime.system_optimizations import CARRIED_LEDGER_FILE
from installer.runtime.board_firmware import NEEDS_IMAGE, BoardAdmission, BoardStatus, admission_path
from installer.runtime.cli import main, resolve_runtime_paths
from installer.runtime.errors import InstallerError
from installer.runtime.mainboard_firmware import MainboardFirmwareError, mainboard_serial
from installer.runtime.manifest import load_manifest
from installer.runtime.models import ImagePin
from installer.runtime.reporter import create_reporter
from installer.runtime.thr_firmware import ThrFirmwareError
from installer.tests.helpers import REPO_ROOT, temp_path

MANIFEST = load_manifest(REPO_ROOT / "installer/package.yaml")
# The package's closed-loop driver image, and its pin.
DRIVER_IMAGE = b"QIDI's closed-loop driver firmware 03.01.10.15" * 64
DRIVER_PIN = ImagePin(size=len(DRIVER_IMAGE), sha256=hashlib.sha256(DRIVER_IMAGE).hexdigest())
TARGET = "01.01.07.01"
SOC = f"QD_MAX4_SOC_{TARGET}_20261016_Release_NA"
CONFIG_KEPT = ("MCU_ID.cfg", "saved_variables.cfg")
# QIDI's mainboard image 02.01.01.11, which the mainboard manifest knows as a predecessor.
MCU_11 = "33375f0debcb04fee18fbb65437fe6bd4082a6a6a70b2755f1de58320c56eab8"
PATCHED_MCU = "02.01.01.12-valve-oil.1"
BLOCK = ("#*# <---------------------- SAVE_CONFIG ---------------------->\n"
         "#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.\n#*#\n")
# The operator's printer.cfg: SAVE_CONFIG commented out the body's PID and wrote a mesh, a shaper, a section the new
# firmware drops, and an option Valve Oil's install sets.
OPERATOR_CFG = ("[include MCU_ID.cfg]\n[extruder]\ncontrol = pid\n#pid_Kp = 20.0\n[bed_mesh]\nspeed = 300\n"
                "[chamber_light]\npin = PA1\n\n" + BLOCK
                + "#*# [extruder]\n#*# pid_kp = 25.5\n#*#\n#*# [bed_mesh default]\n#*# points =\n#*# \t0.1, 0.2\n"
                  "#*# \t0.3, 0.4\n#*#\n#*# [input_shaper]\n#*# shaper_freq_x = 51.2\n#*#\n"
                  "#*# [chamber_light]\n#*# value = 1\n#*#\n#*# [bed_mesh]\n#*# speed = 999\n#*#\n")
# QIDI's printer.cfg in the package, with QIDI's own SAVE_CONFIG block.
PACKAGE_CFG = ("[include MCU_ID.cfg]\n[extruder]\ncontrol = pid\npid_Kp = 20.0\n[bed_mesh]\nspeed = 300\n\n" + BLOCK
               + "#*# [input_shaper]\n#*# shaper_type_x = ei\n#*# shaper_freq_x = 54.4\n#*#\n")
PACKAGE_VARIABLES = "[Variables]\nz_offset = 0.0\nbox_count = 0\n"


class PowerCut(BaseException):
    """The printer loses power: nothing after this point runs."""


def _write_json(path: Path, value: dict) -> None:
    """Plant a pending record or result as the update run writes them."""
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def package(*, extra=(), manifest=None) -> bytes:
    """A QIDI package for the target firmware, with `extra` (ZipInfo, data) entries added."""
    components = manifest or {
        "SOC": {"file": SOC, "version": TARGET, "region": "NA"},
        "MCU": {"file": "QD_MAX4_MCU_02.01.01.12_20260609.bin", "version": "02.01.01.12"},
        "THR": {"file": "QD_MAX4_THR_02.02.01.08_20260303.bin", "version": "02.02.01.08"},
        "CLOSED_LOOP_MOTOR": {"file": "QD_MAX4_CLOSED_LOOP_03.01.10.15_20260907.bin", "version": "03.01.10.15"},
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("firmware_manifest.json", json.dumps(components))
        for entry in components.values():
            archive.writestr(entry["file"], DRIVER_IMAGE if "_CLOSED_LOOP_" in entry["file"]
                             else b"payload of " + entry["file"].encode())
        for info, data in extra:
            archive.writestr(info, data)
    return out.getvalue()


class Klipper:
    """QIDI's old Klipper as the run sees it through its API socket, a real Unix socket served from a thread: its idle
    state, the closed-loop drivers, and QIDI's UPGRADE_MOTOR, whose transfer runs on the printer's fake clock as the
    harness against QIDI's real modules showed it: in progress 4 s after the command, waiting for the driver at 99 %
    from 35 s, success at 45 s, the driver answering again with its new version at 52 s. `modes[axis]` scripts each
    transfer: ok; silent (the loader never answers: timeout at 24 s); stall (ACKs stop at 35 %: timeout at 31 s);
    noversion (success, but the driver comes back on its old firmware); slow (the new version shows 40 s after
    success); lag (the session shows as started, address and upgrading state, 10 s before it is in progress); stuck
    (started, never in progress); refuse (QIDI's module refuses on the console); cut (the power goes mid-transfer);
    cuttook (the power goes once the image is in); drop (Klipper's socket closes on the command, and the loader never
    answers). After Klipper starts, drivers report no version for 8 s; a driver left in its loader answers nothing."""

    ADDRS = {"x": 1, "y": 3}

    def __init__(self, printer):
        self.printer = printer
        self.version = {"x": "03.01.10.13", "y": "03.01.10.13"}
        self.hardware = {"x": "9.1", "y": "9.1"}
        self.state = {"x": "idle", "y": "idle"}
        self.last = {"x": ("idle", 0.0), "y": ("idle", 0.0)}
        self.dead = set()
        self.modes = {"x": [], "y": []}
        self.transfer = None  # (axis, mode, start)
        self.quiet_until = 0.0  # drivers report no version until then, as after Klipper starts
        self.ready = True
        directory = tempfile.mkdtemp(prefix="kl", dir="/tmp" if os.path.isdir("/tmp") else None)
        atexit.register(shutil.rmtree, directory, True)
        self.path = Path(directory) / "klippy.sock"
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.path))
        self.server.listen(4)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        buffer = b""
        with connection:
            while True:
                try:
                    data = connection.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                buffer += data
                while b"\x03" in buffer:
                    raw, buffer = buffer.split(b"\x03", 1)
                    request = json.loads(raw)
                    result, messages = self._handle(request["method"], request.get("params") or {})
                    if messages == "drop":
                        return
                    out = [*messages, {"id": request["id"], "result": result}]
                    try:
                        connection.sendall(b"".join(json.dumps(message).encode() + b"\x03" for message in out))
                    except OSError:
                        return

    def _handle(self, method, params):
        p = self.printer
        if method == "info":
            return {"state": "ready" if self.ready else "startup"}, []
        if method == "objects/query":
            self._finish()
            return {"status": {name: self._object(name) for name in params["objects"]}}, []
        if method != "gcode/script":
            return {}, []
        script = params["script"]
        p.events.append(("klipper", script.split(" FILE=")[0], p.services["moonraker.service"],
                         p.services["qidi-client.service"]))
        if script == "FIRMWARE_RESTART":
            self.restart()
        elif script.startswith("UPGRADE_MOTOR "):
            axis = script.split("STEPPER=")[1].split()[0]
            mode = self.modes[axis].pop(0) if self.modes[axis] else "ok"
            if mode == "refuse":
                return {}, [{"params": {"response": "This command is not match the  stepper hardware. Please use "
                                                    "UPGRADE_MOTOR_V13 ."}}]
            image = Path(script.split("FILE=")[1])
            assert image.read_bytes() == DRIVER_IMAGE
            start = p.now + (10 if mode == "lag" else 0)
            self.transfer = (axis, {"lag": "ok", "drop": "silent"}.get(mode, mode), start)
            p.now += 4.2  # the command returns once the transfer is prepared
            if mode == "cut":
                p.power_cut = True
            if mode == "cuttook":
                p.now += 60
                self._finish()
                p.power_cut = True
            if mode == "drop":
                return None, "drop"
        return {}, []

    def restart(self):
        """FIRMWARE_RESTART or a boot: a driver left in its loader now answers nothing."""
        self._finish(force=True)
        for axis in self.dead:
            self.version[axis], self.state[axis] = None, "not ready"
        self.quiet_until = self.printer.now + 8

    def _finish(self, force=False):
        if self.transfer is None:
            return
        axis, mode, start = self.transfer
        elapsed = self.printer.now - start
        if mode in ("ok", "noversion", "slow", "cuttook") and elapsed >= (85 if mode == "slow" else 52):
            self.version[axis] = self.version[axis] if mode == "noversion" else "03.01.10.15"
            self.state[axis], self.last[axis] = "idle", ("success", 100.0)
            self.dead.discard(axis)
        elif (mode == "silent" and elapsed >= 24) or (mode == "stall" and elapsed >= 31) or (force and elapsed >= 4):
            self.state[axis], self.last[axis] = "upgrading", ("timeout", 35.0 if mode == "stall" else 0.0)
            self.dead.add(axis)
        else:
            return
        self.transfer = None

    def _progress(self, axis):
        """(is_upgrading, status, percentage, version, state) while a transfer of `axis` runs, else None."""
        if self.transfer is None or self.transfer[0] != axis:
            return None
        _, mode, start = self.transfer
        elapsed = self.printer.now - start
        version = self.version[axis]
        if elapsed < 4 or mode == "stuck":
            return (False,) + self.last[axis] + (version, "upgrading")
        if mode in ("ok", "noversion", "slow", "cuttook"):
            if elapsed >= 45:
                return (False, "success", 100.0, None, "not ready")
            if elapsed >= 35:
                return (True, "await_motor_status", 99.0, version, "upgrading")
        percentage = {"silent": 0.0, "stall": min(35.0, (elapsed - 4) * 3)}.get(mode, min(98.0, (elapsed - 4) * 3))
        return (True, "in_progress", percentage, version, "upgrading")

    def _object(self, name):
        p = self.printer
        if name in ("print_stats", "pause_resume", "idle_timeout", "heaters", "extruder"):
            return {"print_stats": {"state": p.print_state}, "pause_resume": {"is_paused": False},
                    "idle_timeout": {"state": "Idle"}, "heaters": {"available_heaters": ["extruder"]},
                    "extruder": {"target": p.heater}}[name]
        if name == "configfile":
            return {"settings": {f"closed_loop {axis}": {"query_cycle": 10} for axis in self.ADDRS}}
        if name == "cl_interface":
            return {"upgrading_addrs": [self.ADDRS[self.transfer[0]]] if self.transfer else []}
        axis = name.split()[-1]
        upgrading, status, percentage, version, state = self._progress(axis) or (
            (False,) + self.last[axis] + (self.version[axis], self.state[axis]))
        if p.now < self.quiet_until and self._progress(axis) is None:
            version, state = None, "not ready"
        return {"stepper_addr": self.ADDRS[axis], "stepper_version": version,
                "stepper_hd_version": self.hardware[axis] if version else None, "version_ok": version is not None,
                "stepper_state": state,
                "upgrade_status": {"is_upgrading": upgrading, "percentage": percentage, "status": status}}


class Response:
    def __init__(self, data: bytes, status=200, headers=None):
        self.data, self.status, self.headers = io.BytesIO(data), status, headers or {}

    def read(self, size=-1):
        return self.data.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Printer:
    """A Max 4 on an older QIDI firmware: its update folder, config, systemd, sudo, dpkg, Moonraker, USB, the THR and
    mainboard tools, and this project's mirror. Board delivery itself is Valve Oil's own."""

    def __init__(self, *, firmware="01.01.06.05", mainboard="02.01.01.12", archive=None, mirror="ok"):
        root = temp_path("firmware-update-")
        self.home = root / "home"
        data = self.home / "printer_data"
        (data / "config").mkdir(parents=True)
        (data / "config/tltg_optimized_state.yaml").write_text("installed\n")
        (data / "config/printer.cfg").write_text(OPERATOR_CFG)
        # No saved_variables.cfg.bak: QIDI's postinst then resets saved_variables.cfg to the package's defaults.
        (data / "config/saved_variables.cfg").write_text("[Variables]\nz_offset = 0.25\n")
        self.update = self.home / "update"
        self.update.mkdir()
        old_soc = f"QD_MAX4_SOC_{firmware}_X_NA"
        (self.update / "firmware_manifest.json").write_text(json.dumps({"SOC": {"file": old_soc, "version": firmware}}))
        (self.update / old_soc).write_bytes(b"QIDI's SOC package of the installed firmware")
        self.bundle = self.home / "tltg-optimized-macros"
        self.bundle.mkdir()
        for name in ("installer", "firmware", "scripts"):
            (self.bundle / name).symlink_to(REPO_ROOT / name)
        self.systemd = root / "systemd"
        self.systemd.mkdir()
        # The touchscreen app left the panel asleep: its backlight is off, and only root may turn it on.
        self.backlight = root / "backlight"
        self.backlight.mkdir()
        (self.backlight / "brightness").write_text("0\n")
        (self.backlight / "max_brightness").write_text("255\n")
        # The mainboard's USB name carries its firmware version.
        self.by_id = root / "by-id"
        self.by_id.mkdir()
        (self.by_id / self.usb_name(mainboard)).write_text("")
        (data / "config/MCU_ID.cfg").write_text(f"[mcu]\nserial: {self.by_id / self.usb_name(mainboard)}\n")
        self.paths = resolve_runtime_paths(bundle_root=self.bundle, environ={
            "TLTG_OPTIMIZED_PRINTER_DATA_ROOT": str(data),
            "TLTG_OPTIMIZED_FIRMWARE_MANIFEST": str(self.update / "firmware_manifest.json"),
            "TLTG_OPTIMIZED_MOONRAKER_URL": "http://moonraker.invalid/printer/objects/query?print_stats"})
        self.archive = package() if archive is None else archive
        max4 = next(printer for printer in MANIFEST.printers if printer.id == "max4")
        self.printer = replace(max4, target_firmware=replace(
            max4.target_firmware, sha256=hashlib.sha256(self.archive).hexdigest(), size=len(self.archive),
            closed_loop_image=DRIVER_PIN))
        self.manifest = replace(MANIFEST, printers=tuple(self.printer if p.id == "max4" else p for p in MANIFEST.printers))
        self.firmware, self.mainboard, self.mirror = firmware, mainboard, mirror
        self.thr = "stock"
        self.services = dict.fromkeys((*firmware_update.QIDI_SERVICES, firmware_update.TIMER_NAME), "active")
        self.print_state, self.heater, self.free = "standby", 0, 64 * 2 ** 30
        self.dpkg_version = firmware
        self.events: list[tuple] = []
        self.cut = None  # (event, count): the event at which power is lost
        self.hooks = {}  # event -> callable run once when it happens
        self.mirror_requests = 0
        self.mcu_outcome = "flashed"
        self.dpkg_rc, self.install_rc, self.dpkg_locked = 0, 0, False
        self.usb_late = False  # at boot the USB devices appear only after the run waits once
        self.sudo_default = True  # the printer user's sudo password is QIDI's default
        self.now, self.power_cut = 0.0, False
        self.klipper = Klipper(self)
        self.output = io.StringIO()
        self.reporter = create_reporter(self.output, prefer_plain=True)
        self.admit()

    def admit(self, *, admitted=True, mcu_sha=MCU_11):
        mcu = (BoardStatus("mcu", NEEDS_IMAGE, version=self.mainboard, application="qidi", application_sha256=mcu_sha)
               if self.mainboard.endswith(".11") else BoardStatus("mcu", NEEDS_IMAGE, version=self.mainboard,
                                                                  application="stock"))
        boards = (BoardStatus("thr", NEEDS_IMAGE if admitted else "unrecognized_bootloader", version="02.02.01.08",
                              application="stock"), mcu)
        admission = BoardAdmission(package_version=MANIFEST.package.version, printer="max4", firmware=self.firmware,
                                   target_firmware=TARGET, new_install=False, boards=boards)
        admission_path(self.paths).write_text(admission.to_json())

    # ---- seams --------------------------------------------------------------------------------------------------

    def event(self, *event):
        self.events.append(event)
        hook = self.hooks.pop(event[0], None)
        if hook is not None:
            hook()
        if self.cut is not None and event[:len(self.cut[0])] == self.cut[0]:
            count = sum(1 for seen in self.events if seen[:len(self.cut[0])] == self.cut[0])
            if count == self.cut[1]:
                self.cut = None
                raise PowerCut(event)

    def run(self, command, **kwargs):
        command = [str(part) for part in command]
        as_root = command[:4] == ["sudo", "-S", "-p", ""]
        if as_root:
            command = command[4:]
            if command in (["-v"], ["-k", "-v"]):
                return self.done(command, rc=int(command == ["-k", "-v"] and self.sudo_default is False))
        name = command[0]
        if name == "systemctl":
            action, unit = command[1], command[-1]
            if action == "show":
                return self.done(command, f"ActiveState={self.services.get(command[2], 'inactive')}\n")
            if action == "is-active":
                self.event("is-active")
                return self.done(command, "inactive\n", rc=3)
            if action == "start" and "--no-block" in command:
                self.event("unit")
                with patch.dict(os.environ):
                    firmware_update.run_firmware_update(**self.unit_seams())
                return self.done(command)
            if action in ("start", "stop"):
                self.event(action, unit, self.update_firmware())
                self.services[unit] = "active" if action == "start" else "inactive"
            return self.done(command)
        if name == "install":
            shutil.copyfile(command[-2], command[-1])
            return self.done(command)
        if name == "sh":
            echo, level, to, target = shlex.split(command[2])
            assert as_root and (echo, to, Path(target)) == ("echo", ">", self.backlight / "brightness")
            Path(target).write_text(level + "\n")
            return self.done(command)
        if name == "dpkg-deb":
            self.event("dpkg-deb")
            return self.done(command, f"Package: qd-max4-system\nVersion: {TARGET}\nArchitecture: arm64\n")
        if name == "dpkg":
            # dpkg is never given a time limit: killing sudo would leave dpkg running as root.
            assert kwargs.get("timeout") is None
            self.event("dpkg", command[1])
            if self.dpkg_locked:
                self.dpkg_locked = False
                return self.done(command, "dpkg: error: dpkg frontend lock was locked by another process with pid 7\n",
                                 rc=2)
            if "-i" in command:
                assert self.services["qidi-client.service"] == "inactive" and self.services["klipper.service"] == "inactive"
                # QIDI's preinst deletes the config folder except six file names, Valve Oil's state included, then
                # the package's files are unpacked and its postinst resets saved_variables.cfg when it had no .bak.
                config = self.home / "printer_data/config"
                reset = not (config / "saved_variables.cfg.bak").exists()
                for path in config.iterdir():
                    if path.name not in CONFIG_KEPT:
                        path.unlink()
                (config / "printer.cfg").write_text(PACKAGE_CFG)
                (config / "saved_variables.cfg.bak").write_text(PACKAGE_VARIABLES)
                if reset:
                    (config / "saved_variables.cfg").write_text(PACKAGE_VARIABLES)
                self.dpkg_version = TARGET
                return self.done(command, rc=self.dpkg_rc)
            return self.done(command)
        if name == "dpkg-query":
            self.event("dpkg-query")
            return self.done(command, f"install ok installed\t{self.dpkg_version}")
        if name == "rm":
            shutil.rmtree(command[-1])
            return self.done(command)
        if "bootstrap.py" in " ".join(command):
            env = kwargs["env"]
            assert env[safety.FIRMWARE_UPDATE_RUN_ENV] == firmware_update.load_record(self.paths)["id"]
            assert env[firmware_update.LOCK_HELD_ENV] == "1" and command[-3:] == ["install", "--yes", "--plain"]
            # The install after QIDI's package finds the system ledger this update kept for it.
            assert (self.home / "printer_data" / CARRIED_LEDGER_FILE).read_text() == env[safety.FIRMWARE_UPDATE_RUN_ENV]
            self.event("install", self.services["klipper.service"], self.services["qidi-client.service"],
                       self.update_firmware())
            if self.install_rc == 0:
                (self.home / "printer_data/config/tltg_optimized_state.yaml").write_text("reinstalled\n")
            return self.done(command, rc=self.install_rc)
        raise AssertionError(f"unexpected command {command}")

    @staticmethod
    def done(command, stdout="", rc=0):
        return subprocess.CompletedProcess(command, rc, stdout=stdout, stderr="")

    def moonraker(self, url, timeout=None):
        self.events.append(("moonraker",))
        if self.services["moonraker.service"] != "active":
            raise urllib.error.URLError("connection refused")
        names = [urllib.parse.unquote(part.split("=")[0]) for part in urllib.parse.urlsplit(url).query.split("&")]
        status = {"print_stats": {"state": self.print_state}, "webhooks": {"state": "ready"},
                  "heaters": {"available_heaters": ["extruder"]}, "extruder": {"target": self.heater},
                  "closed_loop x": {"stepper_version": "03.01.10.13", "stepper_hd_version": "9.1"},
                  "closed_loop y": {"stepper_version": "03.01.10.13", "stepper_hd_version": "9.1"}}
        return Response(json.dumps({"result": {"status": {name: status[name] for name in names if name in status}}}).encode())

    def mirror_urlopen(self, request, timeout=None):
        self.mirror_requests += 1
        assert request.full_url == self.printer.target_firmware.url
        if self.mirror == "down":
            raise urllib.error.URLError("unreachable")
        data = {"ok": self.archive, "wrong": bytes(len(self.archive)), "long": self.archive + b"more"}[self.mirror]
        start = int(request.headers.get("Range", "bytes=0-")[6:].rstrip("-"))
        if start:
            return Response(data[start:], 206, {"Content-Range": f"bytes {start}-{len(data) - 1}/{len(data)}"})
        return Response(data)

    # The THR and mainboard tools, as they behave towards the run: each refuses while a Klipper restart is pending and
    # finds the mainboard only under the name the config gives it.
    def thr_flow(self, **kwargs):
        assert kwargs["services_stopped"] is True
        if self.paths.restart_marker_path.exists():
            raise ThrFirmwareError("Complete pending Klipper source activation before THR firmware programming.")
        self.event("deliver", self.services["klipper.service"], self.update_firmware())
        outcome, self.thr = ("current" if self.thr == "patched" else "flashed"), "patched"
        return outcome

    def mainboard_flow(self, **kwargs):
        assert kwargs["services_stopped"] is True
        if self.paths.restart_marker_path.exists():
            raise MainboardFirmwareError("Complete pending Klipper source activation before mainboard programming.")
        if kwargs["resolve_usb"](mainboard_serial(self.paths)) is None:
            raise MainboardFirmwareError("The mainboard is not connected, or Klipper is not ready.")
        if self.mainboard == PATCHED_MCU:
            kwargs["outcomes"]["mcu"] = ("current", "")
            return 0
        if self.mcu_outcome == "waiting":
            kwargs["outcomes"]["mcu"] = ("waiting", "waits for QIDI firmware")
            return 0
        if self.mcu_outcome == "unverified":
            (self.home / "printer_data" / safety.MAINBOARD_RECOVERY_MARKER).write_text("{}")
            raise MainboardFirmwareError("Mainboard transfer failed")
        (self.by_id / self.usb_name(self.mainboard)).rename(self.by_id / self.usb_name(PATCHED_MCU))
        self.mainboard = PATCHED_MCU
        self.event("written")
        kwargs["outcomes"]["mcu"] = ("flashed", "")
        return 0

    @staticmethod
    def usb_name(version):
        return f"usb-Klipper_QIDI_MAIN_V2_1.0.{version.split('.')[3][:2]}_ID-if00"

    def resolve_usb(self, serial):
        return "2-1.3" if serial and Path(serial).exists() and Path(serial).parent == self.by_id else None

    def sleep(self, seconds):
        self.now += seconds
        if self.power_cut:
            self.power_cut = False
            raise PowerCut("mid-transfer")
        if self.usb_late:
            self.usb_late = False
            for path in (self.by_id.parent / "by-id-later").iterdir():
                path.rename(self.by_id / path.name)

    def carry(self, paths, state_path, update_id):
        # The update keeps the installed state's system ledger while that state is still there.
        self.event("carry", state_path.exists())
        (paths.printer_data_root / CARRIED_LEDGER_FILE).write_text(update_id)
        return True

    def draw(self, lines, reporter=None):
        self.event("screen", "updating" if lines is update_screen.UPDATING else "stopped")
        # The real drawing, against a framebuffer that isn't there, fails without stopping the update.
        return update_screen.draw(lines, device=str(self.home / "missing-fb0"), reporter=reporter)

    def disk_usage(self, path):
        return SimpleNamespace(free=self.free)

    def update_firmware(self):
        try:
            return json.loads((self.update / "firmware_manifest.json").read_text())["SOC"]["version"]
        except (OSError, ValueError, KeyError):
            return None

    def unit_seams(self):
        return {"paths": self.paths, "reporter": self.reporter, "environ": {}, "manifest": self.manifest,
                "run": self.run, "urlopen": self.moonraker, "mirror_urlopen": self.mirror_urlopen,
                "sleep": self.sleep, "clock": lambda: self.now,
                "klipper": lambda paths, run: firmware_update.KlipperAPI(self.klipper.path), "draw": self.draw, "disk_usage": self.disk_usage,
                "resolve_usb": self.resolve_usb, "sync": lambda: None}

    @contextmanager
    def patched(self):
        with ExitStack() as stack:
            for target, value in ((firmware_update, {"SYSTEMD_DIR": self.systemd, "SERIAL_BY_ID": self.by_id,
                                                     "save_carried_system_ledger": self.carry}),
                                  (update_screen, {"BACKLIGHT": str(self.backlight)}),
                                  (board_firmware, {"flash_thr_firmware": self.thr_flow,
                                                    "run_mainboard_firmware": self.mainboard_flow})):
                for name, replacement in value.items():
                    stack.enter_context(patch.object(target, name, replacement))
            yield

    def start(self, **kwargs):
        options = {"paths": self.paths, "reporter": self.reporter, "manifest": self.manifest, "kind": "install",
                   "environ": {}, "urlopen": self.moonraker, "mirror_urlopen": self.mirror_urlopen, "run": self.run,
                   "sleep": lambda seconds: None, "disk_usage": self.disk_usage}
        with self.patched():
            return firmware_update.start_firmware_update(**{**options, **kwargs})

    def resume(self):
        """An operator runs ./install.sh again."""
        with self.patched():
            return firmware_update.report_or_attach(paths=self.paths, reporter=self.reporter, environ={}, run=self.run,
                                                    sleep=lambda seconds: None, urlopen=self.moonraker)

    def boot(self):
        """Power returns: the services start by themselves and the unit runs, if the record is there."""
        self.services = dict.fromkeys(self.services, "active")
        self.klipper.restart()
        if self.usb_late:
            later = self.by_id.parent / "by-id-later"
            later.mkdir(exist_ok=True)
            for path in self.by_id.iterdir():
                path.rename(later / path.name)
        with patch.dict(os.environ), self.patched():
            if firmware_update.record_path(self.paths).exists():
                return firmware_update.run_firmware_update(**self.unit_seams())
        return 0

    # ---- what the printer looks like ------------------------------------------------------------------------------

    def snapshot(self):
        skip = (valve_oil_status.STATUS_FILE, valve_oil_status.LOCK_FILE, firmware_update.RESULT_FILE,
                firmware_update.RECORD_LOCK_FILE, ".tltg_optimized_installer.lock")
        return {str(path.relative_to(self.home)): path.read_bytes() for path in sorted(self.home.rglob("*"))
                if path.is_file() and not path.is_symlink() and ".cache" not in path.parts and path.name not in skip}

    def record(self):
        return firmware_update.load_record(self.paths)


class FirmwareUpdateTests(unittest.TestCase):
    """A firmware update installs the pinned QIDI package from the mirror, flashes the boards with Klipper stopped,
    and reinstalls Valve Oil, detached from the session, resuming after a power loss; anything that keeps it from
    starting leaves the printer as it was, and anything that stops it later is recorded with a way forward."""

    @staticmethod
    def driver_commands(printer):
        return [event[1:] for event in printer.events if event[0] == "klipper"]

    def assert_updated(self, printer: Printer):
        # The operator's saved calibration is back, in the form Klipper's SAVE_CONFIG writes, over QIDI's: the body
        # option it sets is commented out, the dropped section and the option Valve Oil sets are not carried.
        config = printer.home / "printer_data/config"
        body, block = (config / "printer.cfg").read_text().split(BLOCK.rstrip())
        self.assertEqual(body, PACKAGE_CFG.split(BLOCK)[0].replace("pid_Kp", "#pid_Kp"))
        self.assertEqual(block, "\n#*# [input_shaper]\n#*# shaper_type_x = ei\n#*# shaper_freq_x = 51.2\n#*#\n"
                                "#*# [extruder]\n#*# pid_kp = 25.5\n#*#\n#*# [bed_mesh default]\n#*# points =\n"
                                "#*# \t0.1, 0.2\n#*# \t0.3, 0.4\n")
        self.assertEqual((config / "saved_variables.cfg").read_text(), "[Variables]\nz_offset = 0.25\nbox_count = 0\n")
        message = valve_oil_status.load_status(printer.paths).run.message
        self.assertIn("Restored the saved calibration from before the update: [extruder], [bed_mesh default], "
                      "[input_shaper]", message)
        self.assertIn("[chamber_light] (QIDI firmware 01.01.07.01's config has no such section)", message)
        self.assertIn("[bed_mesh] speed (Valve Oil's install sets it)", message)
        self.assertIn(MANIFEST.backup.label_prefix, message)  # where the config from before the update is
        self.assertEqual(printer.update_firmware(), TARGET)
        self.assertEqual(printer.dpkg_version, TARGET)
        self.assertEqual((printer.thr, printer.mainboard), ("patched", PATCHED_MCU))
        self.assertIsNone(printer.record())
        self.assertEqual(printer.services, dict.fromkeys(printer.services, "active"))
        # The system ledger was kept before QIDI's package deleted the installed state, and is gone with the update.
        self.assertEqual({event for event in printer.events if event[0] == "carry"}, {("carry", True)})
        self.assertFalse((printer.home / "printer_data" / CARRIED_LEDGER_FILE).exists())
        self.assertFalse(any((printer.home / name).exists() for name in (qidi_package.STAGING_DIRECTORY, qidi_package.PREVIOUS_DIRECTORY)))
        self.assertFalse(qidi_package.archive_path(printer.paths, printer.printer.target_firmware).exists())
        self.assertEqual((printer.home / "printer_data/config/tltg_optimized_state.yaml").read_text(), "reinstalled\n")
        # Klipper finds the mainboard under the name its new firmware gives it, as the touchscreen app would set it.
        self.assertIn(printer.usb_name(PATCHED_MCU), (printer.home / "printer_data/config/MCU_ID.cfg").read_text())
        status = valve_oil_status.load_status(printer.paths)
        self.assertEqual(status.run.state, valve_oil_status.SUCCEEDED)
        self.assertFalse([item for item in status.pending if item.kind == valve_oil_status.FIRMWARE])
        self.assertEqual(firmware_update.load_result(printer.paths)["state"], firmware_update.SUCCEEDED)

    def test_update_from_a_mainboard_the_new_klipper_cannot_use_flashes_it_before_klipper_starts(self):
        printer = Printer(firmware="01.01.06.03", mainboard="02.01.01.11")
        # A Klipper restart pending for Klipper files QIDI's package replaces doesn't stop the board tools; another
        # program briefly holds dpkg's lock; a partial download longer than the package starts again.
        printer.paths.restart_marker_path.write_text("{}")
        printer.dpkg_locked = True
        part = qidi_package.cache_dir(printer.paths) / (printer.printer.target_firmware.archive + ".part")
        part.parent.mkdir(parents=True)
        part.write_bytes(bytes(len(printer.archive) + 1))
        result = printer.start()
        self.assertEqual(result.state, firmware_update.SUCCEEDED, printer.output.getvalue())
        self.assert_updated(printer)
        self.assertFalse(printer.paths.restart_marker_path.exists())
        kinds = [event[0] for event in printer.events]
        # The boards are flashed after the SOC install and the update folder, with Klipper stopped; nothing starts or
        # waits for Klipper before that, and the touchscreen stays stopped until the installer has run.
        self.assertEqual(printer.events[kinds.index("deliver")], ("deliver", "inactive", TARGET))
        self.assertLess(kinds.index("dpkg"), kinds.index("deliver"))
        self.assertNotIn(("start", "klipper.service"), [event[:2] for event in printer.events[:kinds.index("deliver")]])
        self.assertEqual(printer.events[kinds.index("install")], ("install", "active", "inactive", TARGET))
        self.assertLess(kinds.index("install"), printer.events.index(("start", "qidi-client.service", TARGET)))
        self.assertIn(("screen", "updating"), printer.events)
        self.assertEqual((printer.backlight / "brightness").read_text(), "255\n")  # the sleeping panel was lit
        # The board's serial number, part of its USB name, is not logged.
        self.assertNotIn("_ID-if00", printer.output.getvalue())
        unit = (printer.systemd / firmware_update.UNIT).read_text()
        self.assertIn(f"ConditionPathExists={firmware_update.record_path(printer.paths)}", unit)
        self.assertIn("bootstrap.py firmware-update", unit)
        # What QIDI's package deletes from the config folder is kept in a restorable installer backup first.
        backup = next(printer.paths.printer_data_root.glob(MANIFEST.backup.label_prefix + "-01.01.06.03-*.zip"))
        self.assertIn("config/tltg_optimized_state.yaml", zipfile.ZipFile(backup).namelist())
        # The closed-loop drivers are updated first, X then Y, with QIDI's command, while the old Klipper runs alone:
        # the touchscreen and Moonraker are stopped and the motors off before the first transfer.
        self.assertEqual(self.driver_commands(printer), [("M84", "inactive", "inactive"),
                                                         ("UPGRADE_MOTOR STEPPER=x", "inactive", "inactive"),
                                                         ("UPGRADE_MOTOR STEPPER=y", "inactive", "inactive")])
        self.assertLess(printer.events.index(("klipper", "UPGRADE_MOTOR STEPPER=y", "inactive", "inactive")),
                        kinds.index("dpkg"))
        boards = {board.id: board for board in valve_oil_status.load_status(printer.paths).boards}
        self.assertEqual((boards["closed_loop"].state, boards["closed_loop"].firmware),
                         (valve_oil_status.CURRENT, "03.01.10.15"))
        # The result was reported to the session that started it.
        self.assertIsNone(firmware_update.report_or_attach(paths=printer.paths, reporter=printer.reporter, environ={}))

    def test_closed_loop_drivers_get_an_image_only_when_admitted_and_count_as_updated_only_once_verified(self):
        def board(printer):
            return {b.id: b for b in valve_oil_status.load_status(printer.paths).boards}["closed_loop"]

        # A driver not exactly as expected: neither driver is sent anything, and the rest of the update goes on.
        for case, change in (("other version", lambda k: k.version.update(x="03.01.10.25")),
                             ("other hardware", lambda k: k.hardware.update(y="9.2")),
                             ("not idle", lambda k: k.state.update(x="upgrading"))):
            with self.subTest(case=case):
                printer = Printer()
                change(printer.klipper)
                self.assertEqual(printer.start().state, firmware_update.SUCCEEDED, printer.output.getvalue())
                self.assertEqual(self.driver_commands(printer), [])
                self.assertEqual(board(printer).state, valve_oil_status.HELD)
                self.assertIn("touchscreen's firmware page", board(printer).detail)
                self.assertIn("/issues/new", board(printer).issue_url)
                self.assertIn("X driver reports" if case != "other hardware" else "hardware 9.2", board(printer).detail)

        def update(modes):
            printer = Printer()
            printer.klipper.modes["x"] = list(modes)
            return printer, printer.start()

        # QIDI's module refuses: nothing reached the driver, so both keep their firmware and the update goes on.
        printer, result = update(["refuse"])
        self.assertEqual(result.state, firmware_update.SUCCEEDED)
        self.assertEqual([c[0] for c in self.driver_commands(printer)], ["M84", "UPGRADE_MOTOR STEPPER=x"])
        self.assertEqual(board(printer).state, valve_oil_status.HELD)
        self.assertIn("not match the  stepper hardware", board(printer).detail)
        self.assertIn("to the X and Y closed-loop motor drivers", board(printer).detail)
        # Refused at Y after X was verified: the status says Y keeps its firmware, not that both are updated.
        printer = Printer()
        printer.klipper.modes["y"] = ["refuse"]
        self.assertEqual(printer.start().state, firmware_update.SUCCEEDED)
        self.assertEqual((board(printer).state, board(printer).firmware),
                         (valve_oil_status.HELD, "03.01.10.13, 03.01.10.15"))
        self.assertIn("to the Y closed-loop motor driver,", board(printer).detail)
        # A transfer that stalls, or ends with QIDI's "success" but the old firmware, is retried until verified.
        for modes in (["stall", "ok"], ["noversion", "ok"]):
            with self.subTest(modes=modes):
                printer, result = update(modes)
                self.assertEqual(result.state, firmware_update.SUCCEEDED, printer.output.getvalue())
                self.assertEqual([c[0] for c in self.driver_commands(printer)],
                                 ["M84", "UPGRADE_MOTOR STEPPER=x", "UPGRADE_MOTOR STEPPER=x", "UPGRADE_MOTOR STEPPER=y"])
                self.assertEqual(board(printer).state, valve_oil_status.CURRENT)
        # Three failed transfers: the bus is reset, Y is not touched, QIDI's package is not installed, everything runs
        # again, and the record waits for the operator.
        printer, result = update(["silent"] * 3)
        self.assertEqual(result.state, firmware_update.FAILED)
        self.assertEqual([c[0] for c in self.driver_commands(printer)],
                         ["M84"] + ["UPGRADE_MOTOR STEPPER=x"] * 3 + ["FIRMWARE_RESTART"])
        self.assertNotIn("dpkg", [event[0] for event in printer.events])
        self.assertEqual(printer.services, dict.fromkeys(printer.services, "active"))
        self.assertEqual((printer.record()["state"], printer.record()["failed_step"]), ("failed", "closed_loop"))
        self.assertEqual(board(printer).state, valve_oil_status.NEEDS_IMAGE)
        self.assertTrue(board(printer).issue_url.startswith("https://github.com/thelegendtubaguy/valve-oil/issues/new"))
        self.assertIn("./install.sh", result.message)
        # The X driver now answers nothing; the run recorded it as admitted, so the operator's retry sends it the
        # image again, then updates Y and finishes.
        self.assertIsNone(printer.klipper.version["x"])
        self.assertEqual(printer.resume(), 0, printer.output.getvalue())
        self.assert_updated(printer)

        # The power goes mid-transfer: the record says which driver was being sent the image, and the boot resume
        # waits for Klipper, sends it again, and finishes.
        printer = Printer()
        printer.klipper.modes["x"] = ["cut"]
        with self.assertRaises(PowerCut):
            printer.start()
        state = printer.record()["closed_loop_state"]
        self.assertEqual((state["sending"]["axis"], state["transfers"], state["sent"]), ("x", {"x": 1}, True))
        self.assertEqual(printer.boot(), 0, printer.output.getvalue())
        self.assert_updated(printer)
        self.assertEqual([c[0] for c in self.driver_commands(printer)].count("UPGRADE_MOTOR STEPPER=x"), 2)

        # A print started before Moonraker stopped: what was stopped starts again and nothing is sent.
        printer = Printer()
        printer.hooks["screen"] = lambda: setattr(printer, "print_state", "printing")
        result = printer.start()
        self.assertEqual((result.state, [b.code for b in result.blockers]), (firmware_update.NOT_STARTED, ["printing"]))
        self.assertEqual(self.driver_commands(printer), [])
        self.assertEqual(printer.services, dict.fromkeys(printer.services, "active"))
        self.assertIsNone(printer.record())

    def test_closed_loop_drivers_are_sent_an_image_only_when_it_is_needed_and_safe(self):
        def sends(printer, axis):
            return [c[0] for c in self.driver_commands(printer)].count(f"UPGRADE_MOTOR STEPPER={axis}")

        def update(modes):
            printer = Printer()
            printer.klipper.modes["x"] = list(modes)
            return printer, printer.start()

        # A session Klipper shows as started is a transfer even before it is in progress: watched, never "refused"
        # (which would go on to stop Klipper mid-transfer). One that never progresses fails and resets the bus.
        def board(printer):
            return {b.id: b for b in valve_oil_status.load_status(printer.paths).boards}["closed_loop"]

        printer, result = update(["lag"])
        self.assertEqual((result.state, sends(printer, "x"), board(printer).state),
                         (firmware_update.SUCCEEDED, 1, valve_oil_status.CURRENT), printer.output.getvalue())
        printer, result = update(["stuck"] * 3)
        self.assertEqual((result.state, sends(printer, "x"), sends(printer, "y")), (firmware_update.FAILED, 3, 0))
        self.assertEqual(self.driver_commands(printer)[-1][0], "FIRMWARE_RESTART")
        # A driver slow to report its new version after QIDI's success isn't sent the image again.
        printer, result = update(["slow"])
        self.assertEqual((result.state, sends(printer, "x")), (firmware_update.SUCCEEDED, 1))
        # Klipper's socket closes on the command: it counts as a transfer, and FIRMWARE_RESTART goes out on a new
        # connection before the next one.
        printer, result = update(["drop"])
        self.assertEqual(result.state, firmware_update.SUCCEEDED, printer.output.getvalue())
        self.assertEqual([c[0] for c in self.driver_commands(printer)],
                         ["M84", "UPGRADE_MOTOR STEPPER=x", "FIRMWARE_RESTART", "UPGRADE_MOTOR STEPPER=x",
                          "UPGRADE_MOTOR STEPPER=y"])

        # The power goes once X has its image; at boot the drivers report no version for a few seconds. The resume
        # waits for them: X, on the target, isn't sent the image again, and Y, on the old version, once.
        printer = Printer()
        printer.klipper.modes["x"] = ["cuttook"]
        with self.assertRaises(PowerCut):
            printer.start()
        self.assertEqual(printer.boot(), 0, printer.output.getvalue())
        self.assert_updated(printer)
        self.assertEqual((sends(printer, "x"), sends(printer, "y")), (1, 1))

        # Continuing after a driver was sent an image, with Klipper never ready: FIRMWARE_RESTART is still sent, the
        # update fails, and everything runs again.
        printer = Printer()
        printer.klipper.modes["x"] = ["cut"]
        with self.assertRaises(PowerCut):
            printer.start()
        printer.klipper.ready = False
        self.assertEqual(printer.boot(), 1)
        self.assertEqual(self.driver_commands(printer)[-1][0], "FIRMWARE_RESTART")
        self.assertEqual((printer.record()["state"], printer.record()["failed_step"]), ("failed", "closed_loop"))
        self.assertEqual(printer.services, dict.fromkeys(printer.services, "active"))

        # A driver image whose path can't go into a G-code line as it is: nothing is sent and nothing changed.
        components = {"SOC": {"file": SOC, "version": TARGET, "region": "NA"},
                      "MCU": {"file": "QD_MAX4_MCU_02.01.01.12_20260609.bin", "version": "02.01.01.12"},
                      "THR": {"file": "QD_MAX4_THR_02.02.01.08_20260303.bin", "version": "02.02.01.08"},
                      "CLOSED_LOOP_MOTOR": {"file": "QD_MAX4_CLOSED_LOOP_03.01.10.15 copy.bin",
                                            "version": "03.01.10.15"}}
        printer = Printer(archive=package(manifest=components))
        result = printer.start()
        self.assertEqual(result.state, firmware_update.NOT_STARTED, printer.output.getvalue())
        self.assertEqual(self.driver_commands(printer), [])
        self.assertEqual(printer.services, dict.fromkeys(printer.services, "active"))
        self.assertIsNone(printer.record())

    def test_power_loss_at_any_point_resumes_at_boot_to_the_same_end(self):
        def lose_working_files(printer):
            shutil.rmtree(printer.home / qidi_package.STAGING_DIRECTORY)
            shutil.rmtree(qidi_package.cache_dir(printer.paths))

        def no_installable_old_package(printer):
            # Between the SOC install and the new update folder, the touchscreen's own board update must find no SOC
            # package to install again.
            self.assertEqual(printer.update_firmware(), "01.01.06.03")
            self.assertEqual([path.name for path in printer.update.iterdir() if "_SOC_" in path.name], [])

        def late_usb(printer):
            printer.usb_late = True

        cuts = [(("stop", "klipper.service"), None),
                # Inside dpkg before it unpacks, with the cached and unpacked package lost: fetched again.
                (("dpkg", "-i"), lose_working_files),
                (("dpkg-query",), no_installable_old_package),
                # After the mainboard write, with the mainboard renamed and late on USB at boot.
                (("written",), late_usb),
                (("install",), None), (("start", "qidi-client.service"), None)]
        for cut, after in cuts:
            with self.subTest(cut=cut):
                printer = Printer(firmware="01.01.06.03", mainboard="02.01.01.11")
                printer.cut = (cut, 1)
                with self.assertRaises(PowerCut):
                    printer.start()
                record = printer.record()
                self.assertEqual(record["state"], "running")
                self.assertTrue(safety.firmware_update_blocks(printer.paths.printer_data_root))
                if after is not None:
                    after(printer)
                requests, before = printer.mirror_requests, len(printer.events)
                self.assertEqual(printer.boot(), 0, printer.output.getvalue())
                self.assert_updated(printer)
                later = printer.events[before:]
                if "soc" in record["done"] and "install" not in record["done"]:
                    # Once QIDI's package is in, the boot resume stops everything and goes to the boards without
                    # asking Moonraker anything.
                    self.assertEqual(later[0][0], "stop")
                    deliver = ("deliver", "inactive", TARGET)
                    if deliver in later:
                        self.assertNotIn(("moonraker",), later[:later.index(deliver)])
                if after is lose_working_files:
                    self.assertEqual(printer.mirror_requests, requests + 1)

    def test_power_loss_while_qidi_update_folder_is_replaced_leaves_a_readable_manifest(self):
        printer = Printer()
        renames = []

        def rename(source, destination):
            renames.append(source)
            if len(renames) == 2:
                raise PowerCut("between the renames")
            os.replace(source, destination)

        with patch.object(qidi_package, "exchange_paths", return_value=False), patch.object(qidi_package.os, "rename", side_effect=rename):
            with self.assertRaises(PowerCut):
                printer.start()
        self.assertIsNone(printer.update_firmware())  # moved aside; the boot resume puts the new one in place
        self.assertEqual(printer.boot(), 0, printer.output.getvalue())
        self.assert_updated(printer)

    def test_nothing_changes_when_the_update_cannot_start(self):
        cases = {
            "printing": lambda p: setattr(p, "print_state", "printing"),
            "heaters_on": lambda p: setattr(p, "heater", 60),
            "board_not_admitted": lambda p: p.admit(admitted=False),
            # A mainboard on a QIDI image this release knows only by its reported version can't be flashed.
            "board_not_deliverable": lambda p: (setattr(p, "mainboard", "02.01.01.11"), p.admit(mcu_sha="0" * 64)),
            "free_space": lambda p: setattr(p, "free", 2 ** 20),
            "mirror_unreachable": lambda p: setattr(p, "mirror", "down"),
            "package_unverified": lambda p: setattr(p, "mirror", "wrong"),
            "package_unverified ": lambda p: setattr(p, "mirror", "long"),
            "sudo_password": lambda p: setattr(p, "sudo_default", False),
            # A print starts while the package is unpacked, after the run's own check.
            "printing ": lambda p: p.hooks.update({"dpkg-deb": lambda: setattr(p, "print_state", "printing")}),
        }
        for case, prepare in cases.items():
            with self.subTest(case=case):
                printer = Printer(firmware="01.01.06.03" if "deliverable" in case else "01.01.06.05")
                prepare(printer)
                before = printer.snapshot()
                result = printer.start()
                self.assertEqual((result.state, [blocker.code for blocker in result.blockers]),
                                 (firmware_update.NOT_STARTED, [case.strip()]))
                self.assertFalse(result.handled)
                self.assertEqual(printer.snapshot(), before)
                self.assertEqual(printer.services, dict.fromkeys(printer.services, "active"))
                self.assertFalse(any(event[0] == "dpkg" or event[0] == "stop" and case != "printing "
                                     for event in printer.events))
                self.assertEqual(self.driver_commands(printer), [])
                self.assertFalse(qidi_package.archive_path(printer.paths, printer.printer.target_firmware).exists()
                                 and case != "printing ")
                waiting = [item for item in valve_oil_status.load_status(printer.paths).pending if item.kind == valve_oil_status.FIRMWARE]
                self.assertEqual([blocker.code for blocker in waiting[0].blocked_by], [case.strip()])
                self.assertIsNone(valve_oil_status.load_status(printer.paths).run)  # no update ran
                # The next install from a terminal installs for the current firmware instead of stopping.
                self.assertIsNone(firmware_update.report_or_attach(paths=printer.paths, reporter=printer.reporter, environ={}))

    def test_hostile_package_entries_are_refused_before_anything_changes(self):
        def entry(name, mode=stat.S_IFREG | 0o644):
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            return info, b"x"

        manifest = {"SOC": {"file": SOC, "version": TARGET}}
        archives = {
            "parent": package(extra=[entry("../escaped")]),
            "absolute": package(extra=[entry("/escaped")]),
            "nested": package(extra=[entry("sub/escaped")]),
            "symlink": package(extra=[entry("escaped", stat.S_IFLNK | 0o777)]),
            "device": package(extra=[entry("escaped", stat.S_IFCHR | 0o644)]),
            "hidden": package(extra=[entry(qidi_package.STAGED_MARKER)]),
            "missing file": package(manifest={**manifest, "MCU": {"file": "../../escaped", "version": "1"}}),
            "other firmware": package(manifest={"SOC": {"file": SOC, "version": "01.01.06.05"}}),
        }
        for case, archive in archives.items():
            with self.subTest(case=case):
                printer = Printer(archive=archive)
                before = printer.snapshot()
                result = printer.start()
                self.assertEqual((result.state, [blocker.code for blocker in result.blockers]),
                                 (firmware_update.NOT_STARTED, [qidi_package.UNVERIFIED]))
                self.assertEqual(printer.snapshot(), before)
                self.assertFalse(list(printer.home.parent.rglob("escaped")))
                self.assertFalse(any(event[0] in ("stop", "dpkg") for event in printer.events))
                self.assertIsNone(printer.record())

    def test_a_failure_after_the_printer_changed_is_recorded_and_continued_on_request(self):
        # The SOC install fails: Klipper and Moonraker come back, the touchscreen stays stopped with the screen saying
        # the update didn't finish, and a boot doesn't retry it.
        printer = Printer()
        printer.dpkg_rc = 1
        result = printer.start()
        self.assertEqual(result.state, firmware_update.FAILED)
        self.assertEqual(printer.record()["failed_step"], "soc")
        self.assertEqual({unit for unit, state in printer.services.items() if state == "active"},
                         {"klipper.service", "moonraker.service"})
        self.assertEqual(printer.events[-1], ("screen", "stopped"))
        self.assertEqual((printer.backlight / "brightness").read_text(), "255\n")
        run = valve_oil_status.load_status(printer.paths).run
        self.assertEqual((run.state, run.firmware_update), (valve_oil_status.FAILED, printer.record()["id"]))
        # The waiting update's reason stays short: the failure is the run's message.
        plan = firmware_update.firmware_update_plan(printer.paths, manifest=printer.manifest, urlopen=printer.moonraker,
                                                    disk_usage=printer.disk_usage)
        self.assertIn(valve_oil_status.Blocker("last_run_failed", firmware_update.LAST_ATTEMPT_FAILED), plan.blockers)
        events = len(printer.events)
        printer.services["qidi-client.service"] = "inactive"
        self.assertEqual(printer.boot(), 0)
        self.assertEqual(len(printer.events), events)
        # Klipper runs again, so a print may start: continuing it then would stop the print.
        printer.dpkg_rc, printer.print_state = 0, "printing"
        self.assertEqual(printer.resume(), 1)
        self.assertFalse([event for event in printer.events[events:] if event[0] in ("stop", "unit")])
        self.assertEqual(printer.record()["state"], "failed")
        # Short of space for dpkg by then: it stops before dpkg, still recorded.
        printer.print_state, printer.free = "standby", 2 ** 20
        self.assertEqual(printer.resume(), 1)
        self.assertNotIn(("dpkg", "-i"), [event[:2] for event in printer.events[events:]])
        printer.free = 64 * 2 ** 30
        # Leftovers that won't go at the end don't turn a finished update into a failure.
        with patch.object(qidi_package, "remove_cache", side_effect=OSError("busy")):
            self.assertEqual(printer.resume(), 0, printer.output.getvalue())
        self.assertEqual(printer.update_firmware(), TARGET)
        self.assertIsNone(printer.record())

    def test_a_board_failure_ends_with_the_way_out_its_state_allows(self):
        # The mainboard is left on a version the new Klipper can't use: the touchscreen comes back for QIDI's own board
        # update, and the record waits for the operator.
        printer = Printer(firmware="01.01.06.03", mainboard="02.01.01.11")
        printer.mcu_outcome = "waiting"
        result = printer.start()
        self.assertEqual(result.state, firmware_update.FAILED)
        self.assertIn("touchscreen's firmware page", result.message)
        self.assertNotIn("install", [event[0] for event in printer.events])
        self.assertEqual(printer.services, dict.fromkeys(printer.services, "active"))
        # Continuing fails to start the unit: the record isn't then continued at the next boot.
        original = printer.run
        printer.run = lambda command, **kw: (Printer.done(command, rc=1) if "--no-block" in command else original(command, **kw))
        self.assertEqual(printer.resume(), 1)
        printer.run = original
        self.assertNotIn("request", printer.record())
        events = len(printer.events)
        self.assertEqual(printer.boot(), 0)
        self.assertEqual(len(printer.events), events)
        printer.mcu_outcome = "flashed"
        self.assertEqual(printer.resume(), 0, printer.output.getvalue())
        self.assert_updated(printer)

        # A board write that can't be verified keeps that board's rescue record: Klipper and the touchscreen stay
        # stopped, Moonraker serves Fluidd, and the rescue it names may run, but only once the run has stopped.
        printer = Printer(firmware="01.01.06.03", mainboard="02.01.01.11")
        printer.mcu_outcome = "unverified"
        self.assertEqual(printer.start().state, firmware_update.FAILED)
        self.assertEqual({unit for unit, state in printer.services.items() if state == "active"}, {"moonraker.service"})
        self.assertEqual(printer.events[-1], ("screen", "stopped"))
        self.assertIn("mainboard-firmware.sh rescue", printer.record()["message"])
        with self.assertRaises(InstallerError):
            safety.ensure_no_pending_firmware(printer.paths.recovery_sentinel_path)
        safety.ensure_no_pending_firmware(printer.paths.recovery_sentinel_path, allow_mainboard=True)
        failed = printer.record()
        _write_json(firmware_update.record_path(printer.paths), {**failed, "state": "running"})
        with self.assertRaises(InstallerError):
            safety.ensure_no_pending_firmware(printer.paths.recovery_sentinel_path, allow_mainboard=True)
        _write_json(firmware_update.record_path(printer.paths), failed)
        # Until the rescue has run, continuing is refused before anything stops, by ./install.sh or Update now and by
        # the unit itself, with the command to run.
        events = len(printer.events)
        self.assertEqual(printer.resume(), 1)
        self.assertIn("mainboard-firmware.sh rescue first, then run ./install.sh", printer.output.getvalue())
        _write_json(firmware_update.record_path(printer.paths), {**failed, "request": "asked"})
        with patch.dict(os.environ), printer.patched():
            self.assertEqual(firmware_update.run_firmware_update(**printer.unit_seams()), 1)
        self.assertEqual(printer.events[events:], [("is-active",)])
        self.assertEqual({unit for unit, state in printer.services.items() if state == "active"}, {"moonraker.service"})
        self.assertEqual(printer.record()["state"], "failed")
        self.assertIn("mainboard-firmware.sh rescue", firmware_update.load_result(printer.paths)["message"])
        # The rescue puts QIDI's 02.01.01.12 back, under its new USB name; continuing then finishes the update.
        (printer.home / "printer_data" / safety.MAINBOARD_RECOVERY_MARKER).unlink()
        (printer.by_id / printer.usb_name("02.01.01.11")).rename(printer.by_id / printer.usb_name("02.01.01.12"))
        printer.mainboard, printer.mcu_outcome = "02.01.01.12", "flashed"
        self.assertEqual(printer.resume(), 0, printer.output.getvalue())
        self.assert_updated(printer)

    def test_an_error_in_the_failure_path_or_a_crash_at_every_boot_still_ends_in_a_recorded_failure(self):
        printer = Printer()
        printer.dpkg_rc = 1
        printer.draw = lambda lines, reporter=None: (_ for _ in ()).throw(RuntimeError("no screen")) if (
            lines is update_screen.STOPPED) else None
        self.assertEqual(printer.start().state, firmware_update.FAILED)
        self.assertEqual(printer.record()["state"], "failed")
        self.assertIn("unexpected error", firmware_update.load_result(printer.paths)["message"])
        self.assertEqual(printer.services["moonraker.service"], "active")
        self.assertEqual(valve_oil_status.load_status(printer.paths).run.state, valve_oil_status.FAILED)

        # The power goes inside dpkg at every boot: after a few boots the update is recorded as failed and leaves the
        # printer running at the next one.
        printer = Printer()
        printer.cut = (("dpkg", "-i"), 1)
        with self.assertRaises(PowerCut):
            printer.start()
        for boot in range(firmware_update.MAX_BOOT_ATTEMPTS):
            printer.cut = (("dpkg", "-i"), sum(1 for event in printer.events if event[:2] == ("dpkg", "-i")) + 1)
            with self.assertRaises(PowerCut):
                printer.boot()
        printer.cut, events = None, len(printer.events)
        self.assertEqual(printer.boot(), 1)
        self.assertEqual(printer.record()["state"], "failed")
        self.assertEqual(len(printer.events), events)
        self.assertEqual(firmware_update.load_result(printer.paths)["state"], firmware_update.FAILED)

    def test_installer_hands_off_reports_dry_runs_and_falls_back_to_the_current_firmware(self):
        printer = Printer()
        env = {"TLTG_OPTIMIZED_PRINTER_DATA_ROOT": str(printer.paths.printer_data_root),
               "TLTG_OPTIMIZED_FIRMWARE_MANIFEST": str(printer.paths.firmware_manifest_path),
               "TLTG_OPTIMIZED_MOONRAKER_URL": printer.paths.moonraker_url}
        real_start = firmware_update.start_firmware_update

        def install(*arguments, mirror="ok"):
            printer.mirror = mirror

            def start(**kwargs):
                return real_start(**{**kwargs, "manifest": printer.manifest, "urlopen": printer.moonraker,
                                     "mirror_urlopen": printer.mirror_urlopen, "run": printer.run,
                                     "sleep": lambda seconds: None, "disk_usage": printer.disk_usage})

            output = io.StringIO()
            admission = BoardAdmission.from_json(admission_path(printer.paths).read_text())
            with printer.patched(), patch.object(firmware_update, "start_firmware_update", side_effect=start), \
                    patch("installer.runtime.cli.board_preflight", return_value=(printer.printer, admission)), \
                    patch("installer.runtime.cli.run_install") as run_install, \
                    patch("installer.runtime.cli.deliver_board_firmware") as deliver, \
                    patch("installer.runtime.cli.repair_installer_ownership"), \
                    patch("installer.runtime.cli.maybe_schedule_recommended_reboot"):
                code = main(["install", "--yes", "--plain", *arguments], bundle_root=printer.bundle, environ=env,
                            stream=output)
            return code, run_install.called, deliver.called, output.getvalue()

        # A dry run reports the whole plan and downloads, stops, and changes nothing.
        before = printer.snapshot()
        code, installed, _, output = install("--dry-run")
        self.assertEqual((code, installed, printer.mirror_requests), (0, False, 0))
        self.assertIn(f"QIDI firmware {TARGET} is available", output)
        self.assertEqual(printer.snapshot(), before)
        # Without the mirror the firmware stays, and Valve Oil is installed for the current firmware.
        code, installed, delivered, output = install(mirror="down")
        self.assertEqual((code, installed, delivered), (0, True, True), output)
        self.assertEqual(printer.update_firmware(), "01.01.06.05")
        # With it, the installer hands the install to the firmware update and installs nothing itself.
        code, installed, _, output = install()
        self.assertEqual((code, installed), (0, False), output)
        self.assert_updated(printer)
        self.assertIn("Firmware update finished", output)

        # A session that ended while the update ran sees its result at the next install, which then starts nothing.
        printer = Printer()
        self.assertEqual(printer.start(follow=False).state, firmware_update.RUNNING)
        result = firmware_update.load_result(printer.paths)
        _write_json(firmware_update.result_path(printer.paths), {**result, "reported": False})
        self.assertEqual(firmware_update.report_or_attach(paths=printer.paths, reporter=printer.reporter, environ={}), 0)
        self.assertIsNone(firmware_update.report_or_attach(paths=printer.paths, reporter=printer.reporter, environ={}))

    def test_one_update_at_a_time_and_records_this_release_cannot_carry_on(self):
        printer = Printer()
        record = {"schema": firmware_update.SCHEMA, "id": "running-one", "kind": "automatic", "state": "running",
                  "printer": "max4", "from_firmware": "01.01.06.05", "target_firmware": TARGET, "done": ["check"],
                  "current_request": "r1", "release": "26.11.1.1"}
        _write_json(firmware_update.record_path(printer.paths), record)
        original = printer.run
        printer.run = lambda command, **kw: (Printer.done(command, "active\n") if command[:2] == ["systemctl", "is-active"]
                                             else original(command, **kw))
        self.assertEqual(printer.start(follow=False).state, firmware_update.RUNNING)
        self.assertEqual(printer.record(), record)
        self.assertEqual((printer.mirror_requests, printer.events), (0, []))
        with self.assertRaises(InstallerError):
            safety.ensure_no_pending_firmware(printer.paths.recovery_sentinel_path)
        printer.run = original

        # The boot-started unit finishes while a session asks to continue: the finished record is never written back.
        printer.hooks["is-active"] = lambda: printer.boot()
        self.assertEqual(printer.resume(), 0, printer.output.getvalue())
        self.assertIsNone(printer.record())
        self.assertEqual(printer.update_firmware(), TARGET)

        # Records from another release: dropped when nothing of QIDI's package was installed (the install then plans
        # afresh), otherwise kept with the command that finishes them; a newer format names its release. Each leaves a
        # result and a stated way forward, and none is continued by this release.
        for case, changes, outcome in (("not installed", {"target_firmware": "01.01.08.01"}, None),
                                       ("installed", {"target_firmware": "01.01.08.01", "soc_attempted": True}, 1),
                                       ("newer format", {"schema": 2, "release": "27.1.1.1"}, 1)):
            with self.subTest(case=case):
                printer = Printer()
                foreign = {**record, "state": "failed", "release": "27.1.1.1", **changes}
                _write_json(firmware_update.record_path(printer.paths), foreign)
                self.assertEqual(printer.resume(), outcome)
                self.assertEqual(printer.events, [("is-active",)])
                result = firmware_update.load_result(printer.paths)
                self.assertEqual(result["id"], "running-one")
                if outcome is None:
                    self.assertIsNone(printer.record())
                else:
                    self.assertIn(firmware_update.release_command("27.1.1.1"), result["message"])
                    self.assertTrue(printer.record())
                    # The unit, at boot, writes the same way out rather than exiting without a result.
                    _write_json(firmware_update.record_path(printer.paths), {**foreign, "state": "running"})
                    self.assertEqual(printer.boot(), 1)
                    self.assertIn("27.1.1.1", firmware_update.load_result(printer.paths)["message"])


if __name__ == "__main__":
    unittest.main()
