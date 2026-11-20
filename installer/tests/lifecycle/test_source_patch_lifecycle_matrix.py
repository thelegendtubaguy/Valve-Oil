from __future__ import annotations

import contextlib
import functools
import hashlib
import io
import json
import os
import pwd
import shutil
import socket
import subprocess
import tarfile
import time
import types
import unittest
import urllib.parse
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from installer.runtime import box_enablement, errors, klipper_cfg, process_restart
from installer.runtime.box_enablement import (
    SavedVariablePersistenceError,
    detect_box_enablement_opportunity,
    maybe_reconcile_tool_slots_after_box_count_change,
    maybe_write_required_tool_slot_variables,
    saved_variable_verify_marker_path,
)
from installer.runtime import auto_update, firmware_update, update_window, valve_oil_status
from installer.runtime.board_images import load_board_manifest, pinned_board_image
from installer.runtime.errors import InstallerError
from installer.runtime.auto_update import (
    AutoUpdateError,
    LOCK_HELD_ENV,
    SYSTEM_ROOT_ENV,
    TIMER_CONTENT,
    TIMER_NAME,
    enrollment_path,
    run_auto_update_check,
    staging_root,
    state_path,
)
from installer.runtime.cli import main
from installer.runtime.backup import load_backup_snapshot, snapshot_runtime_tree
from installer.runtime.bootstrap import copy_bundle
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.compatibility import load_supported_upgrade_sources
from installer.runtime.errors import ActivePrintError, PrinterStateError, UnsupportedFirmwareError
from installer.runtime.manifest import load_manifest
from installer.runtime.naming import BUNDLE_ROOT_NAME, CACHE_DIRECTORY, LEGACY_BUNDLE_ROOT_NAME, LEGACY_CACHE_DIRECTORIES
from installer.runtime.models import (
    InstalledState,
    ManagedTreeState,
    PatchLedgerEntry,
)
from installer.runtime.reporter import PlainReporter
from installer.runtime.restore_helper import run_restore_helper
from installer.runtime.runner import run_install
from installer.runtime.state_file import load_installed_state, write_installed_state
from installer.runtime.uninstall import run_uninstall
from installer.tests.helpers import (
    REPO_ROOT,
    _JsonResponse,
    build_env,
    homing_fixture_bytes,
    homing_smart5_fixture_bytes,
    homing_sync_reset_26_11_18_1_bytes,
    homing_sync_reset_fixture_bytes,
    moonraker_urlopen,
    snapshot_tree,
    stock_printer_data,
    temp_path,
)


DESIRED_HOMING_SHA256 = "fa1102164822317f06d3f6564eef7cd5e07c9e262209b1340cf19520f0fbaca7"
SYNC_RESET_DESIRED_HOMING_SHA256 = "3065abb2faad5ed580eb14389495440074512bd6fcb9a7929d09997bb50d823d"
SOURCE_CASES = (
    ("01.01.06.03", "standard", DESIRED_HOMING_SHA256),
    ("01.01.06.04", "standard", DESIRED_HOMING_SHA256),
    ("01.01.06.04", "sync-reset", SYNC_RESET_DESIRED_HOMING_SHA256),
    ("01.01.06.05", "sync-reset", SYNC_RESET_DESIRED_HOMING_SHA256),
)
# Restoring a source writes back the recorded preimage whatever the variant, so restore paths need one case per
# payload family and firmware config group, not every admitted variant.
RESTORE_CASES = (SOURCE_CASES[0], SOURCE_CASES[3])


def _section_hashes(data: bytes) -> dict[str, str]:
    text = data.decode("utf-8").replace("\r\n", "\n")
    names = [line[1:line.index("]")] for line in text.splitlines() if line.startswith("[") and "]" in line]
    return {
        name: klipper_cfg.normalized_section_sha256(klipper_cfg.resolve_unique_section(text, name).text)
        for name in names
    }


class SourcePatchLifecycleMatrixTests(unittest.TestCase):
    def setUp(self):
        self.manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        self.compatibility = load_supported_upgrade_sources(
            REPO_ROOT / "installer/supported_upgrade_sources.yaml"
        )

    def _fixture(self, firmware: str, *, source_variant: str = "standard"):
        printer = self.manifest.printer_for_firmware(firmware)
        stock_source = (
            homing_smart5_fixture_bytes()
            if printer.id == "smart5"
            else homing_sync_reset_fixture_bytes()
            if source_variant == "sync-reset"
            else homing_fixture_bytes(firmware)
        )
        printer_root = stock_printer_data(self.manifest, firmware, homing=stock_source)
        paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(
                printer_root,
                moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats",
            ),
        )
        return printer_root, paths, stock_source

    def _fixture_urlopen(self, printer_root, *, state="standby"):
        return moonraker_urlopen(
            state,
            saved_variables_path=printer_root / "config/saved_variables.cfg",
        )

    def _run_install(self, paths, *, environ=None):
        return run_install(
            paths,
            self.manifest,
            PlainReporter(io.StringIO()),
            urlopen=moonraker_urlopen(
                saved_variables_path=paths.config_root / "saved_variables.cfg"
            ),
            environ=environ,
        )

    @staticmethod
    def _installed_bundle(printer_root: Path, prefix: str, name: str = BUNDLE_ROOT_NAME) -> Path:
        """The bundle the update check runs from, in the printer user's home."""
        bundle_root = printer_root.parent / name
        bundle_root.mkdir()
        (bundle_root / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        for name in ("installer", "firmware", "scripts"):
            (bundle_root / name).symlink_to(REPO_ROOT / name)
        return bundle_root

    def _assert_homing_speed(self, printer_root: Path, value: str) -> None:
        text = (printer_root / "config/printer.cfg").read_text(encoding="utf-8")
        for stepper in ("stepper_x", "stepper_y"):
            self.assertEqual(
                klipper_cfg.resolve_unique_option(text, stepper, "homing_speed").value,
                value,
            )

    def _assert_speeds(self, printer_root: Path, **values: str) -> None:
        text = (printer_root / "config/printer.cfg").read_text(encoding="utf-8")
        options = {"max_velocity": ("printer", "max_velocity"),
                   "bed_mesh": ("bed_mesh", "speed"),
                   "z_tilt": ("z_tilt", "speed")}
        for name, value in values.items():
            section, option = options[name]
            self.assertEqual(
                klipper_cfg.resolve_unique_option(text, section, option).value, value)

    def _assert_homing_retract_speed(self, printer_root: Path, value: str) -> None:
        text = (printer_root / "config/printer.cfg").read_text(encoding="utf-8")
        for stepper in ("stepper_x", "stepper_y"):
            self.assertEqual(
                klipper_cfg.resolve_unique_option(text, stepper, "homing_retract_speed").value,
                value,
            )

    def test_fresh_stock_install_applies_source_and_records_preimage_for_all_variants(self):
        for firmware, source_variant, desired_sha256 in SOURCE_CASES:
            with self.subTest(firmware=firmware, source_variant=source_variant):
                printer_root, paths, stock_source = self._fixture(
                    firmware, source_variant=source_variant
                )
                self._run_install(paths)

                self._assert_homing_speed(printer_root, "65")
                self._assert_homing_retract_speed(printer_root, "500.0")
                self.assertEqual(
                    hashlib.sha256(
                        (paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes()
                    ).hexdigest(),
                    desired_sha256,
                )
                state = load_installed_state(
                    printer_root / "config/tltg_optimized_state.yaml"
                )
                self.assertEqual(len(state.source_patches), 1)
                entry = state.source_patches[0]
                self.assertEqual(entry.firmware, firmware)
                self.assertEqual(entry.original_bytes, stock_source)
                self.assertEqual(entry.original_sha256, hashlib.sha256(stock_source).hexdigest())

    def test_install_takes_over_a_patched_source_an_unfinished_install_never_recorded(self):
        # A power cut can stop an install after it patched homing.py and before it wrote its state.
        for left_by, homing in (("26.11.18.1", homing_sync_reset_26_11_18_1_bytes()),
                                ("this release", (REPO_ROOT / "installer/klipper/qidi/homing-sync-reset.py").read_bytes())):
            with self.subTest(left_by=left_by):
                printer_root, paths, stock_source = self._fixture("01.01.07.01", source_variant="sync-reset")
                homing_path = paths.managed_klipper_root / "klippy/extras/homing.py"
                homing_path.write_bytes(homing)

                self._run_install(paths)

                self.assertEqual(hashlib.sha256(homing_path.read_bytes()).hexdigest(),
                                 SYNC_RESET_DESIRED_HOMING_SHA256)
                state = load_installed_state(printer_root / "config/tltg_optimized_state.yaml")
                self.assertEqual([entry.original_bytes for entry in state.source_patches], [stock_source])
                run_uninstall(
                    paths,
                    self.manifest,
                    self.compatibility,
                    PlainReporter(io.StringIO()),
                    input_stream=io.StringIO("Y\n"),
                    urlopen=self._fixture_urlopen(printer_root),
                )
                self.assertEqual(homing_path.read_bytes(), stock_source)

    def test_install_refuses_firmware_whose_soc_file_names_another_printer(self):
        for firmware, other_prefix in (("01.01.07.01", "QD_PLUS5_SOC_"), ("01.01.02.01", "QD_MAX4_SOC_")):
            with self.subTest(firmware=firmware):
                printer_root, paths, _ = self._fixture(firmware, source_variant="sync-reset")
                (printer_root / "firmware_manifest.json").write_text(
                    json.dumps({"SOC": {"file": f"{other_prefix}{firmware}_Release_NA", "version": firmware}}),
                    encoding="utf-8",
                )
                before = (printer_root / "config/printer.cfg").read_bytes()

                with self.assertRaises(UnsupportedFirmwareError):
                    self._run_install(paths)

                self.assertEqual((printer_root / "config/printer.cfg").read_bytes(), before)
                self.assertFalse((printer_root / "config/tltg_optimized_state.yaml").exists())

    def test_firmware_shared_by_two_printers_needs_the_soc_file_name(self):
        # The Plus 5 and Smart 5 both ship 01.01.02.01; without the SOC file name the printer is unknown.
        printer_root, paths, _ = self._fixture("01.01.02.01", source_variant="sync-reset")
        (printer_root / "firmware_manifest.json").write_text(
            json.dumps({"SOC": {"version": "01.01.02.01"}}), encoding="utf-8"
        )
        before = (printer_root / "config/printer.cfg").read_bytes()

        with self.assertRaises(UnsupportedFirmwareError):
            self._run_install(paths)

        self.assertEqual((printer_root / "config/printer.cfg").read_bytes(), before)
        self.assertFalse((printer_root / "config/tltg_optimized_state.yaml").exists())

    def test_smart5_install_uses_the_smart5_baseline_and_uninstall_restores_it(self):
        printer_root, paths, stock_source = self._fixture("smart5-01.01.02.01")
        stock = {
            name: (printer_root / "config" / name).read_bytes()
            for name in ("printer.cfg", "klipper-macros-qd/kinematics.cfg", "klipper-macros-qd/filament.cfg",
                         "klipper-macros-qd/qd_macro.cfg", "klipper-macros-qd/fans.cfg")
        }
        self._run_install(paths)

        state = load_installed_state(printer_root / "config/tltg_optimized_state.yaml")
        self.assertEqual(state.runtime_firmware, "smart5-01.01.02.01")
        printer_cfg = (printer_root / "config/printer.cfg").read_text(encoding="utf-8")
        # Motor-stall homing and the speed limits stay QIDI's until measured on a Smart 5.
        for stepper in ("stepper_x", "stepper_y"):
            self.assertEqual(klipper_cfg.resolve_unique_option(printer_cfg, stepper, "homing_speed").value, "50")
            self.assertEqual(klipper_cfg.resolve_unique_option(printer_cfg, stepper, "homing_retract_dist").value, "50.0")
        self.assertEqual(klipper_cfg.resolve_unique_option(printer_cfg, "stepper_z", "homing_retract_dist").value, "10.0")
        self._assert_speeds(printer_root, max_velocity="400", bed_mesh="150")
        self.assertEqual(
            klipper_cfg.resolve_unique_option(printer_cfg, "virtual_sdcard", "on_error_gcode").value,
            "VO_CANCEL_PRINT_ON_ERROR",
        )
        self.assertFalse(klipper_cfg.has_section(printer_cfg, "filament_switch_sensor filament_switch_sensor"))
        self.assertIn(b"\r\n", (printer_root / "config/printer.cfg").read_bytes())

        installed = printer_root / "config/tltg-optimized-macros"
        homing = (installed / "homing.cfg").read_text(encoding="utf-8")
        self.assertIn("SET_SECOND_HOMING ENABLE=1", homing)
        self.assertNotIn("SET_TMC_CURRENT", homing)
        self.assertNotIn("SET_HOMING_MODE", homing)
        self.assertIn("G1 X175 F5000", (installed / "nozzle_motion.cfg").read_text(encoding="utf-8"))
        # No Smart 5 Box load has been traced, so no Box motion default is saved.
        saved_variables = (printer_root / "config/saved_variables.cfg").read_text(encoding="utf-8")
        self.assertNotIn("vo_box_load_fast_length", saved_variables)
        self.assertEqual(
            hashlib.sha256((paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes()).hexdigest(),
            "0419d86046b771bdb482a29f862e59518e03dcfa7b8af150b9003190ef474bf3",
        )

        run_uninstall(
            paths,
            self.manifest,
            self.compatibility,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("Y\n"),
            urlopen=self._fixture_urlopen(printer_root),
        )
        for name, data in stock.items():
            with self.subTest(restored=name):
                restored = (printer_root / "config" / name).read_bytes()
                self.assertEqual(restored.count(b"\n"), restored.count(b"\r\n"))
                self.assertEqual(_section_hashes(restored), _section_hashes(data))
        self.assertFalse(installed.exists())
        self.assertEqual((paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes(), stock_source)

    def test_plus5_install_uses_the_plus5_baseline_and_uninstall_restores_it(self):
        printer_root, paths, stock_source = self._fixture("01.01.02.01", source_variant="sync-reset")
        stock = {
            name: (printer_root / "config" / name).read_bytes()
            for name in ("printer.cfg", "klipper-macros-qd/kinematics.cfg", "klipper-macros-qd/filament.cfg",
                         "klipper-macros-qd/fans.cfg")
        }
        self._run_install(paths)

        printer_cfg = (printer_root / "config/printer.cfg").read_text(encoding="utf-8")
        # Sensorless X/Y homing stays single-touch at 55 mm/s. The open-loop X/Y motors top out at 400 mm/s,
        # probing travel included.
        for stepper in ("stepper_x", "stepper_y"):
            self.assertEqual(klipper_cfg.resolve_unique_option(printer_cfg, stepper, "homing_speed").value, "55")
            self.assertEqual(klipper_cfg.resolve_unique_option(printer_cfg, stepper, "homing_retract_dist").value, "0")
        self._assert_speeds(printer_root, max_velocity="400", bed_mesh="400", z_tilt="400")
        self.assertEqual(
            klipper_cfg.resolve_unique_option(printer_cfg, "virtual_sdcard", "on_error_gcode").value,
            "VO_CANCEL_PRINT_ON_ERROR",
        )
        self.assertFalse(klipper_cfg.has_section(printer_cfg, "filament_switch_sensor filament_switch_sensor"))
        self.assertIn(b"\r\n", (printer_root / "config/printer.cfg").read_bytes())
        self.assertFalse(klipper_cfg.has_section(
            (printer_root / "config/klipper-macros-qd/kinematics.cfg").read_text(encoding="utf-8"), "homing_override"))

        installed = printer_root / "config/tltg-optimized-macros"
        homing = (installed / "homing.cfg").read_text(encoding="utf-8")
        self.assertIn("SET_TMC_CURRENT STEPPER=stepper_x", homing)
        self.assertNotIn("ENFORCE_MODE", homing)
        self.assertNotIn("SET_HOMING_MODE", homing)
        self.assertIn("switch_pin: !THR:PA1", (installed / "printer_profile.cfg").read_text(encoding="utf-8"))
        # The Plus 5 gets its own Box motion defaults: a fast push long enough to reach the toolhead sensor.
        saved_variables = (printer_root / "config/saved_variables.cfg").read_text(encoding="utf-8")
        self.assertEqual(
            klipper_cfg.resolve_unique_option(saved_variables, "Variables", "vo_box_load_fast_length").value, "850"
        )
        self.assertNotIn("vo_box_load_approach_speed =", saved_variables)
        self.assertEqual(
            hashlib.sha256((paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes()).hexdigest(),
            SYNC_RESET_DESIRED_HOMING_SHA256,
        )

        run_uninstall(
            paths,
            self.manifest,
            self.compatibility,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("Y\n"),
            urlopen=self._fixture_urlopen(printer_root),
        )
        # Uninstall puts a removed section back at the end of its file, in the file's line endings.
        for name, data in stock.items():
            with self.subTest(restored=name):
                restored = (printer_root / "config" / name).read_bytes()
                self.assertEqual(restored.count(b"\n"), restored.count(b"\r\n"))
                self.assertEqual(_section_hashes(restored), _section_hashes(data))
        self.assertFalse(installed.exists())
        self.assertEqual((paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes(), stock_source)

    def test_plus5_update_replaces_earlier_release_values_and_uninstall_restores_the_originals(self):
        firmware = "01.01.02.01"
        printer_root, paths, _ = self._fixture(firmware, source_variant="sync-reset")
        cfg = printer_root / "config/printer.cfg"
        # The earlier release's 565 mm/s probing travel and error macro name, and an operator's own top speed
        # above 400.
        text = cfg.read_text(encoding="utf-8")
        for section in ("z_tilt", "bed_mesh"):
            text = klipper_cfg.set_option_value(text, section, "speed", "565")
        text = klipper_cfg.set_option_value(text, "virtual_sdcard", "on_error_gcode", "OPTIMIZED_CANCEL_PRINT_ON_ERROR")
        cfg.write_text(klipper_cfg.set_option_value(text, "printer", "max_velocity", "500"), encoding="utf-8")
        write_installed_state(
            printer_root / "config/tltg_optimized_state.yaml",
            InstalledState(
                schema_version=1,
                package_id="qidi-max4-optimized",
                package_version=self.manifest.package.version,
                runtime_firmware=firmware,
                backup_label="prior-install",
                installed_at="2026-11-12T00:00:00Z",
                managed_tree=ManagedTreeState("config/tltg-optimized-macros", ()),
                patch_ledger=(
                    *(PatchLedgerEntry(f"{section}_speed", "config/printer.cfg", section, "speed", "150", "565",
                                       "applied")
                      for section in ("z_tilt", "bed_mesh")),
                    PatchLedgerEntry("virtual_sdcard_on_error_gcode", "config/printer.cfg", "virtual_sdcard",
                                     "on_error_gcode", "CANCEL_PRINT", "OPTIMIZED_CANCEL_PRINT_ON_ERROR", "applied"),
                ),
            ),
        )

        self._run_install(paths)
        self._assert_speeds(printer_root, max_velocity="400", bed_mesh="400", z_tilt="400")
        self.assertEqual(self._on_error_gcode(printer_root), "VO_CANCEL_PRINT_ON_ERROR")

        run_uninstall(
            paths,
            self.manifest,
            self.compatibility,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("Y\n"),
            urlopen=self._fixture_urlopen(printer_root),
        )
        self._assert_speeds(printer_root, max_velocity="500", bed_mesh="150", z_tilt="150")
        self.assertEqual(self._on_error_gcode(printer_root), "CANCEL_PRINT")

    def _on_error_gcode(self, printer_root):
        text = (printer_root / "config/printer.cfg").read_text(encoding="utf-8")
        return klipper_cfg.resolve_unique_option(text, "virtual_sdcard", "on_error_gcode").value

    def test_install_leaves_extra_modules_readable_by_klipper(self):
        # Klipper may run as another user than the installer.
        printer_root, paths, _ = self._fixture("01.01.07.01", source_variant="sync-reset")
        modules = [
            paths.managed_klipper_root / module.destination
            for module in self.manifest.install.extra_modules
        ]
        self._run_install(paths)
        for module in modules:
            self.assertEqual(module.stat().st_mode & 0o777, 0o644, module.name)

        # Earlier installs left new modules 0600; the next install repairs them.
        for module in modules:
            module.chmod(0o600)
        self._run_install(paths)
        for module in modules:
            self.assertEqual(module.stat().st_mode & 0o777, 0o644, module.name)

    def test_installs_end_with_klipper_running_what_they_installed(self):
        printer_root, paths, _ = self._fixture("01.01.02.01", source_variant="sync-reset")
        fake = self._fixture_urlopen(printer_root)
        restarts = []

        def urlopen(request, timeout=0):
            if "/machine/services/restart" in getattr(request, "full_url", str(request)):
                restarts.append(request)
            return fake(request, timeout=timeout)

        def install():
            run_install(paths, self.manifest, PlainReporter(io.StringIO()), urlopen=urlopen)

        # Every install ends with Klipper restarted, without asking.
        install()
        self.assertEqual(len(restarts), 1)
        install()
        self.assertEqual(len(restarts), 2)

        # Klipper keeps an imported module until its process ends, so its own RESTART isn't enough.
        module = self.manifest.install.extra_modules[0]
        installed = paths.managed_klipper_root / module.destination
        installed.write_bytes(b"# an earlier release's module\n")
        install()
        self.assertEqual(len(restarts), 3)
        self.assertEqual(installed.read_bytes(), (REPO_ROOT / "installer" / module.source).read_bytes())
        self.assertFalse(paths.restart_marker_path.exists())

    def test_install_initializes_absent_release_defaults_and_preserves_operator_values(self):
        # An operator's 0 retention, or a Box motion setting reset to QIDI's value (None), is a choice, also when an
        # earlier release saved it under its tltg_ name, which Klipper copies to the vo_ name once it restarts.
        for prefix, existing in (("vo", None), ("vo", "0"), ("tltg", "0")):
            with self.subTest(prefix=prefix, existing=existing):
                printer_root, paths, _ = self._fixture("01.01.06.03")
                saved_variables_path = printer_root / "config/saved_variables.cfg"
                if existing is not None:
                    text = saved_variables_path.read_text(encoding="utf-8")
                    saved_variables_path.write_text(
                        text
                        + f"{prefix}_keep_loaded_between_prints = {existing}\n"
                        + f"{prefix}_box_load_approach_speed = None\n",
                        encoding="utf-8",
                    )

                self._run_install(paths)

                saved_variables = saved_variables_path.read_text(encoding="utf-8")
                self.assertEqual(
                    klipper_cfg.resolve_unique_option(
                        saved_variables,
                        "Variables",
                        f"{prefix}_keep_loaded_between_prints",
                    ).value,
                    existing or "1",
                )
                for name, value in box_enablement.BOX_MOTION_DEFAULTS.items():
                    if existing is None:
                        self.assertEqual(
                            klipper_cfg.resolve_unique_option(saved_variables, "Variables", name).value,
                            value,
                        )
                    elif not (prefix == "vo" and name == "vo_box_load_approach_speed"):
                        self.assertNotIn(f"{name} =", saved_variables)
                if prefix == "tltg":
                    self.assertNotIn("vo_keep_loaded_between_prints =", saved_variables)

    def test_install_updates_box_motion_left_at_earlier_defaults(self):
        # The load pair, the extruder pull-back, and the approach speed are still at earlier defaults; the
        # operator changed one unload speed.
        printer_root, paths, _ = self._fixture("01.01.06.03")
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text(
            saved_variables_path.read_text(encoding="utf-8")
            + "vo_box_load_fast_speed_1 = 120\n"
            + "vo_box_load_fast_speed_2 = 100\n"
            + "vo_box_unload_speed_1 = 115\n"
            + "vo_box_unload_speed_2 = 90\n"
            + "vo_box_unload_extruder_length = 50\n"
            + "vo_box_load_approach_speed = 15\n",
            encoding="utf-8",
        )

        self._run_install(paths)

        saved_variables = saved_variables_path.read_text(encoding="utf-8")
        for name, value in (
            ("vo_box_load_fast_speed_1", "85"),
            ("vo_box_load_fast_speed_2", "65"),
            ("vo_box_unload_speed_1", "115"),
            ("vo_box_unload_speed_2", "90"),
            ("vo_box_unload_extruder_length", "55"),
            ("vo_box_load_approach_speed", "25"),
        ):
            self.assertEqual(klipper_cfg.resolve_unique_option(saved_variables, "Variables", name).value, value)
        self.assertNotIn("vo_box_load_fast_length =", saved_variables)

    def test_noninteractive_box_reconciliation_adds_missing_mappings_without_replacing_existing_ones(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text(
            "[Variables]\n"
            "box_count = 2\n"
            "enable_box = 1\n"
            "value_t0 = 'slot0'\n"
            "value_t1 = 'slot3'\n"
            "value_t2 = 'slot2'\n"
            "value_t3 = 'slot3'\n",
            encoding="utf-8",
        )

        self._run_install(paths)

        saved_variables = saved_variables_path.read_text(encoding="utf-8")
        self.assertEqual(
            klipper_cfg.resolve_unique_option(
                saved_variables, "Variables", "value_t1"
            ).value,
            "'slot3'",
        )
        for tool in range(4, 8):
            self.assertEqual(
                klipper_cfg.resolve_unique_option(
                    saved_variables, "Variables", f"value_t{tool}"
                ).value,
                f"'slot{tool}'",
            )
        self.assertNotIn(
            "value_t",
            (paths.config_root / "tltg_optimized_state.yaml").read_text(
                encoding="utf-8"
            ),
        )

        saved_variables_path.write_text(
            klipper_cfg.set_option_value(saved_variables, "Variables", "box_count", "3"),
            encoding="utf-8",
        )
        reporter = PlainReporter(io.StringIO())
        urlopen = self._fixture_urlopen(printer_root)
        self.assertTrue(maybe_reconcile_tool_slots_after_box_count_change(
            paths=paths, reporter=reporter, urlopen=urlopen
        ))
        reconciled = saved_variables_path.read_text(encoding="utf-8")
        for tool in range(12):
            self.assertEqual(
                klipper_cfg.resolve_unique_option(
                    reconciled, "Variables", f"value_t{tool}"
                ).value,
                "'slot3'" if tool == 1 else f"'slot{tool}'",
            )
        self.assertFalse(maybe_reconcile_tool_slots_after_box_count_change(
            paths=paths, reporter=reporter, urlopen=urlopen
        ))
        self.assertEqual(saved_variables_path.read_text(encoding="utf-8"), reconciled)

    def test_a_new_install_turns_box_support_on_and_later_installs_leave_it_and_mappings_as_set(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text(
            "[Variables]\nbox_count = 1\nenable_box = 0\nvalue_t1 = 'slot2'\n", encoding="utf-8"
        )

        def option(name):
            return klipper_cfg.resolve_unique_option(
                saved_variables_path.read_text(encoding="utf-8"), "Variables", name
            ).value

        self._run_install(paths)
        self.assertEqual(option("enable_box"), "1")
        self.assertEqual(option("value_t1"), "'slot2'")  # a mapping that doesn't line up isn't touched or asked about

        # The operator turns it off for external spools; the next install leaves it so.
        saved_variables_path.write_text(
            saved_variables_path.read_text(encoding="utf-8").replace("enable_box = 1", "enable_box = 0"),
            encoding="utf-8",
        )
        self._run_install(paths)
        self.assertEqual(option("enable_box"), "0")
        self.assertEqual(option("value_t1"), "'slot2'")

    def test_flow_controller_box_detection_does_not_require_box_extras(self):
        printer_root, paths, _ = self._fixture("01.01.07.01", source_variant="sync-reset")
        (printer_root / "config/box.cfg").write_text("# [include box1.cfg]\n", encoding="utf-8")
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text("[Variables]\nbox_count = 1\nenable_box = 0\n", encoding="utf-8")
        flow = {"T_MANAGER": {}}
        cases = (
            ("rs485_box", {"gcode": {"commands": flow}, "multi_color_controller": {"box_count": 0}, "box485": {"box_count": 1}}, True),
            ("usb_box", {"gcode": {"commands": flow}, "multi_color_controller": {"box_count": 2}, "box485": {"box_count": 0}}, True),
            ("no_box", {"gcode": {"commands": flow}, "multi_color_controller": {"box_count": 0}, "box485": {"box_count": 0}}, False),
            ("legacy_without_flow", {"gcode": {"commands": {}}, "multi_color_controller": {"box_count": 2}}, False),
        )
        for name, box_status, available in cases:
            with self.subTest(name=name):
                urlopen = moonraker_urlopen(saved_variables_path=saved_variables_path, box_status=box_status)
                opportunity = detect_box_enablement_opportunity(paths, urlopen=urlopen)
                self.assertEqual(opportunity is not None, available)

    def test_missing_mapping_recheck_preserves_new_nonempty_choice(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text(
            "[Variables]\nbox_count = 1\nenable_box = 1\n",
            encoding="utf-8",
        )
        base_urlopen = self._fixture_urlopen(printer_root)
        object_queries = 0
        gcode_scripts = []

        def urlopen(request, timeout=0):
            nonlocal object_queries
            url = getattr(request, "full_url", str(request))
            if "printer/objects/query?save_variables" in url:
                object_queries += 1
                if object_queries == 2:
                    saved_variables_path.write_text(
                        saved_variables_path.read_text(encoding="utf-8")
                        + "value_t0 = 'operator-choice'\n",
                        encoding="utf-8",
                    )
            if "/printer/gcode/script" in url:
                gcode_scripts.append(json.loads(request.data.decode("utf-8"))["script"])
            return base_urlopen(request, timeout=timeout)

        self.assertTrue(
            maybe_write_required_tool_slot_variables(
                paths=paths, reporter=PlainReporter(io.StringIO()), urlopen=urlopen
            )
        )
        self.assertFalse(any("VARIABLE=value_t0" in script for script in gcode_scripts))
        self.assertEqual(
            klipper_cfg.resolve_unique_option(
                saved_variables_path.read_text(encoding="utf-8"), "Variables", "value_t0"
            ).value,
            "'operator-choice'",
        )

    def test_source_restart_rejects_changed_authorized_saved_values(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text(
            "[Variables]\nbox_count = 1\nenable_box = 0\nvalue_t0 = 'custom0'\n",
            encoding="utf-8",
        )
        base_urlopen = self._fixture_urlopen(printer_root)

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            response = base_urlopen(request, timeout=timeout)
            if url.endswith("/machine/services/restart"):
                saved_variables_path.write_text(
                    saved_variables_path.read_text(encoding="utf-8").replace(
                        "enable_box = 1", "enable_box = 0"
                    ),
                    encoding="utf-8",
                )
            return response

        with self.assertRaises(SavedVariablePersistenceError):
            run_install(
                paths,
                self.manifest,
                PlainReporter(io.StringIO()),
                input_stream=io.StringIO("Y\nN\n"),
                urlopen=urlopen,
            )

        with self.assertRaises(SavedVariablePersistenceError):
            run_install(
                paths,
                self.manifest,
                PlainReporter(io.StringIO()),
                input_stream=io.StringIO("Y\nN\n"),
                urlopen=urlopen,
            )

        marker_path = saved_variable_verify_marker_path(paths)
        expected_marker = json.loads(marker_path.read_text(encoding="utf-8"))
        self.assertEqual(expected_marker["schema_version"], 1)
        self.assertEqual(expected_marker["values"]["enable_box"], "1")
        self.assertTrue(
            (paths.managed_klipper_root / "klippy/extras/homing.py").is_file()
        )

        with self.assertRaises(SavedVariablePersistenceError):
            self._run_install_with_urlopen(paths, self._fixture_urlopen(printer_root))
        self.assertEqual(
            json.loads(marker_path.read_text(encoding="utf-8")), expected_marker
        )

        saved_variables_path.write_text(
            saved_variables_path.read_text(encoding="utf-8").replace(
                "enable_box = 0", "enable_box = 1"
            ),
            encoding="utf-8",
        )
        marker_bytes = marker_path.read_bytes()
        run_install(
            paths,
            self.manifest,
            PlainReporter(io.StringIO()),
            dry_run=True,
            urlopen=self._fixture_urlopen(printer_root),
        )
        self.assertEqual(marker_path.read_bytes(), marker_bytes)

        saved_variables_path.write_text(
            saved_variables_path.read_text(encoding="utf-8").replace(
                "enable_box = 1", "enable_box = 0"
            ),
            encoding="utf-8",
        )
        with mock.patch(
            "installer.runtime.uninstall.atomic_delete", side_effect=OSError("marker delete failed")
        ):
            with self.assertRaises(OSError):
                run_uninstall(
                    paths,
                    self.manifest,
                    self.compatibility,
                    PlainReporter(io.StringIO()),
                    input_stream=io.StringIO("Y\n"),
                    urlopen=self._fixture_urlopen(printer_root),
                )
        self.assertEqual(marker_path.read_bytes(), marker_bytes)
        self.assertTrue((printer_root / "config/tltg_optimized_state.yaml").exists())

        run_uninstall(
            paths,
            self.manifest,
            self.compatibility,
            PlainReporter(io.StringIO()),
            input_stream=io.StringIO("Y\n"),
            urlopen=self._fixture_urlopen(printer_root),
        )
        self.assertFalse(marker_path.exists())
        self.assertFalse((printer_root / "config/tltg_optimized_state.yaml").exists())

        self._run_install_with_urlopen(paths, self._fixture_urlopen(printer_root))
        self.assertTrue((printer_root / "config/tltg_optimized_state.yaml").exists())

        saved_variables = saved_variables_path.read_text(encoding="utf-8")
        self.assertEqual(
            klipper_cfg.resolve_unique_option(saved_variables, "Variables", "value_t0").value,
            "'custom0'",
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(
                saved_variables, "Variables", "vo_keep_loaded_between_prints"
            ).value,
            "1",
        )
        for tool in range(1, 4):
            self.assertEqual(
                klipper_cfg.resolve_unique_option(
                    saved_variables, "Variables", f"value_t{tool}"
                ).value,
                f"'slot{tool}'",
            )

    def test_saved_variable_write_failure_stops_install_without_file_fallback(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        before = saved_variables_path.read_bytes()
        base_urlopen = self._fixture_urlopen(printer_root)

        def failing_urlopen(request, timeout=0):
            if "/printer/gcode/script" in getattr(request, "full_url", str(request)):
                raise OSError("Moonraker unavailable")
            return base_urlopen(request, timeout=timeout)

        with self.assertRaises(SavedVariablePersistenceError) as raised:
            self._run_install_with_urlopen(paths, failing_urlopen)
        self.assertEqual(saved_variables_path.read_bytes(), before)
        # Nothing was written before the saved variables failed, which the USB installer's screen tells the operator.
        self.assertEqual(raised.exception.install_outcome, errors.OUTCOME_UNCHANGED)

        # A failure after the state is written, as in the boot logo, leaves Valve Oil installed and says so.
        with mock.patch("installer.runtime.runner.boot_logo.install", side_effect=InstallerError("logo")), \
                self.assertRaises(InstallerError) as raised:
            self._run_install_with_urlopen(paths, self._fixture_urlopen(printer_root))
        self.assertEqual(raised.exception.install_outcome, errors.OUTCOME_INSTALLED)
        self.assertTrue((printer_root / self.manifest.state_file).is_file())

    def test_saved_variable_queued_behind_klipper_startup_is_waited_for(self):
        # QIDI's start-up Box check holds Klipper's G-code queue: Moonraker takes SAVE_VARIABLE but doesn't reply,
        # and Klipper runs it once the check ends, or, when it never ends, not within the wait.
        for busy_seconds, completes in ((150, True), (None, False)):
            with self.subTest(busy_seconds=busy_seconds):
                printer_root, paths, _ = self._fixture("01.01.06.03")
                saved_variables_path = printer_root / "config/saved_variables.cfg"
                before = saved_variables_path.read_bytes()
                base_urlopen = self._fixture_urlopen(printer_root)
                now = [0.0]
                queued, scripts = [], []

                def busy():
                    return busy_seconds is None or now[0] < busy_seconds

                def urlopen(request, timeout=0):
                    url = getattr(request, "full_url", str(request))
                    if "/printer/gcode/script" in url:
                        scripts.append(json.loads(request.data.decode("utf-8"))["script"])
                        if busy():
                            queued.append(request)
                            now[0] += timeout
                            raise socket.timeout("timed out")
                    elif not busy():
                        while queued:
                            base_urlopen(queued.pop(0), timeout=timeout)
                    return base_urlopen(request, timeout=timeout)

                def sleep(seconds):
                    now[0] += seconds

                clock = types.SimpleNamespace(monotonic=lambda: now[0], sleep=sleep)
                with mock.patch.object(box_enablement, "time", clock):
                    if completes:
                        self._run_install_with_urlopen(paths, urlopen)
                        self.assertFalse(queued)
                        self.assertEqual(
                            klipper_cfg.resolve_unique_option(
                                saved_variables_path.read_text(encoding="utf-8"),
                                "Variables",
                                "vo_keep_loaded_between_prints",
                            ).value,
                            "1",
                        )
                    else:
                        with self.assertRaises(SavedVariablePersistenceError):
                            self._run_install_with_urlopen(paths, urlopen)
                        self.assertEqual(saved_variables_path.read_bytes(), before)
                        self.assertEqual(len(scripts), 1)
                # A queued command is never sent again.
                self.assertEqual(len(scripts), len(set(scripts)))

    def test_install_repairs_klipper_halted_on_a_config_error(self):
        # Klipper halts on a config error, so it has no print state or saved variables until it restarts. Only a
        # printer with Valve Oil installed is repaired: its own files may be what halted it.
        for case in ("new_install", "starts", "stays_halted"):
            with self.subTest(case=case):
                printer_root, paths, _ = self._fixture("01.01.06.03")
                saved_variables_path = printer_root / "config/saved_variables.cfg"
                base_urlopen = self._fixture_urlopen(printer_root)
                state_path = printer_root / "config/tltg_optimized_state.yaml"
                helper = paths.managed_klipper_root / "klippy/extras/tltg_probe_test.py"
                if case != "new_install":
                    self._run_install_with_urlopen(paths, base_urlopen)
                    # The missing helper is the config error that halted Klipper.
                    helper.unlink()
                    box_enablement.write_saved_variable_verify_marker(paths, {"vo_keep_loaded_between_prints": "1"})
                    saved_variables_path.write_text(
                        saved_variables_path.read_text(encoding="utf-8") + "vo_keep_loaded_between_prints = 1\n",
                        encoding="utf-8",
                    )
                state_before = state_path.read_bytes() if state_path.exists() else None
                klipper = {"halted": True, "restarts": 0, "stray": False}

                def urlopen(request, timeout=0):
                    url = getattr(request, "full_url", str(request))
                    if klipper["stray"] and "/printer/info" in url:
                        # While Klipper starts, QIDI's Moonraker can answer with a late reply to another request.
                        klipper["stray"] = False
                        return _JsonResponse({"result": {"eventtime": 1.0, "status": {"print_stats": {}}}})
                    if "/machine/services/restart" in url:
                        klipper["restarts"] += 1
                        klipper["halted"] = case == "stays_halted"
                        klipper["stray"] = True
                    elif klipper["halted"]:
                        if "/printer/info" in url:
                            return _JsonResponse({"result": {"state": "error", "process_id": 100 + klipper["restarts"],
                                                             "state_message": "Unable to load tltg_probe_test"}})
                        if "save_variables" in url or "/printer/gcode/script" in url:
                            raise AssertionError("saved variables used while Klipper is halted")
                        if "print_stats" in url:
                            return _JsonResponse({"result": {"status": {"print_stats": {}}}})
                    return base_urlopen(request, timeout=timeout)

                def without_sleeps():
                    """Klipper's restarts are waited for without real sleeps."""
                    stack = contextlib.ExitStack()
                    for name in ("restart_pending", "restart_klipper_service"):
                        stack.enter_context(mock.patch(f"installer.runtime.runner.{name}", functools.partial(
                            getattr(process_restart, name), sleep=lambda _seconds: None)))
                    return stack

                with without_sleeps():
                    if case == "starts":
                        self._run_install_with_urlopen(paths, urlopen)
                    else:
                        with self.assertRaises(InstallerError) as raised:
                            self._run_install_with_urlopen(paths, urlopen)

                if case == "new_install":
                    self.assertIn("Unable to load tltg_probe_test", raised.exception.message)
                    self.assertEqual(klipper["restarts"], 0)
                    self.assertFalse(state_path.exists())
                    self.assertFalse(helper.exists())
                    self.assertEqual(list(paths.backup_root.glob("*.zip")) if paths.backup_root.exists() else [], [])
                elif case == "starts":
                    self.assertTrue(helper.is_file())
                    self.assertEqual(klipper["restarts"], 1)
                    self.assertFalse(paths.restart_marker_path.exists())
                    self.assertFalse(saved_variable_verify_marker_path(paths).exists())
                    # With source already current, the next install over a halted Klipper restarts the service itself.
                    klipper["halted"] = True
                    with without_sleeps():
                        self._run_install_with_urlopen(paths, urlopen)
                    self.assertEqual(klipper["restarts"], 2)
                    self.assertFalse(paths.restart_marker_path.exists())
                else:
                    # The files are put back and nothing after them runs; Klipper is started on the files on disk.
                    self.assertIn("put back", raised.exception.message)
                    self.assertEqual(raised.exception.install_outcome, errors.OUTCOME_PUT_BACK)
                    self.assertFalse(helper.exists())
                    self.assertEqual(state_path.read_bytes(), state_before)
                    self.assertFalse(paths.restart_marker_path.exists())
                    self.assertTrue(saved_variable_verify_marker_path(paths).exists())
                    self.assertEqual(klipper["restarts"], 2)

    def _run_install_with_urlopen(self, paths, urlopen):
        return run_install(
            paths,
            self.manifest,
            PlainReporter(io.StringIO()),
            urlopen=urlopen,
        )

    def test_prior_managed_homing_values_migrate(self):
        firmware, source_variant, _ = SOURCE_CASES[0]
        printer_root, paths, stock_source = self._fixture(
            firmware, source_variant=source_variant
        )
        cfg = printer_root / "config/printer.cfg"
        cfg.write_text(
            cfg.read_text(encoding="utf-8")
            .replace("homing_speed: 50", "homing_speed: 100")
            .replace("homing_retract_speed: 200.0", "homing_retract_speed: 1000.0"),
            encoding="utf-8",
        )
        text = cfg.read_text(encoding="utf-8")
        for section in ("z_tilt", "bed_mesh"):
            text = klipper_cfg.set_option_value(text, section, "speed", "600")
        cfg.write_text(text, encoding="utf-8")
        write_installed_state(
            printer_root / "config/tltg_optimized_state.yaml",
            InstalledState(
                schema_version=1,
                package_id="qidi-max4-optimized",
                package_version=self.manifest.package.version,
                runtime_firmware=firmware,
                backup_label="prior-install",
                installed_at="2026-06-15T00:00:00Z",
                managed_tree=ManagedTreeState(
                    "config/tltg-optimized-macros", ()
                ),
                patch_ledger=(
                    PatchLedgerEntry(
                        "stepper_x_homing_speed",
                        "config/printer.cfg",
                        "stepper_x",
                        "homing_speed",
                        "50",
                        "100",
                        "applied",
                    ),
                    PatchLedgerEntry(
                        "stepper_y_homing_speed",
                        "config/printer.cfg",
                        "stepper_y",
                        "homing_speed",
                        "50",
                        "100",
                        "applied",
                    ),
                    PatchLedgerEntry(
                        "stepper_x_homing_retract_speed",
                        "config/printer.cfg",
                        "stepper_x",
                        "homing_retract_speed",
                        "200.0",
                        "1000.0",
                        "applied",
                    ),
                    PatchLedgerEntry(
                        "stepper_y_homing_retract_speed",
                        "config/printer.cfg",
                        "stepper_y",
                        "homing_retract_speed",
                        "200.0",
                        "1000.0",
                        "applied",
                    ),
                    *(
                        PatchLedgerEntry(
                            f"{section}_speed",
                            "config/printer.cfg",
                            section,
                            "speed",
                            "150",
                            "600",
                            "applied",
                        )
                        for section in ("z_tilt", "bed_mesh")
                    ),
                ),
            ),
        )

        self._run_install(paths)

        self._assert_homing_speed(printer_root, "65")
        self._assert_homing_retract_speed(printer_root, "500.0")
        text = cfg.read_text(encoding="utf-8")
        for section, option, value in (("z_tilt", "speed", "565"),
                                       ("bed_mesh", "speed", "565"),
                                       ("printer", "max_velocity", "565")):
            self.assertEqual(
                klipper_cfg.resolve_unique_option(text, section, option).value, value)
        state = load_installed_state(
            printer_root / "config/tltg_optimized_state.yaml"
        )
        speeds = {
            entry.id: entry
            for entry in state.patch_ledger
            if entry.id
            in {"stepper_x_homing_speed", "stepper_y_homing_speed"}
        }
        self.assertEqual({entry.expected for entry in speeds.values()}, {"50"})
        self.assertEqual({entry.desired for entry in speeds.values()}, {"65"})
        self.assertEqual(state.source_patches[0].original_bytes, stock_source)

    def test_auto_update_child_initializes_defaults_and_advances_checksum(self):
        firmware, source_variant, desired_sha256 = SOURCE_CASES[0]
        printer_root, _, _ = self._fixture(
            firmware, source_variant=source_variant
        )
        bundle_root = self._installed_bundle(printer_root, "auto-update-lifecycle-")
        paths = resolve_runtime_paths(
            bundle_root=bundle_root,
            environ=build_env(
                printer_root,
                moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats",
            ),
        )
        archive = _release_archive()
        checksum = hashlib.sha256(archive).hexdigest()
        state_path(paths).write_text(
            json.dumps({"latest_checksum": "0" * 64}), encoding="utf-8"
        )
        enrollment_path(paths).write_text("1\n", encoding="utf-8")
        process = {"id": 100}
        child_calls = []
        fallback_urlopen = self._fixture_urlopen(printer_root)

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith(".sha256"):
                return _BytesResponse(f"{checksum} bundle\n".encode())
            if url.endswith(".tar.gz"):
                return _BytesResponse(archive)
            if url.endswith("/printer/info"):
                return _JsonResponse(
                    {"result": {"state": "ready", "process_id": process["id"]}}
                )
            if url.endswith("/machine/services/restart"):
                process["id"] += 1
                return _JsonResponse({"result": "ok"})
            return fallback_urlopen(request, timeout=timeout)

        def child_run(command, **kwargs):
            child_calls.append((command, kwargs))
            self.assertEqual(kwargs["env"][LOCK_HELD_ENV], "1")
            child_paths = resolve_runtime_paths(
                bundle_root=REPO_ROOT,
                environ=build_env(
                    printer_root,
                    moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats",
                ),
            )
            run_install(
                child_paths,
                self.manifest,
                PlainReporter(io.StringIO()),
                urlopen=urlopen,
                environ=kwargs["env"],
            )
            return subprocess.CompletedProcess(command, 0)

        result = run_auto_update_check(
            paths=paths,
            reporter=PlainReporter(io.StringIO()),
            environ={
                "TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256",
                "TLTG_AUTO_UPDATE_ARCHIVE_URL": "https://example.invalid/latest.tar.gz",
            },
            urlopen=urlopen,
            run=child_run,
        )

        self.assertEqual(result.action, "updated")
        self.assertEqual(len(child_calls), 1)
        # The release was staged under the installer's cache in the printer user's home, not /tmp, which QIDI's
        # firmware package empties, and nothing of it is left once the bundle is in place.
        self.assertEqual(child_calls[0][0][1], str(bundle_root / "install.sh"))
        self.assertEqual((bundle_root / "install.sh").read_bytes(), b"#!/bin/sh\nexit 0\n")
        self.assertEqual(list(staging_root(paths).iterdir()), [])
        self.assertTrue(staging_root(paths).is_relative_to(printer_root.parent / ".cache"))
        status = valve_oil_status.load_status(paths)
        self.assertEqual((status.run.kind, status.run.state), ("automatic", valve_oil_status.SUCCEEDED))
        saved_variables = (printer_root / "config/saved_variables.cfg").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            klipper_cfg.resolve_unique_option(
                saved_variables,
                "Variables",
                "vo_keep_loaded_between_prints",
            ).value,
            "1",
        )
        self.assertEqual(
            json.loads(state_path(paths).read_text(encoding="utf-8"))["latest_checksum"],
            checksum,
        )
        self.assertEqual(
            hashlib.sha256(
                (
                    printer_root
                    / "klipper/klippy/extras/homing.py"
                ).read_bytes()
            ).hexdigest(),
            desired_sha256,
        )
        self.assertFalse(paths.restart_marker_path.exists())

    def test_the_installed_bundle_moves_from_the_earlier_folder_and_leaves_a_link(self):
        home = temp_path("bundle-folder-")
        legacy, installed = home / LEGACY_BUNDLE_ROOT_NAME, home / BUNDLE_ROOT_NAME
        legacy.mkdir()
        (legacy / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        # The cache folders under their earlier names go; Valve Oil's own stay.
        for name in (*LEGACY_CACHE_DIRECTORIES, f"{CACHE_DIRECTORY}/addons"):
            (home / name).mkdir(parents=True)
            (home / name / "job.log").write_text("log\n", encoding="utf-8")
        auto_update.retire_legacy_bundle(home)
        self.assertFalse(legacy.is_symlink(), "kept until ~/valve-oil holds the bundle")
        self.assertEqual([str(path.relative_to(home)) for path in sorted((home / ".cache").rglob("*"))],
                         [".cache/valve-oil", ".cache/valve-oil/addons", ".cache/valve-oil/addons/job.log"])
        copy_bundle(legacy, installed, pwd.getpwuid(os.getuid()))
        for _ in range(2):
            auto_update.retire_legacy_bundle(home)
            self.assertEqual(legacy.resolve(), installed.resolve())
            self.assertEqual((legacy / "install.sh").read_text(encoding="utf-8"), "#!/bin/sh\n")
            self.assertEqual(sorted(path.name for path in home.iterdir()), sorted([".cache", legacy.name, installed.name]))

    def test_auto_update_switch_changes_only_enrollment_without_sudo(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats")
        units = printer_root.parent / "system/etc/systemd/system"
        units.mkdir(parents=True)
        env[SYSTEM_ROOT_ENV] = str(printer_root.parent / "system")
        others = sorted(path.name for path in printer_root.iterdir())

        def switch(*flags):
            stream = io.StringIO()
            with mock.patch("subprocess.run", side_effect=AssertionError("the switch ran a command")):
                self.assertEqual(main(["auto-update", *flags, "--plain"], bundle_root=REPO_ROOT, environ=env,
                                      stream=stream), 0)
            return stream.getvalue()

        output = switch("--enable-systemd")
        self.assertIn("Automatic updates are on", output)
        self.assertIn("isn't scheduled", output)
        self.assertEqual(enrollment_path(paths).read_text(encoding="utf-8"), "1\n")
        self.assertEqual(enrollment_path(paths).stat().st_mode & 0o777, 0o600)
        # QIDI's firmware package cleans the config folder; enrollment lives outside it.
        shutil.rmtree(printer_root / "config")
        (printer_root / "config").mkdir()
        self.assertTrue(enrollment_path(paths).exists())
        (units / TIMER_NAME).write_text(TIMER_CONTENT, encoding="utf-8")
        output = switch("--status")
        self.assertIn("Automatic updates are on", output)
        self.assertNotIn("isn't scheduled", output)
        output = switch("--disable")
        self.assertIn("Automatic updates are off", output)
        self.assertEqual(sorted(path.name for path in printer_root.iterdir()), others)
        self.assertTrue((units / TIMER_NAME).exists())
        switch("--enable")
        self.assertTrue(enrollment_path(paths).exists())
        switch("--disable-systemd")
        self.assertFalse(enrollment_path(paths).exists())

    def test_earlier_releases_automatic_update_units_mean_enrolled_and_are_replaced(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        environ = {SYSTEM_ROOT_ENV: str(printer_root.parent / "system")}
        units = printer_root.parent / "system/etc/systemd/system"
        units.mkdir(parents=True)
        legacy_service = ("[Unit]\nDescription=TLTG QIDI Max 4 optimized config auto-update\n"
                          "Wants=network-online.target\nAfter=network-online.target\n\n[Service]\nType=oneshot\n"
                          "User=qidi\nWorkingDirectory=/home/qidi/tltg-optimized-macros\n"
                          "ExecStart=/home/qidi/tltg-optimized-macros/auto-update.sh --run\n")
        legacy_timer = ("[Unit]\nDescription=Run TLTG optimized config auto-update hourly\n\n[Timer]\n"
                        "OnBootSec=10min\nOnUnitActiveSec=1h\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
        self.assertFalse(auto_update.adopt_legacy_enrollment(paths, environ))
        (units / TIMER_NAME).write_text(legacy_timer, encoding="utf-8")
        self.assertTrue(auto_update.adopt_legacy_enrollment(paths, environ))
        self.assertTrue(enrollment_path(paths).exists())
        self.assertEqual(auto_update.patch_service(legacy_service), auto_update.SERVICE_CONTENT)
        self.assertEqual(auto_update.patch_timer(legacy_timer), TIMER_CONTENT)
        self.assertEqual(auto_update.patch_timer(""), TIMER_CONTENT)
        with self.assertRaises(InstallerError):
            auto_update.patch_timer(legacy_timer.replace("OnUnitActiveSec=1h", "OnUnitActiveSec=5min"))

    def test_enrolled_current_release_repairs_saved_variables(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        self._run_install(paths)
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_variables_path.write_text(
            saved_variables_path.read_text(encoding="utf-8").replace(
                "vo_keep_loaded_between_prints = 1\n", ""
            ),
            encoding="utf-8",
        )
        checksum = "a" * 64
        enrollment_path(paths).write_text("1\n", encoding="utf-8")
        state_path(paths).write_text(json.dumps({"latest_checksum": checksum}), encoding="utf-8")
        base_urlopen = self._fixture_urlopen(printer_root)

        def urlopen(request, timeout=0):
            if getattr(request, "full_url", str(request)).endswith(".sha256"):
                return _BytesResponse(f"{checksum} bundle\n".encode())
            return base_urlopen(request, timeout=timeout)

        result = run_auto_update_check(
            paths=paths,
            reporter=PlainReporter(io.StringIO()),
            environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"},
            urlopen=urlopen,
        )
        self.assertEqual(result.action, "already-current")
        self.assertEqual(
            klipper_cfg.resolve_unique_option(
                saved_variables_path.read_text(encoding="utf-8"),
                "Variables",
                "vo_keep_loaded_between_prints",
            ).value,
            "1",
        )

    def test_unattended_saved_variable_repair_skips_active_and_unavailable_printers(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        self._run_install(paths)
        before = (printer_root / "config/saved_variables.cfg").read_bytes()
        checksum = "a" * 64
        enrollment_path(paths).write_text("1\n", encoding="utf-8")
        state_path(paths).write_text(json.dumps({"latest_checksum": checksum}), encoding="utf-8")

        active_base_urlopen = self._fixture_urlopen(printer_root, state="printing")

        def active_urlopen(request, timeout=0):
            if getattr(request, "full_url", str(request)).endswith(".sha256"):
                return _BytesResponse(f"{checksum} bundle\n".encode())
            return active_base_urlopen(request, timeout=timeout)

        def unavailable_urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith(".sha256"):
                return _BytesResponse(f"{checksum} bundle\n".encode())
            return _JsonResponse({"result": {}})

        def halted_urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith("/printer/info"):
                return _JsonResponse({"result": {"state": "error", "state_message": "Config error", "process_id": 1}})
            return unavailable_urlopen(request, timeout=timeout)

        for urlopen, expected_action in (
            (active_urlopen, "skipped-active-print"),
            (unavailable_urlopen, "skipped-unknown-printer-state"),
            # A Klipper halted on an error isn't idle: nothing is updated onto it.
            (halted_urlopen, "skipped-klipper-not-ready"),
        ):
            result = run_auto_update_check(
                paths=paths,
                reporter=PlainReporter(io.StringIO()),
                environ={
                    "TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256",
                    "TLTG_AUTO_UPDATE_ARCHIVE_URL": "https://example.invalid/latest.tar.gz",
                },
                urlopen=urlopen,
            )
            self.assertEqual(result.action, expected_action)
        self.assertEqual((printer_root / "config/saved_variables.cfg").read_bytes(), before)

    def test_new_release_waits_while_a_heater_is_on_outside_a_print(self):
        # Restarting Klipper with a heater on shuts its MCU down until a firmware restart.
        printer_root, paths, _ = self._fixture("01.01.06.03")
        old_checksum = "1" * 64
        state_path(paths).write_text(json.dumps({"latest_checksum": old_checksum}), encoding="utf-8")
        enrollment_path(paths).write_text("1\n", encoding="utf-8")

        for heater_targets in (
            {"extruder": 0.0, "heater_bed": 100.0, "heater_generic chamber": 0.0},
            {"extruder": 0.0, "heater_bed": 0.0, "heater_generic chamber": 60.0},
        ):
            with self.subTest(heater_targets=heater_targets):
                printer = moonraker_urlopen("standby", heater_targets=heater_targets)
                opened_urls = []

                def urlopen(request, timeout=0):
                    url = getattr(request, "full_url", str(request))
                    opened_urls.append(url)
                    if url.endswith(".sha256"):
                        return _BytesResponse(f"{'2' * 64} bundle\n".encode())
                    return printer(request, timeout=timeout)

                result = run_auto_update_check(
                    paths=paths,
                    reporter=PlainReporter(io.StringIO()),
                    environ={
                        "TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256",
                        "TLTG_AUTO_UPDATE_ARCHIVE_URL": "https://example.invalid/latest.tar.gz",
                    },
                    urlopen=urlopen,
                    run=lambda command, **kwargs: self.fail(f"Unexpected command: {command}"),
                )

                self.assertEqual(result.action, "skipped-active-print")
                self.assertFalse(any(url.endswith(".tar.gz") for url in opened_urls))
                self.assertEqual(
                    json.loads(state_path(paths).read_text(encoding="utf-8"))["latest_checksum"], old_checksum
                )

    def _waiting_printer(self, latest="99.1.1.1"):
        """An installed Max 4 on QIDI firmware this release updates from, with a Box connected since the last install
        on QIDI's Box firmware, a newer release published, and a Klipper restart an earlier install left pending."""
        printer_root, paths, _ = self._fixture("01.01.06.05", source_variant="sync-reset")
        (printer_root / "config/tltg_optimized_state.yaml").write_text("", encoding="utf-8")
        (printer_root / "config/box.cfg").write_text("[mcu mcu_box1]\nserial: /dev/serial/by-id/usb-box1\n",
                                                     encoding="utf-8")
        paths.restart_marker_path.write_text("{}", encoding="utf-8")
        printer = self.manifest.printer_for_firmware("01.01.06.05")
        box_stock = load_board_manifest(REPO_ROOT, "box", printer)["box"]["stock_version"]
        versions = {"mcu": pinned_board_image(REPO_ROOT, printer, "mcu").version,
                    "mcu THR": pinned_board_image(REPO_ROOT, printer, "thr").version, "mcu mcu_box1": box_stock}
        state = {"print": "standby", "opened": []}

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            state["opened"].append(url)
            if getattr(request, "data", None) is not None:
                self.fail(f"The check changed something: {url}")
            if url.endswith(".sha256"):
                return _BytesResponse(f"{'2' * 64}  valve-oil.tar.gz\n".encode(), url=(
                    f"https://github.com/thelegendtubaguy/valve-oil/releases/download/v{latest}/"
                    "valve-oil.tar.gz.sha256" if latest else url))
            if url.endswith("/printer/info"):
                return _JsonResponse({"result": {"state": "ready", "process_id": 100}})
            if "printer/objects/query" not in url:
                self.fail(f"Unexpected URL: {url}")
            names = [urllib.parse.unquote(part.split("=")[0]) for part in urllib.parse.urlsplit(url).query.split("&")]
            status = {"print_stats": {"state": state["print"]}, "webhooks": {"state": "ready"},
                      "heaters": {"available_heaters": []},
                      **{name: {"mcu_version": version} for name, version in versions.items()}}
            return _JsonResponse({"result": {"status": {name: status[name] for name in names if name in status}}})

        def forbidden(*args, **kwargs):
            raise AssertionError(f"The check changed something: {args} {kwargs}")

        return printer_root, paths, urlopen, forbidden, state

    def test_unenrolled_check_only_publishes_what_is_waiting(self):
        # The safety boundary: without enrollment the check never installs, starts a firmware update, reconciles,
        # reads or flashes a board, stops or restarts a service, or reboots. It records the latest checksum.
        printer_root, paths, urlopen, forbidden, state = self._waiting_printer()
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats")
        env["TLTG_AUTO_UPDATE_CHECKSUM_URL"] = "https://example.invalid/latest.sha256"
        before = snapshot_tree(printer_root)
        check = functools.partial(run_auto_update_check, urlopen=urlopen, run=forbidden, start_firmware=forbidden,
                                  deliver=forbidden, read_session=forbidden)
        with mock.patch("installer.runtime.cli.run_auto_update_check", side_effect=check), \
                mock.patch("installer.runtime.cli.repair_installer_ownership", side_effect=forbidden), \
                mock.patch("installer.runtime.cli.recover_pending_system_optimization", side_effect=forbidden), \
                mock.patch("installer.runtime.cli.maybe_reconcile_system_optimizations", side_effect=forbidden), \
                mock.patch("installer.runtime.cli.maybe_schedule_host_reboot", side_effect=forbidden), \
                mock.patch("installer.runtime.cli.maybe_schedule_recommended_reboot", side_effect=forbidden):
            self.assertEqual(main(["auto-update", "--run", "--plain"], bundle_root=REPO_ROOT, environ=env,
                                  stream=io.StringIO()), 0)

        self.assertFalse(any(url.endswith(".tar.gz") for url in state["opened"]))
        self.assertEqual(json.loads(state_path(paths).read_text(encoding="utf-8"))["latest_checksum"], "2" * 64)
        written = {"config/tltg_optimized_auto_update_state.json", valve_oil_status.STATUS_FILE,
                   valve_oil_status.LOCK_FILE, ".tltg_optimized_installer.lock"}
        after = snapshot_tree(printer_root)
        self.assertEqual({key: value for key, value in after.items() if key not in written},
                         {key: value for key, value in before.items() if key not in written})
        status = valve_oil_status.load_status(paths)
        self.assertEqual((status.check.enrolled, status.check.result), (False, "ok"))
        self.assertEqual([(item.kind, item.version, item.board, item.blocked_by) for item in status.pending], [
            ("release", "99.1.1.1", None, ()),
            ("firmware", self.manifest.printer_for_firmware("01.01.06.05").target_firmware.version, None, ()),
            ("board", pinned_board_image(REPO_ROOT, self.manifest.printer_for_firmware("01.01.06.05"), "box").version,
             "box1", ()),
        ])
        self.assertTrue(paths.restart_marker_path.exists())
        # Enrolled but printing: still nothing changes, and the waiting work says why it hasn't run.
        paths.restart_marker_path.unlink()
        enrollment_path(paths).write_text("1\n", encoding="utf-8")
        state["print"] = "printing"
        result = run_auto_update_check(paths=paths, reporter=PlainReporter(io.StringIO()), environ=env,
                                       urlopen=urlopen, run=forbidden, start_firmware=forbidden, deliver=forbidden,
                                       read_session=forbidden)
        self.assertEqual(result.action, "skipped-active-print")
        self.assertTrue(all(item.blocked_by[0].code == "printing" for item in valve_oil_status.load_status(paths).pending))

    def test_enrolled_check_starts_the_firmware_update_still_waiting_after_the_release(self):
        # The mirror was unreachable when the release was installed; the release is current now.
        printer_root, paths, urlopen, forbidden, _ = self._waiting_printer(latest=None)
        paths.restart_marker_path.unlink()
        enrollment_path(paths).write_text("1\n", encoding="utf-8")
        state_path(paths).write_text(json.dumps({"latest_checksum": "2" * 64}), encoding="utf-8")
        boards = [{"board": board, "state": "current"} for board in ("thr", "mcu")]
        (printer_root / ".tltg_board_admission.json").write_text(json.dumps({
            "schema_version": 1, "package_version": self.manifest.package.version, "printer": "max4",
            "firmware": "01.01.06.05", "target_firmware": "01.01.07.01", "new_install": False, "boards": boards}))
        started = []

        def start(**kwargs):
            started.append(kwargs)
            return firmware_update.FirmwareUpdateResult(firmware_update.RUNNING)

        with mock.patch("installer.runtime.auto_update._installation_requires_reconciliation", return_value=False), \
                mock.patch("installer.runtime.auto_update.maybe_repair_saved_variables"):
            result = run_auto_update_check(
                paths=paths, reporter=PlainReporter(io.StringIO()),
                environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"},
                urlopen=urlopen, run=forbidden, start_firmware=start, deliver=forbidden, read_session=forbidden,
                disk_usage=lambda path: types.SimpleNamespace(free=2 ** 40))
        self.assertEqual(result.action, "firmware-update-started")
        self.assertEqual([(call["kind"], call["follow"], call["input_stream"]) for call in started],
                         [("automatic", False, None)])

    def test_enrolled_check_waits_for_the_update_window_and_keeps_the_light_off(self):
        # The printer's clock runs in UTC; the install looked the operator's timezone up, and the window is read in it.
        printer_root, paths, urlopen, forbidden, _ = self._waiting_printer(latest=None)
        paths.restart_marker_path.unlink()
        enrollment_path(paths).write_text("1\n", encoding="utf-8")
        state_path(paths).write_text(json.dumps({"latest_checksum": "2" * 64}), encoding="utf-8")
        (printer_root / ".tltg_board_admission.json").write_text(json.dumps({
            "schema_version": 1, "package_version": self.manifest.package.version, "printer": "max4",
            "firmware": "01.01.06.05", "target_firmware": "01.01.07.01", "new_install": False,
            "boards": [{"board": board, "state": "current"} for board in ("thr", "mcu")]}))

        def lookups(answers):
            def lookup(request, timeout=0):
                answer = answers[request.full_url]
                if isinstance(answer, Exception):
                    raise answer
                return _BytesResponse(answer.encode())
            return lookup

        update_window.ensure_timezone(paths, reporter=PlainReporter(io.StringIO()), urlopen=lookups({
            "https://ipapi.co/timezone/": OSError("unreachable"),
            "https://worldtimeapi.org/api/ip": '{"timezone": "America/Chicago", "utc_offset": "-06:00"}'}))
        # A saved timezone is kept at the next install.
        update_window.ensure_timezone(paths, reporter=PlainReporter(io.StringIO()),
                                      urlopen=lookups({"https://ipapi.co/timezone/": "Europe/Berlin"}))
        settings = update_window.load_settings(paths)
        self.assertEqual((settings.timezone, settings.timezone_source), ("America/Chicago", "lookup"))

        hour = datetime.now(ZoneInfo("America/Chicago")).hour
        light = {"value": 0.0}

        def printer(request, timeout=0):
            if "output_pin%20caselight" in getattr(request, "full_url", str(request)):
                return _JsonResponse({"result": {"status": {"output_pin caselight": dict(light)}}})
            return urlopen(request, timeout=timeout)

        started = []

        def start(**kwargs):
            started.append(kwargs["kind"])
            return firmware_update.FirmwareUpdateResult(firmware_update.RUNNING)

        def check():
            with mock.patch("installer.runtime.auto_update._installation_requires_reconciliation", return_value=False), \
                    mock.patch("installer.runtime.auto_update.maybe_repair_saved_variables"):
                return run_auto_update_check(
                    paths=paths, reporter=PlainReporter(io.StringIO()),
                    environ={"TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256"},
                    urlopen=printer, run=forbidden, start_firmware=start, deliver=forbidden, read_session=forbidden,
                    disk_usage=lambda path: types.SimpleNamespace(free=2 ** 40))

        # Outside the window (here it wraps past midnight), the check only publishes; the work waits, saying why.
        update_window.set_window(paths, update_window.Window((hour + 1) % 24, hour))
        self.assertEqual(check().action, "skipped-outside-window")
        self.assertEqual(started, [])
        pending = valve_oil_status.load_status(paths).pending
        self.assertTrue(pending)
        self.assertTrue(all("outside_window" in [blocker.code for blocker in item.blocked_by] for item in pending))
        self.assertFalse(update_window.lights_hold_path(paths).exists())

        # Inside it, the firmware update starts, and the light that was off stays off through the Klipper starts.
        update_window.set_window(paths, update_window.Window(hour, (hour + 1) % 24))
        self.assertEqual(check().action, "firmware-update-started")
        self.assertEqual(started, ["automatic"])
        hold = json.loads(update_window.lights_hold_path(paths).read_text(encoding="utf-8"))
        self.assertGreater(hold["until"], time.time())

        # A light that is on is left to Klipper.
        update_window.lights_hold_path(paths).unlink()
        light["value"] = 1.0
        self.assertEqual(check().action, "firmware-update-started")
        self.assertFalse(update_window.lights_hold_path(paths).exists())

    def test_enrolled_auto_update_recovers_after_firmware_config_cleanup(self):
        firmware = "01.01.07.01"
        printer_root, installed_paths, _ = self._fixture(
            firmware, source_variant="sync-reset"
        )
        self._run_install(installed_paths)
        enrollment_path(installed_paths).write_text("1\n", encoding="utf-8")

        saved_variables = (printer_root / "config/saved_variables.cfg").read_bytes()
        box_config = (printer_root / "config/box.cfg").read_bytes()
        shutil.rmtree(printer_root / "config")
        shutil.copytree(
            REPO_ROOT
            / "installer/stock/qidi-max4-defaults/firmwares"
            / firmware
            / "config",
            printer_root / "config",
        )
        (printer_root / "config/saved_variables.cfg").write_bytes(saved_variables)
        (printer_root / "config/box.cfg").write_bytes(box_config)
        (installed_paths.managed_klipper_root / "klippy/extras/homing.py").write_bytes(
            homing_sync_reset_fixture_bytes()
        )

        # The update check of an install that stopped before it moved to ~/valve-oil runs from the earlier folder.
        bundle_root = self._installed_bundle(printer_root, "auto-update-recovery-", LEGACY_BUNDLE_ROOT_NAME)
        paths = resolve_runtime_paths(
            bundle_root=bundle_root,
            environ=build_env(
                printer_root,
                moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats",
            ),
        )
        self.assertTrue(enrollment_path(paths).exists())
        self.assertFalse(state_path(paths).exists())

        archive = _release_archive()
        checksum = hashlib.sha256(archive).hexdigest()
        process = {"id": 100}
        fallback_urlopen = self._fixture_urlopen(printer_root)

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith(".sha256"):
                return _BytesResponse(f"{checksum} bundle\n".encode())
            if url.endswith(".tar.gz"):
                return _BytesResponse(archive)
            if url.endswith("/printer/info"):
                return _JsonResponse(
                    {"result": {"state": "ready", "process_id": process["id"]}}
                )
            if url.endswith("/machine/services/restart"):
                process["id"] += 1
                return _JsonResponse({"result": "ok"})
            return fallback_urlopen(request, timeout=timeout)

        def child_run(command, **kwargs):
            child_paths = resolve_runtime_paths(
                bundle_root=REPO_ROOT,
                environ=build_env(
                    printer_root,
                    moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats",
                ),
            )
            run_install(
                child_paths,
                self.manifest,
                PlainReporter(io.StringIO()),
                urlopen=urlopen,
                environ=kwargs["env"],
            )
            return subprocess.CompletedProcess(command, 0)

        result = run_auto_update_check(
            paths=paths,
            reporter=PlainReporter(io.StringIO()),
            environ={
                **build_env(
                    printer_root,
                    moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats",
                ),
                "TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256",
                "TLTG_AUTO_UPDATE_ARCHIVE_URL": "https://example.invalid/latest.tar.gz",
            },
            urlopen=urlopen,
            run=child_run,
        )

        self.assertEqual(result.action, "reconciled")
        self.assertEqual(
            json.loads(state_path(paths).read_text(encoding="utf-8"))["latest_checksum"],
            checksum,
        )
        self.assertTrue((printer_root / "config/tltg_optimized_state.yaml").exists())
        self.assertIn(
            "[include tltg-optimized-macros/*.cfg]",
            (printer_root / "config/printer.cfg").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            hashlib.sha256(
                (paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes()
            ).hexdigest(),
            SYNC_RESET_DESIRED_HOMING_SHA256,
        )

    def test_uninstall_removes_enrollment_before_the_update_check(self):
        # With nothing else left to uninstall, uninstall still ends automatic updates first, then removes the check's
        # units, its status, and its records.
        printer_root, paths, _ = self._fixture("01.01.06.03")
        environ = {SYSTEM_ROOT_ENV: str(printer_root.parent / "system")}
        units = printer_root.parent / "system/etc/systemd/system"
        units.mkdir(parents=True)
        (units / TIMER_NAME).write_text(TIMER_CONTENT, encoding="utf-8")
        (units / auto_update.SERVICE_NAME).write_text(auto_update.SERVICE_CONTENT, encoding="utf-8")
        enrollment_path(paths).write_text("1\n", encoding="utf-8")
        valve_oil_status.record_check(paths, enrolled=True, printer=valve_oil_status.PrinterInfo(),
                                      installed_release=None, pending=())
        enrolled_at_removal = []
        remove = auto_update.remove_auto_update_units

        def removing_units(**kwargs):
            enrolled_at_removal.append(enrollment_path(paths).exists())
            return remove(**kwargs)

        with mock.patch("installer.runtime.uninstall.remove_auto_update_units", side_effect=removing_units):
            result = run_uninstall(paths, self.manifest, self.compatibility, PlainReporter(io.StringIO()),
                                   urlopen=moonraker_urlopen(), environ=environ)

        self.assertIsNone(result.backup_label)
        self.assertEqual(enrolled_at_removal, [False])
        self.assertEqual(list(units.iterdir()), [])
        self.assertFalse(valve_oil_status.status_path(paths).exists())

    def test_auto_update_checksum_mismatch_preserves_bundle_and_release_state(self):
        printer_root, _, _ = self._fixture("01.01.06.03")
        bundle_root = self._installed_bundle(printer_root, "auto-update-mismatch-")
        (bundle_root / "old.txt").write_text("old bundle", encoding="utf-8")
        # An interrupted earlier run's download is cleared too.
        (staging_root(resolve_runtime_paths(bundle_root=bundle_root, environ=build_env(printer_root, moonraker_url="x")))
         / "run-interrupted").mkdir(parents=True)
        paths = resolve_runtime_paths(
            bundle_root=bundle_root,
            environ=build_env(
                printer_root,
                moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats",
            ),
        )
        old_checksum = "1" * 64
        advertised_checksum = "2" * 64
        state_path(paths).write_text(
            json.dumps({"latest_checksum": old_checksum}), encoding="utf-8"
        )
        enrollment_path(paths).write_text("1\n", encoding="utf-8")
        child_calls = []

        def urlopen(request, timeout=0):
            url = getattr(request, "full_url", str(request))
            if url.endswith(".sha256"):
                return _BytesResponse(f"{advertised_checksum} bundle\n".encode())
            if url.endswith(".tar.gz"):
                return _BytesResponse(b"not the advertised archive")
            if "printer/objects/query" in url or url.endswith("/printer/info"):
                return moonraker_urlopen()(request, timeout=timeout)
            self.fail(f"Unexpected URL: {url}")

        with self.assertRaises(AutoUpdateError):
            run_auto_update_check(
                paths=paths,
                reporter=PlainReporter(io.StringIO()),
                environ={
                    "TLTG_AUTO_UPDATE_CHECKSUM_URL": "https://example.invalid/latest.sha256",
                    "TLTG_AUTO_UPDATE_ARCHIVE_URL": "https://example.invalid/latest.tar.gz",
                },
                urlopen=urlopen,
                run=lambda command, **kwargs: (
                    child_calls.append(command)
                    or subprocess.CompletedProcess(command, 0)
                ),
            )

        self.assertEqual(child_calls, [])
        self.assertEqual(
            json.loads(state_path(paths).read_text(encoding="utf-8"))[
                "latest_checksum"
            ],
            old_checksum,
        )
        self.assertEqual(
            (bundle_root / "old.txt").read_text(encoding="utf-8"), "old bundle"
        )
        self.assertEqual(list(staging_root(paths).iterdir()), [])

    def test_source_write_failure_rolls_back_source_and_marker(self):
        for firmware, source_variant, _ in RESTORE_CASES:
            with self.subTest(firmware=firmware, source_variant=source_variant):
                printer_root, paths, stock_source = self._fixture(
                    firmware, source_variant=source_variant
                )
                with mock.patch(
                    "installer.runtime.runner.mirror_tree",
                    side_effect=RuntimeError("force failure after source deployment"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "force failure"):
                        self._run_install(paths)

                self.assertEqual(
                    (paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes(),
                    stock_source,
                )
                self.assertFalse(paths.restart_marker_path.exists())
                self.assertFalse(
                    (printer_root / "config/tltg_optimized_state.yaml").exists()
                )

    def test_restore_rejects_non_idle_printer_after_confirmation_without_replacement(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        install = self._run_install(paths)
        assert install.backup_zip_path is not None
        printer_config = printer_root / "config/printer.cfg"
        printer_config.write_text("[printer]\nmodified: yes\n", encoding="utf-8")
        before = printer_config.read_bytes()

        for state in ("printing", "paused", None):
            with self.subTest(state=state):
                with self.assertRaises((ActivePrintError, PrinterStateError)):
                    run_restore_helper(
                        paths,
                        self.manifest,
                        stream=io.StringIO(),
                        input_stream=io.StringIO("RESTORE\n"),
                        backup_path=str(install.backup_zip_path),
                        urlopen=moonraker_urlopen(state),
                    )
                self.assertEqual(printer_config.read_bytes(), before)

    def test_interrupt_after_configuration_write_rolls_back_and_preserves_live_saved_variables(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        saved_variables_path = printer_root / "config/saved_variables.cfg"
        saved_before = saved_variables_path.read_bytes()
        printer_before = (printer_root / "config/printer.cfg").read_bytes()

        with mock.patch(
            "installer.runtime.runner.mirror_tree", side_effect=KeyboardInterrupt
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._run_install(paths)

        self.assertEqual((printer_root / "config/printer.cfg").read_bytes(), printer_before)
        self.assertNotEqual(saved_variables_path.read_bytes(), saved_before)
        self.assertIn(
            b"vo_keep_loaded_between_prints = 1", saved_variables_path.read_bytes()
        )
        self.assertFalse(paths.restart_marker_path.exists())
        self.assertFalse((printer_root / "config/tltg_optimized_state.yaml").exists())

    def test_interrupt_after_configuration_commit_preserves_committed_state(self):
        printer_root, paths, _ = self._fixture("01.01.06.03")
        with mock.patch(
            "installer.runtime.runner.maybe_apply_system_optimizations",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self._run_install(paths)

        self.assertTrue((printer_root / "config/tltg_optimized_state.yaml").exists())
        self._assert_homing_speed(printer_root, "65")
        self.assertTrue(paths.restart_marker_path.exists())

    def test_source_inclusive_restore_restores_stock_source(self):
        for firmware, source_variant, _ in RESTORE_CASES:
            with self.subTest(firmware=firmware, source_variant=source_variant):
                printer_root, paths, stock_source = self._fixture(
                    firmware, source_variant=source_variant
                )
                install = self._run_install(paths)
                assert install.backup_zip_path is not None
                expected_config = load_backup_snapshot(
                    backup_zip_path=install.backup_zip_path, source_directory="config"
                )
                (printer_root / "config/printer.cfg").write_text(
                    "[printer]\nmodified: yes\n", encoding="utf-8"
                )

                result = run_restore_helper(
                    paths,
                    self.manifest,
                    stream=io.StringIO(),
                    input_stream=io.StringIO("RESTORE\nY\n"),
                    backup_path=str(install.backup_zip_path),
                    urlopen=moonraker_urlopen(),
                )

                self.assertEqual(result, 0)
                self.assertEqual(
                    snapshot_runtime_tree(
                        printer_data_root=printer_root, source_directory="config"
                    ),
                    expected_config,
                )
                self.assertEqual(
                    (paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes(),
                    stock_source,
                )
                self.assertFalse(paths.restart_marker_path.exists())

    def test_uninstall_restores_stock_config_and_source(self):
        for firmware, source_variant, _ in RESTORE_CASES:
            with self.subTest(firmware=firmware, source_variant=source_variant):
                printer_root, paths, stock_source = self._fixture(
                    firmware, source_variant=source_variant
                )
                # Operator speeds: two above the 565 mm/s cap, one below it.
                cfg = printer_root / "config/printer.cfg"
                text = klipper_cfg.set_option_value(
                    cfg.read_text(encoding="utf-8"), "bed_mesh", "speed", "700")
                text = klipper_cfg.set_option_value(
                    text, "printer", "max_velocity", "650")
                text = klipper_cfg.set_option_value(text, "z_tilt", "speed", "400")
                # QIDI's 01.01.06.04 config raised query_cycle to 10; X still has the older 5, as after a firmware
                # update, which doesn't rewrite the config.
                stock_cycle = None
                if klipper_cfg.has_section(text, "closed_loop x"):
                    stock_cycle = klipper_cfg.resolve_unique_option(
                        text, "closed_loop x", "query_cycle").value
                    text = klipper_cfg.set_option_value(text, "closed_loop x", "query_cycle", "5")
                # The section install deletes sits last, right above Klipper's SAVE_CONFIG block.
                sensor = klipper_cfg.resolve_unique_section(
                    text, "filament_switch_sensor filament_switch_sensor").text
                text = klipper_cfg.delete_section(
                    text, "filament_switch_sensor filament_switch_sensor")
                if klipper_cfg.AUTOSAVE_HEADER not in text:
                    text += (
                        f"\n{klipper_cfg.AUTOSAVE_HEADER}\n"
                        "#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.\n"
                        "#*#\n#*# [input_shaper]\n#*# shaper_freq_x = 54.2\n"
                    )
                regular, header, saved = text.partition(klipper_cfg.AUTOSAVE_HEADER)
                autosave = header + saved
                text = regular.rstrip("\n") + "\n\n" + sensor.rstrip("\n") + "\n\n" + autosave
                cfg.write_text(text, encoding="utf-8")
                self._run_install(paths)
                self._assert_speeds(printer_root, max_velocity="565",
                                    bed_mesh="565", z_tilt="400")
                installed = cfg.read_text(encoding="utf-8")
                self.assertTrue(installed.endswith("\n" + autosave))
                self.assertFalse(klipper_cfg.has_section(
                    installed, "filament_switch_sensor filament_switch_sensor"))

                responses = io.StringIO("Y\nunused\n")
                base_urlopen = moonraker_urlopen()
                idle_checks = 0

                def uninstall_urlopen(request, timeout=0):
                    nonlocal idle_checks
                    url = getattr(request, "full_url", str(request))
                    if "/printer/objects/query?print_stats" in url:
                        idle_checks += 1
                    return base_urlopen(request, timeout=timeout)

                run_uninstall(
                    paths,
                    self.manifest,
                    self.compatibility,
                    PlainReporter(io.StringIO()),
                    input_stream=responses,
                    urlopen=uninstall_urlopen,
                )

                self.assertEqual(idle_checks, 2)
                self.assertEqual(responses.readline(), "unused\n")
                self._assert_homing_speed(printer_root, "50")
                self._assert_speeds(printer_root, max_velocity="650",
                                    bed_mesh="700", z_tilt="400")
                uninstalled = cfg.read_text(encoding="utf-8")
                if stock_cycle is not None:
                    for axis in ("x", "y"):
                        self.assertEqual(klipper_cfg.resolve_unique_option(
                            uninstalled, f"closed_loop {axis}", "query_cycle").value, stock_cycle)
                self.assertTrue(uninstalled.endswith(
                    "\n" + sensor.rstrip("\n") + "\n\n" + autosave))
                self.assertEqual(
                    (paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes(),
                    stock_source,
                )
                self.assertFalse(
                    (printer_root / "config/tltg_optimized_state.yaml").exists()
                )
                saved_variables = (
                    printer_root / "config/saved_variables.cfg"
                ).read_text(encoding="utf-8")
                self.assertEqual(
                    klipper_cfg.resolve_unique_option(
                        saved_variables,
                        "Variables",
                        "vo_keep_loaded_between_prints",
                    ).value,
                    "1",
                )


class _BytesResponse:
    def __init__(self, body: bytes, url: str = ""):
        self.body = io.BytesIO(body)
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=-1):
        return self.body.read(size)

    def geturl(self):
        return self.url


def _release_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        payload = b"#!/bin/sh\nexit 0\n"
        member = tarfile.TarInfo(f"{BUNDLE_ROOT_NAME}/install.sh")
        member.mode = 0o755
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()
