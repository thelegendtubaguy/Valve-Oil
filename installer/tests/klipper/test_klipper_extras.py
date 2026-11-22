"""Valve Oil's smaller Klipper modules, each with the printer objects it reads faked: what macros are told about the
printer, the warnings it raises, and the guards on the exhaust fan, speed limits, pauses, and the chamber light."""
from __future__ import annotations

import json
import time
import types
import unittest

from installer.tests.helpers import temp_path
from installer.tests.klipper_fakes import load_extra

macro_status = load_extra("macro_status")


class _Status:
    def __init__(self, status=None, **attrs):
        self.status = status or {}
        self.calls = 0
        self.__dict__.update(attrs)

    def get_status(self, eventtime):
        self.calls += 1
        return self.status


class MacroStatusTest(unittest.TestCase):
    """The facts macros read must match what they read before from the
    printer objects, for each way a Box connects."""

    def status(self, objects, commands=(), startup=None):
        class Printer:
            def lookup_object(self, name, default=None):
                return objects.get(name, default)

        status = object.__new__(macro_status.MacroStatus)
        status.printer = Printer()
        status.gcode = _Status(status_commands={name: {} for name in commands})
        status.max_accel = 20000.
        status.startup_shaper_freq = startup or {}
        status.status_failed = set()
        return status.get_status(1.)

    def test_box_available_for_each_box_transport(self) -> None:
        def controller(count):
            return _Status({"box_count": count, "box": {"loaded_slot": "slot2"}})

        cases = [
            ("USB Box", {"box_extras": _Status()}, (), True),
            ("RS-485 Box", {"box485": _Status({"box_count": 1}), "multi_color_controller": controller(0)},
             ("T_MANAGER",), True),
            ("controller Box", {"box485": _Status({"box_count": 0}), "multi_color_controller": controller(2)},
             ("T_MANAGER",), True),
            ("counted Box without QIDI's tool change", {"box485": _Status({"box_count": 1})}, (), False),
            ("no Box", {"box485": _Status({"box_count": 0}), "multi_color_controller": controller(0)},
             ("T_MANAGER",), False),
            ("unreadable count", {"box485": _Status({"box_count": "x"})}, ("T_MANAGER",), False),
        ]
        for label, objects, commands, expected in cases:
            with self.subTest(label):
                status = self.status(objects, commands)
                self.assertIs(status["box_available"], expected)
                self.assertIs(status["vendor_flow"], "T_MANAGER" in commands)

    def test_loaded_slot_matches_multi_color_controller(self) -> None:
        box_extras = _Status()
        save_variables = _Status(allVariables={"last_load_slot": "slot5"})
        usb = _Status({"box": {"loaded_slot": "slot5"}}, _initialized=True,
                      box_adapter=_Status(box1base=box_extras))
        objects = {"box_extras": box_extras, "save_variables": save_variables, "multi_color_controller": usb}
        self.assertEqual(self.status(objects)["loaded_slot"], "slot5")
        self.assertEqual(usb.calls, 0)

        save_variables.allVariables = {}
        self.assertEqual(self.status(objects)["loaded_slot"], "slot16")

        rs485 = _Status({"box": {"loaded_slot": "slot9"}}, _initialized=True, box_adapter=_Status())
        self.assertEqual(self.status({"multi_color_controller": rs485})["loaded_slot"], "slot9")
        self.assertEqual(self.status({})["loaded_slot"], "")
        self.assertEqual(self.status({})["max_accel"], 20000.)

    def test_input_shaper_reports_live_and_saved_calibration(self) -> None:
        class FileConfig:
            def __init__(self, options):
                self.options = options

            def has_option(self, section, option):
                return section == "input_shaper" and option in self.options

            def get(self, section, option):
                return self.options[option]

        def objects(freq_x, freq_y, autosave=None):
            shapers = [_Status(axis=axis, params=_Status(shaper_freq=f)) for axis, f in (("x", freq_x), ("y", freq_y))]
            return {"input_shaper": _Status(get_shapers=lambda: shapers),
                    "configfile": _Status(autosave=_Status(fileconfig=FileConfig(autosave or {})))}

        self.assertEqual(self.status({})["input_shaper"], {"loaded": False, "unshaped_axes": "xy", "saved": False})
        self.assertEqual(self.status(objects(0., 0.))["input_shaper"]["unshaped_axes"], "xy")
        self.assertEqual(self.status(objects(51.8, 0.))["input_shaper"]["unshaped_axes"], "y")
        # QIDI's stock values applied with nothing saved.
        stock = self.status(objects(54.4, 36.2))["input_shaper"]
        self.assertEqual((stock["unshaped_axes"], stock["saved"]), ("", False))
        # A measured calibration that equals QIDI's stock values is still saved.
        measured = objects(54.4, 36.2, {"shaper_freq_x": "54.4", "shaper_freq_y": "36.2"})
        self.assertIs(self.status(measured)["input_shaper"]["saved"], True)
        self.assertIs(self.status(objects(51.8, 41.6), startup={"x": 51.8, "y": 41.6})["input_shaper"]["saved"], True)
        self.assertIs(self.status(objects(51.8, 41.6, {"shaper_freq_y": "0"}), startup={"x": 51.8, "y": 41.6})["input_shaper"]["saved"], False)


printer_warnings = load_extra("printer_warnings")


class PrinterWarningsTest(unittest.TestCase):
    """Fluidd's banner and the console show one error while an axis has no
    input shaper or no saved calibration, and none once both have one, and a
    warning for each Box the installer left a firmware report for."""

    def warnings(self, shaper, unshaped_accel=10000, log_dir=None, saved=None):
        objects = {
            "tltg_macro_status": _Status(max_accel=30000., input_shaper=lambda: shaper,
                                         saved_shaper_freq=lambda axis: (saved or {}).get(axis, 0.)),
            "gcode_macro _vo_globals": _Status(variables={"unshaped_accel": unshaped_accel}),
        }
        handlers = {}

        class Printer:
            def get_printer(self):
                return self

            def get_start_args(self):
                return {"log_file": str(log_dir / "klippy.log")} if log_dir else {}

            def register_event_handler(self, event, handler):
                handlers[event] = handler

            def lookup_object(self, name, default=None):
                return objects.get(name, default)

        warnings = printer_warnings.PrinterWarnings(Printer())
        handlers["klippy:ready"]()
        return warnings.get_status(1.)["warnings"]

    def test_unsupported_box_firmware_reports(self) -> None:
        calibrated = {"loaded": True, "unshaped_axes": "", "saved": True}
        log_dir = temp_path("printer-warnings-")
        for name in ("klippy.log", "tltg_box_firmware_box2.json", "vo_rfid_1.json"):
            (log_dir / name).write_text("{}")
        box, = self.warnings(calibrated, log_dir=log_dir)
        self.assertEqual((box["id"], box["level"]), ("box_firmware_unsupported_box2", "warning"))
        self.assertIn("tltg_box_firmware_box2.json", box["message"])
        (log_dir / "tltg_box_firmware_box2.json").unlink()
        self.assertEqual(self.warnings(calibrated, log_dir=log_dir), [])

    def test_unsupported_thr_and_mainboard_reports_carry_their_issue_links(self) -> None:
        calibrated = {"loaded": True, "unshaped_axes": "", "saved": True}
        log_dir = temp_path("printer-warnings-")
        link = "https://github.com/thelegendtubaguy/valve-oil/issues/new?template=printer-support.yml&printer=QIDI%20Max%204"
        (log_dir / "tltg_thr_firmware.json").write_text(json.dumps({"issue_url": link, "note": "Update it."}))
        (log_dir / "tltg_mainboard_firmware.json").write_text("{}")
        mainboard, thr = self.warnings(calibrated, log_dir=log_dir)
        self.assertEqual((thr["id"], thr["url"]), ("thr_firmware_unsupported", link))
        self.assertTrue(thr["message"].endswith(" Update it."))
        self.assertEqual(mainboard["id"], "mainboard_firmware_unsupported")
        self.assertNotIn("url", mainboard)
        # Only a link to this project's issue form, within GitHub's length, and a short note get through.
        for url, note in (("https://example.invalid/thr", "x" * 301), ("javascript:alert(1)", ["list"]),
                          (link + "x" * 7000, "line\nbreak"), (link + " onclick", None)):
            with self.subTest(url=url[:40]):
                (log_dir / "tltg_thr_firmware.json").write_text(json.dumps({"issue_url": url, "note": note}))
                _, thr = self.warnings(calibrated, log_dir=log_dir)
                self.assertNotIn("url", thr)
                self.assertTrue(thr["message"].endswith("from Fluidd's Logs."))

    def test_calibration_saved_before_a_thr_flash_asks_for_a_new_one(self) -> None:
        calibrated = {"loaded": True, "unshaped_axes": "", "saved": True}
        log_dir = temp_path("printer-warnings-")
        (log_dir / "tltg_input_shaper_thr_flash.json").write_text('{"shaper_freq_x": "54.4", "shaper_freq_y": "36.2"}')
        stale, = self.warnings(calibrated, log_dir=log_dir, saved={"x": 54.4, "y": 36.2})
        self.assertEqual(stale["id"], "input_shaper_after_thr_firmware")
        self.assertEqual(self.warnings(calibrated, log_dir=log_dir, saved={"x": 51.8, "y": 36.2}), [])

    def test_pending_work_is_listed_while_klipper_runs(self) -> None:
        from dataclasses import replace

        from installer.runtime import valve_oil_status as status_file

        data = temp_path("printer-warnings-data-")
        paths = types.SimpleNamespace(printer_data_root=data)
        globals_macro = _Status(variables={"package_version": "26.11.11.3"})

        class Printer:
            def get_printer(self):
                return self

            def get_start_args(self):
                return {"config_file": str(data / "config/printer.cfg")}

            def register_event_handler(self, event, handler):
                pass

            def lookup_object(self, name, default=None):
                return globals_macro if name == "gcode_macro _vo_globals" else default

        warnings = printer_warnings.PrinterWarnings(Printer())
        clock = [100.]

        def pending():
            clock[0] += printer_warnings.PENDING_POLL
            return [w for w in warnings.get_status(clock[0])["warnings"] if w["id"].startswith("valve_oil_")]

        def check(*items, enrolled=False):
            status_file.record_check(paths, enrolled=enrolled, printer=status_file.PrinterInfo(),
                                     installed_release="26.11.11.3", pending=items)

        self.assertEqual(pending(), [])
        # Automatic updates off: the installer has to be run.
        check(status_file.PendingItem(kind="release", version="26.12.1.1"),
              status_file.PendingItem(kind="board", board="thr"), status_file.PendingItem(kind="board", board="box2"))
        release, boards = pending()
        self.assertEqual((release["id"], release["level"]), ("valve_oil_release_waiting", "warning"))
        self.assertIn("26.12.1.1", release["title"])
        self.assertIn("run the Valve Oil installer", release["message"])
        self.assertIn("the THR and Box 2", boards["title"])
        # Automatic updates on: why it hasn't run, read again without a restart.
        (data / ".tltg_optimized_auto_update_enrolled").write_text("1\n")
        check(status_file.PendingItem(kind="firmware", version="01.01.07.01", blocked_by=(status_file.Blocker.of("printing"),)),
              # The release Klipper already runs is no longer waiting, nor is an older one the last check listed
              # before an install went past it.
              status_file.PendingItem(kind="release", version="26.11.11.3"),
              status_file.PendingItem(kind="release", version="26.11.9.1"), enrolled=True)
        firmware, = pending()
        self.assertEqual(firmware["id"], "valve_oil_firmware_waiting")
        self.assertIn(status_file.BLOCKERS["printing"], firmware["message"])
        # The release Klipper runs, found with its installed state missing or drifted, has to be installed again.
        check(status_file.PendingItem(kind="release", version="26.11.11.3", reinstall=True), enrolled=True)
        reinstall, = pending()
        self.assertEqual((reinstall["id"], reinstall["title"]),
                         ("valve_oil_release_waiting", "Valve Oil needs to be installed again."))
        # Waiting only for the update window, as the check words it, it says when it will install.
        window = status_file.Blocker.of(
            "outside_window", status_file.BLOCKERS["outside_window"] + ", 02:00–05:00 America/Chicago")
        check(status_file.PendingItem(kind="release", version="26.12.1.1", blocked_by=(window,)), enrolled=True)
        release, = pending()
        self.assertIn("during the update window, 02:00–05:00 America/Chicago,", release["message"])
        check(status_file.PendingItem(kind="release", version="26.12.1.1",
                                      blocked_by=(window, status_file.Blocker.of("printing"))), enrolled=True)
        self.assertIn(status_file.BLOCKERS["printing"], pending()[0]["message"])
        check(status_file.PendingItem(kind="firmware", version="01.01.07.01", blocked_by=(status_file.Blocker.of("printing"),)),
              enrolled=True)
        # A run whose process is gone was interrupted.
        status_file.begin_run(paths, kind="automatic", step="soc", step_label="Installing QIDI's firmware package")
        status_file.update_status(paths, lambda current: replace(current, run=replace(current.run, pid=2 ** 22 + 1)))
        interrupted, _ = pending()
        self.assertEqual(interrupted["id"], "valve_oil_update_interrupted")
        self.assertTrue(interrupted["message"].endswith(printer_warnings.FINISH_IT))
        # A firmware update that failed partway and waits for the operator says where and what to run, and that Update
        # now continues it too unless a board's rescue has to run first.
        failed = status_file.begin_run(paths, kind="automatic", step="soc", step_label="Installing QIDI firmware",
                                       firmware_update="fw-1")
        status_file.finish_run(paths, failed.id, state=status_file.FAILED, message="dpkg failed. Run ./install.sh again.")
        (data / ".tltg_firmware_update_pending.json").write_text('{"schema": 1, "id": "fw-1", "state": "failed"}')
        alert = pending()[0]
        self.assertEqual(alert["id"], "valve_oil_firmware_update_failed")
        self.assertEqual(alert["message"], "It stopped at: Installing QIDI firmware. dpkg failed. Run ./install.sh again. "
                         + printer_warnings.CONTINUE_IT)
        (data / ".tltg_mainboard_firmware_pending.json").write_text("{}")
        self.assertNotIn(printer_warnings.CONTINUE_IT, pending()[0]["message"])
        for name in (".tltg_firmware_update_pending.json", ".tltg_mainboard_firmware_pending.json"):
            (data / name).unlink()
        # A corrupt or missing file lists nothing.
        status_file.status_path(paths).write_text('{"schema": 1, "pending": [')
        self.assertEqual(pending(), [])
        status_file.remove_status(paths)
        self.assertEqual(pending(), [])

    def test_input_shaper_states(self) -> None:
        self.assertEqual(self.warnings({"loaded": True, "unshaped_axes": "", "saved": True}), [])

        stock, = self.warnings({"loaded": True, "unshaped_axes": "", "saved": False})
        self.assertEqual((stock["id"], stock["level"]), ("input_shaper_uncalibrated", "error"))
        self.assertIn("10000 mm/s²", stock["message"])

        for shaper in ({"loaded": True, "unshaped_axes": "y", "saved": False},
                       {"loaded": True, "unshaped_axes": "x", "saved": True},
                       {"loaded": False, "unshaped_axes": "xy", "saved": False}):
            off, = self.warnings(shaper)
            self.assertEqual((off["id"], off["level"]), ("input_shaper_off", "error"))
        capped, = self.warnings({"loaded": True, "unshaped_axes": "", "saved": False}, unshaped_accel=40000)
        self.assertIn("30000 mm/s²", capped["message"])


chamber_exhaust_guard = load_extra("chamber_exhaust_guard")


class _ChamberCheck:
    """QIDI's verify_heater check for the chamber (printer.cfg's limits)."""

    def __init__(self, temp, target):
        self.hysteresis, self.max_error = 5., 300.
        self.heating_gain, self.check_gain_time = 1., 480.
        self.approaching_target = self.starting_approach = False
        self.last_target = target if temp >= target - self.hysteresis else 0.
        self.goal_temp = self.error = 0.
        self.goal_systime = 9999999999999999.
        self.faulted = False

    def check_event(self, eventtime, temp, target):
        if temp >= target - self.hysteresis or target <= 0.:
            self.approaching_target = self.starting_approach = False
            if temp <= target + self.hysteresis:
                self.error = 0.
            self.last_target = target
            return
        self.error += (target - self.hysteresis) - temp
        if not self.approaching_target:
            if target != self.last_target:
                self.approaching_target = self.starting_approach = True
                self.goal_temp = temp + self.heating_gain
                self.goal_systime = eventtime + self.check_gain_time
            elif self.error >= self.max_error:
                self.faulted = True
        elif temp >= self.goal_temp:
            self.starting_approach = False
            self.error = 0.
            self.goal_temp = temp + self.heating_gain
            self.goal_systime = eventtime + self.check_gain_time
        elif eventtime >= self.goal_systime:
            self.approaching_target = False
        elif self.starting_approach:
            self.goal_temp = min(self.goal_temp, temp + self.heating_gain)
        self.last_target = target


class ChamberExhaustGuardTest(unittest.TestCase):
    """The exhaust fan is turned off before Klipper's chamber heater check
    stops the printer, and stays off for the rest of the print."""

    def run_print(self, temp, target, exhaust_rate, seconds=1800, guard=True, state="printing"):
        sim = types.SimpleNamespace(now=0., temp=temp, fan=0.6, pending=[], messages=[], scripts=[],
                                    state=state)
        check = _ChamberCheck(temp, target)
        callbacks = []

        class Reactor:
            NOW, NEVER = 0., 9999999999999999.

            def register_callback(self, callback):
                callbacks.append(callback)

            def monotonic(self):
                return sim.now

        class GCode:
            def run_script(self, script):
                sim.scripts.append(script)
                # QIDI's fan applies the speed once queued moves reach it.
                sim.pending.append((sim.now + 2., 0.))

            def respond_info(self, message):
                sim.messages.append(message)

        guard_obj = object.__new__(chamber_exhaust_guard.ChamberExhaustGuard)
        guard_obj.printer = types.SimpleNamespace(is_shutdown=lambda: False)
        guard_obj.reactor = Reactor()
        guard_obj.gcode = GCode()
        guard_obj.check = check
        guard_obj.heater = types.SimpleNamespace(get_temp=lambda e: (sim.temp, target))
        guard_obj.fan = types.SimpleNamespace(get_status=lambda e: {"speed": sim.fan})
        guard_obj.print_stats = types.SimpleNamespace(get_status=lambda e: {"state": sim.state})
        guard_obj.stopped = guard_obj.stopping = False
        guard_obj.peak = guard_obj.peak_target = None

        for second in range(seconds):
            sim.now = float(second)
            for due, speed in [p for p in sim.pending if p[0] <= sim.now]:
                sim.fan = speed
                sim.pending.remove((due, speed))
            # The exhaust pulls the chamber down; the heater alone recovers it.
            sim.temp = min(target, sim.temp + (exhaust_rate if sim.fan > 0 else 0.05))
            check.check_event(sim.now, sim.temp, target)
            if check.faulted:
                break
            if guard:
                guard_obj._sample(sim.now)
                while callbacks:
                    callbacks.pop(0)(sim.now)
        return sim, check, guard_obj

    def test_holding_chamber_turns_the_exhaust_off_before_the_check_fails(self) -> None:
        _, unguarded, _ = self.run_print(60., 60., -0.1, guard=False)
        self.assertTrue(unguarded.faulted)

        sim, check, guard = self.run_print(60., 60., -0.1)
        self.assertFalse(check.faulted)
        self.assertEqual((sim.fan, sim.scripts), (0., ["SET_FAN_SPEED FAN=chamber_circulation_fan SPEED=0"]))
        self.assertEqual(len(sim.messages), 1)
        self.assertIn("for the rest of the print", sim.messages[0])
        self.assertIs(guard.get_status()["stopped"], True)

        sim.state = "complete"
        guard._sample(sim.now + 1.)
        self.assertIs(guard.get_status()["stopped"], False)

    def test_stalled_heating_turns_the_exhaust_off_before_the_check_fails(self) -> None:
        _, unguarded, _ = self.run_print(50., 60., 0.0, guard=False)
        self.assertTrue(unguarded.faulted)

        sim, check, _ = self.run_print(50., 60., 0.0)
        self.assertFalse(check.faulted)
        self.assertEqual(sim.fan, 0.)

    def test_chamber_keeping_up_leaves_the_exhaust_on(self) -> None:
        for temp, rate in ((50., 0.01), (60., 0.)):
            sim, check, _ = self.run_print(temp, 60., rate)
            self.assertFalse(check.faulted)
            self.assertEqual((sim.fan, sim.messages), (0.6, []))

    def test_outside_a_print_the_exhaust_is_turned_off_but_not_held_off(self) -> None:
        sim, check, guard = self.run_print(60., 60., -0.1, state="standby")
        self.assertFalse(check.faulted)
        self.assertEqual(sim.fan, 0.)
        self.assertNotIn("rest of the print", sim.messages[0])
        self.assertIs(guard.get_status()["stopped"], False)


velocity_limit = load_extra("velocity_limit")


class VelocityLimitTest(unittest.TestCase):
    """SET_VELOCITY_LIMIT, and QIDI's M203 through it, can't raise the speed
    limit above printer.cfg's max_velocity."""

    def run_command(self, params):
        class Toolhead:
            max_velocity = 565.

            def cmd_SET_VELOCITY_LIMIT(self, gcmd):
                velocity = gcmd.get_float("VELOCITY", None)
                if velocity is not None:
                    self.max_velocity = velocity

        class Gcmd:
            def __init__(self, params):
                self.params = dict(params)
                self.messages = []

            def get_float(self, name, default=None, above=None):
                return float(self.params[name]) if name in self.params else default

            def get_command_parameters(self):
                return self.params

            def respond_info(self, message):
                self.messages.append(message)

        toolhead = Toolhead()
        # Klipper registers its own handler, which re-reads the command line.
        commands = {"SET_VELOCITY_LIMIT": toolhead.cmd_SET_VELOCITY_LIMIT}

        class Gcode:
            gcode_help = {}

            def create_gcode_command(self, command, commandline, params):
                return Gcmd(params)

            def register_command(self, name, handler, desc=None):
                old = commands.pop(name, None)
                if handler is not None:
                    commands[name] = handler
                return old

        class Printer:
            handlers = {}

            def lookup_object(self, name, default=None):
                return {"gcode": gcode, "toolhead": toolhead}.get(name, default)

            def register_event_handler(self, event, handler):
                self.handlers[event] = handler

        class Section:
            def getfloat(self, name, above=None):
                return 565.

        gcode, printer = Gcode(), Printer()

        class Config:
            def get_printer(self):
                return printer

            def getsection(self, name):
                return Section()

        velocity_limit.load_config(Config())
        printer.handlers["klippy:connect"]()
        gcmd = Gcmd(params)
        commands["SET_VELOCITY_LIMIT"](gcmd)
        return toolhead.max_velocity, gcmd.messages

    def test_velocity_above_max_velocity_is_lowered(self) -> None:
        velocity, (message,) = self.run_command({"VELOCITY": "800"})
        self.assertEqual(velocity, 565.)
        self.assertIn("using 565 mm/s", message)
        self.assertEqual(self.run_command({"VELOCITY": "300"}), (300., []))
        self.assertEqual(self.run_command({"ACCEL": "5000"}), (565., []))


pause_timeout = load_extra("pause_timeout")


class PauseTimeoutTest(unittest.TestCase):
    """A paused print's idle timeout is vo_pause_timeout_hours, 72 by
    default, and a paused print is cancelled when it runs out."""

    def printer(self, variables=None, cancel_fails=False):
        class Sim:
            ran = []
            messages = []
            handlers = {}
            callbacks = []
            timeout = 43200.

        sim = Sim()

        class PauseResume:
            is_paused = False
            sd_paused = False

        pause_resume = PauseResume()

        class Sdcard:
            def is_active(self):
                return False

            def do_cancel(self):
                sim.ran.append("do_cancel")

        def vendor_pause(gcmd):
            pause_resume.is_paused = pause_resume.sd_paused = True
            run("SET_IDLE_TIMEOUT TIMEOUT=259200")

        def run(script):
            sim.ran.append(script)
            if script.startswith("SET_IDLE_TIMEOUT"):
                sim.timeout = float(script.split("=")[1])
            elif script == "CANCEL_PRINT":
                if cancel_fails:
                    raise RuntimeError("Must home axis first")
                pause_resume.is_paused = False
            elif script == "VO_CANCEL_PRINT_ON_ERROR":
                pause_resume.is_paused = False

        commands = {"PAUSE": vendor_pause}

        class Gcode:
            gcode_help = {}

            def register_command(self, name, handler, desc=None):
                old = commands.pop(name, None)
                if handler is not None:
                    commands[name] = handler
                return old

            def run_script(self, script):
                run(script)

            run_script_from_command = run_script

            def respond_info(self, message):
                sim.messages.append(message)

        class IdleTimeout:
            @property
            def idle_timeout(self):
                return sim.timeout

        objects = {
            "gcode": Gcode(), "pause_resume": pause_resume,
            "virtual_sdcard": Sdcard(), "idle_timeout": IdleTimeout(),
            "save_variables": types.SimpleNamespace(allVariables=variables or {}),
        }

        class Printer:
            def get_reactor(self):
                return types.SimpleNamespace(register_callback=sim.callbacks.append)

            def lookup_object(self, name, default=None):
                return objects.get(name, default)

            def register_event_handler(self, event, handler):
                sim.handlers[event] = handler

        pause_timeout.load_config(types.SimpleNamespace(get_printer=Printer))
        sim.handlers["klippy:ready"]()

        def idle():
            sim.handlers["idle_timeout:idle"](0.)
            while sim.callbacks:
                sim.callbacks.pop(0)(0.)

        sim.pause = lambda: commands["PAUSE"](None)
        sim.idle = idle
        sim.pause_resume = pause_resume
        self.assertIsNot(commands["PAUSE"], vendor_pause)
        return sim

    def test_pause_keeps_qidis_72_hours_by_default(self) -> None:
        for variables in ({}, {"vo_pause_timeout_hours": 0},
                          {"vo_pause_timeout_hours": "soon"}):
            sim = self.printer(variables)
            sim.pause()
            self.assertEqual(sim.timeout, 72 * 3600.)

    def test_pause_uses_the_saved_hours(self) -> None:
        sim = self.printer({"vo_pause_timeout_hours": 1.5})
        sim.pause()
        self.assertEqual(sim.timeout, 5400.)
        sim.ran.clear()
        sim.pause()
        self.assertEqual(sim.ran, ["SET_IDLE_TIMEOUT TIMEOUT=259200"])

    def test_idle_timeout_on_a_paused_print_cancels_it_as_fluidd_does(self) -> None:
        sim = self.printer()
        sim.pause()
        sim.ran.clear()
        sim.idle()
        self.assertEqual(sim.ran, ["do_cancel", "CANCEL_PRINT"])
        self.assertIn("72 hours", sim.messages[0])
        self.assertFalse(sim.pause_resume.is_paused)

    def test_cancel_that_cannot_move_still_ends_the_pause(self) -> None:
        sim = self.printer(cancel_fails=True)
        sim.pause()
        with self.assertLogs(level="ERROR"):
            sim.idle()
        self.assertEqual(sim.ran[-1], "VO_CANCEL_PRINT_ON_ERROR")
        self.assertFalse(sim.pause_resume.is_paused)

    def test_idle_timeout_without_a_paused_print_cancels_nothing(self) -> None:
        sim = self.printer()
        sim.idle()
        self.assertEqual((sim.ran, sim.messages), ([], []))


update_lights = load_extra("update_lights")


class UpdateLightsTest(unittest.TestCase):
    """While an automatic update's hold lasts, every Klipper start turns
    the chamber light, which printer.cfg starts on, off again; an ended
    hold is removed and the light is left alone."""

    def start_klipper(self, hold, *, caselight=True):
        root = temp_path("update-lights-")
        (root / "config").mkdir()
        if hold is not None:
            (root / update_lights.HOLD_FILE).write_text(json.dumps(hold))
        ran, handlers = [], {}
        objects = {"gcode": types.SimpleNamespace(run_script=ran.append)}
        if caselight:
            objects["output_pin caselight"] = object()
        printer = types.SimpleNamespace(
            get_reactor=lambda: types.SimpleNamespace(register_callback=lambda callback: callback(0.)),
            get_start_args=lambda: {"config_file": str(root / "config/printer.cfg")},
            register_event_handler=handlers.__setitem__,
            lookup_object=lambda name, default=None: objects.get(name, default))
        update_lights.load_config(types.SimpleNamespace(get_printer=lambda: printer))
        handlers["klippy:ready"]()
        return ran, root / update_lights.HOLD_FILE

    def test_the_light_stays_off_only_while_the_hold_lasts(self) -> None:
        ran, hold = self.start_klipper({"until": time.time() + 600})
        self.assertEqual((ran, hold.exists()), (["SET_PIN PIN=caselight VALUE=0"], True))
        ran, hold = self.start_klipper({"until": time.time() - 1})
        self.assertEqual((ran, hold.exists()), ([], False))
        for held, caselight in ((None, True), ({"until": "soon"}, True), ({"until": time.time() + 600}, False)):
            with self.subTest(hold=held, caselight=caselight):
                self.assertEqual(self.start_klipper(held, caselight=caselight)[0], [])


prime_handoff = load_extra("prime_handoff", "vo_prime_handoff")


class _Move:
    """A G-code command line with Klipper's parameter reads."""

    def __init__(self, line):
        self.line = line
        self.params = {word[0]: word[1:] for word in line.split()[1:]}

    def get_float(self, name, default=None, minval=None, maxval=None):
        return float(self.params[name]) if name in self.params else default


class PrimeHandoffTest(unittest.TestCase):
    """The prime line's retract is put back once, right before the first
    move that extrudes, wherever the slicer travelled first; a print that
    ends before then puts nothing back."""

    def arm(self, *, absolute=False, e_position=0., state="printing"):
        ran = []
        klipper = {name: (lambda gcmd: ran.append(gcmd.line)) for name in ("G0", "G1")}
        handlers = dict(klipper)

        def register_command(name, func, desc=None):
            if func is None:
                return handlers.pop(name, None)
            self.assertNotIn(name, handlers)
            handlers[name] = func

        def run_script_from_command(script):
            # Only the extruder moves; the G-code E position is kept.
            self.assertIn("SAVE_GCODE_STATE", script)
            self.assertIn("RESTORE_GCODE_STATE", script)
            ran.append(float(script.split(" E")[1].split()[0]))

        gcode = types.SimpleNamespace(register_command=register_command, gcode_help={},
                                      run_script_from_command=run_script_from_command)
        print_stats = _Status({"state": state})
        objects = {
            "gcode": gcode,
            "print_stats": print_stats,
            "gcode_move": types.SimpleNamespace(
                get_status=lambda eventtime=None: {"absolute_extrude": absolute,
                                                   "gcode_position": (10., 0., 1., e_position)}),
        }
        events = {}
        printer = types.SimpleNamespace(
            get_reactor=lambda: types.SimpleNamespace(monotonic=lambda: 1.),
            lookup_object=lambda name, default=None: objects.get(name, default),
            register_event_handler=events.__setitem__)
        prime_handoff.load_config(types.SimpleNamespace(get_printer=lambda: printer))
        events["klippy:connect"]()
        handlers["_VO_PRIME_RETRACT"](_Move("_VO_PRIME_RETRACT"))

        def run(*lines):
            for line in lines:
                handlers[line.split()[0]](_Move(line))

        return run, ran, handlers, klipper, print_stats

    def test_put_back_after_orcaslicers_travel_to_the_first_part(self) -> None:
        run, ran, handlers, klipper, _ = self.arm()
        # OrcaSlicer's layer-change retract, travel and unretract, then the first wall.
        run("G1 Z1 F1200", "G1 E-.1 F1800", "G1 X265.573 Y209.566 F30000", "G1 Z.2",
            "G1 E.1 F1800", "G1 X242.427 Y232.712 E1.18189", "G1 X216.458 Y258.681 E1.32602")
        self.assertEqual(ran, [-0.8, "G1 Z1 F1200", "G1 E-.1 F1800", "G1 X265.573 Y209.566 F30000",
                               "G1 Z.2", 0.8, "G1 E.1 F1800", "G1 X242.427 Y232.712 E1.18189",
                               "G1 X216.458 Y258.681 E1.32602"])
        self.assertIs(handlers["G1"], klipper["G1"])
        self.assertIs(handlers["G0"], klipper["G0"])

    def test_absolute_extrusion_puts_back_on_the_first_move_past_the_e_position(self) -> None:
        run, ran, _, _, _ = self.arm(absolute=True, e_position=5.)
        run("G0 X100 Y100", "G1 E4.9 F1800", "G1 X101 E5.2")
        self.assertEqual(ran, [-0.8, "G0 X100 Y100", "G1 E4.9 F1800", 0.8, "G1 X101 E5.2"])

    def test_a_print_that_ends_first_puts_nothing_back(self) -> None:
        run, ran, handlers, klipper, print_stats = self.arm()
        print_stats.status["state"] = "cancelled"
        run("G1 E10 F300")
        self.assertEqual(ran, [-0.8, "G1 E10 F300"])
        self.assertIs(handlers["G1"], klipper["G1"])
