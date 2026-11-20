from __future__ import annotations

import ast
import configparser
import io
import logging
import os
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from installer.tests.klipper_fakes import CommandError, Reactor, load_extra


save_variables_fast = load_extra("save_variables_fast")

INITIAL = (
    "[Variables]\n"
    "box_count = 2\n"
    "box_drying_roll_enable = 1\n"
    "enable_box = 1\n"
    "last_load_slot = 'slot3'\n"
    "retry_step = None\n"
    "runout_0 = 0\n"
    "slot3 = 2\n"
    "vo_spoolman_spools = {'slot0': 1, 'slot1': 2}\n"
    "\n"
)


class Gcode:
    error = CommandError


class SaveVariables:
    """QIDI's save_variables: loadVariables as shipped."""

    default_values = {"box_count": 0, "enable_box": 0}

    def __init__(self, filename):
        self.filename = filename
        self.loadVariables()

    def loadVariables(self):
        varfile = configparser.ConfigParser()
        varfile.read(self.filename)
        self.allVariables = {
            name: ast.literal_eval(val) for name, val in varfile.items("Variables")}
        for key, default in self.default_values.items():
            setattr(self, key, self.allVariables.get(key, default))


class BoxExtras:
    """QIDI's box_extras/box2base save_variable, as recovered from the
    compiled modules."""

    def __init__(self, save_variables):
        self.save_variables = save_variables
        self.gcode = Gcode()
        self.saves = 0

    def save_variable(self, varname, value):
        self.saves += 1
        sv = self.save_variables
        newvars = dict(sv.allVariables)
        newvars[varname] = value
        varfile = configparser.ConfigParser()
        varfile.add_section("Variables")
        for name, val in sorted(newvars.items()):
            varfile.set("Variables", name, repr(val))
        try:
            with open(sv.filename, "w") as f:
                varfile.write(f)
        except Exception:
            raise self.gcode.error("Unable to save variable %s as %s" % (varname, value))
        sv.loadVariables()


class Printer:
    def __init__(self, objects):
        self.objects = objects

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)


def state(save_vars):
    return (
        Path(save_vars.filename).read_text(encoding="utf-8"),
        [(name, type(value), repr(value)) for name, value in save_vars.allVariables.items()],
        {name: repr(getattr(save_vars, name)) for name in save_vars.default_values},
    )


class BoxSaveTest(unittest.TestCase):
    """QIDI's Box modules save variables without SAVE_VARIABLE; the faster
    save must leave the same file and values."""

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: [p.unlink() for p in root.iterdir()] and root.rmdir())
        self.root = root

    def printer(self, name):
        path = self.root / name
        path.write_text(INITIAL, encoding="utf-8")
        save_vars = SaveVariables(str(path))
        box = BoxExtras(save_vars)
        fast = object.__new__(save_variables_fast.SaveVariablesFast)
        fast.printer = Printer({"box_extras": box})
        fast.save_vars = save_vars
        fast.gcode = Gcode()
        return save_vars, box, fast

    def test_saves_match_qidi(self) -> None:
        qidi_vars, qidi_box, _ = self.printer("qidi.cfg")
        fast_vars, fast_box, fast = self.printer("fast.cfg")
        fast._patch_vendor_saves()
        fast._patch_vendor_saves()

        saves = [
            ("last_load_slot", "slot1"),
            ("last_load_slot", "slot1"),
            # QIDI writes the name as given; the file keeps the saved
            # lowercase value and moves it to where the name sorts.
            ("BOX_DRYING_ROLL_ENABLE", 0),
            ("BOX_DRYING_ROLL_ENABLE", 0),
            ("New_Key", 5),
            ("runout_0", 0),
            ("slot3", -2),
            ("box_count", 3),
            ("retry_step", "EXTRUDER_LOAD1_slot3"),
            ("diff_mv", 0.07),
            ("nested", {"a": [1, (2, 3)], 4: None}),
            ("quote", "it's \"q\"\nnext"),
        ]
        for varname, value in saves:
            with self.subTest(save=(varname, value)):
                qidi_box.save_variable(varname, value)
                fast_box.save_variable(varname, value)
                self.assertEqual(state(fast_vars), state(qidi_vars))
        self.assertEqual(fast_box.saves, 0)

    def test_unchanged_save_leaves_the_file_alone(self) -> None:
        save_vars, box, fast = self.printer("fast.cfg")
        fast._patch_vendor_saves()
        for unchanged in ((("runout_0", 0), ("enable_box", 1), ("retry_step", None)),
                          (("BOX_DRYING_ROLL_ENABLE", 0), ("BOX_DRYING_ROLL_ENABLE", 0))):
            box.save_variable(*unchanged[0])
            os.utime(save_vars.filename, ns=(0, 0))
            for varname, value in unchanged:
                box.save_variable(varname, value)
            self.assertEqual(os.stat(save_vars.filename).st_mtime_ns, 0)

    def test_values_the_file_cant_hold_exactly_go_to_qidi(self) -> None:
        for value in ("50%", {1, 2}, float("nan"), b"x"):
            with self.subTest(value=value):
                save_vars, box, fast = self.printer("fast.cfg")
                fast._patch_vendor_saves()
                try:
                    box.save_variable("odd", value)
                except Exception:
                    pass
                self.assertEqual(box.saves, 1)

    def test_other_save_variables_go_to_qidi(self) -> None:
        save_vars, box, fast = self.printer("fast.cfg")
        fast._patch_vendor_saves()
        box.save_variables = SaveVariables(save_vars.filename)

        box.save_variable("slot3", 1)

        self.assertEqual(box.saves, 1)


class SaveVariableCommandTest(unittest.TestCase):
    def test_matches_qidi_handler(self) -> None:
        root = Path(tempfile.mkdtemp())
        path = root / "saved_variables.cfg"
        path.write_text(INITIAL, encoding="utf-8")
        self.addCleanup(lambda: [p.unlink() for p in root.iterdir()] and root.rmdir())
        save_vars = SaveVariables(str(path))
        expected = Path(root / "expected.cfg")
        expected.write_text(INITIAL, encoding="utf-8")
        qidi_vars = SaveVariables(str(expected))
        fast = object.__new__(save_variables_fast.SaveVariablesFast)
        fast.save_vars = save_vars
        fast.original = None

        class Command:
            error = CommandError

            def __init__(self, params):
                self.params = params

            def get(self, name):
                return self.params[name]

        for name, value in (("last_load_slot", "'slot5'"), ("z_offset", "0.05"), ("enable_box", "1")):
            fast.cmd_SAVE_VARIABLE(Command({"VARIABLE": name, "VALUE": value}))
            BoxExtras(qidi_vars).save_variable(name, ast.literal_eval(value))
            self.assertEqual(state(save_vars), state(qidi_vars))


class EarlierNamesTest(unittest.TestCase):
    """Settings saved under tltg_ names before 26.11.18.2 carry over to their vo_ names."""

    def test_copies_each_tltg_variable_to_its_unsaved_vo_name(self) -> None:
        root = Path(tempfile.mkdtemp())
        path = root / "saved_variables.cfg"
        path.write_text(
            "[Variables]\n"
            "enable_box = 1\n"
            "tltg_box_load_fast_speed_1 = 90\n"
            "tltg_keep_loaded_between_prints = 0\n"
            "tltg_spoolman_spools = {'slot0': 4}\n"
            "tltg_start_bed_mesh_profile = 'default'\n"
            "tltg_staggered_start_heating_dwell_seconds = 12.5\n"
            "vo_keep_loaded_between_prints = 1\n"
            "\n",
            encoding="utf-8",
        )
        self.addCleanup(lambda: [p.unlink() for p in root.iterdir()] and root.rmdir())
        save_vars = SaveVariables(str(path))
        fast = object.__new__(save_variables_fast.SaveVariablesFast)
        fast.save_vars = save_vars

        fast._copy_earlier_names()

        reread = SaveVariables(str(path)).allVariables
        self.assertEqual(reread, save_vars.allVariables)
        self.assertEqual(reread["vo_box_load_fast_speed_1"], 90)
        self.assertEqual(reread["vo_spoolman_spools"], {"slot0": 4})
        self.assertEqual(reread["vo_start_bed_mesh_profile"], "default")
        self.assertEqual(reread["vo_staggered_start_heating_dwell_seconds"], 12.5)
        # A vo_ setting already saved is the operator's; the tltg_ ones stay for earlier releases.
        self.assertEqual(reread["vo_keep_loaded_between_prints"], 1)
        self.assertEqual(reread["tltg_keep_loaded_between_prints"], 0)
        written = path.read_text(encoding="utf-8")
        fast._copy_earlier_names()
        self.assertEqual(path.read_text(encoding="utf-8"), written)


AUTOSAVE_HEADER = (
    "\n"
    "#*# <---------------------- SAVE_CONFIG ---------------------->\n"
    "#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.\n"
    "#*#\n"
)
OPERATORS_CONFIG = "[printer]\nkinematics = corexy\n\n[probe]\npin = PB1\n"
PRINTER_CFG = OPERATORS_CONFIG + AUTOSAVE_HEADER + "#*# [probe]\n#*# z_offset = 0.100\n"
PENDING = "[probe]\nz_offset = 0.250\n"


class ConfigError(Exception):
    """Klipper's configfile.error."""


def parse_config(data, folder):
    """Klipper's config parse, which reads every [include] file again each time."""
    fileconfig = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(";", "#"))
    try:
        fileconfig.read_string(data)
        for section in fileconfig.sections():
            if section.startswith("include "):
                fileconfig.read_string((folder / section.split()[1]).read_text(encoding="utf-8"))
    except configparser.Error as e:
        raise ConfigError(str(e))
    return types.SimpleNamespace(fileconfig=fileconfig)


def split_autosave(data):
    """Klipper's reading of printer.cfg: the operator's part, and the SAVE_CONFIG block without its line prefixes.
    A block with a line missing its prefix is ignored."""
    regular, found, block = data.partition(AUTOSAVE_HEADER)
    lines = block.strip().split("\n") if found else []
    if any(line != "#*#" and not line.startswith("#*# ") for line in lines):
        return data, ""
    return regular, "\n".join(line[4:] for line in lines)


class PrinterConfig:
    """Klipper's configfile as SAVE_CONFIG uses it. It notes each thread that reads or parses the config, and a
    held read keeps a background save in progress until the test lets it go."""

    def __init__(self, pending, folder):
        self.autosave = parse_config(pending, folder)
        self.klipper_thread = threading.current_thread()
        self.threads = set()
        self.reading = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def _read_config_file(self, filename):
        self.threads.add(threading.current_thread())
        self.reading.set()
        if threading.current_thread() is not self.klipper_thread:
            self.release.wait(5)
        return Path(filename).read_text(encoding="utf-8")

    def _find_autosave_data(self, data):
        return split_autosave(data)

    def _build_config_wrapper(self, data, filename):
        self.threads.add(threading.current_thread())
        return parse_config(data, Path(filename).parent)

    def _strip_duplicates(self, data, config):
        lines, section = data.split("\n"), None
        for number, line in enumerate(lines):
            if line.startswith("["):
                section = line[1:-1].strip()
            elif config.fileconfig.has_option(section, line.split("=")[0].strip()):
                lines[number] = "#" + line
        return "\n".join(lines)

    def _build_config_string(self, config):
        text = io.StringIO()
        config.fileconfig.write(text)
        return text.getvalue().strip()


class Klipper:
    """Klipper's config, printer, and G-code objects as background_save_config uses them, with QIDI's SAVE_CONFIG
    and SAVE_CONFIG_QD registered and a probe offset waiting to be saved."""

    def __init__(self, module, cfg):
        self.cfg = cfg
        self.reactor = Reactor()
        self.configfile = PrinterConfig(PENDING, cfg.parent)
        self.console = []
        self.saving = lambda: False
        self.qidi_saves_during_a_background_save = []
        self.commands = {"SAVE_CONFIG": self.qidi_save, "SAVE_CONFIG_QD": self.qidi_save}
        module.load_config(self)

    def get_printer(self):
        return self

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return {"config_file": str(self.cfg)}

    def lookup_object(self, name):
        return {"gcode": self, "configfile": self.configfile}[name]

    def register_command(self, name, handler, desc=None):
        previous = self.commands.pop(name, None)
        if handler is not None:
            self.commands[name] = handler
        return previous

    def respond_raw(self, message):
        self.console.append((threading.current_thread(), message))

    def qidi_save(self, gcmd):
        # QIDI renames printer.cfg to a backup and the new file into its place, with no fsync.
        self.qidi_saves_during_a_background_save.append(self.saving())
        new = self.cfg.with_name("printer_new.cfg")
        new.write_text(self.cfg.read_text(encoding="utf-8") + "#*# saved by QIDI\n", encoding="utf-8")
        os.replace(self.cfg, self.cfg.with_name("printer-qidi.cfg"))
        os.replace(new, self.cfg)

    def run(self, command):
        self.commands[command](types.SimpleNamespace(respond_raw=self.respond_raw))

    def finish_saves(self):
        self.configfile.release.set()
        for thread in self.configfile.threads - {threading.current_thread()}:
            thread.join(5)


class BackgroundSaveConfigTest(unittest.TestCase):
    """VO_SAVE_CONFIG_QD writes printer.cfg off Klipper's main thread, and no other save writes it meanwhile."""

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.cfg = self.folder / "printer.cfg"
        self.cfg.write_text(PRINTER_CFG, encoding="utf-8")
        configfile = types.ModuleType("configfile")
        configfile.AUTOSAVE_HEADER, configfile.error = AUTOSAVE_HEADER, ConfigError
        self.module = load_extra("background_save_config", stand_ins={"configfile": configfile})

    def klipper(self):
        klipper = Klipper(self.module, self.cfg)
        self.addCleanup(klipper.finish_saves)
        return klipper

    def start_held_save(self, klipper):
        """Start a background save and hold it where it first reads printer.cfg."""
        klipper.configfile.release.clear()
        klipper.run("VO_SAVE_CONFIG_QD")
        self.assertTrue(klipper.configfile.reading.wait(5))
        self.assertNotIn(threading.current_thread(), klipper.configfile.threads)
        (thread,) = klipper.configfile.threads
        return thread

    def files(self):
        return {path.name: path.read_text(encoding="utf-8") for path in self.folder.iterdir()}

    def test_pending_values_are_saved_without_reading_or_writing_on_klippers_thread(self) -> None:
        klipper = self.klipper()
        thread = self.start_held_save(klipper)
        # The command has returned while the save still reads the config, so Klipper's main thread isn't held.
        self.assertTrue(thread.is_alive())
        self.assertEqual(self.files(), {"printer.cfg": PRINTER_CFG})

        klipper.finish_saves()

        self.assertNotIn(threading.current_thread(), klipper.configfile.threads)
        files = self.files()
        regular, saved = split_autosave(files.pop("printer.cfg"))
        self.assertEqual(regular.rstrip(), OPERATORS_CONFIG.rstrip())
        values = parse_config(saved, self.folder).fileconfig
        self.assertEqual({name: dict(values[name]) for name in values.sections()}, {"probe": {"z_offset": "0.250"}})
        # The file a restart read until now stays as the backup, and nothing half-written is left beside it.
        self.assertEqual(list(files.values()), [PRINTER_CFG])
        self.assertEqual(klipper.console, [])

    def test_save_that_conflicts_with_an_included_file_leaves_printer_cfg_alone_and_says_so(self) -> None:
        # A save comments out the value it replaces in printer.cfg, but can't in a file printer.cfg includes.
        (self.folder / "probe.cfg").write_text("[probe]\nz_offset = 1.0\n", encoding="utf-8")
        self.cfg.write_text("[include probe.cfg]\n" + PRINTER_CFG, encoding="utf-8")
        before = self.files()
        klipper = self.klipper()

        klipper.run("VO_SAVE_CONFIG_QD")
        klipper.finish_saves()

        self.assertEqual(self.files(), before)
        # The save's thread hands its report to Klipper's own thread, which is the one that writes the console.
        self.assertEqual(klipper.console, [])
        for callback in klipper.reactor.callbacks:
            callback(klipper.reactor.now)
        self.assertEqual([thread for thread, _message in klipper.console], [threading.current_thread()])

    def test_every_other_config_save_waits_for_a_background_save_to_finish(self) -> None:
        for command in ("SAVE_CONFIG", "SAVE_CONFIG_QD", "VO_SAVE_CONFIG_QD"):
            # A Klipper RESTART builds new objects while a save started before it may still be writing.
            for restarted in (False, True):
                with self.subTest(command, after_restart=restarted):
                    klipper = self.klipper()
                    thread = self.start_held_save(klipper)
                    later = self.klipper() if restarted else klipper
                    later.saving = thread.is_alive
                    # A waiting command pauses, which is when Klipper's other work, and the save, get to run.
                    later.reactor.on_pause = lambda waketime: klipper.finish_saves()

                    later.run(command)
                    still_saving = thread.is_alive()
                    klipper.finish_saves()
                    later.finish_saves()

                    self.assertFalse(still_saving)
                    self.assertEqual(later.qidi_saves_during_a_background_save,
                                     [] if command == "VO_SAVE_CONFIG_QD" else [False])

    def test_printer_cfg_qidi_saves_is_flushed_to_storage_before_the_command_ends(self) -> None:
        # QIDI renames the new printer.cfg into place unflushed; a power cut soon after leaves it empty.
        for command in ("SAVE_CONFIG", "SAVE_CONFIG_QD"):
            with self.subTest(command):
                klipper = self.klipper()
                flushed = []
                with mock.patch.object(os, "fsync", side_effect=lambda fd: flushed.append(os.fstat(fd).st_ino)):
                    klipper.run(command)
                self.assertEqual(klipper.qidi_saves_during_a_background_save, [False])
                self.assertIn(self.cfg.stat().st_ino, flushed)
                self.assertIn(self.folder.stat().st_ino, flushed)


if __name__ == "__main__":
    unittest.main()
