from __future__ import annotations

import io
import json
import os
import unittest
from pathlib import Path

from installer.runtime import earlier_names, valve_oil_status
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.compatibility import load_supported_upgrade_sources
from installer.runtime.manifest import load_manifest
from installer.runtime.reporter import PlainReporter
from installer.runtime.runner import run_install
from installer.runtime.state_file import load_installed_state
from installer.runtime.uninstall import run_uninstall
from installer.tests.helpers import REPO_ROOT, build_env, homing_fixture_bytes, moonraker_urlopen, stock_printer_data

FIRMWARE = "01.01.06.03"
# Modules releases before 26.11.22.2 already installed under vo_ names.
ALREADY_VO = {"vo_renamed_commands.py", "vo_homing_guard.py", "vo_prime_handoff.py"}


class EarlierNamesTests(unittest.TestCase):
    """Printers on releases before 26.11.22.2, whose names were tltg, move to the vo names."""

    def setUp(self) -> None:
        earlier_names.EARLIER_PARENT = False
        self.addCleanup(setattr, earlier_names, "EARLIER_PARENT", False)
        self.manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
        self.root = stock_printer_data(self.manifest, FIRMWARE, homing=homing_fixture_bytes(FIRMWARE))
        self.paths = resolve_runtime_paths(
            bundle_root=REPO_ROOT,
            environ=build_env(self.root, moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats"),
        )

    def install(self) -> None:
        run_install(self.paths, self.manifest, PlainReporter(io.StringIO()),
                    urlopen=moonraker_urlopen(saved_variables_path=self.paths.config_root / "saved_variables.cfg"))

    def age(self) -> None:
        """Leave the printer as an earlier release did: the same install under the tltg names."""
        root = self.root
        for earlier, current, _ in earlier_names.PRINTER_DATA_MOVES:
            if (root / current).exists() and current != earlier_names.MACROS:
                os.replace(root / current, root / earlier)
        state = root / "config/tltg_optimized_state.yaml"
        state.write_text(
            state.read_text(encoding="utf-8").replace("config/vo-macros", "config/tltg-optimized-macros")
            .replace("id: valve-oil\n", "id: qidi-max4-optimized\n"),
            encoding="utf-8",
        )
        os.replace(root / earlier_names.MACROS, root / earlier_names.EARLIER_MACROS)
        printer_cfg = root / "config/printer.cfg"
        printer_cfg.write_text(
            printer_cfg.read_text(encoding="utf-8").replace(earlier_names.INCLUDE, earlier_names.EARLIER_INCLUDE),
            encoding="utf-8",
        )
        extras = self.paths.managed_klipper_root / "klippy/extras"
        for module in extras.glob("vo_*.py"):
            if module.name not in ALREADY_VO:
                module.rename(extras / ("tltg_" + module.name[len("vo_"):]))
        (root / ".tltg_box485_salt").write_text("salt\n", encoding="utf-8")
        (root / "..tltg_optimized_host_reboot_required.5kooz5tg").write_text("", encoding="utf-8")
        (root / ".tltg-optimized-macros-before-optimize-01.01.07.01-26.11.18.2-20261120T161421Z.eyajtevj.zip").write_bytes(b"")
        (root / "logs").mkdir(exist_ok=True)
        (root / "logs/tltg_box_firmware_box2.json").write_text("{}\n", encoding="utf-8")
        (root / "config/tltg_bed_soak").mkdir(exist_ok=True)
        (root / "config/tltg_bed_soak/20261110-195948-bed100-chamber50.jsonl").write_text("{}\n", encoding="utf-8")

    def names(self) -> list[str]:
        return sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*") if "tltg" in path.name)

    def test_an_update_moves_every_earlier_name_and_uninstall_leaves_none(self) -> None:
        self.install()
        self.age()
        self.assertTrue(earlier_names.migrate_when_free(self.paths))
        self.assertEqual(self.names(), [name for name in self.names() if name.startswith("klipper/")])
        printer_cfg = (self.root / "config/printer.cfg").read_text(encoding="utf-8")
        self.assertEqual(printer_cfg.count(earlier_names.INCLUDE), 1)
        self.assertNotIn(earlier_names.EARLIER_INCLUDE, printer_cfg)
        self.assertEqual(load_installed_state(self.root / "config/vo_state.yaml").managed_tree.root, "config/vo-macros")
        self.assertEqual((self.root / ".vo_box485_salt").read_text(encoding="utf-8"), "salt\n")
        self.assertTrue((self.root / "logs/vo_box_firmware_box2.json").is_file())
        self.assertTrue((self.root / "config/vo_bed_soak/20261110-195948-bed100-chamber50.jsonl").is_file())
        # Nothing left to move: the next run leaves printer_data alone.
        self.assertFalse(earlier_names.migrate_when_free(self.paths))

        self.install()
        self.assertEqual(self.names(), [])
        self.assertEqual(load_installed_state(self.root / "config/vo_state.yaml").package_id, "valve-oil")

        self.age()
        earlier_names.migrate_when_free(self.paths)
        compatibility = load_supported_upgrade_sources(REPO_ROOT / "installer/supported_upgrade_sources.yaml")
        run_uninstall(self.paths, self.manifest, compatibility, PlainReporter(io.StringIO()), input_stream=io.StringIO("Y\n"),
                      urlopen=moonraker_urlopen(saved_variables_path=self.paths.config_root / "saved_variables.cfg"))
        self.assertEqual([name for name in self.names() if not name.startswith(("config/vo_bed_soak", "logs/"))], [])
        self.assertFalse((self.root / earlier_names.MACROS).exists())
        self.assertNotIn("vo-macros", (self.root / "config/printer.cfg").read_text(encoding="utf-8"))

    def test_an_earlier_release_update_check_keeps_the_files_it_writes_after_the_install(self) -> None:
        # The earlier update check holds only the earlier lock and started its run under the earlier names.
        env = earlier_names.adopt_earlier_environment({"TLTG_OPTIMIZED_INSTALLER_LOCK_HELD": "1", "TLTG_FIRMWARE_UPDATE_KIND": "automatic"})
        self.assertTrue(earlier_names.EARLIER_PARENT)
        self.assertEqual((env["VO_INSTALLER_LOCK_HELD"], env["VO_FIRMWARE_UPDATE_KIND"]), ("1", "automatic"))
        run = valve_oil_status.begin_run(self.paths, kind="automatic", step="install")
        os.replace(self.root / ".vo_status.json", self.root / ".tltg_valve_oil_status.json")
        (self.root / "config/tltg_optimized_auto_update_state.json").write_text('{"latest_checksum": "1"}\n', encoding="utf-8")

        earlier_names.migrate_when_free(self.paths)
        self.assertTrue((self.root / ".tltg_valve_oil_status.json").is_file())
        earlier_names.finish_earlier_parent_run(self.paths, succeeded=True)
        finished = valve_oil_status.load_status(self.paths).run
        self.assertEqual((finished.id, finished.state), (run.id, valve_oil_status.SUCCEEDED))

        # Then the earlier update check writes its result under the earlier names, and the next run takes it.
        (self.root / "config/tltg_optimized_auto_update_state.json").write_text('{"latest_checksum": "2"}\n', encoding="utf-8")
        earlier_names.EARLIER_PARENT = False
        earlier_names.migrate_when_free(self.paths)
        self.assertEqual(self.names(), [])
        self.assertEqual(json.loads((self.root / "config/vo_auto_update_state.json").read_text())["latest_checksum"], "2")
        self.assertEqual(valve_oil_status.load_status(self.paths).run.state, valve_oil_status.SUCCEEDED)

    def test_a_firmware_update_an_earlier_release_started_is_left_to_it(self) -> None:
        (self.root / ".tltg_firmware_update_pending.json").write_text("{}\n", encoding="utf-8")
        (self.root / ".tltg_optimized_auto_update_enrolled").write_text("1\n", encoding="utf-8")
        with self.assertRaises(earlier_names.EarlierNamesError):
            earlier_names.migrate_when_free(self.paths)
        self.assertTrue((self.root / ".tltg_optimized_auto_update_enrolled").exists())

    def test_earlier_host_files_go_only_when_they_are_the_package_s(self) -> None:
        host = self.root.parent / "host"
        unit = host / "etc/systemd/system/tltg-status.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("# tltg-optimized:status-unit\n[Unit]\n", encoding="utf-8")
        foreign = host / "usr/local/lib/tltg_status.py"
        foreign.parent.mkdir(parents=True)
        foreign.write_text("print('someone else')\n", encoding="utf-8")
        fluidd = host / "home/qidi/tltg-fluidd"
        fluidd.mkdir(parents=True)
        (fluidd / ".tltg-optimized-fluidd").write_text("v1\n", encoding="utf-8")
        served = host / "home/qidi/tltg-valve-oil-ui"
        served.mkdir(parents=True)
        (served / ".tltg-optimized-valve-oil-ui").write_text("v1\n", encoding="utf-8")

        removed = earlier_names.retire_earlier_host_files(
            root=host, sudo_password=None, folders=True, keep=lambda path: path == "/home/qidi/tltg-valve-oil-ui",
        )
        self.assertEqual(sorted(removed), ["/etc/systemd/system/tltg-status.service", "/home/qidi/tltg-fluidd"])
        self.assertTrue(foreign.exists())
        self.assertTrue(served.exists())


if __name__ == "__main__":
    unittest.main()
