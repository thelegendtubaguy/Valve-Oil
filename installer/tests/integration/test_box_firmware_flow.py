from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from urllib.parse import parse_qs, unquote, urlsplit

from installer.runtime import board_firmware, box_firmware, mainboard_firmware, update_check, valve_oil_status
from installer.runtime.board_images import BOARD_MANIFESTS as BOARD_MANIFEST_PATHS
from installer.runtime.auto_update import LOCK_HELD_ENV
from installer.runtime.board_images import pinned_board_image
from installer.runtime.cli import main, parse_args, resolve_runtime_paths
from installer.runtime.errors import InstallerError
from installer.runtime.manifest import load_manifest
from installer.runtime.reporter import create_reporter
from installer.runtime.runner import block_qidi_package_updates
from installer.runtime.safety import BOX_RECOVERY_MARKER, MAINBOARD_RECOVERY_MARKER, ensure_no_recovery_sentinel
from installer.tests.helpers import REPO_ROOT, build_env, temp_path
from scripts import flash_box_firmware as protocol
from scripts import thr_bootloaders
from scripts.build_box_firmware import apply_patches
from scripts.flash_mcu_firmware import MAINBOARD

USB = {"mcu_box1": "2-1.4.2", "mcu_box2": "2-1.4.1.2", "mcu": "2-1.3"}
MANIFEST = load_manifest(REPO_ROOT / "installer/package.yaml")


def synthetic_manifest(name="max4-box-valve-oil-v1.json"):
    manifest = json.loads((REPO_ROOT / "firmware" / name).read_text())
    box = protocol.image_spec(manifest)
    stock = bytearray(box["size"])
    for entry in box["patches"]:
        if "before" in entry:
            offset = int(entry["offset"], 0)
            value = bytes.fromhex(entry["before"])
            stock[offset:offset + len(value)] = value
    for entry in box["patches"]:
        if "before_sha256" in entry:
            offset = int(entry["offset"], 0)
            entry["before_sha256"] = hashlib.sha256(stock[offset:offset + len(bytes.fromhex(entry["after"]))]).hexdigest()
    image = bytearray(stock)
    for entry in box["patches"]:
        offset = int(entry["offset"], 0)
        value = bytes.fromhex(entry["after"])
        image[offset:offset + len(value)] = value
    box["stock_sha256"] = hashlib.sha256(stock).hexdigest()
    box["patched_sha256"] = hashlib.sha256(image).hexdigest()
    if "superseded" in box:
        box["superseded"] = [{"patch_id": "earlier", "version": box["stock_version"] + "-earlier",
                              "sha256": hashlib.sha256(superseded_image(stock)).hexdigest()}]
    if "predecessors" in box:
        box["predecessors"] = [{"version": "02.01.01.11", "size": len(predecessor_image(stock)),
                                "sha256": hashlib.sha256(predecessor_image(stock)).hexdigest()}]
    boot = bytes(range(256)) * 128
    manifest["bootloaders"] = [{"sha256": hashlib.sha256(boot).hexdigest(), "identity_reply": "0112"}]
    return manifest, bytes(stock), boot


def superseded_image(stock):
    return b"\x01" + stock[1:]


def predecessor_image(stock):
    # QIDI's image of an earlier version, shorter than the stock image as 02.01.01.11 is.
    return b"\x02" + stock[1:-224]


class Printer:
    def __init__(self, manifest, stock, boot, apps, *, failure=None):
        self.manifest, self.boot, self.failure = manifest, boot, failure
        images = {"stock": stock, "patched": apply_patches(stock, protocol.image_spec(manifest), "test"),
                  "superseded": superseded_image(stock), "predecessor": predecessor_image(stock),
                  "modified": b"\x03" + predecessor_image(stock)[1:]}
        self.apps = {name: images[kind] for name, kind in apps.items()}
        self.states = {"klipper.service": "active", "qidi-client.service": "active",
                       "tltg-optimized-auto-update.timer": "inactive"}
        self.original = dict(self.states)
        self.transfers = []
        self.service_changes = 0
        self.print_state = "standby"
        self.chamber = 0

    def urlopen(self, url, timeout=5):
        status = {"print_stats": {"state": self.print_state}, "webhooks": {"state": "ready"},
                  "idle_timeout": {"state": "Ready"},
                  "heaters": {"available_heaters": ["extruder", "heater_bed", "heater_generic chamber"]},
                  "extruder": {"target": 0}, "heater_bed": {"target": 0}, "heater_generic chamber": {"target": self.chamber}}
        for name, app in self.apps.items():
            kind = protocol.image_identity(app, self.manifest)
            spec = protocol.image_spec(self.manifest)
            version = (spec["superseded"][0]["version"] if kind == "superseded" else
                       spec["predecessors"][0]["version"] if kind == "predecessor" or (kind is None and app and "predecessors" in spec) else
                       spec["patched_version" if kind == "patched" else "stock_version"])
            status["mcu" if name == "mcu" else "mcu " + name] = {"mcu_version": version}
        return io.BytesIO(json.dumps({"result": {"status": status}}).encode())

    def run(self, command, **kwargs):
        command = list(command)

        def result(code=0, output=""):
            return subprocess.CompletedProcess(command, code, stdout=output, stderr="")

        if command[:4] == ["sudo", "-S", "-p", ""]:
            command = command[4:]
        if command == ["-v"]:
            return result()
        if command[:2] == ["systemctl", "show"]:
            return result(output="LoadState=loaded\nActiveState=" + self.states[command[2]] + "\n")
        if command[0] == "systemctl":
            self.service_changes += 1
            if self.failure == "restore" and command[1:] == ["start", "klipper.service"]:
                return result(1)
            self.states[command[2]] = "inactive" if command[1] == "stop" else "active"
            return result()
        assert command[1].endswith(("/scripts/flash_box_firmware.py", "/scripts/flash_mcu_firmware.py")), command
        assert all(state == "inactive" for state in self.states.values()), "serial access while services run"
        name = {usb: box for box, usb in USB.items()}[command[command.index("--usb") + 1]]
        if command[2] == "snapshot":
            destination = Path(command[command.index("--output-dir") + 1])
            destination.mkdir()
            boot = self.boot if self.failure != "unknown-boot" else bytes(0x8000)
            report = protocol.save_snapshot(destination, boot, self.apps[name] + bytes(1024), self.manifest)
            ok = report["supported_bootloader"] and report["application"] != "unrecognized"
            return result(0 if ok else protocol.UNSUPPORTED)
        if command[2] == "transfer":
            try:
                protocol.load_backup(Path(command[command.index("--backup-dir") + 1]), self.manifest)
            except ValueError:
                return result(1)
            self.transfers.append((name, "--rescue" in command))
            if getattr(self, "on_transfer", None):
                self.on_transfer(kwargs)
            if self.failure == "transfer" and not self.transfers[-1][1]:
                self.apps[name] = b""
                return result(1)
            self.apps[name] = Path(command[command.index("--image") + 1]).read_bytes()
            return result()
        raise AssertionError(command)

    def usb_state(self, usb):
        name = {usb: box for box, usb in USB.items()}[usb]
        return (protocol.APP_IDS if self.apps.get(name) else ("28e9", "018a")), "/dev/ttyACM0"

    def check_rescue_with_only_an_unsupported_bootloader(self, test, paths, reporter, marker, name, **kwargs):
        with test.assertRaises(InstallerError):
            test.execute(paths, self, reporter, "rescue", **kwargs)
        test.assertEqual((self.transfers, self.states), ([], self.original))
        test.assertFalse(marker.exists())
        # Earlier releases recorded such a rescue as unfinished and left the services stopped.
        marker.write_text(json.dumps({"services": self.original, "action": "rescue", "phase": "programming",
                                      "box": name, "usb": USB[name]}))
        self.states = dict.fromkeys(self.states, "inactive")
        self.apps[name], app = b"", self.apps[name]
        with test.assertRaises(InstallerError):
            test.execute(paths, self, reporter, "rescue", **kwargs)
        test.assertTrue(marker.exists())
        self.apps[name] = app
        test.assertEqual(test.execute(paths, self, reporter, "rescue", **kwargs), 0)
        test.assertEqual((self.transfers, self.states), ([], self.original))
        test.assertFalse(marker.exists())


class BoxFirmwareFlowTests(unittest.TestCase):
    def fixture(self, apps, **kwargs):
        root = temp_path("box-firmware-flow-")
        manifest, stock, boot = synthetic_manifest()
        bundle = root / "bundle"
        (bundle / "scripts").mkdir(parents=True)
        (bundle / "firmware").mkdir()
        (bundle / "scripts/flash_box_firmware.py").write_text("# fixture")
        (bundle / box_firmware.MANIFEST).write_text(json.dumps(manifest))
        (bundle / "installer").mkdir()
        shutil.copy2(REPO_ROOT / "installer/package.yaml", bundle / "installer/package.yaml")
        data = root / "printer_data"
        (data / "config").mkdir(parents=True)
        (data / "logs").mkdir()
        (data / "config/box.cfg").write_text("".join(f"[mcu {name}]\nserial: /dev/serial/by-id/{name}\n\n" for name in apps))
        (root / "klippy-env/bin").mkdir(parents=True)
        (root / "klippy-env/bin/python").write_text("")
        paths = resolve_runtime_paths(bundle_root=bundle, environ=build_env(data, moonraker_url="http://printer.invalid/printer/objects/query"))
        printer = Printer(manifest, stock, boot, apps, **kwargs)
        printer.stock = stock
        reporter = create_reporter(io.StringIO(), prefer_plain=True, bundle_root=REPO_ROOT, environ={})
        return paths, printer, reporter

    def execute(self, paths, printer, reporter, action, answer="FLASH\n", **kwargs):
        return box_firmware.run_box_firmware(
            paths=paths, reporter=reporter, action=action, input_stream=io.StringIO(answer),
            run=printer.run, urlopen=printer.urlopen, sleep=lambda seconds: None,
            clock=iter(range(0, 10 ** 6, 10)).__next__, resolve_usb=lambda serial: USB[serial.rsplit("/", 1)[1]],
            usb_state=printer.usb_state, **kwargs)

    def test_install_and_restore_flash_only_boxes_that_need_it_and_verify(self):
        paths, printer, reporter = self.fixture({"mcu_box1": "stock", "mcu_box2": "patched"})
        self.assertEqual(self.execute(paths, printer, reporter, "install"), 0)
        self.assertEqual(printer.transfers, [("mcu_box1", False)])
        self.assertEqual({protocol.image_identity(app, printer.manifest) for app in printer.apps.values()}, {"patched"})
        self.assertEqual(printer.states, printer.original)
        self.assertFalse((paths.printer_data_root / BOX_RECOVERY_MARKER).exists())
        verification, = (paths.printer_data_root / box_firmware.BACKUP_DIRECTORY).glob("*/mcu_box1/verification.json")
        self.assertTrue(json.loads(verification.read_text())["verified"])
        self.assertEqual(self.execute(paths, printer, reporter, "restore", boxes=[2]), 0)
        self.assertEqual(printer.transfers[-1], ("mcu_box2", False))
        self.assertEqual(protocol.image_identity(printer.apps["mcu_box2"], printer.manifest), "stock")
        self.assertEqual(printer.states, printer.original)

    def test_install_updates_earlier_tltg_firmware_from_qidi_stock_image(self):
        paths, printer, reporter = self.fixture({"mcu_box1": "superseded", "mcu_box2": "stock"})
        with self.assertRaises(InstallerError):
            self.execute(paths, printer, reporter, "install")
        self.assertEqual((printer.transfers, printer.states), ([], printer.original))
        update = paths.printer_data_root.parent / "update"
        update.mkdir()
        (update / "QD_MAX4_BOX_02.03.01.21_20260124.bin").write_bytes(printer.stock)
        self.assertEqual(self.execute(paths, printer, reporter, "install"), 0)
        self.assertEqual(sorted(printer.transfers), [("mcu_box1", False), ("mcu_box2", False)])
        self.assertEqual({protocol.image_identity(app, printer.manifest) for app in printer.apps.values()}, {"patched"})
        self.assertEqual(printer.states, printer.original)

    def test_unsupported_bootloader_is_captured_without_programming(self):
        paths, printer, reporter = self.fixture({"mcu_box1": "stock"}, failure="unknown-boot")
        self.assertEqual(self.execute(paths, printer, reporter, "install"), 0)
        self.assertEqual(printer.transfers, [])
        self.assertEqual(printer.states, printer.original)
        self.assertFalse((paths.printer_data_root / BOX_RECOVERY_MARKER).exists())
        self.assertIn("not changed:", reporter.stream.getvalue())
        printer.check_rescue_with_only_an_unsupported_bootloader(
            self, paths, reporter, paths.printer_data_root / BOX_RECOVERY_MARKER, "mcu_box1", boxes=[1])

    def test_automatic_updates_skip_a_box_captured_as_unsupported_until_a_release_admits_it(self):
        paths, printer, reporter = self.fixture({"mcu_box1": "stock"}, failure="unknown-boot")
        automatic = {LOCK_HELD_ENV: "1"}
        backups = paths.printer_data_root / box_firmware.BACKUP_DIRECTORY
        logs = paths.printer_data_root / "logs"
        # A report left for a Box no longer in box.cfg goes away.
        (logs / "tltg_box_firmware_box2.json").write_text("{}")
        self.after_install(paths, printer, reporter, environ=automatic)
        captures = list(backups.iterdir())
        self.assertEqual((len(captures), printer.transfers, printer.states), (1, [], printer.original))
        # Fluidd's banner and Logs get the shareable report.
        published = json.loads((logs / "tltg_box_firmware_box1.json").read_text())
        self.assertFalse(published["supported_bootloader"])
        self.assertEqual([path.name for path in logs.iterdir()], ["tltg_box_firmware_box1.json"])
        changes = printer.service_changes
        self.after_install(paths, printer, reporter, environ=automatic)
        self.assertEqual((list(backups.iterdir()), printer.service_changes), (captures, changes))
        self.assertTrue((logs / "tltg_box_firmware_box1.json").is_file())
        # A later release admits the bootloader the capture found.
        manifest_path = paths.bundle_root / box_firmware.MANIFEST
        admitted = json.loads(manifest_path.read_text())
        admitted["bootloaders"].append({"sha256": hashlib.sha256(bytes(0x8000)).hexdigest(), "identity_reply": "0112"})
        manifest_path.write_text(json.dumps(admitted))
        printer.manifest = admitted
        self.after_install(paths, printer, reporter, environ=automatic)
        self.assertEqual((printer.transfers, printer.states), ([("mcu_box1", False)], printer.original))
        self.assertEqual(list(logs.iterdir()), [])

    def test_unsafe_states_and_cancellation_never_touch_services(self):
        for case in ("printing", "chamber-hot", "cancel", "automatic-update-restore"):
            with self.subTest(case=case):
                paths, printer, reporter = self.fixture({"mcu_box1": "patched" if case.endswith("restore") else "stock"})
                printer.print_state = "printing" if case == "printing" else "standby"
                printer.chamber = 60 if case == "chamber-hot" else 0
                if case == "cancel":
                    self.assertEqual(self.execute(paths, printer, reporter, "install", answer="flash\n"), 1)
                elif case == "automatic-update-restore":
                    with self.assertRaisesRegex(InstallerError, "automatic-update"):
                        self.execute(paths, printer, reporter, "restore", environ={LOCK_HELD_ENV: "1"})
                else:
                    with self.assertRaises(InstallerError):
                        self.execute(paths, printer, reporter, "install")
                self.assertEqual(printer.transfers, [])
                self.assertEqual(printer.states, printer.original)

    def test_failed_write_blocks_installer_until_rescue_restores_stock(self):
        paths, printer, reporter = self.fixture({"mcu_box1": "stock"}, failure="transfer")
        with self.assertRaises(box_firmware.BoxFirmwareError):
            self.execute(paths, printer, reporter, "install")
        self.assertTrue((paths.printer_data_root / BOX_RECOVERY_MARKER).is_file())
        self.assertTrue(all(state == "inactive" for state in printer.states.values()))
        self.assertIn("rescue --box 1", reporter.stream.getvalue())
        with self.assertRaises(InstallerError):
            ensure_no_recovery_sentinel(paths.recovery_sentinel_path)
        with self.assertRaises(InstallerError):
            self.execute(paths, printer, reporter, "install")
        self.assertEqual(self.execute(paths, printer, reporter, "rescue", boxes=[1]), 0)
        self.assertEqual(printer.transfers[-1], ("mcu_box1", True))
        self.assertEqual(protocol.image_identity(printer.apps["mcu_box1"], printer.manifest), "stock")
        self.assertEqual(printer.states, printer.original)
        self.assertFalse((paths.printer_data_root / BOX_RECOVERY_MARKER).exists())

    def after_install(self, paths, printer, reporter, *, environ=None, dry_run=False):
        box_firmware.flash_box_firmware_after_install(
            paths=paths, reporter=reporter, dry_run=dry_run, environ=environ, input_stream=None,
            run=printer.run, urlopen=printer.urlopen, sleep=lambda seconds: None,
            clock=iter(range(0, 10 ** 6, 10)).__next__, resolve_usb=lambda serial: USB[serial.rsplit("/", 1)[1]],
            usb_state=printer.usb_state)

    def test_install_and_automatic_update_flash_every_box_that_needs_it_without_a_prompt(self):
        for case in ("install", "automatic-update"):
            with self.subTest(case=case):
                environ = {LOCK_HELD_ENV: "1"} if case == "automatic-update" else None
                paths, printer, reporter = self.fixture({"mcu_box1": "stock", "mcu_box2": "patched"})
                self.after_install(paths, printer, reporter, environ=environ)
                self.assertEqual(printer.transfers, [("mcu_box1", False)])
                self.assertEqual({protocol.image_identity(app, printer.manifest) for app in printer.apps.values()}, {"patched"})
                self.assertEqual(printer.states, printer.original)
                self.after_install(paths, printer, reporter, environ=environ)
                self.assertEqual(len(printer.transfers), 1)

    def test_restore_holds_a_box_on_qidi_firmware_until_the_next_release(self):
        paths, printer, reporter = self.fixture({"mcu_box1": "stock", "mcu_box2": "stock"})
        self.after_install(paths, printer, reporter)
        self.assertEqual(self.execute(paths, printer, reporter, "restore", boxes=[1]), 0)
        self.assertEqual(len(printer.transfers), 3)
        # Installs and update checks of the same release leave it on QIDI's firmware.
        for environ in (None, {LOCK_HELD_ENV: "1"}):
            self.after_install(paths, printer, reporter, environ=environ)
        self.assertEqual(len(printer.transfers), 3)
        self.assertEqual(protocol.image_identity(printer.apps["mcu_box1"], printer.manifest), "stock")
        # Installing the next release ends the hold.
        package = paths.installer_root / "package.yaml"
        package.write_text(package.read_text().replace('version: "', 'version: "99.', 1))
        self.after_install(paths, printer, reporter)
        self.assertEqual(printer.transfers[-1], ("mcu_box1", False))
        self.assertEqual({protocol.image_identity(app, printer.manifest) for app in printer.apps.values()}, {"patched"})
        # So does the operator asking for that Box with box-firmware.sh install.
        self.assertEqual(self.execute(paths, printer, reporter, "restore", boxes=[2]), 0)
        self.assertEqual(self.execute(paths, printer, reporter, "install", boxes=[2]), 0)
        self.assertEqual(printer.transfers[-1], ("mcu_box2", False))
        self.assertFalse((paths.printer_data_root / box_firmware.HOLD_FILE).exists())

    def test_install_leaves_boxes_alone_when_it_cannot_flash_safely(self):
        for case in ("dry-run", "chamber-hot", "no-stock-image"):
            with self.subTest(case=case):
                paths, printer, reporter = self.fixture(
                    {"mcu_box1": "superseded" if case == "no-stock-image" else "stock"})
                printer.chamber = 60 if case == "chamber-hot" else 0
                self.after_install(paths, printer, reporter, dry_run=case == "dry-run")
                self.assertEqual((printer.transfers, printer.states), ([], printer.original))
                if case in ("chamber-hot", "no-stock-image"):
                    self.assertIn("Box firmware not updated", reporter.stream.getvalue())

    def test_every_service_is_started_again_when_one_fails_to_start(self):
        paths, printer, reporter = self.fixture({"mcu_box1": "stock"}, failure="restore")
        with self.assertRaises(box_firmware.BoxFirmwareError):
            self.after_install(paths, printer, reporter)
        self.assertEqual(printer.transfers, [("mcu_box1", False)])
        self.assertEqual(printer.states, {**printer.original, "klipper.service": "inactive"})
        self.assertTrue((paths.printer_data_root / BOX_RECOVERY_MARKER).is_file())

    def test_install_stops_with_rescue_instructions_after_an_unverified_flash(self):
        paths, printer, reporter = self.fixture({"mcu_box1": "stock"}, failure="transfer")
        with self.assertRaises(box_firmware.BoxFirmwareError):
            self.after_install(paths, printer, reporter)
        self.assertTrue((paths.printer_data_root / BOX_RECOVERY_MARKER).is_file())
        self.assertTrue(all(state == "inactive" for state in printer.states.values()))
        self.assertIn("rescue --box 1", reporter.stream.getvalue())

    def test_box_firmware_script_arguments(self):
        self.assertEqual(parse_args(["box-firmware", "install", "--box", "1"]).box, [1])
        for argv in (["box-firmware"], ["box-firmware", "install", "--yes"], ["install", "--box", "1"], ["install", "install"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                parse_args(argv)


class MainboardFirmwareFlowTests(unittest.TestCase):
    def fixture(self, kind, *, soc_file="QD_MAX4_SOC_01.01.07.01_20261016_Release_NA", firmware="01.01.07.01", **kwargs):
        root = temp_path("mainboard-firmware-flow-")
        manifest, stock, boot = synthetic_manifest("max4-mcu-valve-oil-v1.json")
        bundle = root / "bundle"
        (bundle / "scripts").mkdir(parents=True)
        (bundle / "firmware").mkdir()
        (bundle / "scripts/flash_mcu_firmware.py").write_text("# fixture")
        (bundle / mainboard_firmware.MANIFEST).write_text(json.dumps(manifest))
        (bundle / "installer").mkdir()
        shutil.copy2(REPO_ROOT / "installer/package.yaml", bundle / "installer/package.yaml")
        data = root / "printer_data"
        (data / "config").mkdir(parents=True)
        (data / "config/MCU_ID.cfg").write_text("[mcu]\nserial: /dev/serial/by-id/mcu\n")
        (data / "firmware_manifest.json").write_text(json.dumps({"SOC": {"file": soc_file, "version": firmware}}))
        (root / "klippy-env/bin").mkdir(parents=True)
        (root / "klippy-env/bin/python").write_text("")
        paths = resolve_runtime_paths(bundle_root=bundle, environ=build_env(data, moonraker_url="http://printer.invalid/printer/objects/query"))
        printer = Printer(manifest, stock, boot, {"mcu": kind}, **kwargs)
        printer.stock = stock
        reporter = create_reporter(io.StringIO(), prefer_plain=True, bundle_root=REPO_ROOT, environ={})
        return paths, printer, reporter

    def execute(self, paths, printer, reporter, action, answer="FLASH\n", **kwargs):
        return mainboard_firmware.run_mainboard_firmware(
            paths=paths, reporter=reporter, action=action, input_stream=io.StringIO(answer),
            run=printer.run, urlopen=printer.urlopen, sleep=lambda seconds: None,
            clock=iter(range(0, 10 ** 6, 10)).__next__, resolve_usb=lambda serial: USB[serial.rsplit("/", 1)[1]],
            usb_state=printer.usb_state, **kwargs)

    def identity(self, printer):
        return protocol.image_identity(printer.apps["mcu"], printer.manifest)

    def test_install_and_restore_flash_only_when_needed_and_verify(self):
        paths, printer, reporter = self.fixture("stock")
        self.assertEqual(self.execute(paths, printer, reporter, "install"), 0)
        self.assertEqual((printer.transfers, self.identity(printer)), ([("mcu", False)], "patched"))
        self.assertEqual(printer.states, printer.original)
        self.assertFalse((paths.printer_data_root / MAINBOARD_RECOVERY_MARKER).exists())
        verification, = (paths.printer_data_root / mainboard_firmware.BACKUP_DIRECTORY).glob("*/verification.json")
        self.assertTrue(json.loads(verification.read_text())["verified"])
        self.assertEqual(self.execute(paths, printer, reporter, "install"), 0)
        self.assertEqual(len(printer.transfers), 1)
        self.assertEqual(self.execute(paths, printer, reporter, "restore"), 0)
        self.assertEqual((printer.transfers[-1], self.identity(printer)), (("mcu", False), "stock"))
        self.assertEqual(printer.states, printer.original)

    def test_install_updates_the_earlier_fix_from_qidi_stock_image(self):
        paths, printer, reporter = self.fixture("superseded")
        with self.assertRaises(InstallerError):
            self.execute(paths, printer, reporter, "install")
        self.assertEqual((printer.transfers, printer.states), ([], printer.original))
        update = paths.printer_data_root.parent / "update"
        update.mkdir()
        (update / "QD_MAX4_MCU_02.01.01.12_20260609.bin").write_bytes(printer.stock)
        self.assertEqual(self.execute(paths, printer, reporter, "install"), 0)
        self.assertEqual((printer.transfers, self.identity(printer)), ([("mcu", False)], "patched"))
        self.assertEqual(printer.states, printer.original)

    def test_qidi_predecessor_is_replaced_only_once_the_printer_runs_the_target_firmware(self):
        def stock_in_update(paths, printer):
            (paths.printer_data_root.parent / "update").mkdir()
            (paths.printer_data_root.parent / "update/QD_MAX4_MCU_02.01.01.12_20260609.bin").write_bytes(printer.stock)

        # On QIDI firmware whose Klipper expects 02.01.01.11, the mainboard is left as it is: read by its reported
        # version, or by readback when the caller stopped the services.
        for action, services_stopped in (("install", False), ("install", True), ("restore", False)):
            with self.subTest(action=action, services_stopped=services_stopped):
                paths, printer, reporter = self.fixture(
                    "predecessor", soc_file="QD_MAX4_SOC_01.01.06.03_20260507_Release_NA", firmware="01.01.06.03")
                stock_in_update(paths, printer)
                if services_stopped:
                    printer.states = printer.original = dict.fromkeys(printer.states, "inactive")
                    printer.urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("Moonraker is stopped"))
                outcomes = {}
                self.assertEqual(self.execute(paths, printer, reporter, action, confirmed=True,
                                              services_stopped=services_stopped, outcomes=outcomes), 0)
                self.assertEqual((printer.transfers, outcomes["mcu"][0], printer.states), ([], "waiting", printer.original))
        # On the target firmware it gets the pinned image, built from QIDI's stock image for that firmware, which is
        # also what rescue restores; without one nothing is changed.
        paths, printer, reporter = self.fixture("predecessor")
        with self.assertRaises(InstallerError):
            self.execute(paths, printer, reporter, "install")
        self.assertEqual((printer.transfers, printer.states), ([], printer.original))
        stock_in_update(paths, printer)
        self.assertEqual(self.execute(paths, printer, reporter, "install"), 0)
        self.assertEqual((printer.transfers, self.identity(printer)), ([("mcu", False)], "patched"))
        before, = (paths.printer_data_root / mainboard_firmware.BACKUP_DIRECTORY).glob("*/before/application.bin")
        self.assertEqual(before.read_bytes(), predecessor_image(printer.stock))
        # A mainboard pinned to QIDI's image (the Plus 5) gets that image itself.
        paths, printer, reporter = self.fixture("predecessor", soc_file="QD_PLUS5_SOC_01.01.02.01_20261016_Release_NA",
                                                firmware="01.01.02.01")
        stock_in_update(paths, printer)
        self.assertEqual(self.execute(paths, printer, reporter, "install"), 0)
        self.assertEqual((printer.transfers, self.identity(printer)), ([("mcu", False)], "stock"))
        # Another image reporting that version, or an unknown bootloader, is refused before any reset.
        for app, failure in (("modified", None), ("predecessor", "unknown-boot")):
            with self.subTest(app=app, failure=failure):
                paths, printer, reporter = self.fixture(app, failure=failure)
                stock_in_update(paths, printer)
                self.assertEqual(self.execute(paths, printer, reporter, "install"), 0)
                self.assertEqual((printer.transfers, printer.states), ([], printer.original))
                self.assertIn("not changed:", reporter.stream.getvalue())

    def test_unsupported_bootloader_is_captured_without_programming(self):
        paths, printer, reporter = self.fixture("stock", failure="unknown-boot")
        self.assertEqual(self.execute(paths, printer, reporter, "install"), 0)
        self.assertEqual((printer.transfers, printer.states), ([], printer.original))
        self.assertFalse((paths.printer_data_root / MAINBOARD_RECOVERY_MARKER).exists())
        self.assertIn("not changed:", reporter.stream.getvalue())
        printer.check_rescue_with_only_an_unsupported_bootloader(
            self, paths, reporter, paths.printer_data_root / MAINBOARD_RECOVERY_MARKER, "mcu")

    def test_unsafe_states_and_cancellation_never_touch_services(self):
        for case in ("printing", "chamber-hot", "cancel", "automatic-update-restore", "unsupported-firmware"):
            with self.subTest(case=case):
                paths, printer, reporter = self.fixture(
                    "stock", firmware="09.09.09.09" if case == "unsupported-firmware" else "01.01.07.01")
                printer.print_state = "printing" if case == "printing" else "standby"
                printer.chamber = 60 if case == "chamber-hot" else 0
                if case == "cancel":
                    self.assertEqual(self.execute(paths, printer, reporter, "install", answer="flash\n"), 1)
                elif case == "automatic-update-restore":
                    with self.assertRaisesRegex(InstallerError, "automatic-update"):
                        self.execute(paths, printer, reporter, "restore", environ={LOCK_HELD_ENV: "1"})
                else:
                    with self.assertRaises(InstallerError):
                        self.execute(paths, printer, reporter, "install")
                self.assertEqual((printer.transfers, printer.states), ([], printer.original))

    def test_installs_flash_without_a_prompt_also_with_services_stopped_by_the_caller(self):
        for services_stopped in (False, True):
            with self.subTest(services_stopped=services_stopped):
                paths, printer, reporter = self.fixture("stock")
                if services_stopped:
                    printer.states = printer.original = dict.fromkeys(printer.states, "inactive")
                    printer.urlopen = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("Moonraker is stopped"))
                outcomes, seen = {}, []

                def hang_up(kwargs):
                    seen.append((kwargs.get("start_new_session"), signal.getsignal(signal.SIGHUP)))
                    reporter.stream.close()  # the SSH session is gone
                    os.kill(os.getpid(), signal.SIGINT)  # and Ctrl-C was pressed

                printer.on_transfer = hang_up
                try:
                    self.assertEqual(self.execute(paths, printer, reporter, "install", answer="", confirmed=True,
                                                  services_stopped=services_stopped, environ={LOCK_HELD_ENV: "1"},
                                                  outcomes=outcomes), 0)
                except KeyboardInterrupt:
                    self.fail("Ctrl-C stopped the flash mid-write")
                self.assertEqual(seen, [(True, signal.SIG_IGN)])
                self.assertEqual((printer.transfers, self.identity(printer)), ([("mcu", False)], "patched"))
                self.assertEqual((outcomes["mcu"][0], printer.states), ("flashed", printer.original))
                if services_stopped:
                    self.assertEqual(printer.service_changes, 0)

    def test_plus5_mainboard_gets_only_qidi_firmware(self):
        # The Plus 5 has no closed-loop drivers, so its mainboard is pinned to QIDI's image.
        plus5 = {"soc_file": "QD_PLUS5_SOC_01.01.02.01_20261016_Release_NA", "firmware": "01.01.02.01"}
        paths, printer, reporter = self.fixture("stock", **plus5)
        self.assertEqual(self.execute(paths, printer, reporter, "install"), 0)
        self.assertEqual((printer.transfers, printer.states), ([], printer.original))
        paths, printer, reporter = self.fixture("patched", **plus5)
        update = paths.printer_data_root.parent / "update"
        update.mkdir()
        (update / "QD_PLUS5_MCU_02.01.01.12_20261016.bin").write_bytes(printer.stock)
        self.assertEqual(self.execute(paths, printer, reporter, "install"), 0)
        self.assertEqual((printer.transfers, self.identity(printer)), ([("mcu", False)], "stock"))

    def test_failed_write_blocks_installer_until_rescue_restores_stock(self):
        paths, printer, reporter = self.fixture("stock", failure="transfer")
        with self.assertRaises(mainboard_firmware.MainboardFirmwareError):
            self.execute(paths, printer, reporter, "install")
        self.assertTrue((paths.printer_data_root / MAINBOARD_RECOVERY_MARKER).is_file())
        self.assertTrue(all(state == "inactive" for state in printer.states.values()))
        self.assertIn("mainboard-firmware.sh rescue", reporter.stream.getvalue())
        with self.assertRaises(InstallerError):
            ensure_no_recovery_sentinel(paths.recovery_sentinel_path)
        with self.assertRaises(InstallerError):
            self.execute(paths, printer, reporter, "install")
        self.assertEqual(self.execute(paths, printer, reporter, "rescue"), 0)
        self.assertEqual((printer.transfers[-1], self.identity(printer)), (("mcu", True), "stock"))
        self.assertEqual(printer.states, printer.original)
        self.assertFalse((paths.printer_data_root / MAINBOARD_RECOVERY_MARKER).exists())

    def test_transfer_tool_programs_only_images_pinned_for_the_target_firmware(self):
        # A Plus 5 mainboard is pinned to QIDI's stock image, so this project's build is refused before any serial access.
        manifest, stock, boot = synthetic_manifest("max4-mcu-valve-oil-v1.json")
        root = temp_path("mainboard-transfer-pins-")
        (root / "manifest.json").write_text(json.dumps(manifest))
        (root / "backup").mkdir()
        protocol.save_snapshot(root / "backup", boot, stock + bytes(1024), manifest)
        images = {"stock": stock, "patched": apply_patches(stock, protocol.image_spec(manifest), "test"),
                  "predecessor": predecessor_image(stock)}
        for build, image in images.items():
            (root / build).write_bytes(image)
        board = replace(MAINBOARD, manifest=root / "manifest.json")
        # QIDI's predecessor image is admitted only as what a mainboard runs; it is never programmed.
        cases = ([(None, build, build != "predecessor") for build in images]
                 + [("09.09.09.09", build, False) for build in images])
        for printer in load_manifest(REPO_ROOT / "installer/package.yaml").printers:
            pinned = pinned_board_image(REPO_ROOT, printer, "mcu")
            cases += [(pinned.firmware, build, build in ("stock", pinned.build)) for build in images]
        seen = []

        def serial_access(usb):
            seen.append((signal.getsignal(signal.SIGHUP), signal.getsignal(signal.SIGINT)))
            raise RuntimeError("stop before serial")

        argv = ["transfer", "--usb", USB["mcu"], "--image", str(root / "stock"), "--backup-dir", str(root / "backup"),
                "--confirm-write", "--printer-idle-confirmed"]
        with patch.object(protocol, "require_ownership"), patch.object(protocol, "usb_state", side_effect=serial_access), \
                patch.dict(sys.modules, {"serial": types.ModuleType("serial")}), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(protocol.main(argv, board=board), 1)
        self.assertEqual(seen, [(signal.SIG_IGN, signal.SIG_IGN)])
        self.assertIsNot(signal.getsignal(signal.SIGINT), signal.SIG_IGN)
        for firmware, build, admitted in cases:
            with self.subTest(firmware=firmware, build=build):
                argv = ["transfer", "--usb", USB["mcu"], "--image", str(root / build), "--backup-dir", str(root / "backup"),
                        "--confirm-write", "--printer-idle-confirmed", *(["--firmware", firmware] if firmware else [])]
                with patch.object(protocol, "require_ownership", side_effect=RuntimeError("stop before serial")) as ownership, \
                        patch.object(protocol, "usb_state", side_effect=AssertionError("serial access")), \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(protocol.main(argv, board=board), 1)
                self.assertEqual(ownership.called, admitted)

    def test_a_board_leaving_usb_while_it_is_looked_up_reads_as_not_there_yet(self):
        # A bootloader starting the application disconnects its USB device; the readback that follows a transfer
        # polls for the application's device through that moment and must keep waiting.
        root = temp_path("usb-devices-")
        (root / "2-1.3/2-1.3:1.0/tty/ttyACM0").mkdir(parents=True)
        for name, value in (("idVendor", "1d50\n"), ("idProduct", "614e\n")):
            (root / "2-1.3" / name).write_text(value)
        listing = Path.glob

        def leaving(path, pattern):
            if path.name == "tty":
                raise FileNotFoundError(2, "No such file or directory", str(path))
            return listing(path, pattern)

        with patch.object(protocol, "USB_DEVICES", root):
            self.assertEqual(protocol.usb_state("2-1.3"), (("1d50", "614e"), "/dev/ttyACM0"))
            with patch.object(Path, "glob", leaving):
                self.assertEqual(protocol.usb_state("2-1.3"), (("1d50", "614e"), None))
            self.assertEqual(protocol.usb_state("2-1.9"), (None, None))


BOARD_MANIFESTS = {kind: json.loads((REPO_ROOT / path).read_text()) for kind, path in BOARD_MANIFEST_PATHS.items()}
UNKNOWN = "0" * 64
# A Box's by-id path carries its MCU serial; it must never reach a link, a warning, or the admission record.
BOX_SERIAL = "usb-Klipper_stm32f401xc_SECRETSERIAL0042-if00"


class Boards:
    """A printer whose THR, mainboard, and USB Boxes answer the board tools' read-only snapshots, behind Moonraker.

    Each board is (bootloader, application): bootloader "admitted" or "unknown"; application "stock", "patched",
    "unknown", or a QIDI version string: the manifest's predecessor image of that version, or an image the manifests
    don't have ("<version>+modified", or a version without a predecessor).
    """

    def __init__(self, boards):
        self.boards = dict(boards)
        self.states = {"klipper.service": "active", "qidi-client.service": "active",
                       "tltg-optimized-auto-update.timer": "active"}
        self.original = dict(self.states)
        self.snapshots, self.stops = [], 0
        self.on_snapshot = self.fail_start = None
        self.print_state = "standby"

    def kind(self, name):
        return "box" if name.startswith("box") else name

    def version(self, name):
        _, app = self.boards[name]
        spec = image_spec_of(self.kind(name))
        return spec.get(app + "_version", app.split("+")[0] if app != "unknown" else spec["stock_version"])

    def report(self, name):
        boot, app = self.boards[name]
        kind = self.kind(name)
        spec = image_spec_of(kind)
        bootloaders = (sorted(thr_bootloaders.ADMITTED_BOOTLOADERS) if kind == "thr"
                       else [entry["sha256"] for entry in BOARD_MANIFESTS[kind]["bootloaders"]])
        return {"bootloader_sha256": bootloaders[0] if boot == "admitted" else UNKNOWN,
                "supported_bootloader": boot == "admitted",
                "application_sha256": spec.get(app + "_sha256") or next(
                    (entry["sha256"] for entry in spec.get("predecessors", ()) if entry["version"] == app), UNKNOWN),
                # A firmware someone built themselves names the build machine in its version and build strings.
                "version": self.version(name) if app != "unknown" else "v0.12.0-builder@hostname-secret",
                "build_versions": "gcc: builder@hostname-secret", "bootloader_page_sha256": [UNKNOWN] * 16,
                "constants": {"MCU": "stm32", "BUILD_HOST": "hostname-secret"}}

    def urlopen(self, url, timeout=5):
        from urllib.parse import unquote, urlsplit
        status = {}
        for item in urlsplit(url).query.split("&"):
            name = unquote(item.split("=", 1)[0])
            if name.startswith("mcu"):
                board = {"mcu": "mcu", "mcu THR": "thr"}.get(name) or "box" + name.rsplit("box", 1)[-1]
                status[name] = {"mcu_version": self.version(board)} if board in self.boards else {}
            if name.startswith("closed_loop") and getattr(self, "drivers", None):
                status[name] = {"stepper_version": self.drivers}
        status.update({"print_stats": {"state": self.print_state}, "webhooks": {"state": "ready"},
                       "idle_timeout": {"state": "Ready"},
                       "heaters": {"available_heaters": ["extruder", "heater_bed", "heater_generic chamber"]},
                       "extruder": {"target": 0}, "heater_bed": {"target": 0}, "heater_generic chamber": {"target": 0}})
        return io.BytesIO(json.dumps({"result": {"status": status}}).encode())

    def run(self, command, **kwargs):
        command = list(command)
        if command[:4] == ["sudo", "-S", "-p", ""]:
            command = command[4:]
        if command == ["-v"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(command, 0, stdout="LoadState=loaded\nActiveState="
                                               + self.states[command[2]] + "\n", stderr="")
        if command[0] == "systemctl":
            self.stops += command[1] == "stop"
            if command[1:] == ["start", self.fail_start]:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
            self.states[command[2]] = "inactive" if command[1] == "stop" else "active"
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        assert command[2] == "snapshot", "a board was programmed: " + str(command)
        if self.on_snapshot:
            self.on_snapshot(kwargs)
        assert all(state == "inactive" for state in self.states.values()), "serial access while services run"
        tool = Path(command[1]).name
        name = ("thr" if tool == "flash_thr_firmware.py" else "mcu" if tool == "flash_mcu_firmware.py"
                else {usb: box for box, usb in BOARD_USB.items()}[command[command.index("--usb") + 1]])
        self.snapshots.append(name)
        before = Path(command[command.index("--output-dir") + 1])
        before.mkdir()
        report = self.report(name)
        (before / "report.json").write_text(json.dumps(report))
        admitted = report["supported_bootloader"] and report["application_sha256"] != UNKNOWN
        return subprocess.CompletedProcess(command, 0 if admitted else 3, stdout="", stderr="")


BOARD_USB = {"box1": "2-1.4.2", "box2": "2-1.4.1.2", "box3": "2-1.4.1.3", "mcu": "2-1.3"}


def image_spec_of(kind):
    return BOARD_MANIFESTS[kind][kind]


class BoardAdmissionTests(unittest.TestCase):
    """Installs read every board before changing anything, refuse a new install with a board this release doesn't
    admit, and bring admitted boards to their pinned images without a question."""

    def fixture(self, boards, *, firmware="01.01.07.01", existing=False):
        root = temp_path("board-admission-")
        data = root / "printer_data"
        (data / "config").mkdir(parents=True)
        (data / "logs").mkdir()
        shutil.copy2(REPO_ROOT / "config/printer.cfg", data / "config/printer.cfg")
        (data / "config/MCU_ID.cfg").write_text("[mcu]\nserial: /dev/serial/by-id/mcu\n")
        (data / "config/box.cfg").write_text("".join(
            f"[mcu mcu_{name}]\nserial: /dev/serial/by-id/{BOX_SERIAL}-{name}\n\n" for name in boards if name.startswith("box")))
        (data / "firmware_manifest.json").write_text(json.dumps(
            {"SOC": {"file": f"QD_MAX4_SOC_{firmware}_20261016_Release_NA", "version": firmware}}))
        (root / "klippy-env/bin").mkdir(parents=True)
        (root / "klippy-env/bin/python").write_text("")
        if existing:
            (data / "config/tltg_optimized_state.yaml").write_text("")
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=build_env(
            data, moonraker_url="http://printer.invalid/printer/objects/query"))
        printer = Boards(boards)
        reporter = create_reporter(io.StringIO(), prefer_plain=True, bundle_root=REPO_ROOT, environ={})
        return paths, printer, reporter

    def seams(self, printer):
        return {"run": printer.run, "urlopen": printer.urlopen, "sleep": lambda seconds: None,
                "clock": iter(range(0, 10 ** 6, 10)).__next__,
                "resolve_usb": lambda serial: BOARD_USB["mcu" if serial.endswith("/mcu") else serial.rsplit("-", 1)[1]]}

    def preflight(self, paths, printer, reporter, **kwargs):
        return board_firmware.board_preflight(paths=paths, reporter=reporter, manifest=MANIFEST,
                                              **{**self.seams(printer), **kwargs})

    def deliver(self, paths, printer, reporter, board_printer, admission, **kwargs):
        """Delivery with the board flows replaced by records of what they were asked; the flows have their own tests."""
        calls = []

        def thr(**kw):
            calls.append(("thr", kw.get("input_stream"), kw.get("pinned").sha256))
            return "flashed"

        def mainboard(**kw):
            calls.append(("mcu", kw["confirmed"], kw["pinned"].build))
            kw["outcomes"]["mcu"] = ("flashed", "")
            return 0

        def boxes(**kw):
            calls.append(("box", tuple(kw["boxes"]), kw["input_stream"]))
            kw["outcomes"].update({f"mcu_box{number}": ("flashed", "") for number in kw["boxes"]})
            return 0

        with patch.object(board_firmware, "flash_thr_firmware", side_effect=thr), \
                patch.object(board_firmware, "run_mainboard_firmware", side_effect=mainboard), \
                patch.object(board_firmware, "flash_box_firmware_after_install", side_effect=boxes):
            delivery = board_firmware.deliver_board_firmware(
                paths=paths, reporter=reporter, manifest=MANIFEST, printer=board_printer, admission=admission,
                input_stream=None, **kwargs, **{key: value for key, value in self.seams(printer).items()})
        return calls, delivery

    def test_new_install_with_a_board_this_release_does_not_admit_is_refused_before_any_change(self):
        for board, unadmitted in (("thr", ("unknown", "stock")), ("mcu", ("admitted", "unknown")),
                                  ("box1", ("unknown", "stock"))):
            with self.subTest(board=board):
                boards = {"thr": ("admitted", "stock"), "mcu": ("admitted", "stock"), "box1": ("admitted", "stock")}
                boards[board] = unadmitted
                paths, printer, reporter = self.fixture(boards)
                with self.assertRaises(board_firmware.BoardAdmissionError):
                    self.preflight(paths, printer, reporter)
                # Every board was read, nothing programmed, and the services run again.
                self.assertEqual((sorted(printer.snapshots), printer.states), (sorted(boards), printer.original))
                self.assertIsNone(board_firmware.load_board_admission(paths))
                self.assertFalse(board_firmware.existing_installation(paths, MANIFEST))
                output = reporter.stream.getvalue()
                self.assertNotIn("SECRETSERIAL", output)
                link = next(word for word in output.split() if word.startswith(board_firmware.ISSUE_FORM))
                self.assertLessEqual(len(link), board_firmware.MAX_ISSUE_URL)
                fields = parse_qs(urlsplit(link).query)
                self.assertEqual((fields["template"], fields["printer"], fields["firmware"], fields["box"]),
                                 (["printer-support.yml"], ["QIDI Max 4"], ["01.01.07.01"], ["One USB Box"]))
                self.assertIn("SOC: 01.01.07.01", fields["printer_details"][0])
                self.assertEqual(json.loads(fields["board_report"][0])["bootloader_sha256"],
                                 printer.report(board)["bootloader_sha256"])
                self.assertNotIn("hostname-secret", unquote(link))
                # The board's shareable report is kept.
                self.assertTrue(any(paths.printer_data_root.glob(".tltg_*_firmware_backups/**/before/report.json")))

    def test_reading_the_boards_survives_the_terminal_and_starts_every_service_again(self):
        paths, printer, reporter = self.fixture({"thr": ("admitted", "stock"), "mcu": ("admitted", "stock")},
                                                existing=True)
        seen = []

        def hang_up(kwargs):
            seen.append((kwargs.get("start_new_session"), signal.getsignal(signal.SIGHUP)))
            reporter.stream.close()  # the SSH session is gone
            os.kill(os.getpid(), signal.SIGINT)  # and Ctrl-C was pressed

        printer.on_snapshot, printer.fail_start = hang_up, "klipper.service"
        interrupt = signal.getsignal(signal.SIGINT)
        try:
            with self.assertRaises(board_firmware.BoardAdmissionError):
                self.preflight(paths, printer, reporter)
        except KeyboardInterrupt:
            self.fail("Ctrl-C stopped reading the boards with the services stopped")
        self.assertEqual((printer.snapshots, set(seen)), (["thr", "mcu"], {(True, signal.SIG_IGN)}))
        # Klipper wouldn't start; the touchscreen and the update timer run again anyway.
        self.assertEqual(printer.states, {**printer.original, "klipper.service": "inactive"})
        self.assertIs(signal.getsignal(signal.SIGINT), interrupt)

    def test_an_install_after_uninstall_is_new_and_an_enrolled_printer_is_not(self):
        from installer.runtime.auto_update import enrollment_path
        from installer.runtime.backup import build_install_backup_label, create_config_backup, utc_now

        paths, printer, reporter = self.fixture({"thr": ("unknown", "stock"), "mcu": ("admitted", "stock")})
        # Uninstall keeps the installer's backups but removes the state, the record, and enrollment.
        create_config_backup(printer_data_root=paths.printer_data_root, source_directory="config",
                             backup_label=build_install_backup_label(
                                 label_prefix=MANIFEST.backup.label_prefix, firmware_version="01.01.07.01",
                                 package_version=MANIFEST.package.version, moment=utc_now()))
        with self.assertRaises(board_firmware.BoardAdmissionError):
            self.preflight(paths, printer, reporter)
        # An enrolled printer whose config a QIDI update emptied is still Valve Oil's, and is updated in today's mode.
        enrollment_path(paths).write_text("1\n")
        _, admission = self.preflight(paths, printer, reporter)
        self.assertFalse(admission.new_install or admission.delivery_admitted)

    def test_new_install_with_a_box_that_is_not_connected_says_so(self):
        paths, printer, reporter = self.fixture({"thr": ("admitted", "stock"), "mcu": ("admitted", "stock"),
                                                 "box1": ("admitted", "stock"), "box2": ("admitted", "stock")})
        resolve = self.seams(printer)["resolve_usb"]
        with self.assertRaisesRegex(board_firmware.BoardAdmissionError, "box.cfg is not connected"):
            self.preflight(paths, printer, reporter, resolve_usb=lambda serial: None if serial.endswith("box2") else resolve(serial))
        output = reporter.stream.getvalue()
        self.assertIn("Box 2 is listed in box.cfg but is not connected", output)
        self.assertNotIn("once Klipper is ready", output)

    def test_existing_install_with_an_unadmitted_thr_is_updated_in_todays_mode(self):
        paths, printer, reporter = self.fixture({"thr": ("unknown", "stock"), "mcu": ("admitted", "stock"),
                                                 "box1": ("admitted", "stock")}, existing=True)
        board_printer, admission = self.preflight(paths, printer, reporter)
        self.assertEqual(admission.board("thr").state, board_firmware.UNRECOGNIZED_BOOTLOADER)
        self.assertFalse(admission.delivery_admitted or block_qidi_package_updates(admission))
        calls, delivery = self.deliver(paths, printer, reporter, board_printer, admission)
        # No THR or mainboard firmware; the Box keeps getting Valve Oil's Box firmware as before.
        self.assertEqual(calls, [("box", (1,), None)])
        warning = json.loads((paths.printer_data_root / "logs/tltg_thr_firmware.json").read_text())
        self.assertEqual(warning["issue_url"], admission.board("thr").issue_url)
        self.assertNotIn("SECRETSERIAL", (paths.printer_data_root / board_firmware.ADMISSION_FILE).read_text())
        # The THR is replaced by one this release admits, reporting the same version.
        printer.boards.update(thr=("admitted", "stock"), box1=("admitted", "patched"))
        automatic = {LOCK_HELD_ENV: "1"}
        reads = len(printer.snapshots)
        # Automatic updates don't stop Klipper to read a board found unsupported again; they check the earlier read
        # against each release.
        _, admission = self.preflight(paths, printer, reporter, environ=automatic)
        self.assertEqual(admission.board("thr").state, board_firmware.UNRECOGNIZED_BOOTLOADER)
        with patch.object(thr_bootloaders, "ADMITTED_BOOTLOADERS", thr_bootloaders.ADMITTED_BOOTLOADERS | {UNKNOWN}):
            _, admission = self.preflight(paths, printer, reporter, environ=automatic)
        self.assertEqual(admission.board("thr").state, board_firmware.NEEDS_IMAGE)
        self.assertNotIn("thr", printer.snapshots[reads:])
        # An install the operator runs reads it again and delivers firmware.
        reads = len(printer.snapshots)
        board_printer, admission = self.preflight(paths, printer, reporter)
        self.assertTrue(admission.delivery_admitted and block_qidi_package_updates(admission))
        self.assertEqual(printer.snapshots[reads:], ["thr", "mcu"])
        calls, _ = self.deliver(paths, printer, reporter, board_printer, admission)
        self.assertEqual([call[0] for call in calls], ["thr", "mcu"])
        self.assertFalse((paths.printer_data_root / "logs/tltg_thr_firmware.json").exists())

    def test_admitted_boards_get_their_pinned_images_without_a_question_and_current_ones_are_left_alone(self):
        paths, printer, reporter = self.fixture({"thr": ("admitted", "stock"), "mcu": ("admitted", "stock"),
                                                 "box1": ("admitted", "patched"), "box2": ("admitted", "stock")})
        board_printer, admission = self.preflight(paths, printer, reporter)
        self.assertTrue(admission.admitted and block_qidi_package_updates(admission))
        calls, delivery = self.deliver(paths, printer, reporter, board_printer, admission)
        pinned_thr = pinned_board_image(REPO_ROOT, board_printer, "thr")
        self.assertEqual(calls, [("thr", None, pinned_thr.sha256), ("mcu", True, "patched"), ("box", (2,), None)])
        self.assertEqual({status.state for status in delivery.admission.boards}, {board_firmware.CURRENT})
        self.assertEqual(board_firmware.load_board_admission(paths), delivery.admission)
        # The input shaper calibration saved before the THR flash is recorded for the printer warning.
        marker = json.loads((paths.printer_data_root / "logs" / board_firmware.THR_SHAPER_MARKER).read_text())
        self.assertEqual(marker, {"shaper_freq_x": "54.4", "shaper_freq_y": "36.2"})
        # Boards that report their pinned image are neither read nor flashed again.
        printer.boards = {name: (boot, "patched") for name, (boot, _) in printer.boards.items()}
        reads, stops = len(printer.snapshots), printer.stops
        board_printer, admission = self.preflight(paths, printer, reporter)
        calls, _ = self.deliver(paths, printer, reporter, board_printer, admission)
        self.assertEqual((calls, len(printer.snapshots), printer.stops), ([], reads, stops))
        # A Klipper that has only just started reports no board yet, as in a firmware update's install step: the
        # install waits for it instead of stopping it again to read boards it then reports on their pinned images.
        answers, starting = printer.urlopen, [OSError("Klippy Disconnected")] * 3

        def urlopen(url, timeout=5):
            if starting:
                raise starting.pop()
            return answers(url, timeout)

        printer.urlopen = urlopen
        board_printer, admission = self.preflight(paths, printer, reporter)
        self.assertEqual((len(printer.snapshots), printer.stops, starting), (reads, stops, []))
        self.assertEqual({status.state for status in admission.boards}, {board_firmware.CURRENT})

    def test_a_box_this_release_does_not_admit_does_not_stop_firmware_for_the_rest_of_the_printer(self):
        paths, printer, reporter = self.fixture({"thr": ("admitted", "stock"), "mcu": ("admitted", "patched"),
                                                 "box1": ("unknown", "02.03.01.19")}, existing=True)
        board_printer, admission = self.preflight(paths, printer, reporter)
        self.assertEqual(admission.board("box1").state, board_firmware.UNRECOGNIZED_BOOTLOADER)
        self.assertTrue(admission.delivery_admitted and not admission.admitted)
        calls, _ = self.deliver(paths, printer, reporter, board_printer, admission)
        self.assertEqual([call[0] for call in calls], ["thr"])
        warning = json.loads((paths.printer_data_root / "logs/tltg_box_firmware_box1.json").read_text())
        self.assertTrue(warning["issue_url"].startswith(board_firmware.ISSUE_FORM))
        self.assertIn("touchscreen's firmware page", warning["note"])

    def test_qidi_predecessor_is_admitted_and_replaced_only_once_the_printer_runs_the_target_firmware(self):
        # Max 4 01.01.06.03, and the first of QIDI's two 01.01.06.04 packages, carry mainboard 02.01.01.11.
        for firmware in ("01.01.06.03", "01.01.06.04"):
            with self.subTest(firmware=firmware):
                paths, printer, reporter = self.fixture(
                    {"thr": ("admitted", "stock"), "mcu": ("admitted", "02.01.01.11")}, firmware=firmware)
                board_printer, admission = self.preflight(paths, printer, reporter)
                self.assertEqual((admission.board("mcu").state, admission.board("mcu").application),
                                 (board_firmware.NEEDS_IMAGE, board_firmware.QIDI_APPLICATION))
                calls, delivery = self.deliver(paths, printer, reporter, board_printer, admission)
                self.assertEqual(calls, [])
                outcomes = {result.board: result.outcome for result in delivery.results}
                self.assertEqual((outcomes["thr"], outcomes["mcu"]), ("waiting", "waiting"))
                # The firmware update installs QIDI's target package, then delivers the boards again.
                (paths.printer_data_root / "firmware_manifest.json").write_text(json.dumps(
                    {"SOC": {"file": "QD_MAX4_SOC_01.01.07.01_20261016_Release_NA", "version": "01.01.07.01"}}))
                calls, _ = self.deliver(paths, printer, reporter, board_printer, delivery.admission)
                self.assertEqual([call[0] for call in calls], ["thr", "mcu"])
        # A 02.01.01.11 image other than QIDI's is not admitted by its version.
        paths, printer, reporter = self.fixture({"thr": ("admitted", "stock"), "mcu": ("admitted", "02.01.01.11+modified")},
                                                firmware="01.01.06.03")
        with self.assertRaises(board_firmware.BoardAdmissionError):
            self.preflight(paths, printer, reporter)
        # Nor is QIDI's own, by the version its firmware's package carries, while the manifest doesn't name its image.
        load = board_firmware.load_board_manifest
        unnamed = {**BOARD_MANIFESTS["mcu"], "mcu": {**image_spec_of("mcu"), "predecessors": []}}
        paths, printer, reporter = self.fixture({"thr": ("admitted", "stock"), "mcu": ("admitted", "02.01.01.11")},
                                                firmware="01.01.06.03")
        with patch.object(board_firmware, "load_board_manifest",
                          side_effect=lambda root, kind: unnamed if kind == "mcu" else load(root, kind)), \
                self.assertRaises(board_firmware.BoardAdmissionError):
            self.preflight(paths, printer, reporter)

    def test_thr_and_mainboard_wait_for_the_target_firmware_and_boxes_do_not(self):
        # Max 4 01.01.06.05 carries the same THR and mainboard versions as the target, under another Klipper.
        paths, printer, reporter = self.fixture({"thr": ("admitted", "stock"), "mcu": ("admitted", "stock"),
                                                 "box1": ("admitted", "stock")}, firmware="01.01.06.05")
        board_printer, admission = self.preflight(paths, printer, reporter)
        calls, delivery = self.deliver(paths, printer, reporter, board_printer, admission)
        self.assertEqual(calls, [("box", (1,), None)])
        outcomes = {result.board: result.outcome for result in delivery.results}
        self.assertEqual((outcomes["thr"], outcomes["mcu"]), ("waiting", "waiting"))
        self.assertIn("THR: will be updated after the QIDI firmware update to 01.01.07.01", reporter.stream.getvalue())
        # The status the UIs show marks them, and only them, as waiting for that update.
        boards = {board.id: board for board in valve_oil_status.boards_from_admission(
            board_firmware.load_board_admission(paths))}
        self.assertEqual({board for board, status in boards.items() if status.waiting_for_firmware_update}, {"thr", "mcu"})
        self.assertEqual(boards["mcu"].detail, "will be updated after the QIDI firmware update to 01.01.07.01")

    def test_dry_run_touches_no_board_service_or_file(self):
        paths, printer, reporter = self.fixture({"thr": ("unknown", "stock"), "mcu": ("admitted", "stock")})
        before = sorted(path.relative_to(paths.printer_data_root) for path in paths.printer_data_root.rglob("*"))

        def forbidden(*args, **kwargs):
            raise AssertionError("dry run touched a service or board")

        printer.run = forbidden
        board_printer, admission = self.preflight(paths, printer, reporter, dry_run=True)
        calls, _ = self.deliver(paths, printer, reporter, board_printer, admission, dry_run=True)
        self.assertEqual(calls, [])
        self.assertEqual(sorted(path.relative_to(paths.printer_data_root)
                                for path in paths.printer_data_root.rglob("*")), before)

    def test_install_reads_boards_before_it_changes_anything_and_decides_on_qidi_updates_from_them(self):
        self.assertTrue(parse_args(["install", "--yes", "--flash-thr-fifo"]).flash_thr_fifo)  # accepted, ignored
        paths, printer, reporter = self.fixture({"thr": ("unknown", "stock"), "mcu": ("admitted", "stock")})
        env = build_env(paths.printer_data_root, moonraker_url=paths.moonraker_url)
        preflight = lambda **kwargs: board_firmware.board_preflight(**kwargs, **self.seams(printer))

        def install(*flags):
            output = io.StringIO()
            with patch("installer.runtime.cli.board_preflight", side_effect=preflight), \
                    patch("installer.runtime.cli.run_install") as run_install, \
                    patch("installer.runtime.cli.deliver_board_firmware") as deliver, \
                    patch("installer.runtime.cli.repair_installer_ownership"), \
                    patch("installer.runtime.cli.maybe_schedule_recommended_reboot"):
                code = main(["install", "--yes", "--plain", *flags], bundle_root=REPO_ROOT, environ=env, stream=output)
            block = run_install.call_args.kwargs["block_qidi_updates"] if run_install.called else "not installed"
            return code, block, deliver, output.getvalue()

        # A new install with a board this release doesn't admit changes nothing.
        code, block, _, _ = install("--flash-thr-fifo")
        self.assertEqual((code, block), (1, "not installed"))
        # Its dry run reads nothing, so it leaves QIDI's updates as they are.
        code, block, _, output = install("--dry-run")
        self.assertEqual((code, block), (0, None))
        self.assertIn("QIDI's firmware updates are left as they are", output)
        # Every board admitted: blocked. The install settles what the last check listed for this release, such as
        # installing it again, and leaves a newer release waiting.
        printer.boards["thr"] = ("admitted", "stock")
        valve_oil_status.record_check(paths, enrolled=False, printer=valve_oil_status.PrinterInfo(), installed_release=None,
                                      pending=(valve_oil_status.PendingItem(kind="release", version="99.1.1.1"),
                                               valve_oil_status.PendingItem(kind="release", reinstall=True,
                                                                            version=MANIFEST.package.version)))
        code, block, deliver, output = install()
        self.assertEqual((code, block), (0, True))
        self.assertEqual([item.version for item in valve_oil_status.load_status(paths).pending], ["99.1.1.1"])
        self.assertIn("QIDI's firmware updates stay blocked", output)
        self.assertTrue(deliver.call_args.kwargs["admission"].admitted)
        self.assertFalse(deliver.call_args.kwargs["dry_run"])
        # An existing install whose THR this release doesn't admit: unblocked.
        (paths.config_root / "tltg_optimized_state.yaml").write_text("")
        printer.boards["thr"] = ("unknown", "stock")
        code, block, _, output = install()
        self.assertEqual((code, block), (0, False))
        self.assertIn("QIDI's firmware updates are left to the touchscreen (this release doesn't support the THR", output)
        # A board that can't be read, with no earlier record of it, never switches QIDI's updater back on.
        printer.print_state = "printing"
        code, block, _, output = install()
        self.assertEqual((code, block), (0, None))
        self.assertIn("could not be read", output)

    def update_check(self, paths, printer, reporter, *, enrolled, box_outcome=("flashed", "")):
        """One update check's board part: what it finds and publishes, and on an enrolled printer what it does, with
        the Box flow replaced by a record of the Boxes it was asked to flash."""
        seams = self.seams(printer)
        found = update_check.discover(paths, enrolled=enrolled, latest_version=None, reconciliation_required=False,
                                      release_changed=False, urlopen=printer.urlopen, resolve_usb=seams["resolve_usb"],
                                      disk_usage=lambda path: types.SimpleNamespace(free=2 ** 40))
        update_check.publish(paths, found, urlopen=printer.urlopen)
        flashed = []
        if enrolled:
            def boxes(**kw):
                flashed.extend(kw["boxes"])
                kw["outcomes"].update({f"mcu_box{number}": box_outcome for number in kw["boxes"]})

            with patch.object(board_firmware, "flash_box_firmware_after_install", side_effect=boxes):
                update_check.apply(paths, found, reporter=reporter, environ={}, **seams)
        pending = {item.board: item for item in valve_oil_status.load_status(paths).pending}
        return flashed, pending

    def test_update_check_brings_boxes_connected_between_releases_to_their_firmware_only_when_enrolled(self):
        paths, printer, reporter = self.fixture({"thr": ("admitted", "patched"), "mcu": ("admitted", "patched"),
                                                 "box1": ("admitted", "patched")}, existing=True)
        self.deliver(paths, printer, reporter, *self.preflight(paths, printer, reporter))
        # Box 2, on QIDI's firmware, and Box 3, with a bootloader this release doesn't admit, are connected.
        printer.boards.update(box2=("admitted", "stock"), box3=("unknown", "stock"))
        box_cfg = paths.config_root / "box.cfg"
        box_cfg.write_text("".join(f"[mcu mcu_box{number}]\nserial: /dev/serial/by-id/{BOX_SERIAL}-box{number}\n\n"
                                   for number in (1, 2, 3)))
        reads, stops = len(printer.snapshots), printer.stops
        # Not enrolled: the check sees both from what Klipper reports, says to run the installer, and touches nothing.
        flashed, pending = self.update_check(paths, printer, reporter, enrolled=False)
        self.assertEqual((flashed, len(printer.snapshots), printer.stops), ([], reads, stops))
        self.assertEqual({board: item.blocked_by for board, item in pending.items()}, {"box2": (), "box3": ()})
        # With Valve Oil no longer installed as this release installs it, the release is to be installed again, marked
        # so readers don't take it for the release the printer already runs.
        drifted = update_check.discover(paths, enrolled=False, latest_version=None, reconciliation_required=True,
                                        release_changed=False, urlopen=printer.urlopen,
                                        resolve_usb=self.seams(printer)["resolve_usb"],
                                        disk_usage=lambda path: types.SimpleNamespace(free=2 ** 40)).release_item
        self.assertEqual((drifted.version, drifted.reinstall), (MANIFEST.package.version, True))
        # Enrolled: it reads the two Boxes once, flashes Box 2, and leaves Box 3 alone with its warning and issue link.
        flashed, pending = self.update_check(paths, printer, reporter, enrolled=True)
        self.assertEqual((flashed, printer.snapshots[reads:], printer.states), ([2], ["box2", "box3"], printer.original))
        self.assertNotIn("box2", pending)
        warning = json.loads((paths.printer_data_root / "logs/tltg_box_firmware_box3.json").read_text())
        self.assertTrue(warning["issue_url"].startswith(board_firmware.ISSUE_FORM))
        self.assertIn("Box 3: its bootloader is not supported yet", reporter.stream.getvalue())
        self.assertIn("tltg_box_firmware_box3.json in Fluidd's Logs. To get it supported, open: "
                      + warning["issue_url"], reporter.stream.getvalue())
        self.assertEqual(valve_oil_status.load_status(paths).run.kind, "automatic")
        # The next checks read neither again and stop nothing; Box 3 is no longer waiting, only warned about.
        printer.boards["box2"] = ("admitted", "patched")
        reads, stops = len(printer.snapshots), printer.stops
        flashed, pending = self.update_check(paths, printer, reporter, enrolled=True)
        self.assertEqual((flashed, pending, len(printer.snapshots), printer.stops), ([], {}, reads, stops))
        boards = {board.id: board for board in valve_oil_status.load_status(paths).boards}
        self.assertEqual((boards["box2"].state, boards["box3"].state), ("current", "unrecognized_bootloader"))
        # The closed-loop drivers are listed from what Klipper reports, and nothing is sent to them: held on firmware
        # older than this release's QIDI firmware carries, current once on it.
        self.assertNotIn("closed_loop", boards)
        for printer.drivers, state in (("03.01.10.13", "held"), ("03.01.10.15", "current")):
            self.update_check(paths, printer, reporter, enrolled=True)
            drivers = {board.id: board for board in valve_oil_status.load_status(paths).boards}["closed_loop"]
            self.assertEqual((drivers.state, drivers.firmware, len(printer.snapshots), printer.stops),
                             (state, printer.drivers, reads, stops))
        # A Box restored to QIDI's firmware is held there until the next release.
        printer.boards["box2"] = ("admitted", "stock")
        (paths.printer_data_root / box_firmware.HOLD_FILE).write_text(json.dumps({
            "release": MANIFEST.package.version,
            "boxes": [{"box": "mcu_box2", "serial": f"/dev/serial/by-id/{BOX_SERIAL}-box2"}]}))
        flashed, pending = self.update_check(paths, printer, reporter, enrolled=True)
        self.assertEqual((flashed, pending, len(printer.snapshots), printer.stops), ([], {}, reads, stops))
        # A Box updated from the touchscreen to QIDI's firmware is read and flashed once; when that stops before
        # writing, later checks leave it to the next install instead of stopping the printer every hour.
        printer.boards["box1"] = ("admitted", "stock")
        flashed, pending = self.update_check(paths, printer, reporter, enrolled=True,
                                             box_outcome=("skipped", "Klipper was not ready"))
        self.assertEqual((flashed, printer.snapshots[reads:]), ([1], ["box1"]))
        stops = printer.stops
        flashed, pending = self.update_check(paths, printer, reporter, enrolled=True)
        self.assertEqual((flashed, len(printer.snapshots), printer.stops), ([], reads + 1, stops))
        self.assertEqual([blocker.code for blocker in pending["box1"].blocked_by], ["last_run_failed"])
        # A Box write that wasn't verified stops the check, and the waiting work names the rescue to run.
        (paths.printer_data_root / BOX_RECOVERY_MARKER).write_text("{}")
        flashed, pending = self.update_check(paths, printer, reporter, enrolled=True)
        recovery = [blocker for blocker in pending["box1"].blocked_by if blocker.code == "recovery_required"]
        self.assertEqual((flashed, len(recovery)), ([], 1))
        self.assertIn("./box-firmware.sh rescue", recovery[0].message)
        (paths.printer_data_root / BOX_RECOVERY_MARKER).unlink()
        # A Box taken out of box.cfg leaves the status, and its warning goes.
        box_cfg.write_text(f"[mcu mcu_box1]\nserial: /dev/serial/by-id/{BOX_SERIAL}-box1\n\n")
        self.update_check(paths, printer, reporter, enrolled=False)
        self.assertNotIn("box3", {board.id for board in valve_oil_status.load_status(paths).boards})
        self.assertFalse((paths.printer_data_root / "logs/tltg_box_firmware_box3.json").exists())

    def test_issue_links_fit_github(self):
        url, shortened = board_firmware.issue_link(
            printer_name="QIDI Plus 5", firmware="01.01.02.01", box_count=2, details="é" * 5000, output="x" * 5000,
            report={"bootloader_page_sha256": [UNKNOWN] * 16}, report_path="/report.json")
        self.assertTrue(shortened)
        self.assertLessEqual(len(url), board_firmware.MAX_ISSUE_URL)
        self.assertIn("bootloader_page_sha256", parse_qs(urlsplit(url).query)["board_report"][0])


if __name__ == "__main__":
    unittest.main()
