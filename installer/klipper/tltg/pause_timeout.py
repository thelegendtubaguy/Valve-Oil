# Paused-print timeout for Valve Oil.
#
# QIDI's PAUSE turns the nozzle off and sets Klipper's idle timeout to 72
# hours. When that runs out, printer.cfg's idle G-code turns the bed, chamber,
# Box heaters, and fans off, but the print stays paused, and QIDI's
# _KM_IDLE_TIMEOUT records the nozzle's target, by then 0, as the temperature
# to resume at.
#
# This module sets the idle timeout PAUSE leaves to tltg_pause_timeout_hours
# (72 by default, as QIDI's), and when the idle timeout runs out on a paused
# print, cancels the print as Fluidd's cancel does.

import logging

HOURS_VARIABLE = "tltg_pause_timeout_hours"
DEFAULT_HOURS = 72.


class PauseTimeout:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.pause_resume = self.sdcard = self.idle_timeout = None
        self.variables = self.vendor_pause = None
        self.printer.register_event_handler("klippy:ready", self._ready)
        self.printer.register_event_handler("idle_timeout:idle", self._idle)

    def _ready(self):
        lookup = self.printer.lookup_object
        self.pause_resume = lookup("pause_resume", None)
        self.sdcard = lookup("virtual_sdcard", None)
        self.idle_timeout = lookup("idle_timeout", None)
        self.variables = lookup("save_variables", None)
        # QIDI's PAUSE macro takes the PAUSE name at klippy:connect.
        pause = self.gcode.register_command("PAUSE", None)
        if self.pause_resume is None or pause is None:
            if pause is not None:
                self.gcode.register_command("PAUSE", pause)
            logging.info("tltg_pause_timeout: no PAUSE; timeout unchanged")
            return
        self.vendor_pause = pause
        self.gcode.register_command(
            "PAUSE", self.cmd_PAUSE, desc=self.gcode.gcode_help.get("PAUSE"))

    def _hours(self):
        variables = getattr(self.variables, "allVariables", None) or {}
        value = variables.get(HOURS_VARIABLE, DEFAULT_HOURS)
        try:
            hours = float(value)
        except (TypeError, ValueError):
            hours = 0.
        if not 0. < hours < float("inf"):
            logging.info("tltg_pause_timeout: %s=%r is not a positive number"
                         " of hours; using %g", HOURS_VARIABLE, value,
                         DEFAULT_HOURS)
            return DEFAULT_HOURS
        return hours

    def cmd_PAUSE(self, gcmd):
        was_paused = self.pause_resume.is_paused
        try:
            self.vendor_pause(gcmd)
        finally:
            # Also after a PAUSE that stopped with an error once paused, which
            # skipped QIDI's 72 hours and left printer.cfg's 12.
            if self.pause_resume.is_paused and not was_paused:
                self.gcode.run_script_from_command(
                    "SET_IDLE_TIMEOUT TIMEOUT=%.0f"
                    % max(1., self._hours() * 3600.))

    def _idle(self, print_time):
        if self.pause_resume is not None and self.pause_resume.is_paused:
            self.reactor.register_callback(self._cancel)

    def _cancel(self, eventtime):
        if not self.pause_resume.is_paused:
            return
        hours = getattr(self.idle_timeout, "idle_timeout", 0.) / 3600.
        self.gcode.respond_info(
            "The print was paused with nothing running for %g hours, so it's"
            " cancelled. Change how long with %s." % (hours, HOURS_VARIABLE))
        # As Fluidd's cancel: end the file print, then run CANCEL_PRINT.
        try:
            if self.sdcard is not None and (
                    self.sdcard.is_active()
                    or getattr(self.pause_resume, "sd_paused", False)):
                self.sdcard.do_cancel()
            self.gcode.run_script("CANCEL_PRINT")
            return
        except Exception:
            logging.exception("tltg_pause_timeout: CANCEL_PRINT failed")
        # QIDI's CANCEL_PRINT moves the toolhead; if it couldn't, cancel
        # without moving so the printer doesn't stay paused.
        try:
            self.gcode.run_script("OPTIMIZED_CANCEL_PRINT_ON_ERROR")
        except Exception:
            logging.exception(
                "tltg_pause_timeout: OPTIMIZED_CANCEL_PRINT_ON_ERROR failed")


def load_config(config):
    return PauseTimeout(config)
