"""The launcher klipper.service runs at every Klipper start (installer/system/vo_klipper_launch.py): it starts Valve
Oil's Klipper only when the operator chose it and the tree the installer built still fits the printer, and QIDI's
command unchanged otherwise, on any error included. The tree here is built by the installer's own staging, so these
cases also hold the installer and the launcher to the same marker."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from installer.runtime import klipper_tree
from installer.runtime.manifest import load_manifest
from installer.tests.helpers import REPO_ROOT, fake_klipper_tree, load_source, temp_path

LAUNCHER = load_source("vo_klipper_launch", klipper_tree.LAUNCHER_PAYLOAD)
FIRMWARE = "01.01.07.01"
SOC = {"version": FIRMWARE, "file": "QD_MAX4_SOC_01.01.07.01_Release_NA"}
QIDI_ARGS = ["/home/qidi/klipper/klippy/klippy.py", "/home/qidi/printer_data/config/printer.cfg",
             "-I", "/home/qidi/printer_data/comms/klippy.serial", "-l", "/home/qidi/printer_data/logs/klippy.log",
             "-a", "/home/qidi/printer_data/comms/klippy.sock"]


class _Started(BaseException):
    """What a successful exec does: the launcher never returns."""


class LauncherTests(unittest.TestCase):
    def setUp(self):
        root = temp_path("vo-klipper-launcher-")
        manifest, tree_source = fake_klipper_tree(root, load_manifest(REPO_ROOT / "installer/package.yaml"), FIRMWARE,
                                                  unit=False)
        with tree_source:
            tree = klipper_tree.plan(
                source=klipper_tree.TREE_SOURCE, printer="max4", firmware=FIRMWARE, package_version="test",
                extra_modules=(), qidi_modules=manifest.system_optimizations.klipper_tree.modules_for(FIRMWARE),
            )
            klipper_tree.stage(tree, root / "home/qidi/vo-klipper", qidi_klippy=root / "home/qidi/klipper/klippy", soc=SOC)
        self.root = root
        self.data = root / "home/qidi/printer_data"
        self.data.mkdir(parents=True)
        self.tree = root / "home/qidi/vo-klipper"
        self.qidi = root / "home/qidi/klipper/klippy"
        self.paths = {
            "qidi_workdir": str(root / "home/qidi/klipper"),
            "qidi_klippy": str(self.qidi),
            "tree_root": str(self.tree),
            "printer_data": str(self.data),
            "firmware_manifest": str(root / "firmware_manifest.json"),
            "dropin": str(root / "vo-klipper.conf"),
        }
        (root / "firmware_manifest.json").write_text(json.dumps({"SOC": SOC}), encoding="utf-8")
        (root / "vo-klipper.conf").write_text(klipper_tree.DROPIN_CONTENT, encoding="utf-8")
        self.choose("valve_oil")

    def choose(self, use):
        (self.data / klipper_tree.CHOICE_FILE).write_text(json.dumps({"use": use}), encoding="utf-8")

    def start(self, argv=QIDI_ARGS, paths=None, execve=None):
        """One Klipper start: what was executed, with what arguments, environment, and folder."""
        started = {}

        def execv(program, args):
            started.update(program=program, args=args, env=None, cwd=chdir.call_args_list[-1].args[0])
            raise _Started

        def default_execve(program, args, env):
            started.update(program=program, args=args, env=env, cwd=chdir.call_args_list[-1].args[0])
            raise _Started

        with mock.patch.object(LAUNCHER.os, "chdir") as chdir, self.assertRaises(_Started):
            LAUNCHER.main(list(argv), paths={**self.paths, **(paths or {})}, execv=execv, execve=execve or default_execve)
        return started

    def status(self):
        return json.loads((self.data / klipper_tree.STATUS_FILE).read_text(encoding="utf-8"))

    def check(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            LAUNCHER.main(["--check"], paths=self.paths)
        return json.loads(out.getvalue())

    def test_each_reason_to_run_qidis_klipper(self):
        def touch_later(path):
            os.utime(path, ns=(path.stat().st_mtime_ns + 5_000_000_000,) * 2)

        def replace_same_size(path):
            path.write_bytes(bytes(byte ^ 1 for byte in path.read_bytes()))

        cases = {
            # name: (change, selected, would_run, code, available)
            "chosen, and everything fits": (lambda: None, "valve_oil", "valve_oil", "selected", True),
            "no choice made": (lambda: (self.data / klipper_tree.CHOICE_FILE).unlink(), "qidi", "qidi", "not_selected", True),
            "QIDI's Klipper chosen": (lambda: self.choose("qidi"), "qidi", "qidi", "not_selected", True),
            "a choice that can't be read": (lambda: (self.data / klipper_tree.CHOICE_FILE).write_text("{", encoding="utf-8"),
                                            "qidi", "qidi", "choice_unreadable", True),
            "a QIDI firmware update pending": (lambda: (self.data / ".tltg_firmware_update_pending.json").write_text("{}"),
                                               "valve_oil", "qidi", "firmware_update_pending", False),
            "no tree": (lambda: shutil.rmtree(self.tree), "valve_oil", "qidi", "not_installed", False),
            "a tree without its marker": (lambda: (self.tree / klipper_tree.MARKER).unlink(), "valve_oil", "qidi",
                                          "not_installed", False),
            "other firmware": (lambda: Path(self.paths["firmware_manifest"]).write_text(
                json.dumps({"SOC": {**SOC, "version": "01.01.07.02"}}), encoding="utf-8"), "valve_oil", "qidi",
                "firmware_changed", False),
            "no firmware manifest": (lambda: Path(self.paths["firmware_manifest"]).unlink(), "valve_oil", "qidi",
                                     "firmware_changed", False),
            "a QIDI module changed": (lambda: replace_same_size(self.qidi / "extras/box_stepper.so"), "valve_oil", "qidi",
                                      "qidi_modules_changed", False),
            "a QIDI module added": (lambda: (self.qidi / "extras/new.so").write_bytes(b"new"), "valve_oil", "qidi",
                                    "qidi_modules_changed", False),
            "a tree file changed": (lambda: replace_same_size(self.tree / "klippy/klippy.py"), "valve_oil", "qidi",
                                    "tree_changed", False),
            "a copied QIDI module removed": (lambda: (self.tree / "klippy/extras/box_stepper.so").unlink(), "valve_oil",
                                             "qidi", "tree_changed", False),
            "a tree file touched, its content the same": (lambda: touch_later(self.tree / "klippy/klippy.py"),
                                                          "valve_oil", "valve_oil", "selected", True),
            # Klipper would try to rebuild the library, and the printers have no compiler.
            "a C helper source newer than the library": (lambda: touch_later(self.tree / "klippy/chelper/serialqueue.c"),
                                                         "valve_oil", "qidi", "tree_changed", False),
            "no drop-in": (lambda: Path(self.paths["dropin"]).unlink(), "valve_oil", "qidi", "launcher_not_installed", False),
            "an inactive drop-in": (lambda: Path(self.paths["dropin"]).write_text(klipper_tree.DROPIN_INACTIVE),
                                    "valve_oil", "qidi", "launcher_not_installed", False),
        }
        for name, (change, selected, would_run, code, available) in cases.items():
            with self.subTest(name):
                self.setUp()
                change()
                checked = self.check()
                self.assertEqual((checked["selected"], checked["would_run"], checked["code"], checked["available"]),
                                 (selected, would_run, code, available))
                self.assertEqual(checked["reason"], LAUNCHER.REASONS[code])
                self.assertEqual(checked["unavailable_reason"] is None, available)
                # --check starts and writes nothing.
                self.assertFalse((self.data / klipper_tree.STATUS_FILE).exists())
                started = self.start()
                self.assertEqual(self.status()["running"], would_run)
                self.assertEqual(self.status()["reason"], LAUNCHER.REASONS[code])
                if would_run == "qidi":
                    self.assertEqual(started, {"program": "/home/qidi/klippy-env/bin/python",
                                               "args": ["/home/qidi/klippy-env/bin/python", *QIDI_ARGS], "env": None,
                                               "cwd": self.paths["qidi_workdir"]})
                    self.assertIsNone(self.status()["launch_id"])

    def test_valve_oils_klipper_starts_from_its_tree_with_qidis_arguments(self):
        started = self.start()
        status = self.status()
        self.assertEqual(started["args"], ["/home/qidi/klippy-env/bin/python", str(self.tree / "klippy/klippy.py"),
                                           *QIDI_ARGS[1:]])
        self.assertEqual(started["cwd"], str(self.tree))
        self.assertEqual(started["env"][LAUNCHER.LAUNCH_ENV], status["launch_id"])
        self.assertEqual((status["selected"], status["running"], status["attempts"], status["firmware"], status["version"]),
                         ("valve_oil", "valve_oil", 1, FIRMWARE, "v0.14.0-valve-oil-test"))
        # A command that doesn't name QIDI's klippy.py exactly once isn't changed.
        started = self.start(argv=QIDI_ARGS[1:])
        self.assertEqual(started["args"], ["/home/qidi/klippy-env/bin/python", *QIDI_ARGS[1:]])
        self.assertEqual(self.status()["code"], "arguments_unrecognized")

    def test_three_starts_without_ready_fall_back_until_chosen_again(self):
        for attempt in (1, 2, 3):
            self.start()
            self.assertEqual((self.status()["running"], self.status()["attempts"]), ("valve_oil", attempt))
        for _ in range(2):
            started = self.start()
            self.assertEqual(started["args"][1], QIDI_ARGS[0])
            self.assertEqual((self.status()["running"], self.status()["code"]), ("qidi", "not_ready"))
            self.assertEqual(self.check()["available"], True)
        # Choosing Valve Oil's Klipper again counts afresh; a start Klipper acknowledged doesn't count.
        os.utime(self.data / klipper_tree.CHOICE_FILE, ns=(1, 1))
        for _ in range(4):
            self.start()
            self.assertEqual((self.status()["running"], self.status()["attempts"]), ("valve_oil", 1))
            (self.data / klipper_tree.READY_FILE).write_text(json.dumps({"launch_id": self.status()["launch_id"]}))

    def test_any_error_starts_qidis_command_unchanged(self):
        expected = ["/home/qidi/klippy-env/bin/python", *QIDI_ARGS]
        # The launcher's own checks fail.
        started = self.start(paths={"tree_root": None})
        self.assertEqual((started["args"], started["env"], self.status()["code"]), (expected, None, "error"))
        # Valve Oil's Klipper can't be executed.

        def execve(program, args, env):
            raise OSError("exec format error")

        started = self.start(execve=execve)
        self.assertEqual((started["args"], started["env"], self.status()["code"]), (expected, None, "error"))
        # The status can't be written: Valve Oil's Klipper starts anyway.
        (self.data / klipper_tree.STATUS_FILE).unlink()
        (self.data / klipper_tree.STATUS_FILE).mkdir()
        started = self.start()
        self.assertEqual(started["args"][1], str(self.tree / "klippy/klippy.py"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            LAUNCHER.main(["--check"], paths={**self.paths, "tree_root": None})
        self.assertEqual(json.loads(out.getvalue())["would_run"], "qidi")


if __name__ == "__main__":
    unittest.main()
