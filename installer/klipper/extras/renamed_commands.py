# Earlier names of Valve Oil's commands.
#
# Valve Oil's commands and macros were named TLTG_ and OPTIMIZED_ before
# 26.11.18.2. Older instructions, macro buttons, and the installer's patches
# to QIDI's and Moonraker's files still give those names, so each one runs
# its new command and says what it's called now. The OPTIMIZED_ macros the
# slicer packs call kept their names and aren't listed here.

import logging

RENAMED = {
    "TLTG_BED_SOAK": "VO_BED_SOAK",
    "TLTG_BED_SOAK_STOP": "VO_BED_SOAK_STOP",
    "TLTG_BOX_MOTION": "VO_BOX_MOTION",
    "TLTG_BOX_RFID_REPORT": "VO_BOX_RFID_REPORT",
    "TLTG_BOX_SLOT_JOG": "VO_BOX_SLOT_JOG",
    "TLTG_CORNER_BED_SCREW_CHECK": "VO_CORNER_BED_SCREW_CHECK",
    "TLTG_CUT_FILAMENT": "VO_CUT_FILAMENT",
    "TLTG_FILAMENT_SENSOR": "VO_FILAMENT_SENSOR",
    "TLTG_PROBE_ACCURACY_CENTER": "VO_PROBE_ACCURACY_CENTER",
    "TLTG_RESET_TOOL_MAPPINGS": "VO_RESET_TOOL_MAPPINGS",
    "TLTG_RS_BOX_REPORT": "VO_RS_BOX_REPORT",
    "TLTG_SAVE_CONFIG_QD": "VO_SAVE_CONFIG_QD",
    "TLTG_SPOOLMAN_SLOT": "VO_SPOOLMAN_SLOT",
    "TLTG_STEP_LOSS_CORRECT": "VO_STEP_LOSS_CORRECT",
    "TLTG_TEST_PROBE": "VO_TEST_PROBE",
    "OPTIMIZED_CANCEL_PRINT_ON_ERROR": "VO_CANCEL_PRINT_ON_ERROR",
    "OPTIMIZED_DISABLE_BOX_HEATER": "VO_DISABLE_BOX_HEATER",
    "OPTIMIZED_END_FAN_COOLDOWN": "VO_END_FAN_COOLDOWN",
    "OPTIMIZED_END_NOZZLE_CLEANUP": "VO_END_NOZZLE_CLEANUP",
    "OPTIMIZED_EXTRUSION_AND_FLUSH": "VO_EXTRUSION_AND_FLUSH",
    "OPTIMIZED_FILAMENT_SENSOR_INSERTED": "VO_FILAMENT_SENSOR_INSERTED",
    "OPTIMIZED_FILAMENT_SENSOR_RUNOUT": "VO_FILAMENT_SENSOR_RUNOUT",
    "OPTIMIZED_G29_ZSAFE": "VO_G29_ZSAFE",
    "OPTIMIZED_M1004": "VO_M1004",
    "OPTIMIZED_UNLOAD_FILAMENT": "VO_UNLOAD_FILAMENT",
    "OPTIMIZED_WAIT_BED": "VO_WAIT_BED",
    "OPTIMIZED_WAIT_CHAMBER": "VO_WAIT_CHAMBER",
    "OPTIMIZED_WAIT_HOTEND": "VO_WAIT_HOTEND",
    "OPTIMIZED_WIPE_AND_SCRAPE_NOZZLE": "VO_WIPE_AND_SCRAPE_NOZZLE",
    "OPTIMIZED_WIPE_NOZZLE_DROP": "VO_WIPE_NOZZLE_DROP",
}


class RenamedCommands:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        # Once every command is registered, so a name that's in use is left
        # alone and a command this printer doesn't have gets no old name.
        self.printer.register_event_handler("klippy:connect", self._connect)

    def _connect(self):
        handlers = getattr(self.gcode, "ready_gcode_handlers", {})
        for old, new in sorted(RENAMED.items()):
            if old in handlers or new not in handlers:
                continue
            try:
                self.gcode.register_command(
                    old, self._forward(old, new), desc="Renamed " + new)
            except Exception:
                logging.exception("vo_renamed_commands: %s not registered",
                                  old)

    def _forward(self, old, new):
        def cmd(gcmd):
            gcmd.respond_info("%s is now %s." % (old, new))
            params = gcmd.get_raw_command_parameters().strip()
            self.gcode.run_script_from_command(
                ("%s %s" % (new, params)).strip())
        return cmd


def load_config(config):
    return RenamedCommands(config)
