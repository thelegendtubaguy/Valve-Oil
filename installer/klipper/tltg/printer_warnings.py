# Printer warnings for Valve Oil.
#
# Publishes the problems an operator should fix before printing as Klipper
# status, so Fluidd shows them on every page until they are gone, and the
# optimized macros print the same text in the console at startup and after
# every home. Problems: a missing input shaper calibration, an X/Y motor
# slip reported by tltg_step_loss_monitor (until X and Y are homed again),
# and a USB Box the installer can't flash because its bootloader or firmware
# isn't supported yet. The installer leaves that Box's shareable report in
# Klipper's log folder as tltg_box_firmware_box<N>.json and removes it once
# the Box has the Valve Oil firmware; the folder is read when Klipper starts.
#
#   warnings  a list of {"id", "level", "title", "message"}, with level
#             "warning" or "error"; empty when nothing is wrong

import logging
import os
import re

BOX_REPORT = re.compile(r"^tltg_box_firmware_box(\d+)\.json$")
SUPPORT_ISSUE_URL = ("https://github.com/thelegendtubaguy/valve-oil/issues/new"
                     "?template=printer-support.yml")


class PrinterWarnings:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.failed = False
        log_file = self.printer.get_start_args().get("log_file")
        self.log_dir = os.path.dirname(log_file) if log_file else None
        self.box_reports = []
        self.printer.register_event_handler("klippy:ready", self._find_box_reports)

    def _find_box_reports(self):
        try:
            names = os.listdir(self.log_dir) if self.log_dir else []
        except OSError:
            names = []
        reports = []
        for name in names:
            match = BOX_REPORT.match(name)
            if match:
                reports.append((int(match.group(1)), name))
        self.box_reports = sorted(reports)

    def _unshaped_accel(self, macro_status):
        optimized = self.printer.lookup_object(
            "gcode_macro _tltg_optimized_globals", None)
        variables = getattr(optimized, "variables", {})
        try:
            return min(macro_status.max_accel,
                       float(variables.get("unshaped_accel", 10000)))
        except (TypeError, ValueError):
            return macro_status.max_accel

    def _input_shaper(self):
        macro_status = self.printer.lookup_object("tltg_macro_status", None)
        if macro_status is None:
            return []
        shaper = macro_status.input_shaper()
        if shaper["saved"] and not shaper["unshaped_axes"]:
            return []
        limit = ("limiting acceleration during homing and meshing to %d mm/s²."
                 " Run input shaper calibration when possible."
                 % self._unshaped_accel(macro_status))
        if shaper["unshaped_axes"]:
            return [{
                "id": "input_shaper_off",
                "level": "error",
                "title": "Input shaping is off.",
                "message": "TLTG Optimizations is " + limit,
            }]
        return [{
            "id": "input_shaper_uncalibrated",
            "level": "error",
            "title": "No input shaper calibration is saved.",
            "message": "TLTG Optimizations is utilizing Qidi's default shaper"
                       " and " + limit,
        }]

    def _step_loss(self):
        monitor = self.printer.lookup_object("tltg_step_loss_monitor", None)
        message = getattr(monitor, "message", None)
        if not message:
            return []
        return [{
            "id": "xy_step_loss",
            "level": "error",
            "title": "X/Y lost position.",
            "message": message,
        }]

    def _box_firmware(self):
        return [{
            "id": "box_firmware_unsupported_box%d" % number,
            "level": "warning",
            "title": "Box %d can't get the Valve Oil Box firmware yet." % number,
            "message": "Its bootloader or firmware isn't supported yet, so it"
                       " keeps the firmware it has. To get it supported, open"
                       " an issue at " + SUPPORT_ISSUE_URL + " and attach "
                       + name + " from Fluidd's Logs.",
        } for number, name in self.box_reports]

    def get_status(self, eventtime=None):
        try:
            warnings = (self._step_loss() + self._input_shaper()
                        + self._box_firmware())
        except Exception:
            if not self.failed:
                logging.exception("tltg_printer_warnings: check failed")
            self.failed = True
            return {"warnings": []}
        self.failed = False
        return {"warnings": warnings}


def load_config(config):
    return PrinterWarnings(config)
