"""A power cut, or the SSH session closing, can stop an install or uninstall anywhere. The next run must always be
able to finish: the printer is never left where every install and uninstall refuses to go on.

Each case stops a run in a child process with os._exit at a chosen moment, with no rollback, cleanup, or finally
block, as a power cut does, then runs what the operator would run next and checks the printer ends as QIDI's.
"""
from __future__ import annotations

import builtins
import io
import os
import unittest
from pathlib import Path

from installer.runtime import klipper_cfg
from installer.runtime.cli import resolve_runtime_paths
from installer.runtime.compatibility import load_supported_upgrade_sources
from installer.runtime.errors import UnfinishedInstallError
from installer.runtime.legacy_manual_install import INSTALL_STARTED_FILE
from installer.runtime.manifest import load_manifest
from installer.runtime.reporter import PlainReporter
from installer.runtime.runner import run_install
from installer.runtime.system_optimizations import SYSTEM_JOURNAL, SYSTEM_ROOT_ENV, recover_pending_system_optimization
from installer.runtime.uninstall import run_uninstall
from installer.tests.helpers import (
    REPO_ROOT,
    build_env,
    copy_base_runtime,
    fake_host_run,
    fake_system_root,
    moonraker_urlopen,
)

MANIFEST = load_manifest(REPO_ROOT / "installer/package.yaml")
COMPATIBILITY = load_supported_upgrade_sources(REPO_ROOT / "installer/supported_upgrade_sources.yaml")
SENSOR = "filament_switch_sensor filament_switch_sensor"


def _written(name: str):
    """The moment just after a file called `name` is in place."""
    return lambda path, landed: landed and Path(path).name == name


def _about_to_write(name: str):
    """The moment just before a file called `name` is put in place or removed."""
    return lambda path, landed: not landed and Path(path).name == name


class PowerCutRecoveryTests(unittest.TestCase):
    def _printer(self):
        printer_root = copy_base_runtime()
        root = fake_system_root(include_moonraker_cache=True, include_fluidd_bundle=True, include_filament_config=True,
                                include_klipper_box_config=True, include_klipper_print_stats_manager=True,
                                include_klipper_chelper=True, include_nginx_site=True, include_moonraker_asvc=True,
                                include_moonraker_metadata=True, include_qidiclient=True)
        env = build_env(printer_root, moonraker_url="http://moonraker.invalid")
        env[SYSTEM_ROOT_ENV] = str(root)
        (printer_root / "boot-id").write_text("boot\n", encoding="utf-8")
        env["TLTG_OPTIMIZED_BOOT_ID_PATH"] = str(printer_root / "boot-id")
        paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=env)
        return paths, env, root

    def _urlopen(self, paths):
        return moonraker_urlopen(saved_variables_path=paths.config_root / "saved_variables.cfg")

    def _recover(self, paths, env, root):
        # What the CLI does before every install and uninstall.
        recover_pending_system_optimization(paths=paths, manifest=MANIFEST, reporter=PlainReporter(io.StringIO()),
                                            input_stream=io.StringIO(), environ=env, run=fake_host_run(root),
                                            urlopen=self._urlopen(paths))

    def _install(self, paths, env, root):
        self._recover(paths, env, root)
        run_install(paths, MANIFEST, PlainReporter(io.StringIO()), input_stream=io.StringIO("yes\nno\nno\n"),
                    urlopen=self._urlopen(paths), environ=env, run=fake_host_run(root))

    def _uninstall(self, paths, env, root):
        self._recover(paths, env, root)
        run_uninstall(paths, MANIFEST, COMPATIBILITY, PlainReporter(io.StringIO()), input_stream=io.StringIO("Y\nY\n"),
                      urlopen=self._urlopen(paths), environ=env, run=fake_host_run(root))

    def _cut(self, run, moment) -> None:
        """Run `run` in a child stopped dead at `moment`; fail unless the moment came."""
        pid = os.fork()
        if pid == 0:
            try:
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, 1)
                os.dup2(devnull, 2)
                _stop_at(moment)
                run()
            finally:
                os._exit(3)  # the run ended, or failed, before the moment
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 77, "the run never reached the moment")

    def _assert_stock(self, paths, stock_homing: bytes) -> None:
        text = (paths.config_root / "printer.cfg").read_text(encoding="utf-8")
        self.assertNotIn("[include tltg-optimized-macros/*.cfg]", text)
        self.assertTrue(klipper_cfg.has_section(text, SENSOR))
        self.assertEqual((paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes(), stock_homing)
        self.assertFalse((paths.printer_data_root / MANIFEST.state_file).exists())
        self.assertFalse((paths.printer_data_root / SYSTEM_JOURNAL).exists())
        self.assertFalse((paths.printer_data_root / INSTALL_STARTED_FILE).exists())

    def test_the_next_install_finishes_an_install_stopped_anywhere(self):
        moments = {
            "before anything is changed": _written(INSTALL_STARTED_FILE),
            "after the Klipper restart was recorded": _written(".tltg_optimized_klipper_restart_required"),
            "after printer.cfg was changed": _written("printer.cfg"),
            "before the state was written": _about_to_write(Path(MANIFEST.state_file).name),
            "during the system changes": _written(SYSTEM_JOURNAL),
        }
        for name, moment in moments.items():
            with self.subTest(name):
                paths, env, root = self._printer()
                stock_homing = (paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes()
                self._cut(lambda: self._install(paths, env, root), moment)
                if name not in ("before anything is changed", "during the system changes"):
                    # Uninstall can't undo a new install that has no state yet, and says to finish it first.
                    with self.assertRaises(UnfinishedInstallError):
                        self._uninstall(paths, env, root)
                self._install(paths, env, root)
                self._uninstall(paths, env, root)
                self._assert_stock(paths, stock_homing)

    def test_the_next_uninstall_finishes_an_uninstall_stopped_anywhere(self):
        moments = {
            "after the macros were removed": lambda path, landed: landed and Path(path).name == "tltg-optimized-macros",
            "during the system restore": _written(SYSTEM_JOURNAL),
            "before the state was removed": _about_to_write(Path(MANIFEST.state_file).name),
        }
        for name, moment in moments.items():
            with self.subTest(name):
                paths, env, root = self._printer()
                stock_homing = (paths.managed_klipper_root / "klippy/extras/homing.py").read_bytes()
                self._install(paths, env, root)
                self._cut(lambda: self._uninstall(paths, env, root), moment)
                self._uninstall(paths, env, root)
                self._assert_stock(paths, stock_homing)


def _stop_at(moment) -> None:
    """Make every file change check `moment` before and after it lands, and stop the process when it says so."""
    def check(path, landed):
        if moment(os.fspath(path), landed):
            os._exit(77)

    def around(function, target_of):
        def wrapped(*args, **kwargs):
            target = target_of(*args)
            check(target, False)
            result = function(*args, **kwargs)
            check(target, True)
            return result
        return wrapped

    for name, target_of in (("replace", lambda src, dst, *_: dst), ("rename", lambda src, dst, *_: dst),
                            ("unlink", lambda path, *_: path), ("remove", lambda path, *_: path),
                            ("rmdir", lambda path, *_: path)):
        setattr(os, name, around(getattr(os, name), target_of))
    real_open = builtins.open

    def opener(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and any(flag in mode for flag in "wax+"):
            check(file, False)
        return real_open(file, mode, *args, **kwargs)

    builtins.open = opener
    io.open = opener


if __name__ == "__main__":
    unittest.main()
