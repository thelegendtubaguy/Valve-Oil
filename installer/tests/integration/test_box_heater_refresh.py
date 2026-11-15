from __future__ import annotations

import importlib.util
import unittest

from installer.tests.helpers import REPO_ROOT


def _load():
    spec = importlib.util.spec_from_file_location(
        "extras.tltg_box_heater_refresh", REPO_ROOT / "installer/klipper/tltg/box_heater_refresh.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


box_heater_refresh = _load()


class Pwm:
    """Klipper's MCU_pwm keeps its cycle time until the MCU is configured."""

    def __init__(self):
        self.cycle_time = 0.1

    def setup_cycle_time(self, cycle_time, hardware_pwm=False):
        self.cycle_time = cycle_time


class Heater:
    def __init__(self, pwm_delay=0.8):
        self.mcu_pwm = Pwm()
        self.pwm_delay = pwm_delay


class Heaters:
    def __init__(self, heaters):
        self.heaters = heaters


class SaveVariables:
    def __init__(self, variables):
        self.allVariables = variables


class Printer:
    def __init__(self, objects):
        self.objects = objects
        self.handlers = {}

    def register_event_handler(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def send_event(self, event):
        for callback in self.handlers.get(event, []):
            callback()


class Config:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self):
        return self.printer


def mcu_cycle_times(variables, *, box_pwm_delay=0.8):
    """Starts Klippy up to MCU configuration and returns each heater's cycle time."""
    heaters = {
        "heater_bed": Heater(),
        "chamber": Heater(),
        "heater_box1": Heater(box_pwm_delay),
        "heater_box2": Heater(box_pwm_delay),
    }
    objects = {"heaters": Heaters(heaters)}
    if variables is not None:
        objects["save_variables"] = SaveVariables(variables)
    printer = Printer(objects)
    box_heater_refresh.load_config(Config(printer))
    printer.send_event("klippy:mcu_identify")
    return {name: heater.mcu_pwm.cycle_time for name, heater in heaters.items()}


class BoxHeaterCycleTimeTests(unittest.TestCase):
    def test_saved_cycle_time_reaches_only_box_heaters(self):
        for value in (0.0167, "0.02", 0.01, 0.1):
            with self.subTest(value=value):
                times = mcu_cycle_times({"vo_box_heater_pwm_cycle_time": value})
                self.assertEqual(times["heater_box1"], float(value))
                self.assertEqual(times["heater_box2"], float(value))
                self.assertEqual(times["heater_bed"], 0.1)
                self.assertEqual(times["chamber"], 0.1)

    def test_box_heaters_keep_stock_cycle_time_without_a_usable_setting(self):
        for variables in (None, {}, {"vo_box_heater_pwm_cycle_time": 0.005},
                          {"vo_box_heater_pwm_cycle_time": 0.5},
                          {"vo_box_heater_pwm_cycle_time": 0},
                          {"vo_box_heater_pwm_cycle_time": True},
                          {"vo_box_heater_pwm_cycle_time": "fast"},
                          {"vo_box_heater_pwm_cycle_time": float("nan")}):
            with self.subTest(variables=variables):
                times = mcu_cycle_times(variables)
                self.assertEqual(set(times.values()), {0.1})

    def test_cycle_time_stays_within_the_sensor_report_time(self):
        times = mcu_cycle_times({"vo_box_heater_pwm_cycle_time": 0.05}, box_pwm_delay=0.02)
        self.assertEqual(times["heater_box1"], 0.1)


if __name__ == "__main__":
    unittest.main()
