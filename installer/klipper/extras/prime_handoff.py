# Prime line hand-off for Valve Oil.
#
# After the slicer start G-code's front prime line, the slicer travels to the
# first part, which can be across the bed. The nozzle oozed on that travel
# and the print's first line started a few millimetres late. OrcaSlicer
# retracts only its own retraction length before that travel, and puts back
# only that; a retract in the start G-code alone left the first line that much
# shorter, because neither slicer knows to put it back.
#
# _VO_PRIME_RETRACT, at the end of the prime line, pulls the filament back
# (0.8 mm unless LENGTH= says otherwise) and puts it back right before the
# first move after it that extrudes, which is after the travel: in
# OrcaSlicer, its own unretract at the first part. Until then this module
# sees G0, G1, G2 and G3 first; after it, they're Klipper's again. A hand-off
# armed during a print that ends before anything extrudes is dropped.

import logging

MOVES = ("G0", "G1", "G2", "G3")
STATE = "_vo_prime_handoff"
SPEED = 1800
DEFAULT_LENGTH = 0.8
MAX_LENGTH = 5.


class PrimeHandoff:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.gcode_move = None
        self.print_stats = None
        self.pending = 0.
        self.in_print = False
        self.originals = {}
        self.printer.register_event_handler("klippy:connect", self._connect)
        self.gcode.register_command(
            "_VO_PRIME_RETRACT", self.cmd_VO_PRIME_RETRACT,
            desc="Retract at the end of the prime line and put it back on "
                 "the next move that extrudes")

    def _connect(self):
        self.gcode_move = self.printer.lookup_object("gcode_move")
        self.print_stats = self.printer.lookup_object("print_stats", None)

    def get_status(self, eventtime):
        return {"pending": self.pending}

    def _printing(self):
        if self.print_stats is None:
            return False
        state = self.print_stats.get_status(
            self.reactor.monotonic()).get("state")
        return state in ("printing", "paused")

    def _move_extruder(self, distance):
        # SAVE/RESTORE_GCODE_STATE keep the G-code E position, so a print in
        # absolute extrusion carries on where the slicer expects.
        self.gcode.run_script_from_command(
            "SAVE_GCODE_STATE NAME=%s\nM83\nG1 E%.4f F%d\n"
            "RESTORE_GCODE_STATE NAME=%s" % (STATE, distance, SPEED, STATE))

    def _extrudes(self, gcmd):
        e = gcmd.get_float("E", None)
        if e is None:
            return False
        if not self.gcode_move.absolute_extrude:
            return e > 0.
        return e > self.gcode_move.get_status()["gcode_position"][3]

    def cmd_VO_PRIME_RETRACT(self, gcmd):
        length = gcmd.get_float("LENGTH", DEFAULT_LENGTH, minval=0.,
                                maxval=MAX_LENGTH)
        if self.pending or not length:
            return
        self._move_extruder(-length)
        self.pending = length
        self.in_print = self._printing()
        for name in MOVES:
            original = self.gcode.register_command(name, None)
            if original is None:
                continue
            self.originals[name] = original
            self.gcode.register_command(name, self._wrap(original),
                                        desc=self._help(name))

    def _help(self, name):
        return getattr(self.gcode, "gcode_help", {}).get(name)

    def _wrap(self, original):
        def move(gcmd):
            if self.in_print and not self._printing():
                self._release()
            elif self._extrudes(gcmd):
                length = self.pending
                self._release()
                position = self.gcode_move.get_status()["gcode_position"]
                logging.info("vo_prime_handoff: put back %.2f mm at X%.1f"
                             " Y%.1f", length, position[0], position[1])
                self._move_extruder(length)
            original(gcmd)
        return move

    def _release(self):
        self.pending = 0.
        originals, self.originals = self.originals, {}
        for name, original in originals.items():
            self.gcode.register_command(name, None)
            self.gcode.register_command(name, original, desc=self._help(name))


def load_config(config):
    return PrimeHandoff(config)
