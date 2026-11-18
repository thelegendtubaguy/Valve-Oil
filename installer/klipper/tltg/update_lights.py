# Keeps the chamber light off through Valve Oil's automatic updates.
#
# Stock printer.cfg starts [output_pin caselight] on, so every Klipper start
# an update causes (a service restart, a QIDI firmware update, the reboot
# after an install) turns the chamber light on. An automatic update that
# finds the light off writes ~/printer_data/.tltg_update_lights_hold.json
# with the time the hold ends (installer/runtime/update_window.py); while it
# hasn't ended, this module turns the light off when Klipper is ready. An
# ended hold is removed.

import json
import logging
import os
import time

HOLD_FILE = ".tltg_update_lights_hold.json"
PIN = "caselight"


class UpdateLights:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        config_file = self.printer.get_start_args().get("config_file", "")
        # printer_data/config/printer.cfg -> printer_data
        self.path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(config_file))),
            HOLD_FILE)
        self.printer.register_event_handler("klippy:ready", self._ready)

    def _hold_until(self):
        try:
            with open(self.path, "r") as handle:
                until = json.load(handle).get("until")
        except (OSError, ValueError, AttributeError):
            return None
        if isinstance(until, bool) or not isinstance(until, (int, float)):
            return None
        return until

    def _ready(self):
        until = self._hold_until()
        if until is None:
            return
        if time.time() >= until:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            return
        if self.printer.lookup_object("output_pin " + PIN, None) is None:
            return
        self.reactor.register_callback(self._light_off)

    def _light_off(self, eventtime):
        try:
            self.printer.lookup_object("gcode").run_script(
                "SET_PIN PIN=%s VALUE=0" % (PIN,))
            logging.info("tltg_update_lights: chamber light kept off "
                         "during an automatic update")
        except Exception:
            logging.exception("tltg_update_lights: couldn't turn the "
                              "chamber light off")


def load_config(config):
    return UpdateLights(config)
