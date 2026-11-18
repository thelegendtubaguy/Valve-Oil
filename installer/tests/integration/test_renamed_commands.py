from __future__ import annotations

import importlib.util
import re
import types
import unittest

from installer.tests.helpers import REPO_ROOT

spec = importlib.util.spec_from_file_location(
    "vo_renamed_commands", REPO_ROOT / "installer/klipper/extras/renamed_commands.py"
)
renamed_commands = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renamed_commands)

SLICER_ROOTS = (REPO_ROOT / "orcaslicer_gcode", REPO_ROOT / "qidistudio_gcode")


def installed_commands() -> set[str]:
    """Commands Valve Oil's macros define and its Klipper modules register, on any printer."""
    klipper = REPO_ROOT / "installer/klipper"
    names = set()
    for path in (*klipper.glob("macros/*.cfg"), *klipper.glob("printers/*/*.cfg")):
        names.update(m.upper() for m in re.findall(r"^\[gcode_macro ([^\]]+)\]", path.read_text(), re.M))
    for path in klipper.glob("extras/*.py"):
        names.update(re.findall(r"register_command\(\s*\"([A-Z0-9_]+)\"", path.read_text()))
    return names


class Gcode:
    def __init__(self, commands):
        self.ready_gcode_handlers = dict.fromkeys(commands, lambda gcmd: None)
        self.scripts = []

    def register_command(self, name, func, desc=None):
        self.ready_gcode_handlers[name] = func

    def run_script_from_command(self, script):
        self.scripts.append(script)


class Gcmd:
    def __init__(self, raw):
        self.raw = raw
        self.responses = []

    def get_raw_command_parameters(self):
        return self.raw

    def respond_info(self, message):
        self.responses.append(message)


class RenamedCommandsTest(unittest.TestCase):
    def test_each_earlier_name_runs_an_installed_command_and_none_is_still_defined(self) -> None:
        commands = installed_commands()
        slicer = "\n".join(p.read_text() for root in SLICER_ROOTS for p in root.rglob("*") if p.is_file())
        for old, new in renamed_commands.RENAMED.items():
            with self.subTest(old=old):
                self.assertIn(new, commands)
                self.assertNotIn(old, commands)
                # Names the slicer packs call keep their names instead.
                self.assertNotRegex(slicer, rf"\b{old}\b")

    def test_earlier_name_runs_the_new_command_and_leaves_a_name_in_use_alone(self) -> None:
        in_use = lambda gcmd: None
        gcode = Gcode(["VO_BED_SOAK", "VO_WAIT_BED", "VO_BOX_MOTION"])
        gcode.ready_gcode_handlers["OPTIMIZED_WAIT_BED"] = in_use
        events = {}
        printer = types.SimpleNamespace(
            lookup_object=lambda name: gcode,
            register_event_handler=lambda event, handler: events.setdefault(event, handler),
        )
        renamed_commands.RenamedCommands(types.SimpleNamespace(get_printer=lambda: printer))
        events["klippy:connect"]()

        gcmd = Gcmd(" BED=60 CHAMBER=40")
        gcode.ready_gcode_handlers["TLTG_BED_SOAK"](gcmd)
        gcode.ready_gcode_handlers["TLTG_BOX_MOTION"](Gcmd(""))
        self.assertEqual(gcode.scripts, ["VO_BED_SOAK BED=60 CHAMBER=40", "VO_BOX_MOTION"])
        self.assertEqual(gcmd.responses, ["TLTG_BED_SOAK is now VO_BED_SOAK."])
        self.assertIs(gcode.ready_gcode_handlers["OPTIMIZED_WAIT_BED"], in_use)
        # A command this printer doesn't have gets no earlier name.
        self.assertNotIn("TLTG_STEP_LOSS_CORRECT", gcode.ready_gcode_handlers)


if __name__ == "__main__":
    unittest.main()
