from __future__ import annotations

import copy
import dataclasses
import hashlib
import io
import json
import os
import shutil
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

from installer.runtime import boot_path, install_flow, messages, system_optimizations
from installer.runtime import fluidd_box_filaments as fluidd_patch
from installer.runtime import moonraker_allowed_services, moonraker_spoolman, moonraker_used_extruders
from installer.runtime import moonraker_speedups
from installer.runtime import addons_service
from installer.runtime import auto_update, status_service
from installer.runtime import remote_screen
from installer.runtime import adb_usb_only
from installer.runtime import cpu_governor
from installer.runtime import cpu_tuning
from installer.runtime import box_config as box_monitor
from installer.runtime import qidi_agent
from installer.runtime import klipper_chelper, klipper_tree, qidiclient_firmware_popup
from installer.runtime import moonraker_fixes
from installer.runtime import restart_on_exit
from installer.runtime import webcam_id
from installer.runtime import wifi_wait
from installer.runtime import service_priority
from installer.runtime import zram_swap
from installer.runtime.fluidd_box_filaments import OPERATION as FLUIDD_BOX_OPERATION, PATCH_MARKER
from installer.runtime.fluidd_filament_config import OPERATION as FILAMENT_CONFIG_OPERATION
from installer.runtime.klipper_box_config import OPERATION as KLIPPER_BOX_OPERATION
from installer.runtime.klipper_print_stats_manager import OPERATION as KLIPPER_PRINT_STATS_OPERATION
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.compatibility import load_supported_upgrade_sources
from installer.runtime.errors import InstalledPackageValidationError
from installer.runtime.manifest import load_manifest
from installer.runtime.models import InstalledState, ManagedTreeState, SystemOptimizationCliOptions
from installer.runtime.reporter import PlainReporter
from installer.runtime.runner import run_install
from installer.runtime.safety import FIRMWARE_UPDATE_RUN_ENV
from installer.runtime.state_file import StateValidationError, load_installed_state, write_installed_state
from installer.runtime.system_optimizations import (
    SYSTEM_ROOT_ENV,
    SystemOptimizationApplyError,
    SystemOptimizationRecoveryError,
    _compact_host_ledger,
    _replace_system_ledger,
    apply_system_optimizations,
    _journal_path,
    _write_host_journal,
    carried_system_ledger,
    recover_pending_system_optimization,
    restore_system_optimizations,
    validate_system_ledger,
)
from installer.runtime.uninstall import run_uninstall
from installer.tests.helpers import (
    FIXTURES_ROOT,
    REPO_ROOT,
    add_merged_usr,
    build_env,
    copy_base_runtime,
    earlier_blocked_qidiclient,
    fake_host_run,
    fake_klipper_tree,
    fake_qidiclient,
    fake_system_root,
    moonraker_urlopen,
    snapshot_tree,
    stock_printer_data,
    with_fluidd_build,
    with_moonraker_speedups,
    with_valve_oil_ui,
)


class SystemOptimizationFlowTests(unittest.TestCase):
    def test_system_optimization_dry_run_install_and_uninstall_lifecycle(self):
        """One printer's host through its life. Each stage starts from what the one before left; a stage that fails is
        reported by name and the later stages still run."""
        printer_root = copy_base_runtime()
        system_root = fake_system_root(
            include_moonraker_cache=True,
            include_fluidd_bundle=True,
            include_filament_config=True,
            include_klipper_box_config=True,
            include_klipper_print_stats_manager=True,
            include_klipper_chelper=True,
            include_nginx_site=True,
            include_moonraker_asvc=True,
            include_moonraker_metadata=True,
            include_qidiclient=True,
        )
        host_run = fake_host_run(system_root)
        chelper = system_root / klipper_chelper.FILE.lstrip("/")
        chelper_before = chelper.read_bytes()
        chelper_source = chelper.parent / "kin_extruder.c"
        qidiclient = system_root / qidiclient_firmware_popup.FILE.lstrip("/")
        qidiclient_before, qidiclient_patched, qidiclient_unblocked = fake_qidiclient()
        moonraker = system_root / "home/qidi/moonraker/moonraker/components/file_manager/file_manager.py"
        moonraker.write_bytes(moonraker.read_bytes().replace(b"\n", b"\r\n"))
        moonraker_before = moonraker.read_bytes()
        moonraker.chmod(0o640)
        fluidd = system_root / "home/qidi/fluidd/assets/index-DlFvHGAh.js"
        fluidd_before = fluidd.read_bytes()
        service_worker = system_root / "home/qidi/fluidd/sw.js"
        service_worker_before = service_worker.read_bytes()
        box_config = system_root / "home/qidi/klipper/klippy/extras/box_config.py"
        box_config_before = box_config.read_bytes()
        stats_manager = system_root / "home/qidi/klipper/klippy/extras/print_stats_manager.py"
        stats_manager_before = stats_manager.read_bytes()
        klippy_apis = system_root / "home/qidi/moonraker/moonraker/components/klippy_apis.py"
        klippy_apis_before = klippy_apis.read_bytes()
        metadata = system_root / "home/qidi/moonraker/moonraker/components/file_manager/metadata.py"
        metadata_before = metadata.read_bytes()
        spoolman = system_root / "home/qidi/moonraker/moonraker/components/spoolman.py"
        spoolman_before = spoolman.read_bytes()
        nginx_site = system_root / remote_screen.NGINX_SITE.lstrip("/")
        nginx_site_before = nginx_site.read_bytes()
        screen_unit = system_root / remote_screen.UNIT_FILE.lstrip("/")
        screen_server = system_root / remote_screen.SERVER_FILE.lstrip("/")
        addons_unit = system_root / addons_service.UNIT_FILE.lstrip("/")
        addons_server = system_root / addons_service.SERVER_FILE.lstrip("/")
        status_unit = system_root / status_service.UNIT_FILE.lstrip("/")
        status_server = system_root / status_service.SERVER_FILE.lstrip("/")
        monitor_files = [system_root / path.lstrip("/") for path in (box_monitor.MONITOR_FILE, box_monitor.UNIT_FILE,
                                                                     box_monitor.RULE_FILE)]
        check_service = system_root / auto_update.SERVICE_FILE.lstrip("/")
        check_timer = system_root / auto_update.TIMER_FILE.lstrip("/")
        fixes_module = system_root / moonraker_fixes.MODULE_FILE.lstrip("/")
        fixes_path = system_root / moonraker_fixes.PATH_FILE.lstrip("/")
        fixes_mqtt = system_root / moonraker_fixes.MQTT_FILE.lstrip("/")
        screen_state = system_root / "systemd" / f"{remote_screen.UNIT}.json"
        asvc = system_root / moonraker_allowed_services.FILE.lstrip("/")
        asvc_before = asvc.read_bytes()
        before = snapshot_tree(system_root)
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        env[SYSTEM_ROOT_ENV] = str(system_root)
        boot_id = printer_root / "boot-id"
        boot_id.write_text("boot-one\n", encoding="utf-8")
        env["VO_BOOT_ID_PATH"] = str(boot_id)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        compatibility = load_supported_upgrade_sources(
            REPO_ROOT / "installer/supported_upgrade_sources.yaml"
        )
        with self.subTest("a dry run changes nothing"):
            stream = io.StringIO()

            result = run_install(
                paths,
                manifest,
                PlainReporter(stream),
                dry_run=True,
                urlopen=moonraker_urlopen(),
                environ=env,
                system_options=SystemOptimizationCliOptions(disable_ai_detection=True),
                run=host_run,
            )

            self.assertTrue(result.dry_run)
            self.assertEqual(snapshot_tree(system_root), before)
            self.assertFalse((printer_root / manifest.state_file).exists())
            self.assertIn("System optimizations dry-run:", stream.getvalue())
            # QIDI's metadata.py takes the used-filaments patch once the plate-selection patch before it has run.
            self.assertIn(f"would apply {moonraker_used_extruders.OPERATION} and restart Moonraker", stream.getvalue())

        base_urlopen = moonraker_urlopen(
            saved_variables_path=paths.config_root / "saved_variables.cfg"
        )
        install_idle_checks = 0
        service_events = []

        def install_urlopen(request, timeout=0):
            nonlocal install_idle_checks
            url = getattr(request, "full_url", str(request))
            if urllib.parse.urlsplit(url).query == "print_stats&virtual_sdcard":
                install_idle_checks += 1
            if url.endswith("/server/info"):
                service_events.append("moonraker answered")
            if url.endswith("/machine/services/restart"):
                service_events.append("klipper.service via Moonraker")
            return base_urlopen(request, timeout=timeout)

        def install_run(command, **kwargs):
            if list(command)[-3:-1] == ["systemctl", "restart"]:
                service_events.append(list(command)[-1])
                # A printer without the camera service still installs.
                if list(command)[-1] == "crowsnest.service":
                    return CompletedProcess(command, 5, stdout="", stderr="Unit crowsnest.service not found.")
            if list(command)[-2:] == ["systemctl", "daemon-reload"]:
                service_events.append("daemon-reload")
            return host_run(command, **kwargs)

        with self.subTest("install"):
            run_install(
                paths,
                manifest,
                PlainReporter(io.StringIO()),
                urlopen=install_urlopen,
                environ=env,
                system_options=SystemOptimizationCliOptions(disable_ai_detection=True),
                auto_updates=False,
                run=install_run,
            )
            self.assertEqual(install_idle_checks, 2)
            # Every host change is in place before any service restarts, and each restarts once. Zram swap starts first,
            # then the block of QIDI's servers, the touchscreen app restarts after Moonraker answers again, and Klipper restarts
            # once for its host and source changes.
            restarts = [
                zram_swap.UNIT,
                qidi_agent.BLOCK_UNIT,
                "nginx.service",
                "moonraker.service",
                "moonraker answered",
                remote_screen.UNIT,
                addons_service.UNIT,
                status_service.UNIT,
                auto_update.TIMER_NAME,
                "qidi-client.service",
                box_monitor.UNIT,
                qidi_agent.UNIT,
                "crowsnest.service",
                cpu_tuning.TUNING_UNIT,
                cpu_tuning.IRQBALANCE_UNIT,
                cpu_governor.SERVICE,
                "klipper.service via Moonraker",
            ]
            self.assertEqual(service_events[-len(restarts):], restarts)
            self.assertEqual(set(service_events[:-len(restarts)]), {"daemon-reload"})
            # Klipper and Moonraker restart after any exit outside systemd, without restarting them to apply it.
            for dropin in restart_on_exit.FILES.values():
                self.assertEqual((system_root / dropin.lstrip("/")).read_text(encoding="utf-8"), restart_on_exit.CONTENT)
            # QIDI's camera ID script waits for the camera's index0 link from the next boot, without running it now.
            self.assertEqual((system_root / webcam_id.FILE.lstrip("/")).read_text(encoding="utf-8"), webcam_id.CONTENT)
            self.assertNotIn(webcam_id.UNIT, service_events)
            # QIDI's Wi-Fi service waits for wlan0 from the next boot, without a restart that would drop the network now.
            self.assertEqual((system_root / wifi_wait.FILE.lstrip("/")).read_text(encoding="utf-8"), wifi_wait.CONTENT)
            # ...and starts again whenever wlan0 comes back, as after a driver reload.
            self.assertEqual((system_root / wifi_wait.RULE_FILE.lstrip("/")).read_text(encoding="utf-8"),
                             wifi_wait.RULE_CONTENT)
            self.assertNotIn(wifi_wait.UNIT, service_events)
            # Klipper gets the CPU before Moonraker, and both before the touchscreen app, each on its own CPUs.
            for operation, dropin in service_priority.FILES.items():
                text = (system_root / dropin.lstrip("/")).read_text(encoding="utf-8")
                self.assertIn(f"Nice={service_priority.NICE[operation]}\n", text)
                if operation in service_priority.CPU_AFFINITY:
                    self.assertIn(f"CPUAffinity={service_priority.CPU_AFFINITY[operation]}\n", text)
            # algo_app isn't started to apply them, since AI detection may be off.
            self.assertNotIn(service_priority.SERVICES[service_priority.ALGO_APP_OPERATION], service_events)
            # QIDI's tuning script is replaced by one run that pins the interrupts irqbalance now leaves alone.
            for operation, dropin in cpu_tuning.FILES.items():
                self.assertEqual((system_root / dropin.lstrip("/")).read_text(encoding="utf-8"), cpu_tuning.CONTENT[operation])
            # Zram swap's modules and boot script are root-owned under /usr/local, and its unit is enabled.
            zram = system_root / zram_swap.DESTINATION.lstrip("/")
            zram_unit = system_root / zram_swap.UNIT_FILE.lstrip("/")
            for module in manifest.system_optimizations.zram_swap.modules:
                self.assertEqual((zram / f"{module.name}.ko").read_bytes(), (REPO_ROOT / "installer" / module.archive).read_bytes())
            self.assertEqual((zram / zram_swap.SCRIPT).stat().st_mode & 0o777, 0o755)
            self.assertIn("insmod", (zram / zram_swap.SCRIPT).read_text(encoding="utf-8"))
            self.assertEqual(zram_unit.read_text(encoding="utf-8"), zram_swap.UNIT_CONTENT)
            self.assertEqual(json.loads((system_root / "systemd" / f"{zram_swap.UNIT}.json").read_text(encoding="utf-8"))["enabled"], "enabled")
            self.assertIn('GOVERNOR="performance"\n', (system_root / cpu_governor.FILE.lstrip("/")).read_text(encoding="utf-8"))
            # They run already, and the installer recommends a reboot to confirm they start on their own.
            self.assertTrue(paths.reboot_recommended_path.exists())
            paths.reboot_recommended_path.unlink()
            self.assertTrue((system_root / "etc/resolv.conf").is_symlink())
            self.assertIn(
                "deb http://deb.debian.org/debian bullseye",
                (system_root / "etc/apt/sources.list").read_text(encoding="utf-8"),
            )
            rockchip = manifest.system_optimizations.rockchip_root_sync
            dropin = system_root / rockchip.dropin.lstrip("/")
            self.assertEqual(dropin.read_text(encoding="utf-8"), rockchip.dropin_content)
            self.assertTrue(paths.host_reboot_marker_path.exists())
            state = load_installed_state(printer_root / manifest.state_file)
            self.assertIn("rockchip_root_sync", state.system_ledger["restore_preimages"])
            probe_helper = paths.managed_klipper_root / "klippy/extras/vo_probe_test.py"
            self.assertEqual(probe_helper.read_bytes(), (REPO_ROOT / "installer/klipper/extras/probe_test.py").read_bytes())
            # Earlier builds recorded the probe helper as a host operation; loading the state drops it.
            retired = copy.deepcopy(state.system_ledger)
            retired["restore_preimages"]["klipper_probe_signal_test"] = {"path": "/home/qidi/klipper/klippy/extras/vo_probe_test.py", "exists": False}
            retired["outcomes"]["klipper_probe_signal_test"] = {"status": "applied", "source": "yes_install"}
            write_installed_state(printer_root / manifest.state_file, _replace_system_ledger(state, retired))
            state = load_installed_state(printer_root / manifest.state_file)
            self.assertNotIn("klipper_probe_signal_test", state.system_ledger["restore_preimages"])
            self.assertNotIn("klipper_probe_signal_test", state.system_ledger["outcomes"])
            self.assertEqual(state.system_ledger["schema_version"], 2)
            self.assertNotIn("actions", state.system_ledger)
            self.assertIn("rockchip_root_sync", state.system_ledger["committed_transactions"])
            cache_id = system_optimizations.MOONRAKER_CACHE_OPERATION
            self.assertIn(cache_id, state.system_ledger["restore_preimages"])
            self.assertNotEqual(moonraker.read_bytes(), moonraker_before)
            self.assertIn(b"\r\n", moonraker.read_bytes())
            self.assertEqual(moonraker.stat().st_mode & 0o777, 0o640)
            self.assertIn(b"            plateindex = self._vo_3mf_plate_index(filename)\r\n", klippy_apis.read_bytes())
            # Fluidd's Box slot dialog gets the filaments a file uses, for the plate a 3MF prints.
            self.assertIn("plate_index = _3mf_selected_plate_index(", metadata.read_text(encoding="utf-8"))
            self.assertIn("each plate's own filaments", metadata.read_text(encoding="utf-8"))
            # QIDI's CRLF line endings survive both metadata.py patches.
            self.assertEqual(metadata.read_bytes().count(b"\n"), metadata.read_bytes().count(b"\r\n"))
            self.assertIn(b"        self._highest_epos = self._last_epos\r\n", spoolman.read_bytes())
            self.assertIn(FLUIDD_BOX_OPERATION, state.system_ledger["restore_preimages"])
            self.assertIn(PATCH_MARKER.encode(), fluidd.read_bytes())
            self.assertIn(b"Math.floor(u/4)===t-1", fluidd.read_bytes())
            self.assertIn(b"enableFullDisplay:!0", fluidd.read_bytes())
            self.assertIn(b"LOAD_FILAMENT_CONTROL", fluidd.read_bytes())
            self.assertIn(fluidd_patch.PRINT_SLOT_CHANGE_PATCHED.encode(), fluidd.read_bytes())
            self.assertIn(fluidd_patch.BOX_MOTION_START.encode(), fluidd.read_bytes())
            self.assertIn(b"t(VoBoxMotion)", fluidd.read_bytes())
            self.assertIn(fluidd_patch.SERVICE_WORKER_OPERATION, state.system_ledger["restore_preimages"])
            self.assertIn(b'{"revision":"vo-', service_worker.read_bytes())
            self.assertIn(fluidd_patch.SERVICE_WORKER_TAKEOVER_PATCHED.encode(), service_worker.read_bytes())
            self.assertIn(KLIPPER_BOX_OPERATION, state.system_ledger["restore_preimages"])
            self.assertIn(b"config.access_tracking if module == 'heater_generic'", box_config.read_bytes())
            self.assertNotIn(b"\n", box_config.read_bytes().replace(b"\r\n", b""))
            self.assertIn(KLIPPER_PRINT_STATS_OPERATION, state.system_ledger["restore_preimages"])
            self.assertIn(b"if self.printer.is_shutdown() or getattr(self, '_vo_resetting', False):", stats_manager.read_bytes())
            self.assertNotIn(b"\n", stats_manager.read_bytes().replace(b"\r\n", b""))
            # Klipper loads the package's C helper library as it is: it is never older than QIDI's sources, so Klipper never
            # rebuilds it from them.
            self.assertIn(klipper_chelper.OPERATION, state.system_ledger["restore_preimages"])
            self.assertEqual(chelper.read_bytes(), (klipper_chelper.PAYLOAD_DIR / "c_helper.so").read_bytes())
            self.assertGreaterEqual(chelper.stat().st_mtime, chelper_source.stat().st_mtime)
            # The touchscreen app accepts a board version that starts with QIDI's, no longer announces QIDI's online
            # update or unpacks QIDI packages, and stays executable. Nothing else is installed for the block.
            self.assertIn(qidiclient_firmware_popup.OPERATION, state.system_ledger["restore_preimages"])
            self.assertEqual(qidiclient.read_bytes(), qidiclient_patched)
            self.assertEqual(qidiclient.stat().st_mode & 0o777, 0o755)
            # Valve Oil's Box monitor takes box.cfg over from the app: the monitor it installs recognizes the patched app.
            self.assertEqual([path.read_text(encoding="utf-8") for path in monitor_files[1:]],
                             [box_monitor.UNIT_CONTENT, box_monitor.RULE_CONTENT])
            installed_monitor = {}
            exec(compile(monitor_files[0].read_text(encoding="utf-8"), "vo_box_monitor.py", "exec"), installed_monitor)
            self.assertTrue(installed_monitor["app_patched"](str(qidiclient)))
            filament_config = manifest.system_optimizations.fluidd_filament_config
            filament_link = printer_root / filament_config.link
            self.assertEqual(os.readlink(filament_link), filament_config.target)
            self.assertIn(FILAMENT_CONFIG_OPERATION, state.system_ledger["restore_preimages"])
            self.assertIn(b"VoScreenCard:VoScreenCard", fluidd.read_bytes())
            self.assertIn(b'{id:"vo-screen-card",enabled:!0,collapsed:!0}', fluidd.read_bytes())
            self.assertIn(fluidd_patch.QIDIBOX_START.encode(), fluidd.read_bytes())
            self.assertIn(fluidd_patch.QIDIBOX_HIDDEN_PATCHED.encode(), fluidd.read_bytes())
            self.assertIn(b'{id:"vo-qidi-box-card",enabled:!0,collapsed:!1}', fluidd.read_bytes())
            self.assertEqual(screen_unit.read_text(encoding="utf-8"), remote_screen.UNIT_CONTENT)
            self.assertEqual(screen_server.read_bytes(), remote_screen.SERVER_PAYLOAD.read_bytes())
            self.assertEqual(addons_unit.read_text(encoding="utf-8"), addons_service.UNIT_CONTENT)
            self.assertEqual(addons_server.read_bytes(), addons_service.SERVER_PAYLOAD.read_bytes())
            self.assertEqual(status_unit.read_text(encoding="utf-8"), status_service.UNIT_CONTENT)
            self.assertEqual(status_server.read_bytes(), status_service.SERVER_PAYLOAD.read_bytes())
            # The update check is scheduled on every install; the operator didn't turn automatic updates on, and the
            # install doesn't either.
            self.assertEqual(check_service.read_text(encoding="utf-8"), auto_update.SERVICE_CONTENT)
            self.assertEqual(check_timer.read_text(encoding="utf-8"), auto_update.TIMER_CONTENT)
            self.assertEqual(json.loads((system_root / "systemd" / f"{auto_update.TIMER_NAME}.json").read_text(encoding="utf-8"))["enabled"], "enabled")
            self.assertFalse(auto_update.auto_update_enrolled(paths))
            # Moonraker's Python imports the fixes module through the path file, and it loads the MQTT component.
            self.assertEqual(fixes_module.read_bytes(), moonraker_fixes.MODULE_PAYLOAD.read_bytes())
            self.assertEqual(fixes_mqtt.read_bytes(), moonraker_fixes.MQTT_PAYLOAD.read_bytes())
            self.assertEqual(fixes_path.read_text(encoding="utf-8"), moonraker_fixes.PATH_CONTENT)
            self.assertEqual(json.loads(screen_state.read_text(encoding="utf-8"))["enabled"], "enabled")
            # Without a package Fluidd build, nginx keeps serving QIDI's patched bundle.
            self.assertEqual(nginx_site.read_bytes(), nginx_site_before)
            self.assertEqual(state.system_ledger["outcomes"][system_optimizations.FLUIDD_BUILD_OPERATION]["status"], "missing")
            # A repository checkout has no Valve Oil UI build, so it isn't served.
            self.assertEqual(state.system_ledger["outcomes"][system_optimizations.VALVE_OIL_UI_OPERATION]["status"], "missing")
            # Without the package's wheels, Moonraker and Klipper are never pointed at a speedups directory.
            self.assertEqual(state.system_ledger["outcomes"][moonraker_speedups.OPERATION]["status"], "missing")
            self.assertEqual(state.system_ledger["outcomes"][moonraker_speedups.PATH_OPERATION]["status"], "skipped")
            self.assertEqual(state.system_ledger["outcomes"][moonraker_speedups.KLIPPER_PATH_OPERATION]["status"], "skipped")
            # Valve Oil's Klipper isn't checked with QIDI's modules of this firmware, and this host has no QIDI
            # klipper.service for the launcher's drop-in: Klipper stays QIDI's, and the install goes on.
            self.assertEqual(state.system_ledger["outcomes"][klipper_tree.OPERATION]["status"], "unsupported")
            self.assertEqual(state.system_ledger["outcomes"][klipper_tree.DROPIN_OPERATION]["status"], "unsupported")
            self.assertFalse((system_root / klipper_tree.TREE_ROOT.lstrip("/")).exists())
            self.assertFalse((system_root / klipper_tree.DROPIN_FILE.lstrip("/")).exists())
            self.assertEqual(asvc.read_bytes(), asvc_before + b"\nqidi-client\n")
            fluidd_current = fluidd.read_bytes()

        with self.subTest("updates over earlier package versions"):
            # Earlier package versions left owned Fluidd patches, including an older Box motion section, that updates must replace in place.
            fluidd_previous = fluidd_before.decode().replace(fluidd_patch.SLOT_PARSE_STOCK, fluidd_patch.SLOT_PARSE_PATCHED).replace(
                fluidd_patch.FILAS_LIST_STOCK, fluidd_patch._filas_list_patched_v1()
            ).replace(
                fluidd_patch.BOX_MOTION_DEFINE_STOCK,
                fluidd_patch.BOX_MOTION_DEFINE_HEAD + fluidd_patch.BOX_MOTION_START + "const VoBoxMotion={render:h=>h()};"
                + fluidd_patch.BOX_MOTION_END + fluidd_patch.BOX_MOTION_DEFINE_TAIL,
            ).replace(fluidd_patch.BOX_MOTION_RENDER_STOCK, fluidd_patch.BOX_MOTION_RENDER_PATCHED).encode()
            fluidd.write_bytes(fluidd_previous)
            prior_preimage = state.system_ledger["restore_preimages"][cache_id]
            fluidd_preimage = state.system_ledger["restore_preimages"][FLUIDD_BOX_OPERATION]
            cache_policy = copy.deepcopy({**state.system_ledger, "policy": {"system_optimizations": "disabled", "ai_detection": "unset"}})
            cache_policy["outcomes"][FLUIDD_BOX_OPERATION]["postflight"]["sha256"] = __import__("hashlib").sha256(fluidd_previous).hexdigest()
            # An earlier package version patched only the firmware-version notices; updates finish the patch whatever the
            # OS optimization answer, and keep QIDI's executable for restoration.
            qidiclient_preimage = state.system_ledger["restore_preimages"][qidiclient_firmware_popup.OPERATION]
            qidiclient.write_bytes(qidiclient_unblocked)
            cache_policy["outcomes"][qidiclient_firmware_popup.OPERATION]["postflight"]["sha256"] = __import__("hashlib").sha256(qidiclient_unblocked).hexdigest()
            reconciled = apply_system_optimizations(
                paths=paths, spec=manifest.system_optimizations, ledger=cache_policy,
                reporter=PlainReporter(io.StringIO()), input_stream=None, environ=env,
                source="auto_update_reconcile", block_qidi_updates=True, run=host_run,
                urlopen=moonraker_urlopen(),
            )
            self.assertEqual(reconciled["restore_preimages"][cache_id], prior_preimage)
            self.assertEqual(qidiclient.read_bytes(), qidiclient_patched)
            self.assertEqual(reconciled["restore_preimages"][qidiclient_firmware_popup.OPERATION], qidiclient_preimage)
            # A later failure in a run that switches the touchscreen app between its forms rolls it back to the form it
            # had, and the next run switches it; QIDI's executable stays the one copy kept for uninstall.
            def qidiclient_backups():
                return sorted((paths.printer_data_root / system_optimizations.SYSTEM_BACKUP_DIR).rglob("qidiclient"))

            def touchscreen_restart_fails(command, **kwargs):
                if list(command)[-3:] == ["systemctl", "restart", qidiclient_firmware_popup.RESTART_SERVICE] and not restart_failed:
                    restart_failed.append(True)
                    return CompletedProcess(command, 1, stdout="", stderr="restart failed")
                return host_run(command, **kwargs)

            self.assertEqual(qidiclient_backups(), [Path(qidiclient_preimage["backup_path"])])
            for block, before, after in ((False, qidiclient_patched, qidiclient_unblocked), (True, qidiclient_unblocked, qidiclient_patched)):
                restart_failed = []
                with self.assertRaises(SystemOptimizationApplyError) as failed:
                    apply_system_optimizations(
                        paths=paths, spec=manifest.system_optimizations, ledger=reconciled,
                        reporter=PlainReporter(io.StringIO()), input_stream=None, environ=env, source="auto_update_reconcile",
                        block_qidi_updates=block, run=touchscreen_restart_fails, urlopen=moonraker_urlopen(),
                    )
                self.assertEqual(qidiclient.read_bytes(), before)
                self.assertFalse(_journal_path(paths).exists())
                self.assertEqual(failed.exception.ledger["restore_preimages"][qidiclient_firmware_popup.OPERATION], qidiclient_preimage)
                self.assertEqual(qidiclient_backups(), [Path(qidiclient_preimage["backup_path"])])
                reconciled = apply_system_optimizations(
                    paths=paths, spec=manifest.system_optimizations, ledger=failed.exception.ledger,
                    reporter=PlainReporter(io.StringIO()), input_stream=None, environ=env, source="auto_update_reconcile",
                    block_qidi_updates=block, run=host_run, urlopen=moonraker_urlopen(),
                )
                self.assertEqual(qidiclient.read_bytes(), after)
                self.assertEqual(qidiclient_backups(), [Path(qidiclient_preimage["backup_path"])])
            # Runs that change none of them don't recommend another reboot.
            self.assertFalse(paths.reboot_recommended_path.exists())
            self.assertEqual(fluidd.read_bytes(), fluidd_current)
            self.assertEqual(reconciled["restore_preimages"][FLUIDD_BOX_OPERATION], fluidd_preimage)
            self.assertEqual(os.readlink(filament_link), filament_config.target)
            # Updates replace the screen service from an earlier package version.
            prior_server = remote_screen.SERVER_MARKER.encode() + b"print('earlier screen service')\n"
            screen_server.write_bytes(prior_server)
            reconciled["outcomes"][remote_screen.SERVER_OPERATION]["postflight"]["sha256"] = __import__("hashlib").sha256(prior_server).hexdigest()
            # An update that rolls back keeps recording the earlier version, so the next update replaces it instead of
            # preserving it as modified.

            screen_restart_failed = []

            def screen_restart_fails(command, **kwargs):
                if list(command)[-3:] == ["systemctl", "restart", remote_screen.UNIT] and not screen_restart_failed:
                    screen_restart_failed.append(True)
                    return CompletedProcess(command, 1, stdout="", stderr="restart failed")
                return host_run(command, **kwargs)

            with self.assertRaises(SystemOptimizationApplyError) as failed:
                apply_system_optimizations(
                    paths=paths, spec=manifest.system_optimizations, ledger=reconciled,
                    reporter=PlainReporter(io.StringIO()), input_stream=None, environ=env,
                    source="auto_update_reconcile", block_qidi_updates=True, run=screen_restart_fails,
                    urlopen=moonraker_urlopen(),
                )
            self.assertEqual(screen_server.read_bytes(), prior_server)
            reconciled = apply_system_optimizations(
                paths=paths, spec=manifest.system_optimizations, ledger=failed.exception.ledger,
                reporter=PlainReporter(io.StringIO()), input_stream=None, environ=env,
                source="auto_update_reconcile", block_qidi_updates=True, run=host_run,
                urlopen=moonraker_urlopen(),
            )
            self.assertEqual(screen_server.read_bytes(), remote_screen.SERVER_PAYLOAD.read_bytes())
            self.assertFalse(reconciled["restore_preimages"][remote_screen.SERVER_OPERATION]["exists"])

        with self.subTest("package Fluidd build, speedups, and Valve Oil UI"):
            # Serving the package Fluidd build retires QIDI's bundle patches, and Moonraker gets the speedup packages; a
            # failure first rolls both back. nginx never serves a Valve Oil UI directory the package doesn't own.
            manifest = with_valve_oil_ui(with_moonraker_speedups(with_fluidd_build(manifest)))
            ui = system_root / "home/qidi/vo-valve-oil-ui"
            (ui / "private").mkdir(parents=True)
            (ui / "index.html").write_text("someone else's page\n", encoding="utf-8")
            build = system_root / "home/qidi/vo-fluidd"
            previous_build = system_root / "home/qidi/vo-fluidd.vo-previous"
            marker = build / ".vo-fluidd"
            speedups = system_root / moonraker_speedups.DESTINATION.lstrip("/")
            previous_speedups = system_root / moonraker_speedups.PREVIOUS.lstrip("/")
            speedups_marker = speedups / moonraker_speedups.MARKER
            speedups_path = system_root / moonraker_speedups.PATH_FILE.lstrip("/")
            klipper_speedups_path = system_root / moonraker_speedups.KLIPPER_PATH_FILE.lstrip("/")
            speedups_digest = moonraker_speedups.digest(wheel.sha256 for wheel in manifest.system_optimizations.moonraker_speedups.wheels)

            def failing_restart(unit):
                failed = False

                def run(command, **kwargs):
                    nonlocal failed
                    if not failed and list(command)[-3:] == ["systemctl", "restart", unit]:
                        failed = True
                        return CompletedProcess(command, 1, stdout="", stderr="restart failed")
                    return host_run(command, **kwargs)
                return run

            def reconcile(ledger, run=host_run):
                return apply_system_optimizations(
                    paths=paths, spec=manifest.system_optimizations, ledger=ledger,
                    reporter=PlainReporter(io.StringIO()), input_stream=None, environ=env,
                    source="auto_update_reconcile", block_qidi_updates=True, run=run,
                    urlopen=moonraker_urlopen(),
                )

            with self.assertRaises(SystemOptimizationApplyError):
                reconcile(reconciled, failing_restart("nginx.service"))
            self.assertFalse(build.exists())
            self.assertFalse(speedups.exists())
            self.assertFalse(speedups_path.exists())
            self.assertFalse(klipper_speedups_path.exists())
            self.assertEqual(nginx_site.read_bytes(), nginx_site_before)
            self.assertEqual(fluidd.read_bytes(), fluidd_current)
            reconciled = reconcile(reconciled)
            self.assertEqual(marker.read_text(encoding="utf-8"), manifest.system_optimizations.fluidd_build.sha256 + "\n")
            self.assertEqual((build / "assets/index.js").stat().st_mode & 0o777, 0o644)
            self.assertEqual(speedups_marker.read_text(encoding="utf-8"), speedups_digest + "\n")
            self.assertEqual((speedups / "msgspec/_core.so").stat().st_mode & 0o777, 0o755)
            self.assertEqual((speedups / "uvloop/__init__.py").stat().st_mode & 0o777, 0o644)
            self.assertEqual(speedups_path.read_text(encoding="utf-8"), moonraker_speedups.PATH_CONTENT)
            self.assertEqual(klipper_speedups_path.read_text(encoding="utf-8"), moonraker_speedups.KLIPPER_PATH_CONTENT)
            self.assertIn(b"\r\n    root /home/qidi/vo-fluidd;\r\n    gzip_static on;\r\n", nginx_site.read_bytes())
            self.assertIn(b"location /vo-screen/ {\r\n", nginx_site.read_bytes())
            self.assertNotIn(b"\n", nginx_site.read_bytes().replace(b"\r\n", b""))
            self.assertNotIn(b"/valve-oil", nginx_site.read_bytes())
            self.assertEqual((ui / "index.html").read_text(encoding="utf-8"), "someone else's page\n")
            self.assertEqual(reconciled["outcomes"][system_optimizations.VALVE_OIL_UI_OPERATION]["status"], "preserved")
            self.assertEqual(fluidd.read_bytes(), fluidd_before)
            self.assertEqual(service_worker.read_bytes(), service_worker_before)
            self.assertNotIn(FLUIDD_BOX_OPERATION, reconciled["restore_preimages"])
            self.assertNotIn(fluidd_patch.SERVICE_WORKER_OPERATION, reconciled["restore_preimages"])
            self.assertEqual(reconciled["outcomes"][FLUIDD_BOX_OPERATION]["status"], "retired")
            # Updates replace an earlier build and earlier speedups, which come back if a later change fails. Once the
            # directory is free, Valve Oil UI is installed and served at /valve-oil/, and a failure removes both again.
            shutil.rmtree(ui)
            marker.write_text("0" * 64 + "\n", encoding="utf-8")
            speedups_marker.write_text("0" * 64 + "\n", encoding="utf-8")
            screen_server.write_bytes(prior_server)
            reconciled["outcomes"][remote_screen.SERVER_OPERATION]["postflight"]["sha256"] = __import__("hashlib").sha256(prior_server).hexdigest()
            # A site that an earlier version switched to the build, before it served the build's gzip copies.
            prior_site = nginx_site.read_bytes().replace(b"    gzip_static on;\r\n", b"", 1)
            nginx_site.write_bytes(prior_site)
            reconciled["outcomes"][remote_screen.NGINX_OPERATION]["postflight"]["sha256"] = __import__("hashlib").sha256(prior_site).hexdigest()
            with self.assertRaises(SystemOptimizationApplyError):
                reconcile(reconciled, failing_restart(remote_screen.UNIT))
            self.assertEqual(marker.read_text(encoding="utf-8"), "0" * 64 + "\n")
            self.assertFalse(previous_build.exists())
            self.assertEqual(speedups_marker.read_text(encoding="utf-8"), "0" * 64 + "\n")
            self.assertFalse(previous_speedups.exists())
            self.assertEqual(nginx_site.read_bytes(), prior_site)
            self.assertFalse(ui.exists())
            reconciled = reconcile(reconciled)
            self.assertEqual(marker.read_text(encoding="utf-8"), manifest.system_optimizations.fluidd_build.sha256 + "\n")
            self.assertFalse(previous_build.exists())
            self.assertEqual(speedups_marker.read_text(encoding="utf-8"), speedups_digest + "\n")
            self.assertFalse(previous_speedups.exists())
            self.assertEqual(nginx_site.read_bytes().count(b"    gzip_static on;\r\n"), 1)
            self.assertEqual((ui / ".vo-valve-oil-ui").read_text(encoding="utf-8"), manifest.system_optimizations.valve_oil_ui.sha256 + "\n")
            self.assertEqual((ui / "index.html").stat().st_mode & 0o777, 0o644)
            self.assertIn(b"    location ^~ /valve-oil/ {\r\n        alias /home/qidi/vo-valve-oil-ui/;\r\n", nginx_site.read_bytes())
            self.assertIn(b"        alias /home/qidi/vo-valve-oil-ui/index.html;\r\n        add_header Cache-Control \"no-store\";\r\n", nginx_site.read_bytes())
            self.assertNotIn(b"\n", nginx_site.read_bytes().replace(b"\r\n", b""))
            # A QIDI package that installs another reviewed build of the touchscreen app hands it to the next run, which
            # patches it and keeps that build's QIDI executable, alone, for uninstall.
            qidiclient_before, other_blocked, _ = fake_qidiclient(variant=1)
            qidiclient.write_bytes(qidiclient_before)
            reconciled = reconcile(reconciled)
            self.assertEqual(qidiclient.read_bytes(), other_blocked)
            self.assertEqual([backup.read_bytes() for backup in qidiclient_backups()], [qidiclient_before])
            write_installed_state(printer_root / manifest.state_file, _replace_system_ledger(state, reconciled))

        with self.subTest("uninstall"):
            # Declining the system-restoration prompt keeps only the optional OS optimizations; everything install makes
            # regardless of policy is always undone. Moonraker stays unreachable for a while after restoration restarts
            # it, and the pending Klipper restart still runs once it answers again.
            moonraker_down = 0

            # Enrollment goes before the check's timer, so a check that outlives the uninstall can't install anything.
            auto_update.enable_auto_updates(paths=paths, reporter=PlainReporter(io.StringIO()))
            enrolled_when_timer_stopped = []

            def uninstall_run(command, **kwargs):
                nonlocal moonraker_down
                if list(command)[-3:] == ["systemctl", "restart", "moonraker.service"]:
                    moonraker_down = 3
                if list(command)[-1] == auto_update.TIMER_NAME and "disable" in command:
                    enrolled_when_timer_stopped.append(auto_update.auto_update_enrolled(paths))
                return host_run(command, **kwargs)

            base_uninstall_urlopen = moonraker_urlopen()

            def uninstall_urlopen(request, *args, **kwargs):
                nonlocal moonraker_down
                if moonraker_down:
                    moonraker_down -= 1
                    raise urllib.error.URLError("Moonraker is restarting")
                return base_uninstall_urlopen(request, *args, **kwargs)

            # The operator's Klipper choice and what the launcher and Klipper recorded go with Valve Oil.
            for name in klipper_tree.STATE_FILES:
                (paths.printer_data_root / name).write_text("{}\n", encoding="utf-8")
            with mock.patch("installer.runtime.safety.time.sleep"):
                run_uninstall(
                    paths,
                    manifest,
                    compatibility,
                    PlainReporter(io.StringIO()),
                    input_stream=io.StringIO("yes\nno\nno\n"),
                    urlopen=uninstall_urlopen,
                    environ=env,
                    run=uninstall_run,
                )
            self.assertFalse(paths.restart_marker_path.exists())
            self.assertFalse((printer_root / manifest.state_file).exists())
            self.assertNotEqual((system_root / "etc/apt/sources.list").read_text(encoding="utf-8"), "old apt\n")
            self.assertTrue(dropin.exists())
            self.assertEqual(moonraker.read_bytes(), moonraker_before)
            self.assertEqual(moonraker.stat().st_mode & 0o777, 0o640)
            self.assertEqual(fluidd.read_bytes(), fluidd_before)
            self.assertEqual(service_worker.read_bytes(), service_worker_before)
            self.assertEqual(box_config.read_bytes(), box_config_before)
            self.assertEqual(stats_manager.read_bytes(), stats_manager_before)
            self.assertEqual(chelper.read_bytes(), chelper_before)
            self.assertGreaterEqual(chelper.stat().st_mtime, chelper_source.stat().st_mtime)
            self.assertEqual(qidiclient.read_bytes(), qidiclient_before)
            self.assertEqual(qidiclient.stat().st_mode & 0o777, 0o755)
            self.assertEqual(klippy_apis.read_bytes(), klippy_apis_before)
            self.assertEqual(metadata.read_bytes(), metadata_before)
            self.assertEqual(spoolman.read_bytes(), spoolman_before)
            self.assertFalse(probe_helper.exists())
            self.assertFalse(filament_link.is_symlink())
            self.assertEqual(nginx_site.read_bytes(), nginx_site_before)
            self.assertEqual(asvc.read_bytes(), asvc_before)
            self.assertFalse(build.exists())
            self.assertFalse(ui.exists())
            self.assertFalse(speedups.exists())
            self.assertFalse(speedups_path.exists())
            self.assertTrue(speedups_path.parent.is_dir())
            self.assertFalse(klipper_speedups_path.exists())
            self.assertTrue(klipper_speedups_path.parent.is_dir())
            self.assertFalse(screen_unit.exists())
            self.assertFalse(screen_server.exists())
            self.assertFalse(addons_unit.exists())
            self.assertFalse(addons_server.exists())
            self.assertFalse(status_unit.exists())
            self.assertFalse(status_server.exists())
            self.assertFalse((system_root / klipper_tree.LAUNCHER_FILE.lstrip("/")).exists())
            self.assertFalse(any((paths.printer_data_root / name).exists() for name in klipper_tree.STATE_FILES))
            self.assertFalse(any(path.exists() for path in monitor_files))
            self.assertFalse(installed_monitor["app_patched"](str(qidiclient)))
            self.assertFalse(check_service.exists() or check_timer.exists())
            self.assertEqual(enrolled_when_timer_stopped, [False])
            self.assertFalse(fixes_module.exists())
            self.assertFalse(fixes_path.exists())
            self.assertFalse(fixes_mqtt.exists())
            self.assertEqual(json.loads(screen_state.read_text(encoding="utf-8"))["enabled"], "disabled")
            for dropin in restart_on_exit.FILES.values():
                self.assertFalse((system_root / dropin.lstrip("/")).exists())
            self.assertFalse((system_root / webcam_id.FILE.lstrip("/")).exists())
            self.assertFalse((system_root / wifi_wait.FILE.lstrip("/")).exists())
            self.assertFalse((system_root / wifi_wait.RULE_FILE.lstrip("/")).exists())
            for dropin in (*service_priority.FILES.values(), *cpu_tuning.FILES.values()):
                self.assertFalse((system_root / dropin.lstrip("/")).exists())
            # Zram swap and the CPU governor are optional OS optimizations, so declining restoration keeps them.
            self.assertTrue((zram / zram_swap.MARKER).is_file())
            self.assertEqual((system_root / cpu_governor.FILE.lstrip("/")).read_text(encoding="utf-8"), cpu_governor.CONTENT)
            self.assertEqual(zram_unit.read_text(encoding="utf-8"), zram_swap.UNIT_CONTENT)

    def test_os_optimizations_apply_whatever_an_earlier_install_chose(self):
        printer_root = copy_base_runtime(homing_patched=True)
        root = fake_system_root()
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        boot_id = printer_root / "boot-id"
        boot_id.write_text("boot-one\n", encoding="utf-8")
        env["VO_BOOT_ID_PATH"] = str(boot_id)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        state_path = printer_root / manifest.state_file
        urlopen = moonraker_urlopen(saved_variables_path=paths.config_root / "saved_variables.cfg")
        # An earlier install that declined the OS optimizations recorded that choice and changed nothing on the host.
        run_install(paths, manifest, PlainReporter(io.StringIO()), input_stream=None, urlopen=urlopen, environ=env)
        state = load_installed_state(state_path)
        declined = {**(state.system_ledger or {}), "policy": {"system_optimizations": "disabled", "ai_detection": "unset"}}
        write_installed_state(state_path, _replace_system_ledger(state, declined))

        env[SYSTEM_ROOT_ENV] = str(root)
        host_run = fake_host_run(root)
        system_optimizations.maybe_reconcile_system_optimizations(
            paths=paths, manifest=manifest, reporter=PlainReporter(io.StringIO()), environ=env, block_qidi_updates=True,
            run=host_run, urlopen=urlopen,
        )

        # An automatic update applies them anyway, leaving AI detection on.
        state = load_installed_state(state_path)
        self.assertEqual(state.system_ledger["policy"], {"system_optimizations": "enabled", "ai_detection": "keep_enabled"})
        self.assertEqual((root / zram_swap.UNIT_FILE.lstrip("/")).read_text(encoding="utf-8"), zram_swap.UNIT_CONTENT)
        self.assertTrue(paths.host_reboot_marker_path.exists())
        self.assertTrue(paths.reboot_recommended_path.exists())
        self.assertEqual(json.loads((root / "systemd/algo_app.service.json").read_text(encoding="utf-8"))["enabled"], "enabled")

        # The install asks about AI detection while it runs, and not once it is off.
        def questions():
            overview = install_flow.plan_install(paths, manifest, environ=env, run=host_run, urlopen=urlopen)
            return [question.id for question in overview.questions]

        self.assertIn(install_flow.AI_DETECTION, questions())
        algo_app = root / "systemd/algo_app.service.json"
        algo_app.write_text(json.dumps({"exists": True, "service": "algo_app.service", "enabled": "disabled", "active": "inactive"}), encoding="utf-8")
        self.assertNotIn(install_flow.AI_DETECTION, questions())
        run_install(paths, manifest, PlainReporter(io.StringIO()), urlopen=urlopen, environ=env, run=host_run)
        self.assertEqual(load_installed_state(state_path).system_ledger["policy"]["system_optimizations"], "enabled")

    def test_touchscreen_app_keeps_its_form_without_a_decision_and_is_released_when_replaced(self):
        printer_root = copy_base_runtime(homing_patched=True)
        root = fake_system_root(include_qidiclient=True)
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        env[SYSTEM_ROOT_ENV] = str(root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        qidiclient = root / qidiclient_firmware_popup.FILE.lstrip("/")
        stock, blocked, unblocked = fake_qidiclient()
        operation = qidiclient_firmware_popup.OPERATION
        ledger = {"policy": {"system_optimizations": "disabled", "ai_detection": "unset"}}

        def apply(block):
            return apply_system_optimizations(
                paths=paths, spec=manifest.system_optimizations, ledger=ledger, reporter=PlainReporter(io.StringIO()),
                input_stream=None, environ=env, source="yes_install", block_qidi_updates=block, run=fake_host_run(root),
                urlopen=moonraker_urlopen(),
            )

        # Without a decision QIDI's executable still gets the firmware-version notice fix, unblocked, and a patched
        # app keeps blocking or allowing QIDI's packages as it did.
        for block, expected in ((None, unblocked), (True, blocked), (None, blocked), (False, unblocked), (None, unblocked)):
            ledger = apply(block)
            self.assertEqual(qidiclient.read_bytes(), expected, block)
        # An app a development build blocked, USB updates included, keeps only the online-update block, so a
        # QIDI package from a USB drive installs again.
        for block in (None, True):
            qidiclient.write_bytes(earlier_blocked_qidiclient(blocked))
            ledger = apply(block)
            self.assertEqual(qidiclient.read_bytes(), blocked, block)
        report = io.StringIO()
        system_optimizations.maybe_emit_system_dry_run(
            paths=paths, manifest=manifest, reporter=PlainReporter(report), prior_state=None,
            cli_options=SystemOptimizationCliOptions(), environ=env, block_qidi_updates=None, run=fake_host_run(root),
        )
        self.assertIn(f"{operation}: QIDI's package updates stay blocked or allowed as they are now", report.getvalue())
        self.assertEqual(Path(ledger["restore_preimages"][operation]["backup_path"]).read_bytes(), stock)
        # An app that is no longer a reviewed build is left as it is and given up, with its preserved copy.
        unreviewed = stock[:-1] + b"\x01"
        qidiclient.write_bytes(unreviewed)
        ledger = apply(True)
        self.assertEqual(qidiclient.read_bytes(), unreviewed)
        self.assertNotIn(operation, ledger["restore_preimages"])
        self.assertEqual(ledger["outcomes"][operation]["status"], "released")
        self.assertEqual(list((paths.printer_data_root / system_optimizations.SYSTEM_BACKUP_DIR).rglob("qidiclient")), [])

    def test_unreviewed_host_files_are_preserved_without_blocking_other_patches(self):
        printer_root = copy_base_runtime(homing_patched=True)
        root = fake_system_root(
            include_moonraker_cache=True, include_fluidd_bundle=True, include_moonraker_asvc=True, include_qidiclient=True,
            include_klipper_chelper=True,
        )
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        env[SYSTEM_ROOT_ENV] = str(root)
        boot_id = printer_root / "boot-id"
        boot_id.write_text("boot-one\n", encoding="utf-8")
        env["VO_BOOT_ID_PATH"] = str(boot_id)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        # A touchscreen app build nobody reviewed, the same size as a reviewed one.
        qidiclient = root / qidiclient_firmware_popup.FILE.lstrip("/")
        unreviewed = fake_qidiclient()[0][:-1] + b"\x01"
        qidiclient.write_bytes(unreviewed)
        fluidd = root / manifest.system_optimizations.fluidd_box_filament_ids.file.lstrip("/")
        fluidd.write_text(fluidd.read_text().replace('"officiallFilasList",e)', '"officiallFilasList",e||{})'))
        original = fluidd.read_bytes()
        asvc = root / moonraker_allowed_services.FILE.lstrip("/")
        asvc.write_text("[allowed]\nklipper_mcu\n", encoding="utf-8")
        # A C helper library Klipper rebuilt on the printer, the size of QIDI's.
        chelper = root / klipper_chelper.FILE.lstrip("/")
        rebuilt = chelper.read_bytes()[:-1] + b"\x01"
        chelper.write_bytes(rebuilt)
        operator_dropin = root / restart_on_exit.FILES[restart_on_exit.KLIPPER_OPERATION].lstrip("/")
        operator_dropin.parent.mkdir(parents=True)
        operator_dropin.write_text("[Service]\nRemainAfterExit=yes\n", encoding="utf-8")

        ledger = apply_system_optimizations(
            paths=paths, spec=manifest.system_optimizations,
            ledger={"policy": {"system_optimizations": "disabled", "ai_detection": "unset"}},
            reporter=PlainReporter(io.StringIO()), input_stream=None,
            environ=env, source="yes_install", block_qidi_updates=True, run=fake_host_run(root),
            urlopen=moonraker_urlopen(),
        )

        self.assertEqual(fluidd.read_bytes(), original)
        self.assertEqual(ledger["outcomes"][FLUIDD_BOX_OPERATION]["status"], "unsupported")
        # Asked to block QIDI's updates, an unreviewed touchscreen build is still left as it is, so it installs QIDI's
        # packages as it did before.
        self.assertEqual(qidiclient.read_bytes(), unreviewed)
        self.assertEqual(ledger["outcomes"][qidiclient_firmware_popup.OPERATION]["status"], "unsupported")
        self.assertNotIn(qidiclient_firmware_popup.OPERATION, ledger["restore_preimages"])
        self.assertNotIn(FLUIDD_BOX_OPERATION, ledger["restore_preimages"])
        self.assertEqual(chelper.read_bytes(), rebuilt)
        self.assertEqual(ledger["outcomes"][klipper_chelper.OPERATION]["status"], "unsupported")
        self.assertNotIn(klipper_chelper.OPERATION, ledger["restore_preimages"])
        self.assertEqual(asvc.read_text(encoding="utf-8"), "[allowed]\nklipper_mcu\n")
        self.assertEqual(ledger["outcomes"][moonraker_allowed_services.OPERATION]["status"], "unsupported")
        self.assertEqual(operator_dropin.read_text(encoding="utf-8"), "[Service]\nRemainAfterExit=yes\n")
        self.assertEqual(ledger["outcomes"][restart_on_exit.KLIPPER_OPERATION]["status"], "unsupported")
        self.assertIn(restart_on_exit.MOONRAKER_OPERATION, ledger["restore_preimages"])
        self.assertIn(system_optimizations.MOONRAKER_CACHE_OPERATION, ledger["restore_preimages"])

        # Zram swap that fails to start, such as modules the kernel rejects, leaves nothing behind,
        # including the root remount that ran before it.
        host_run = fake_host_run(root)

        def zram_fails(command, **kwargs):
            if list(command)[-3:] == ["systemctl", "restart", zram_swap.UNIT]:
                return CompletedProcess(command, 1, stdout="", stderr="insmod failed")
            return host_run(command, **kwargs)

        with self.assertRaises(SystemOptimizationApplyError):
            apply_system_optimizations(
                paths=paths, spec=manifest.system_optimizations,
                ledger={**ledger, "policy": {"system_optimizations": "enabled", "ai_detection": "keep_enabled"}},
                reporter=PlainReporter(io.StringIO()), input_stream=None,
                environ=env, source="yes_install", block_qidi_updates=True, run=zram_fails,
                urlopen=moonraker_urlopen(),
            )
        self.assertFalse((root / zram_swap.DESTINATION.lstrip("/")).exists())
        self.assertFalse((root / zram_swap.UNIT_FILE.lstrip("/")).exists())
        self.assertEqual(json.loads((root / "systemd" / f"{zram_swap.UNIT}.json").read_text(encoding="utf-8"))["enabled"], "disabled")
        self.assertEqual((root / "mounts/root.options").read_text(encoding="utf-8"), "rw,relatime,sync\n")
        self.assertFalse((root / manifest.system_optimizations.rockchip_root_sync.dropin.lstrip("/")).exists())
        self.assertFalse(paths.host_reboot_marker_path.exists())
        self.assertFalse(_journal_path(paths).exists())

    def test_moonraker_cache_admission_and_restart_failure_preserve_source(self):
        for fault in ("unknown", "unknown_metascan", "symlink", "restart", "concurrent"):
            with self.subTest(fault=fault):
                printer_root = copy_base_runtime(homing_patched=True)
                root = fake_system_root(include_moonraker_cache=True)
                env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
                env[SYSTEM_ROOT_ENV] = str(root)
                paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
                manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
                target = root / manifest.system_optimizations.moonraker_file_manager.file.lstrip("/")
                if fault == "unknown":
                    target.write_text(target.read_text().replace("retries = 3", "retries = 4"))
                elif fault == "unknown_metascan":
                    target.write_text(target.read_text().replace("get_str('filename')", "get_str('filename', '')"))
                original = target.read_bytes()
                if fault == "symlink":
                    other = target.with_name("operator.py")
                    target.rename(other)
                    target.symlink_to(other)
                host_run = fake_host_run(root)
                failed = False

                def run(command, **kwargs):
                    nonlocal failed
                    if not failed and list(command)[-3:] == ["systemctl", "restart", "moonraker.service"]:
                        if fault == "restart":
                            failed = True
                            return CompletedProcess(command, 1, stdout="", stderr="restart failed")
                        if fault == "concurrent":
                            failed = True
                            target.write_bytes(target.read_bytes() + b"\n# concurrent operator edit\n")
                            return CompletedProcess(command, 1, stdout="", stderr="restart failed")
                    return host_run(command, **kwargs)

                expected_error = SystemOptimizationRecoveryError if fault == "concurrent" else SystemOptimizationApplyError
                with self.assertRaises(expected_error):
                    apply_system_optimizations(
                        paths=paths, spec=manifest.system_optimizations,
                        ledger={"policy": {"system_optimizations": "disabled", "ai_detection": "unset"}},
                        reporter=PlainReporter(io.StringIO()), input_stream=None,
                        environ=env, source="yes_install", block_qidi_updates=True, run=run,
                        urlopen=moonraker_urlopen(),
                    )
                if fault == "concurrent":
                    drifted = target.read_bytes()
                    self.assertTrue(drifted.endswith(b"# concurrent operator edit\n"))
                    self.assertTrue(_journal_path(paths).exists())
                    with self.assertRaises(SystemOptimizationRecoveryError):
                        recover_pending_system_optimization(
                            paths=paths, manifest=manifest, reporter=PlainReporter(io.StringIO()),
                            input_stream=None, environ=env, run=host_run,
                            urlopen=moonraker_urlopen(),
                        )
                    self.assertEqual(target.read_bytes(), drifted)
                else:
                    self.assertEqual(target.read_bytes(), original)
                    self.assertFalse(_journal_path(paths).exists())
                self.assertEqual(target.is_symlink(), fault == "symlink")

    def test_forged_host_preimages_fail_closed_before_uninstall(self):
        printer_root = copy_base_runtime(homing_patched=True)
        system_root = fake_system_root()
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        env[SYSTEM_ROOT_ENV] = str(system_root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        forged = {
            "schema_version": 2,
            "policy": {"system_optimizations": "enabled", "ai_detection": "unset"},
            "restore_preimages": {
                "apt_sources": {
                    "file": {
                        "path": "/etc/shadow",
                        "exists": True,
                        "type": "file",
                        "backup_path": "/tmp/shadow",
                        "sha256": "0" * 64,
                        "mode": "0600",
                        "uid": 0,
                        "gid": 0,
                    }
                }
            },
            "outcomes": {"apt_sources": {"status": "applied"}},
            "committed_transactions": {},
        }
        with self.assertRaisesRegex(Exception, "Host file restoration preimage is invalid"):
            validate_system_ledger(forged, manifest=manifest, paths=paths)
        journal = {"schema_version": 1, "operation": "apt_sources", "transaction_id": "x", "phase": "apply", "preimage": forged["restore_preimages"]["apt_sources"]}
        _journal_path(paths).write_text(json.dumps(journal), encoding="utf-8")
        before = snapshot_tree(system_root)
        # A journal no run can act on is set aside, untouched, so it stops nothing; nothing it names is restored.
        self.assertFalse(recover_pending_system_optimization(
            paths=paths, manifest=manifest, reporter=PlainReporter(io.StringIO()),
            input_stream=io.StringIO(), environ=env, run=fake_host_run(system_root),
            urlopen=moonraker_urlopen(),
        ))
        self.assertFalse(_journal_path(paths).exists())
        self.assertEqual(len(list(paths.printer_data_root.glob(f"{_journal_path(paths).name}.set-aside-*"))), 1)
        self.assertEqual(snapshot_tree(system_root), before)

    def test_host_ledger_migration_and_rockchip_journal_recovery(self):
        printer_root = copy_base_runtime(homing_patched=True)
        system_root = fake_system_root()
        host_run = fake_host_run(system_root)
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        env[SYSTEM_ROOT_ENV] = str(system_root)
        boot = printer_root / "boot-id"
        boot.write_text("test-boot\n")
        env["VO_BOOT_ID_PATH"] = str(boot)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        legacy = {
            "policy": {"system_optimizations": "enabled", "ai_detection": "unset"},
            "actions": [{"id": "rockchip_root_sync", "status": "applied", "transaction_id": "committed", "desired": {}}],
            "restore_preimages": {},
        }
        compacted = _compact_host_ledger(legacy)
        self.assertEqual(compacted["schema_version"], 2)
        self.assertEqual(compacted["committed_transactions"]["rockchip_root_sync"], "committed")
        self.assertNotIn("actions", compacted)

        rockchip = manifest.system_optimizations.rockchip_root_sync
        journal = {
            "schema_version": 1,
            "operation": "rockchip_root_sync",
            "transaction_id": "committed",
            "started_at": "2026-01-01T00:00:00Z",
            "phase": "captured",
            "preimage": {
                "classification": "defective_stock",
                "dropin": {"path": rockchip.dropin, "exists": False},
                "desired_dropin": rockchip.dropin_content,
                "unit": {},
                "mount_options": ["rw", "sync"],
                "marker": {"exists": False},
            },
        }
        _journal_path(paths).write_text(json.dumps(journal), encoding="utf-8")
        self.assertTrue(
            recover_pending_system_optimization(
                paths=paths,
                manifest=manifest,
                reporter=PlainReporter(io.StringIO()),
                input_stream=io.StringIO(),
                environ=env,
                run=host_run,
                urlopen=moonraker_urlopen(),
            )
        )
        self.assertFalse(_journal_path(paths).exists())

        # The remount runs before the other operations, and a crash before the installed state commits it still
        # rolls it back.
        mount_before = (system_root / "mounts/root.options").read_text(encoding="utf-8")
        apply_system_optimizations(
            paths=paths, spec=manifest.system_optimizations,
            ledger={"policy": {"system_optimizations": "enabled", "ai_detection": "keep_enabled"}},
            reporter=PlainReporter(io.StringIO()), input_stream=None,
            environ=env, source="yes_install", block_qidi_updates=True, run=host_run,
            urlopen=moonraker_urlopen(),
        )
        self.assertNotIn("sync", (system_root / "mounts/root.options").read_text(encoding="utf-8").strip().split(","))
        self.assertTrue(
            recover_pending_system_optimization(
                paths=paths,
                manifest=manifest,
                reporter=PlainReporter(io.StringIO()),
                input_stream=io.StringIO(),
                environ=env,
                run=host_run,
                urlopen=moonraker_urlopen(),
            )
        )
        self.assertEqual((system_root / "mounts/root.options").read_text(encoding="utf-8"), mount_before)
        self.assertFalse((system_root / rockchip.dropin.lstrip("/")).exists())
        self.assertFalse(_journal_path(paths).exists())

    def test_uninstall_preserves_user_modified_host_state(self):
        printer_root = copy_base_runtime(homing_patched=True)
        system_root = fake_system_root(include_moonraker_cache=True, include_filament_config=True)
        host_run = fake_host_run(system_root)
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        env[SYSTEM_ROOT_ENV] = str(system_root)
        boot = printer_root / "boot-id"
        boot.write_text("test-boot\n")
        env["VO_BOOT_ID_PATH"] = str(boot)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        compatibility = load_supported_upgrade_sources(REPO_ROOT / "installer/supported_upgrade_sources.yaml")
        run_install(
            paths, manifest, PlainReporter(io.StringIO()), input_stream=io.StringIO("yes\nno\n"),
            urlopen=moonraker_urlopen(saved_variables_path=paths.config_root / "saved_variables.cfg"), environ=env,
            run=host_run,
        )
        (system_root / "etc/apt/sources.list").write_text("operator apt\n", encoding="utf-8")
        moonraker = system_root / manifest.system_optimizations.moonraker_file_manager.file.lstrip("/")
        drifted = moonraker.read_bytes() + b"\n# operator modification outside the patched class\n"
        moonraker.write_bytes(drifted)
        filament_link = printer_root / manifest.system_optimizations.fluidd_filament_config.link
        filament_link.unlink()
        filament_link.symlink_to("/home/qidi/operator_filaments")
        state = load_installed_state(printer_root / manifest.state_file)
        cache_policy = {**state.system_ledger, "policy": {"system_optimizations": "disabled", "ai_detection": "unset"}}
        reconciled = apply_system_optimizations(
            paths=paths, spec=manifest.system_optimizations, ledger=cache_policy,
            reporter=PlainReporter(io.StringIO()), input_stream=None, environ=env,
            source="auto_update_reconcile", block_qidi_updates=True, run=host_run,
            urlopen=moonraker_urlopen(),
        )
        self.assertEqual(moonraker.read_bytes(), drifted)
        self.assertEqual(os.readlink(filament_link), "/home/qidi/operator_filaments")
        write_installed_state(printer_root / manifest.state_file, _replace_system_ledger(state, reconciled))
        (system_root / "etc/apt/sources.list").write_text("operator apt\n", encoding="utf-8")
        probe_helper = paths.managed_klipper_root / "klippy/extras/vo_probe_test.py"
        probe_modified = probe_helper.read_bytes() + b"\n# operator modification\n"
        probe_helper.write_bytes(probe_modified)
        zram = system_root / zram_swap.DESTINATION.lstrip("/")
        (zram / zram_swap.MARKER).unlink()
        report = io.StringIO()
        run_uninstall(
            paths, manifest, compatibility, PlainReporter(report), input_stream=io.StringIO("yes\nyes\nno\n"),
            urlopen=moonraker_urlopen(), environ=env,
            run=host_run,
        )
        self.assertEqual((system_root / "etc/apt/sources.list").read_text(encoding="utf-8"), "operator apt\n")
        self.assertEqual(moonraker.read_bytes(), drifted)
        self.assertEqual(os.readlink(filament_link), "/home/qidi/operator_filaments")
        self.assertEqual(probe_helper.read_bytes(), probe_modified)
        self.assertTrue(zram.is_dir())
        self.assertFalse((system_root / zram_swap.UNIT_FILE.lstrip("/")).exists())

    def test_legacy_host_preimages_migrate_and_restore(self):
        printer_root = copy_base_runtime(homing_patched=True)
        system_root = fake_system_root()
        host_run = fake_host_run(system_root)
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        env[SYSTEM_ROOT_ENV] = str(system_root)
        boot = printer_root / "boot-id"
        boot.write_text("test-boot\n", encoding="utf-8")
        env["VO_BOOT_ID_PATH"] = str(boot)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        compatibility = load_supported_upgrade_sources(REPO_ROOT / "installer/supported_upgrade_sources.yaml")
        run_install(paths, manifest, PlainReporter(io.StringIO()), input_stream=io.StringIO("yes\nno\nno\n"), urlopen=moonraker_urlopen(saved_variables_path=paths.config_root / "saved_variables.cfg"), environ=env, run=host_run)
        state_path = printer_root / manifest.state_file
        state = load_installed_state(state_path)
        legacy = copy.deepcopy(state.system_ledger)
        for preimage in legacy["restore_preimages"].values():
            self._make_legacy_preimage(preimage, system_root)
        legacy.pop("schema_version")
        legacy["actions"] = [
            {"id": operation_id, "status": "applied", "transaction_id": outcome.get("transaction_id"), "desired": outcome.get("desired", {})}
            for operation_id, outcome in legacy.pop("outcomes").items()
        ]
        legacy.pop("committed_transactions")
        write_installed_state(state_path, _replace_system_ledger(state, legacy))
        run_install(paths, manifest, PlainReporter(io.StringIO()), input_stream=io.StringIO("yes\nno\nno\n"), urlopen=moonraker_urlopen(saved_variables_path=paths.config_root / "saved_variables.cfg"), environ=env, run=host_run)
        migrated = load_installed_state(state_path).system_ledger
        self.assertEqual(migrated["restore_preimages"]["apt_sources"]["file"]["sha256"], __import__("hashlib").sha256(b"old apt\n").hexdigest())
        migrated_gifs = Path(migrated["restore_preimages"]["qidiclient_static_gifs"]["backup_dir"])
        self.assertTrue(migrated_gifs.is_relative_to(paths.printer_data_root / system_optimizations.SYSTEM_BACKUP_DIR))
        # Simulate power loss after atomic state commit but before migration-journal cleanup.
        migration_target = migrated_gifs.parent
        migration_id = migration_target.name.removeprefix(system_optimizations.LEGACY_MIGRATION_PREFIX)
        (migration_target / system_optimizations.LEGACY_MIGRATION_OWNER).write_text(
            json.dumps({"kind": "legacy-system-ledger-gifs", "migration_id": migration_id}), encoding="utf-8"
        )
        (paths.printer_data_root / system_optimizations.LEGACY_MIGRATION_JOURNAL).write_text(
            json.dumps({"schema_version": 1, "kind": "legacy-system-ledger-gifs", "migration_id": migration_id, "target": str(migration_target)}), encoding="utf-8"
        )
        system_optimizations.recover_pending_legacy_system_ledger_migration(paths, state_path)
        self.assertTrue(migrated_gifs.is_dir())
        run_uninstall(paths, manifest, compatibility, PlainReporter(io.StringIO()), input_stream=io.StringIO("yes\nyes\nno\n"), urlopen=moonraker_urlopen(), environ=env, run=host_run)
        self.assertEqual((system_root / "etc/apt/sources.list").read_text(encoding="utf-8"), "old apt\n")
        self.assertEqual((system_root / "home/qidi/QIDI_Client/access/account/process.gif").read_bytes(), b"old")
        self.assertFalse((system_root / zram_swap.DESTINATION.lstrip("/")).exists())

    def test_legacy_migration_has_no_writes_before_preflight_dry_run_or_failed_plan(self):
        paths, manifest, env, root = self._legacy_host_context()
        state_path = paths.printer_data_root / manifest.state_file
        before_state = state_path.read_bytes()
        backup_root = paths.printer_data_root / system_optimizations.SYSTEM_BACKUP_DIR
        before = snapshot_tree(backup_root)
        with self.assertRaisesRegex(RuntimeError, "preflight stop"):
            run_install(
                paths,
                manifest,
                PlainReporter(io.StringIO()),
                dry_run=True,
                urlopen=moonraker_urlopen(),
                disk_usage=lambda _: (_ for _ in ()).throw(RuntimeError("preflight stop")),
                environ=env,
                run=fake_host_run(root),
            )
        self.assertEqual(snapshot_tree(backup_root), before)
        with mock.patch("installer.runtime.runner.build_install_plan", side_effect=RuntimeError("invalid plan")):
            with self.assertRaisesRegex(RuntimeError, "invalid plan"):
                run_install(paths, manifest, PlainReporter(io.StringIO()), urlopen=moonraker_urlopen(), environ=env, run=fake_host_run(root))
        self.assertEqual(state_path.read_bytes(), before_state)
        self.assertEqual(snapshot_tree(backup_root), before)
        self._assert_legacy_migration_interruption_recovers_unreferenced_copy(paths, manifest, env, state_path)

    def _assert_legacy_migration_interruption_recovers_unreferenced_copy(self, paths, manifest, env, state_path):
        before_state = state_path.read_bytes()
        migrated_paths = []
        system_optimizations.migrate_legacy_system_ledger(
            load_installed_state(state_path).system_ledger,
            manifest=manifest,
            paths=paths,
            environ=env,
            created_paths=migrated_paths,
        )
        self.assertEqual(len(migrated_paths), 1)
        self.assertTrue(migrated_paths[0].exists())
        self.assertTrue((paths.printer_data_root / system_optimizations.LEGACY_MIGRATION_JOURNAL).exists())
        system_optimizations.recover_pending_legacy_system_ledger_migration(paths, state_path)
        self.assertFalse(migrated_paths[0].exists())
        self.assertFalse((paths.printer_data_root / system_optimizations.LEGACY_MIGRATION_JOURNAL).exists())
        self.assertEqual(state_path.read_bytes(), before_state)

    def test_legacy_migration_cleanup_requires_owned_dedicated_target(self):
        invalid_cases = (
            ("backup-root", lambda backup_root, unrelated: {"schema_version": 1, "kind": "legacy-system-ledger-gifs", "migration_id": "a" * 32, "target": str(backup_root)}),
            ("unrelated-subtree", lambda backup_root, unrelated: {"schema_version": 1, "kind": "legacy-system-ledger-gifs", "migration_id": "b" * 32, "target": str(unrelated)}),
            ("malformed-schema", lambda backup_root, unrelated: {"schema_version": 999, "kind": "legacy-system-ledger-gifs", "migration_id": "c" * 32, "target": str(unrelated)}),
            ("missing-identity", lambda backup_root, unrelated: {"schema_version": 1, "target": str(unrelated)}),
        )
        paths, _, _, _ = self._host_context()
        backup_root = paths.printer_data_root / system_optimizations.SYSTEM_BACKUP_DIR
        unrelated = backup_root / "retained-preimage"
        unrelated.mkdir(parents=True)
        retained = unrelated / "retained.bin"
        retained.write_bytes(b"retain")
        journal = paths.printer_data_root / system_optimizations.LEGACY_MIGRATION_JOURNAL
        for name, payload_for in invalid_cases:
            with self.subTest(name=name):
                journal.write_text(json.dumps(payload_for(backup_root, unrelated)), encoding="utf-8")
                # An invalid journal is dropped so the next run can go on; nothing it names is removed.
                system_optimizations.recover_pending_legacy_system_ledger_migration(
                    paths, paths.printer_data_root / "absent-state.yaml"
                )
                self.assertFalse(journal.exists())
                self.assertEqual(retained.read_bytes(), b"retain")

        link = backup_root / ".ledger-migration-link"
        link.symlink_to(unrelated, target_is_directory=True)
        journal = paths.printer_data_root / system_optimizations.LEGACY_MIGRATION_JOURNAL
        journal.write_text(json.dumps({"schema_version": 1, "kind": "legacy-system-ledger-gifs", "migration_id": "a" * 32, "target": str(link)}), encoding="utf-8")
        system_optimizations.recover_pending_legacy_system_ledger_migration(
            paths, paths.printer_data_root / "absent-state.yaml"
        )
        self.assertFalse(journal.exists())
        self.assertEqual(retained.read_bytes(), b"retain")

        paths, _, _, _ = self._host_context()
        backup_root = paths.printer_data_root / system_optimizations.SYSTEM_BACKUP_DIR
        collision = backup_root / f"{system_optimizations.LEGACY_MIGRATION_PREFIX}{'c' * 32}"
        collision.mkdir(parents=True)
        retained = collision / "retained.bin"
        retained.write_bytes(b"retain")
        old = paths.printer_data_root / "old-gifs"
        (old / "account").mkdir(parents=True)
        (old / "account/process.gif").write_bytes(b"old")
        preimage = {
            "backup_dir": str(old),
            "replaced": ["account/process.gif"],
            "files": {"account/process.gif": {"sha256": __import__("hashlib").sha256(b"old").hexdigest()}},
        }
        migration_id = mock.Mock(hex="c" * 32)
        with mock.patch.object(system_optimizations.uuid, "uuid4", return_value=migration_id):
            with self.assertRaises(FileExistsError):
                system_optimizations._materialize_legacy_gif_preimage(preimage, paths=paths)
        self.assertEqual(retained.read_bytes(), b"retain")

    def test_forged_ledger_uninstall_has_no_backup_or_host_side_effect(self):
        paths, manifest, env, root = self._installed_host_context()
        state_path = paths.printer_data_root / manifest.state_file
        state = load_installed_state(state_path)
        forged = {
            "schema_version": 2,
            "policy": {"system_optimizations": "enabled", "ai_detection": "unset"},
            "restore_preimages": {"apt_sources": {"file": {"path": "/etc/shadow", "exists": True, "type": "file", "backup_path": "/tmp/shadow", "sha256": "0" * 64, "mode": "0600", "uid": 0, "gid": 0}}},
            "outcomes": {"apt_sources": {"status": "applied"}},
            "committed_transactions": {},
        }
        write_installed_state(state_path, _replace_system_ledger(state, forged))
        before = snapshot_tree(root)
        compatibility = load_supported_upgrade_sources(REPO_ROOT / "installer/supported_upgrade_sources.yaml")
        with mock.patch("installer.runtime.uninstall.create_config_backup") as backup, mock.patch("installer.runtime.uninstall.prune_installer_backups") as prune:
            with self.assertRaises(InstalledPackageValidationError):
                run_uninstall(paths, manifest, compatibility, PlainReporter(io.StringIO()), input_stream=io.StringIO(), urlopen=moonraker_urlopen(), environ=env, run=fake_host_run(root))
        backup.assert_not_called(); prune.assert_not_called()
        self.assertEqual(snapshot_tree(root), before)

    def test_forged_legacy_rockchip_journal_is_never_acted_on(self):
        legacy = {"policy": {"system_optimizations": "enabled", "ai_detection": "unset"}, "restore_preimages": {}, "outcomes": {}, "committed_transactions": {}, "actions": [{"id": "rockchip_root_sync", "status": "applied", "transaction_id": "same"}]}
        # Recovery loads only an on-disk installed state; use a valid install then replace its ledger.
        paths, manifest, env, root = self._installed_host_context()
        state_path = paths.printer_data_root / manifest.state_file
        write_installed_state(state_path, _replace_system_ledger(load_installed_state(state_path), legacy))
        journal = {"schema_version": 1, "operation": "rockchip_root_sync", "transaction_id": "same", "phase": "captured", "preimage": {"classification": "defective_stock", "dropin": {"path": "/etc/shadow", "exists": False}, "desired_dropin": manifest.system_optimizations.rockchip_root_sync.dropin_content, "unit": {}, "mount_options": ["rw", "sync"], "marker": {"exists": False}}}
        _journal_path(paths).write_text(json.dumps(journal), encoding="utf-8")
        before = snapshot_tree(root)
        self.assertFalse(recover_pending_system_optimization(paths=paths, manifest=manifest, reporter=PlainReporter(io.StringIO()), input_stream=io.StringIO(), environ=env, run=fake_host_run(root), urlopen=moonraker_urlopen()))
        self.assertFalse(_journal_path(paths).exists())
        self.assertEqual(snapshot_tree(root), before)

    def test_rockchip_restores_full_admitted_mount_preimage(self):
        paths, manifest, env, root = self._host_context()
        (root / "mounts/root.options").write_text("ro,noatime,sync\n", encoding="utf-8")
        # A kernel other than the build the zram modules were made for, such as one from a QIDI update, runs without them.
        (root / "proc/sys/kernel/version").write_text("#5 SMP PREEMPT Mon Jan 5 10:00:00 CST 2026\n", encoding="utf-8")
        run_install(paths, manifest, PlainReporter(io.StringIO()), input_stream=io.StringIO("yes\nno\nno\n"), urlopen=moonraker_urlopen(saved_variables_path=paths.config_root / "saved_variables.cfg"), environ=env, run=fake_host_run(root))
        outcomes = load_installed_state(paths.printer_data_root / manifest.state_file).system_ledger["outcomes"]
        self.assertEqual(outcomes[zram_swap.OPERATION]["status"], "unsupported")
        self.assertEqual(outcomes[zram_swap.UNIT_OPERATION]["status"], "skipped")
        self.assertFalse((root / zram_swap.DESTINATION.lstrip("/")).exists())
        self.assertFalse((root / zram_swap.UNIT_FILE.lstrip("/")).exists())
        self.assertEqual((root / cpu_governor.FILE.lstrip("/")).read_text(encoding="utf-8"), cpu_governor.CONTENT)
        # ADB stays on the USB port: its service keeps running, and the profile file keeps it off the network.
        usbdevice = root / "systemd" / f"{adb_usb_only.SERVICE}.json"
        self.assertFalse(usbdevice.exists())
        self.assertEqual((root / adb_usb_only.FILE.lstrip("/")).read_text(encoding="utf-8"), adb_usb_only.CONTENT)
        # An update that finds the repair already in place still lets uninstall put the mount back, including after
        # an earlier version recorded that outcome without the mount options. ADB, which an earlier version turned
        # off, comes back on.
        state_path = paths.printer_data_root / manifest.state_file
        state = load_installed_state(state_path)
        state.system_ledger["outcomes"]["rockchip_root_sync"] = {"status": "already_current_owned", "source": "yes_install", "postflight": {"classification": "already_current_owned"}}
        on = {"exists": True, "service": adb_usb_only.SERVICE, "enabled": "enabled", "active": "active"}
        state.system_ledger["restore_preimages"]["service_usbdevice.service"] = on
        write_installed_state(state_path, state)
        usbdevice.write_text(json.dumps({**on, "enabled": "disabled", "active": "inactive"}), encoding="utf-8")
        run_install(paths, manifest, PlainReporter(io.StringIO()), input_stream=io.StringIO("yes\nno\nno\n"), urlopen=moonraker_urlopen(saved_variables_path=paths.config_root / "saved_variables.cfg"), environ=env, run=fake_host_run(root))
        ledger = load_installed_state(state_path).system_ledger
        self.assertEqual({key: json.loads(usbdevice.read_text(encoding="utf-8"))[key] for key in ("enabled", "active")}, {"enabled": "enabled", "active": "active"})
        self.assertNotIn("service_usbdevice.service", ledger["restore_preimages"])
        self.assertEqual(ledger["outcomes"]["service_usbdevice.service"]["status"], "retired")
        compatibility = load_supported_upgrade_sources(REPO_ROOT / "installer/supported_upgrade_sources.yaml")
        report = io.StringIO()
        run_uninstall(paths, manifest, compatibility, PlainReporter(report), input_stream=io.StringIO("yes\nyes\n"), urlopen=moonraker_urlopen(), environ=env, run=fake_host_run(root))
        self.assertNotIn("Rockchip system state was modified", report.getvalue())
        self.assertEqual((root / "mounts/root.options").read_text(encoding="utf-8"), "ro,noatime,sync\n")
        # Accepted restoration puts back QIDI's ondemand governor, and QIDI's ADB settings.
        self.assertFalse((root / cpu_governor.FILE.lstrip("/")).exists())
        self.assertFalse((root / adb_usb_only.FILE.lstrip("/")).exists())

    def test_compensation_journal_blocks_until_all_services_recover(self):
        paths, manifest, env, root = self._host_context()
        calls = {"fail_apply": True, "fail_restore": True}
        base_run = fake_host_run(root)
        def run(command, **kwargs):
            actual = command[4:] if command[:4] == ["sudo", "-S", "-p", ""] else command
            if actual == ["systemctl", "disable", "--now", "bluetooth"] and calls["fail_apply"]:
                return CompletedProcess(command, 1, stdout="failed")
            if actual in (["systemctl", "enable", "xl2tpd"], ["systemctl", "start", "xl2tpd"], ["systemctl", "enable", "--now", "xl2tpd"]) and calls["fail_restore"]:
                return CompletedProcess(command, 1, stdout="failed")
            return base_run(command, **kwargs)
        ledger = {"policy": {"system_optimizations": "enabled", "ai_detection": "keep_enabled"}, "restore_preimages": {}, "actions": []}
        selected = ("service_xl2tpd", "service_bluetooth")
        with mock.patch.object(system_optimizations, "_selected_operation_ids", return_value=selected):
            with self.assertRaises(SystemOptimizationRecoveryError) as failed:
                apply_system_optimizations(paths=paths, spec=manifest.system_optimizations, ledger=ledger, reporter=PlainReporter(io.StringIO()), input_stream=io.StringIO(), environ=env, source="test", block_qidi_updates=True, run=run, urlopen=moonraker_urlopen())
        # The failure that started the rollback stays visible beside the rollback's own failure.
        self.assertIn(messages.SYSTEM_OPTIMIZATIONS_FAILED, failed.exception.message)
        self.assertIn(messages.SYSTEM_RESTORE_FAILED, failed.exception.message)
        payload = json.loads(_journal_path(paths).read_text(encoding="utf-8"))
        self.assertIn("service_xl2tpd", payload["preimages"])
        self.assertIn("service_bluetooth", payload["preimages"])
        calls["fail_apply"] = False
        with self.assertRaises(SystemOptimizationRecoveryError) as pending:
            recover_pending_system_optimization(paths=paths, manifest=manifest, reporter=PlainReporter(io.StringIO()), input_stream=io.StringIO(), environ=env, run=run, urlopen=moonraker_urlopen())
        self.assertIn(messages.SYSTEM_OPTIMIZATIONS_FAILED, pending.exception.message)
        self.assertTrue(_journal_path(paths).exists())
        with self.assertRaises(SystemOptimizationRecoveryError):
            apply_system_optimizations(paths=paths, spec=manifest.system_optimizations, ledger=ledger, reporter=PlainReporter(io.StringIO()), input_stream=io.StringIO(), environ=env, source="next-lifecycle", block_qidi_updates=True, run=run, urlopen=moonraker_urlopen())
        calls["fail_restore"] = False
        output = io.StringIO()
        self.assertTrue(recover_pending_system_optimization(paths=paths, manifest=manifest, reporter=PlainReporter(output), input_stream=io.StringIO(), environ=env, run=run, urlopen=moonraker_urlopen()))
        self.assertIn(messages.SYSTEM_OPTIMIZATIONS_FAILED, output.getvalue())
        self.assertFalse(_journal_path(paths).exists())

    def test_a_rollback_that_keeps_failing_is_set_aside_after_its_last_attempt(self):
        # Such as a service QIDI's package removed: the rollback can never finish, and must not stop every run.
        paths, manifest, env, root = self._host_context()
        calls = {"fail_apply": True}
        base_run = fake_host_run(root)
        def run(command, **kwargs):
            actual = command[4:] if command[:4] == ["sudo", "-S", "-p", ""] else command
            if actual == ["systemctl", "disable", "--now", "bluetooth"] and calls["fail_apply"]:
                return CompletedProcess(command, 1, stdout="failed")
            if actual in (["systemctl", "enable", "xl2tpd"], ["systemctl", "start", "xl2tpd"], ["systemctl", "enable", "--now", "xl2tpd"]):
                return CompletedProcess(command, 1, stdout="failed")
            return base_run(command, **kwargs)
        ledger = {"policy": {"system_optimizations": "enabled", "ai_detection": "keep_enabled"}, "restore_preimages": {}, "actions": []}
        with mock.patch.object(system_optimizations, "_selected_operation_ids", return_value=("service_xl2tpd", "service_bluetooth")):
            with self.assertRaises(SystemOptimizationRecoveryError):
                apply_system_optimizations(paths=paths, spec=manifest.system_optimizations, ledger=ledger, reporter=PlainReporter(io.StringIO()), input_stream=io.StringIO(), environ=env, source="test", block_qidi_updates=True, run=run, urlopen=moonraker_urlopen())
        calls["fail_apply"] = False
        recover = lambda output: recover_pending_system_optimization(paths=paths, manifest=manifest, reporter=PlainReporter(output), input_stream=io.StringIO(), environ=env, run=run, urlopen=moonraker_urlopen())
        for _ in range(system_optimizations.JOURNAL_RECOVERY_ATTEMPTS - 1):
            with self.assertRaises(SystemOptimizationRecoveryError):
                recover(io.StringIO())
            self.assertTrue(_journal_path(paths).exists())
        output = io.StringIO()
        self.assertFalse(recover(output))
        self.assertFalse(_journal_path(paths).exists())
        self.assertIn("could not be rolled back", output.getvalue())
        self.assertEqual(len(list(paths.printer_data_root.glob(f"{_journal_path(paths).name}.set-aside-*"))), 1)

    def test_pending_journal_recovers_past_a_gif_backup_a_power_cut_emptied(self):
        paths, manifest, env, root = self._host_context()
        calls = {"fail": True}
        base_run = fake_host_run(root)
        def run(command, **kwargs):
            actual = command[4:] if command[:4] == ["sudo", "-S", "-p", ""] else command
            if calls["fail"] and actual in (["systemctl", "disable", "--now", "bluetooth"], ["systemctl", "enable", "xl2tpd"],
                                            ["systemctl", "start", "xl2tpd"], ["systemctl", "enable", "--now", "xl2tpd"]):
                return CompletedProcess(command, 1, stdout="failed")
            return base_run(command, **kwargs)
        ledger = {"policy": {"system_optimizations": "enabled", "ai_detection": "keep_enabled"}, "restore_preimages": {}, "actions": []}
        selected = ("qidiclient_static_gifs", "service_xl2tpd", "service_bluetooth")
        with mock.patch.object(system_optimizations, "_selected_operation_ids", return_value=selected):
            with self.assertRaises(SystemOptimizationRecoveryError):
                apply_system_optimizations(paths=paths, spec=manifest.system_optimizations, ledger=ledger, reporter=PlainReporter(io.StringIO()), input_stream=io.StringIO(), environ=env, source="test", block_qidi_updates=True, run=run, urlopen=moonraker_urlopen())
        gifs = json.loads(_journal_path(paths).read_text(encoding="utf-8"))["preimages"]["qidiclient_static_gifs"]
        damaged, *intact = gifs["replaced"]
        (Path(gifs["backup_dir"]) / damaged).write_bytes(b"")
        access = root / gifs["destination"].lstrip("/")
        calls["fail"] = False

        self.assertTrue(recover_pending_system_optimization(paths=paths, manifest=manifest, reporter=PlainReporter(io.StringIO()), input_stream=io.StringIO(), environ=env, run=run, urlopen=moonraker_urlopen()))

        self.assertFalse(_journal_path(paths).exists())
        for relative in intact:
            self.assertEqual((access / relative).read_bytes(), (Path(gifs["backup_dir"]) / relative).read_bytes())

    def test_an_update_from_an_earlier_release_s_patches_rolls_back_to_them(self):
        # Issue #390: releases before 26.11.22.3 wrote the same patches under tltg names. An update whose host changes
        # failed could neither roll them back nor, at every later run, replay the rollback it left pending.
        paths, manifest, env, root = self._host_context()
        files = {
            moonraker_spoolman.OPERATION: (moonraker_spoolman.FILE, "moonraker_spoolman.py"),
            KLIPPER_PRINT_STATS_OPERATION: ("/home/qidi/klipper/klippy/extras/print_stats_manager.py", "klipper_print_stats_manager.py"),
        }
        earlier = {}
        for operation_id, (path, fixture) in files.items():
            patched = system_optimizations.GUARDED_FILE_PATCHES[operation_id].patch((FIXTURES_ROOT / fixture).read_text(encoding="utf-8"))
            earlier[path] = patched.replace("# vo:", "# tltg-optimized:").replace("vo_", "tltg_").replace("VO_", "TLTG_").encode("utf-8")
            self.assertNotEqual(earlier[path], patched.encode("utf-8"))
            target = root / path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(earlier[path])
        calls = {"fail_apply": True, "fail_restore": True}
        base_run = fake_host_run(root)
        def run(command, **kwargs):
            actual = command[4:] if command[:4] == ["sudo", "-S", "-p", ""] else command
            if actual == ["systemctl", "disable", "--now", "bluetooth"] and calls["fail_apply"]:
                return CompletedProcess(command, 1, stdout="failed")
            if actual in (["systemctl", "enable", "xl2tpd"], ["systemctl", "start", "xl2tpd"], ["systemctl", "enable", "--now", "xl2tpd"]) and calls["fail_restore"]:
                return CompletedProcess(command, 1, stdout="failed")
            return base_run(command, **kwargs)
        def apply():
            ledger = {"policy": {"system_optimizations": "enabled", "ai_detection": "keep_enabled"}, "restore_preimages": {}, "actions": []}
            selected = ("service_xl2tpd", *files, "service_bluetooth")
            with mock.patch.object(system_optimizations, "_selected_operation_ids", return_value=selected):
                apply_system_optimizations(paths=paths, spec=manifest.system_optimizations, ledger=ledger, reporter=PlainReporter(io.StringIO()), input_stream=io.StringIO(), environ=env, source="test", block_qidi_updates=True, run=run, urlopen=moonraker_urlopen())
        recover = lambda: recover_pending_system_optimization(paths=paths, manifest=manifest, reporter=PlainReporter(io.StringIO()), input_stream=io.StringIO(), environ=env, run=run, urlopen=moonraker_urlopen())

        # A rollback that only a service stops leaves the journal, and the next run replays it past both files.
        with self.assertRaises(SystemOptimizationRecoveryError) as failed:
            apply()
        self.assertNotIn("does not match the reviewed QIDI source", failed.exception.message)
        self.assertNotIn("changed during the transaction", failed.exception.message)
        calls["fail_restore"] = False
        self.assertTrue(recover())
        self.assertFalse(_journal_path(paths).exists())
        for path, content in earlier.items():
            self.assertEqual((root / path.lstrip("/")).read_bytes(), content)

        # A rollback that finishes puts the earlier release's patches back at once.
        with self.assertRaises(SystemOptimizationApplyError):
            apply()
        self.assertFalse(_journal_path(paths).exists())
        for path, content in earlier.items():
            self.assertEqual((root / path.lstrip("/")).read_bytes(), content)

    def test_rockchip_drift_journal_never_replays_after_interrupt(self):
        paths, manifest, env, root = self._installed_host_context()
        state = load_installed_state(paths.printer_data_root / manifest.state_file)
        mount_options = root / "mounts/root.options"
        mount_options.write_text("ro,noatime,async\n", encoding="utf-8")
        reporter = PlainReporter(io.StringIO())
        original_line = reporter.line
        def interrupt_after_preserve(message):
            original_line(message)
            if message == "Rockchip system state was modified after install and was preserved.":
                raise KeyboardInterrupt
        with mock.patch.object(reporter, "line", side_effect=interrupt_after_preserve):
            with self.assertRaises(KeyboardInterrupt):
                system_optimizations.restore_system_optimizations(paths=paths, manifest=manifest, state=state, reporter=reporter, input_stream=io.StringIO(), environ=env, include_optional=True, run=fake_host_run(root), urlopen=moonraker_urlopen())
        self.assertEqual(mount_options.read_text(encoding="utf-8"), "ro,noatime,async\n")
        recover_pending_system_optimization(paths=paths, manifest=manifest, reporter=PlainReporter(io.StringIO()), input_stream=io.StringIO(), environ=env, run=fake_host_run(root), urlopen=moonraker_urlopen())
        self.assertEqual(mount_options.read_text(encoding="utf-8"), "ro,noatime,async\n")

    def test_unsafe_or_incomplete_mount_preimage_fails_before_host_mutation(self):
        paths, manifest, env, root = self._installed_host_context()
        state = load_installed_state(paths.printer_data_root / manifest.state_file)
        original = list(state.system_ledger["restore_preimages"]["rockchip_root_sync"]["mount_options"])
        before = snapshot_tree(root)
        for mount_preimage in (["rw", "bind"], ["sync"]):
            with self.subTest(mount_preimage=mount_preimage):
                state.system_ledger["restore_preimages"]["rockchip_root_sync"]["mount_options"] = mount_preimage
                with self.assertRaisesRegex(Exception, "Rockchip restoration preimage is invalid"):
                    system_optimizations.restore_system_optimizations(
                        paths=paths,
                        manifest=manifest,
                        state=state,
                        reporter=PlainReporter(io.StringIO()),
                        input_stream=io.StringIO(),
                        environ=env,
                        include_optional=True,
                        run=fake_host_run(root),
                        urlopen=moonraker_urlopen(),
                    )
                self.assertFalse(_journal_path(paths).exists())
                self.assertEqual(snapshot_tree(root), before)
        state.system_ledger["restore_preimages"]["rockchip_root_sync"]["mount_options"] = original

    def test_a_firmware_update_carries_what_uninstall_needs_across_qidis_package(self):
        # QIDI's package deletes the installed state with the config folder and replaces QIDI's own files. The
        # update's install still knows what the earlier install changed on the host, and uninstall undoes all of it.
        printer_root = copy_base_runtime(homing_patched=True)
        system_root = fake_system_root(include_moonraker_cache=True)
        host_run = fake_host_run(system_root)
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        env[SYSTEM_ROOT_ENV] = str(system_root)
        boot = printer_root / "boot-id"
        boot.write_text("test-boot\n")
        env["VO_BOOT_ID_PATH"] = str(boot)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        compatibility = load_supported_upgrade_sources(REPO_ROOT / "installer/supported_upgrade_sources.yaml")
        stock_config = printer_root.parent / "stock-config"
        shutil.copytree(paths.config_root, stock_config, symlinks=True)
        before = snapshot_tree(system_root)
        urlopen = moonraker_urlopen(saved_variables_path=paths.config_root / "saved_variables.cfg")
        run_install(paths, manifest, PlainReporter(io.StringIO()), input_stream=io.StringIO("yes\nno\n"), urlopen=urlopen,
                    environ=env, run=host_run)
        state_path = printer_root / manifest.state_file
        installed = load_installed_state(state_path).system_ledger
        self.assertTrue(system_optimizations.save_carried_system_ledger(paths, state_path, "update-1"))

        shutil.rmtree(paths.config_root)
        shutil.copytree(stock_config, paths.config_root, symlinks=True)
        moonraker = manifest.system_optimizations.moonraker_file_manager.file.lstrip("/")
        newer = before[moonraker] + b"\n# QIDI's newer firmware\n"
        (system_root / moonraker).write_bytes(newer)
        # The newer firmware's dhcpcd hook writes /etc/resolv.conf with fallback servers itself.
        hook = system_optimizations.QIDI_DNS_HOOK.lstrip("/")
        hook_bytes = f"#!/bin/sh\necho \"# {system_optimizations.QIDI_DNS_HOOK_MARKER}\" > /etc/resolv.conf\n".encode()
        (system_root / hook).write_bytes(hook_bytes)
        self.assertTrue((system_root / "etc/resolv.conf").is_symlink())
        # Only that update's own install reads what it carried.
        self.assertIsNone(system_optimizations.load_carried_system_ledger(
            paths, manifest=manifest, environ={**env, FIRMWARE_UPDATE_RUN_ENV: "another-update"}, run=host_run))
        run_install(paths, manifest, PlainReporter(io.StringIO()), input_stream=None, urlopen=urlopen,
                    environ={**env, FIRMWARE_UPDATE_RUN_ENV: "update-1"}, run=host_run)
        updated = load_installed_state(state_path).system_ledger
        self.assertEqual(updated["policy"], installed["policy"])
        # Valve Oil gives DNS back to QIDI's hook: /etc/resolv.conf is a file again, with servers in it, and
        # uninstall no longer restores it.
        self.assertEqual(set(updated["restore_preimages"]), set(installed["restore_preimages"]) - {"dns"})
        resolv = system_root / "etc/resolv.conf"
        self.assertFalse(resolv.is_symlink())
        self.assertIn("nameserver ", resolv.read_text(encoding="utf-8"))
        resolvconf = "etc/resolvconf/resolv.conf.d/"
        self.assertEqual({name: (system_root / name).read_bytes() for name in (resolvconf + "head", resolvconf + "tail")},
                         {name: before[name] for name in (resolvconf + "head", resolvconf + "tail")})

        run_uninstall(paths, manifest, compatibility, PlainReporter(io.StringIO()),
                      input_stream=io.StringIO("yes\nyes\nno\n"), urlopen=moonraker_urlopen(), environ=env, run=host_run)
        # The fake host keeps its own notes on units and resolvconf runs under systemd/ and run/.
        after = {name: data for name, data in snapshot_tree(system_root).items()
                 if name in before or not name.startswith(("systemd/", "run/"))}
        self.assertEqual(after, {**before, moonraker: newer, hook: hook_bytes, "etc/resolv.conf": resolv.read_bytes()})

    def test_valve_oils_klipper_installs_beside_qidis_and_uninstall_removes_it(self):
        """Valve Oil's Klipper on a Plus 5 through its life: installed beside QIDI's ~/klipper, which nothing changes,
        with the launcher and the klipper.service drop-in that runs it; replaced by later packages; left as it is where
        QIDI's modules or unit aren't the ones it was made for; removed by uninstall after the drop-in and launcher."""
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        printer_root = stock_printer_data(manifest, "01.01.02.01", homing=b"# QIDI's homing.py\n")
        root = fake_system_root()
        manifest, tree_source = fake_klipper_tree(root, manifest, "01.01.02.01")
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        env[SYSTEM_ROOT_ENV] = str(root)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        host_run = fake_host_run(root)
        tree = root / klipper_tree.TREE_ROOT.lstrip("/")
        qidi = root / "home/qidi/klipper"
        unit = root / klipper_tree.UNIT_FILE.lstrip("/")
        launcher = root / klipper_tree.LAUNCHER_FILE.lstrip("/")
        dropin = root / klipper_tree.DROPIN_FILE.lstrip("/")
        operations = (klipper_tree.OPERATION, klipper_tree.LAUNCHER_OPERATION, klipper_tree.DROPIN_OPERATION)
        qidi_before = snapshot_tree(qidi)

        def apply(ledger, run=host_run, source="yes_install", output=None):
            return apply_system_optimizations(
                paths=paths, spec=manifest.system_optimizations, ledger=ledger,
                reporter=PlainReporter(output if output is not None else io.StringIO()),
                input_stream=None, environ=env, source=source, block_qidi_updates=True, manifest=manifest, run=run,
                urlopen=moonraker_urlopen(),
            )

        reload_failures = []

        def failing_reload(command, **kwargs):
            # The first reload fails, and the rollback's succeeds.
            if list(command)[-2:] == ["systemctl", "daemon-reload"] and not reload_failures:
                reload_failures.append(True)
                return CompletedProcess(command, 1, stdout="", stderr="reload failed")
            return host_run(command, **kwargs)

        def marker():
            return json.loads((tree / klipper_tree.MARKER).read_text(encoding="utf-8"))

        with tree_source:
            source = klipper_tree.TREE_SOURCE
            with self.subTest("a dry run changes nothing"):
                report = io.StringIO()
                system_optimizations.maybe_emit_system_dry_run(
                    paths=paths, manifest=manifest, reporter=PlainReporter(report), prior_state=None,
                    cli_options=SystemOptimizationCliOptions(), environ=env, block_qidi_updates=None, run=host_run,
                )
                self.assertIn(f"would install Valve Oil's Klipper v0.14.0-valve-oil-{manifest.package.version} at "
                              f"{klipper_tree.TREE_ROOT}", report.getvalue())
                self.assertFalse(tree.exists() or launcher.exists() or dropin.exists())

            with self.subTest("install"):
                ledger = apply({"policy": {"system_optimizations": "disabled", "ai_detection": "unset"}})
                # The tree is the source's klippy with the Plus 5's files over it, Valve Oil's modules where QIDI's
                # tree has them, and QIDI's compiled modules; its C helper library is the package's, newer than its
                # sources, so Klipper never rebuilds it.
                self.assertEqual((tree / "klippy/klippy.py").read_text(encoding="utf-8"), "# Valve Oil's klippy.py\n")
                self.assertEqual((tree / "klippy/extras/heaters.py").read_text(encoding="utf-8"), "# Plus 5 heaters\n")
                self.assertFalse((tree / "klippy/mcu.py").exists())
                module = manifest.install.extra_modules[0]
                self.assertEqual((tree / module.destination).read_bytes(), (paths.installer_root / module.source).read_bytes())
                self.assertEqual((tree / "klippy/extras/box_stepper.so").read_bytes(), (qidi / "klippy/extras/box_stepper.so").read_bytes())
                self.assertEqual((tree / klipper_tree.CHELPER).read_bytes(), klipper_tree.CHELPER_PAYLOAD.read_bytes())
                self.assertTrue(klipper_tree.chelper_newest(tree))
                # Moonraker serves config/ and docs/ as config_examples and docs, and warns when they're missing.
                for name in klipper_tree.MOONRAKER_DIRS:
                    self.assertEqual((tree / name / "README.md").read_bytes(), (source / name / "README.md").read_bytes())
                    self.assertIn(f"{name}/README.md", marker()["files"])
                self.assertEqual((tree / "klippy/.version").read_text(encoding="utf-8"),
                                 f"v0.14.0-valve-oil-{manifest.package.version}\n")
                self.assertEqual({key: marker()[key] for key in ("printer", "firmware", "soc")}, {
                    "printer": "plus5", "firmware": "01.01.02.01",
                    "soc": {"version": "01.01.02.01", "file": "QD_PLUS5_SOC_01.01.02.01_Release_NA"}})
                self.assertEqual(snapshot_tree(qidi), qidi_before)
                # Klipper starts through the launcher, which runs QIDI's command until the operator chooses otherwise,
                # so nothing restarts Klipper for it.
                self.assertEqual(launcher.read_bytes(), klipper_tree.LAUNCHER_PAYLOAD.read_bytes())
                self.assertEqual(dropin.read_text(encoding="utf-8"), klipper_tree.DROPIN_CONTENT)
                self.assertEqual([ledger["outcomes"][operation]["status"] for operation in operations], ["applied"] * 3)
                self.assertEqual([operation for operation in ledger["restore_preimages"] if operation in operations],
                                 list(operations))
                installed = marker()
                self.assertEqual(apply(ledger)["restore_preimages"], ledger["restore_preimages"])
                self.assertEqual(marker(), installed)

            with self.subTest("a later package replaces the tree; a failure puts the earlier one back"):
                (source / "klippy/klippy.py").write_text("# a later klippy.py\n", encoding="utf-8")
                dropin.unlink()
                with self.assertRaises(SystemOptimizationApplyError) as failed:
                    apply(ledger, run=failing_reload)
                self.assertEqual(marker(), installed)
                self.assertFalse(dropin.exists())
                self.assertFalse(root.joinpath(klipper_tree.PREVIOUS.lstrip("/")).exists())
                self.assertFalse(root.joinpath(klipper_tree.STAGING.lstrip("/")).exists())
                # A reconcile starts the new tree when it is the operator's choice.
                (paths.printer_data_root / klipper_tree.CHOICE_FILE).write_text('{"use": "valve_oil"}\n', encoding="utf-8")
                restarted = []

                def reconcile_run(command, **kwargs):
                    if list(command)[-3:-1] == ["systemctl", "restart"]:
                        restarted.append(list(command)[-1])
                    return host_run(command, **kwargs)

                ledger = apply(failed.exception.ledger, run=reconcile_run, source="auto_update_reconcile")
                self.assertEqual((tree / "klippy/klippy.py").read_text(encoding="utf-8"), "# a later klippy.py\n")
                self.assertFalse(root.joinpath(klipper_tree.PREVIOUS.lstrip("/")).exists())
                self.assertIn("klipper.service", restarted)
                self.assertEqual(ledger["restore_preimages"][klipper_tree.OPERATION], klipper_tree.PREIMAGE)

            with self.subTest("a power cut while the tree is swapped is finished by the next run"):
                (source / "klippy/klippy.py").write_text("# the newest klippy.py\n", encoding="utf-8")
                installed = marker()
                previous = root / klipper_tree.PREVIOUS.lstrip("/")
                _write_host_journal(paths, operation_id=klipper_tree.OPERATION, transaction_id="cut", phase="apply",
                                    preimage=klipper_tree.UPDATE_PREIMAGE)
                # The cut came between moving the installed tree aside and putting the new one in its place.
                tree.rename(previous)
                staging = root / klipper_tree.STAGING.lstrip("/")
                (staging / "klippy").mkdir(parents=True)
                self.assertTrue(recover_pending_system_optimization(
                    paths=paths, manifest=manifest, reporter=PlainReporter(io.StringIO()), input_stream=None,
                    environ=env, run=host_run, urlopen=moonraker_urlopen()))
                self.assertEqual(marker(), installed)
                self.assertFalse(previous.exists())
                ledger = apply(ledger)
                self.assertFalse(staging.exists())
                self.assertEqual((tree / "klippy/klippy.py").read_text(encoding="utf-8"), "# the newest klippy.py\n")

            with self.subTest("QIDI's modules or unit not the ones it was made for"):
                # A QIDI package changed QIDI's modules, and an older one left compiled modules behind: the tree
                # stays, unused by the launcher, and uninstall still removes it. What differs is left in Klipper's log
                # folder with a prefilled issue for the operator to attach it to; a compiled copy of a module the tree
                # has its own of isn't among it. A later package checked with the new modules replaces the tree, which
                # QIDI's leftovers stay out of, and the report goes.
                installed = marker()
                box_stepper = qidi / "klippy/extras/box_stepper.so"
                box_stepper.write_bytes(b"QIDI's newer box_stepper\n")
                stray = qidi / "klippy/extras/qidi_legacy.so"
                stray.write_bytes(b"an older QIDI module\n")
                own_copy = qidi / "klippy/extras/heaters.cpython-39-aarch64-linux-gnu.so"
                own_copy.write_bytes(b"an older QIDI heaters\n")
                output = io.StringIO()
                ledger = apply(ledger, output=output)
                self.assertEqual(ledger["outcomes"][klipper_tree.OPERATION]["status"], "unsupported")
                self.assertEqual(marker(), installed)
                self.assertIn(klipper_tree.OPERATION, ledger["restore_preimages"])
                report_path = paths.printer_data_root / "logs" / klipper_tree.MODULES_REPORT
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual((report["printer"], report["firmware"], report["missing"]), ("plus5", "01.01.02.01", []))
                self.assertEqual([(entry["path"], entry["sha256"]) for entry in report["changed"]],
                                 [("extras/box_stepper.so", hashlib.sha256(box_stepper.read_bytes()).hexdigest())])
                self.assertEqual([entry["path"] for entry in report["extra"]], ["extras/qidi_legacy.so"])
                self.assertEqual(len(report["modules"]), 4)
                self.assertIn(str(report_path), output.getvalue())
                self.assertIn(report["issue_url"], output.getvalue())
                link = urllib.parse.parse_qs(urllib.parse.urlsplit(report["issue_url"]).query)
                self.assertEqual(link["template"], ["printer-support.yml"])
                self.assertIn("qidi_legacy", link["klipper_modules"][0])
                stray.unlink()
                spec = manifest.system_optimizations
                pins = dict(spec.klipper_tree.modules_for("01.01.02.01"))
                pins["extras/box_stepper.so"] = dataclasses.replace(
                    pins["extras/box_stepper.so"], size=box_stepper.stat().st_size,
                    sha256=__import__("hashlib").sha256(box_stepper.read_bytes()).hexdigest())
                manifest = dataclasses.replace(manifest, system_optimizations=dataclasses.replace(
                    spec, klipper_tree=dataclasses.replace(spec.klipper_tree, qidi_modules=(("01.01.02.01", tuple(sorted(pins.items()))),))))
                ledger = apply(ledger)
                self.assertEqual(ledger["outcomes"][klipper_tree.OPERATION]["status"], "applied")
                self.assertEqual((tree / "klippy/extras/box_stepper.so").read_bytes(), box_stepper.read_bytes())
                self.assertFalse((tree / "klippy/extras/heaters.cpython-39-aarch64-linux-gnu.so").exists())
                self.assertFalse(report_path.exists())
                # A unit QIDI changed leaves the drop-in in place but inactive, and it is active again once the unit is
                # QIDI's again.
                unit_text = unit.read_text(encoding="utf-8")
                unit.write_text(unit_text.replace("$KLIPPER_ARGS", "-u $KLIPPER_ARGS"), encoding="utf-8")
                ledger = apply(ledger)
                self.assertEqual(dropin.read_text(encoding="utf-8"), klipper_tree.DROPIN_INACTIVE)
                unit.write_text(unit_text, encoding="utf-8")
                ledger = apply(ledger)
                self.assertEqual(dropin.read_text(encoding="utf-8"), klipper_tree.DROPIN_CONTENT)

            with self.subTest("a QIDI firmware update carries ownership across QIDI's package"):
                carried = carried_system_ledger(ledger, manifest=manifest, paths=paths, environ=env, run=host_run)
                self.assertEqual({operation: carried["restore_preimages"][operation] for operation in operations},
                                 {operation: ledger["restore_preimages"][operation] for operation in operations})
                # QIDI's package left its units without Valve Oil's drop-ins; the update's install puts them back.
                shutil.rmtree(dropin.parent)
                ledger = apply(carried)
                self.assertEqual(dropin.read_text(encoding="utf-8"), klipper_tree.DROPIN_CONTENT)

            with self.subTest("uninstall removes the drop-in, then the launcher, then the tree"):
                restored = []
                real_journal = system_optimizations._write_host_journal

                def journal(paths_, *, operation_id, **kwargs):
                    if operation_id in operations:
                        restored.append(operation_id)
                    return real_journal(paths_, operation_id=operation_id, **kwargs)

                state = InstalledState(
                    schema_version=1, package_id=manifest.package.id, package_version=manifest.package.version,
                    runtime_firmware="01.01.02.01", backup_label="test", installed_at="2026-11-22T00:00:00Z",
                    managed_tree=ManagedTreeState(root="config/vo-macros", files=()), patch_ledger=(),
                    system_ledger=ledger,
                )
                with mock.patch.object(system_optimizations, "_write_host_journal", journal):
                    restore_system_optimizations(
                        paths=paths, manifest=manifest, state=state, reporter=PlainReporter(io.StringIO()),
                        input_stream=None, environ=env, include_optional=True, run=host_run, urlopen=moonraker_urlopen(),
                    )
                self.assertEqual(restored, list(reversed(operations)))
                self.assertFalse(tree.exists() or launcher.exists() or dropin.exists())
                self.assertEqual(unit.read_text(encoding="utf-8"), unit_text)
                # QIDI's files are as they were, besides the ones this test changed as QIDI's packages would.
                changed = {"klippy/extras/box_stepper.so", "klippy/extras/qidi_legacy.so",
                           "klippy/extras/heaters.cpython-39-aarch64-linux-gnu.so"}
                self.assertEqual({name: data for name, data in snapshot_tree(qidi).items() if name not in changed},
                                 {name: data for name, data in qidi_before.items() if name not in changed})

    def _legacy_host_context(self):
        paths, manifest, env, root = self._installed_host_context()
        state_path = paths.printer_data_root / manifest.state_file
        state = load_installed_state(state_path)
        legacy = copy.deepcopy(state.system_ledger)
        for preimage in legacy["restore_preimages"].values():
            self._make_legacy_preimage(preimage, root)
        legacy.pop("schema_version")
        legacy["actions"] = [
            {"id": operation_id, "status": "applied", "transaction_id": outcome.get("transaction_id"), "desired": outcome.get("desired", {})}
            for operation_id, outcome in legacy.pop("outcomes").items()
        ]
        legacy.pop("committed_transactions")
        write_installed_state(state_path, _replace_system_ledger(state, legacy))
        return paths, manifest, env, root

    def _host_context(self):
        printer_root = copy_base_runtime(homing_patched=True); root = fake_system_root()
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid"); env[SYSTEM_ROOT_ENV] = str(root)
        boot = printer_root / "boot-id"
        boot.write_text("test-boot\n", encoding="utf-8")
        env["VO_BOOT_ID_PATH"] = str(boot)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        return paths, load_manifest(REPO_ROOT / "installer/package.yaml"), env, root

    def _installed_host_context(self):
        paths, manifest, env, root = self._host_context()
        run_install(paths, manifest, PlainReporter(io.StringIO()), input_stream=io.StringIO("yes\nno\nno\n"), urlopen=moonraker_urlopen(saved_variables_path=paths.config_root / "saved_variables.cfg"), environ=env, run=fake_host_run(root))
        return paths, manifest, env, root

    def _make_legacy_preimage(self, preimage, root):
        if not isinstance(preimage, dict): return
        if "sha256" in preimage:
            preimage.pop("sha256")
        if "backup_path" in preimage:
            return
        if "files" in preimage:
            for item in preimage["files"]: self._make_legacy_preimage(item, root)
        if "file" in preimage: self._make_legacy_preimage(preimage["file"], root)
        if preimage.get("destination"):
            source = Path(preimage["backup_dir"])
            destination = root / preimage["destination"].lstrip("/") / ".gif-backup-old"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination)
            preimage["backup_dir"] = str(destination)
            for metadata in preimage.get("files", {}).values(): metadata.pop("sha256", None)


class BootFilesFlowTests(unittest.TestCase):
    """QIDI's Format External Storage unpacks dosfstools over /, replacing merged /usr's /sbin link with a directory;
    the printer then stops at the boot logo on its next start (boot_path.py)."""

    def _install_env(self, root):
        printer_root = copy_base_runtime(homing_patched=True)
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        env[SYSTEM_ROOT_ENV] = str(root)
        boot_id = printer_root / "boot-id"
        boot_id.write_text("boot-one\n", encoding="utf-8")
        env["VO_BOOT_ID_PATH"] = str(boot_id)
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        urlopen = moonraker_urlopen(saved_variables_path=paths.config_root / "saved_variables.cfg")
        return printer_root, env, paths, manifest, urlopen

    def _calls(self, root):
        host_run = fake_host_run(root)
        calls = []

        def run(command, **kwargs):
            calls.append([part for part in command if part not in ("sudo", "-S", "-p", "")])
            return host_run(command, **kwargs)

        return run, calls

    def test_install_repairs_a_sbin_qidis_format_replaced_and_keeps_its_tools(self):
        root = add_merged_usr(fake_system_root(), dosfstools_deb=True)
        # What `dpkg-deb -x dosfstools.deb /` leaves: a real /sbin with only dosfstools in it.
        (root / "sbin").unlink()
        (root / "sbin").mkdir()
        (root / "sbin/mkfs.fat").write_bytes(b"qidi mkfs.fat")
        (root / "sbin/mkfs.fat").chmod(0o755)
        os.symlink("mkfs.fat", root / "sbin/mkdosfs")
        self.assertTrue(boot_path.check(root)[0].repairable)
        printer_root, env, paths, manifest, urlopen = self._install_env(root)
        run, calls = self._calls(root)

        before = snapshot_tree(root)
        report = io.StringIO()
        run_install(paths, manifest, PlainReporter(report), dry_run=True, urlopen=urlopen, environ=env, run=run)
        self.assertEqual(snapshot_tree(root), before)
        self.assertIn("Would repair the files the printer starts from: /sbin is a directory", report.getvalue())

        report = io.StringIO()
        run_install(paths, manifest, PlainReporter(report), urlopen=urlopen, environ=env, run=run)
        self.assertEqual(os.readlink(root / "sbin"), "usr/sbin")
        self.assertEqual(boot_path.check(root), ())
        # The directory's tools moved into /usr/sbin, and dpkg then installs QIDI's package over them so dpkg owns
        # them; QIDI's app finds its tool and doesn't unpack the package again.
        self.assertEqual(os.readlink(root / "usr/sbin/mkdosfs"), "mkfs.fat")
        self.assertTrue(boot_path.format_tool_present(root))
        self.assertTrue(boot_path.dosfstools_registered(root))
        self.assertEqual(len(list(root.glob("sbin.valve-oil-*"))), 1)
        self.assertIn("Repaired /sbin", report.getvalue())
        self.assertEqual([call[:3] for call in calls].count(["dpkg", f"--root={root}", "-i"]), 1)

    def test_install_installs_qidis_dosfstools_only_when_its_format_tool_is_missing(self):
        root = add_merged_usr(fake_system_root(), dosfstools_deb=True)
        printer_root, env, paths, manifest, urlopen = self._install_env(root)
        run, calls = self._calls(root)
        report = io.StringIO()
        run_install(paths, manifest, PlainReporter(report), dry_run=True, urlopen=urlopen, environ=env, run=run)
        self.assertIn(messages.DOSFSTOOLS_WOULD_INSTALL, report.getvalue())
        self.assertFalse(boot_path.format_tool_present(root))

        report = io.StringIO()
        run_install(paths, manifest, PlainReporter(report), urlopen=urlopen, environ=env, run=run)
        installs = [call for call in calls if call[:1] == ["dpkg"]]
        self.assertEqual(len(installs), 1)
        self.assertTrue(boot_path.format_tool_present(root))
        self.assertIn(messages.DOSFSTOOLS_INSTALLED, report.getvalue())

        # Once present, later installs leave it be; uninstall keeps it, since removing it re-arms QIDI's bug.
        run_install(paths, manifest, PlainReporter(io.StringIO()), urlopen=urlopen, environ=env, run=run)
        self.assertEqual(len([call for call in calls if call[:1] == ["dpkg"]]), 1)
        compatibility = load_supported_upgrade_sources(REPO_ROOT / "installer/supported_upgrade_sources.yaml")
        run_uninstall(paths, manifest, compatibility, PlainReporter(io.StringIO()), environ=env, urlopen=urlopen, run=run)
        self.assertTrue(boot_path.format_tool_present(root))
        self.assertEqual(os.readlink(root / "sbin"), "usr/sbin")

    def test_install_refuses_before_any_change_when_the_boot_files_cant_be_repaired(self):
        root = add_merged_usr(fake_system_root(), dosfstools_deb=True)
        (root / "sbin").unlink()
        os.symlink("usr/local/sbin", root / "sbin")
        printer_root, env, paths, manifest, urlopen = self._install_env(root)
        run, calls = self._calls(root)
        before, printer_before = snapshot_tree(root), snapshot_tree(printer_root)
        with self.assertRaises(boot_path.BootPathError) as raised:
            run_install(paths, manifest, PlainReporter(io.StringIO()), urlopen=urlopen, environ=env, run=run)
        self.assertIn(boot_path.RECOVERY_DOC_URL, raised.exception.message)
        self.assertEqual(snapshot_tree(root), before)
        self.assertEqual(snapshot_tree(printer_root), printer_before)
        self.assertFalse((printer_root / manifest.state_file).exists())
