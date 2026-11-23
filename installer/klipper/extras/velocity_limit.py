# Velocity limit for Valve Oil.
#
# Klipper's SET_VELOCITY_LIMIT VELOCITY= raises the toolhead's speed limit
# above printer.cfg's max_velocity, and QIDI's M203 macro passes a slicer's
# machine limit straight to it. On the Max 4, a diagonal move turns one CoreXY
# motor at up to 1.41 times the toolhead speed, and at 800 mm/s that motor
# lost steps. This module holds SET_VELOCITY_LIMIT to printer.cfg's
# max_velocity the way QIDI's Klipper already holds ACCEL to max_accel: a
# higher VELOCITY is lowered to it and the console says so.

import logging


class VelocityLimit:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.max_velocity = config.getsection("printer").getfloat(
            "max_velocity", above=0.)
        self.original = None
        self.printer.register_event_handler("klippy:connect", self._connect)

    def _connect(self):
        # Klipper 0.14 moved the handler from ToolHead to a helper object, so
        # take whatever handler is registered.
        self.original = self.gcode.register_command("SET_VELOCITY_LIMIT", None)
        if self.original is None:
            logging.info("vo_velocity_limit: SET_VELOCITY_LIMIT not found")
            return
        self.gcode.register_command(
            "SET_VELOCITY_LIMIT", self.cmd_SET_VELOCITY_LIMIT,
            desc=self.gcode.gcode_help.get("SET_VELOCITY_LIMIT"))

    def cmd_SET_VELOCITY_LIMIT(self, gcmd):
        velocity = gcmd.get_float("VELOCITY", None, above=0.)
        if velocity is not None and velocity > self.max_velocity:
            gcmd.respond_info(
                "VELOCITY=%g is above this printer's max_velocity; using"
                " %g mm/s." % (velocity, self.max_velocity))
            # The registered handler re-reads the parameters from the
            # command line, so hand it a rewritten one.
            params = dict(gcmd.get_command_parameters())
            params["VELOCITY"] = repr(self.max_velocity)
            line = "SET_VELOCITY_LIMIT " + " ".join(
                "%s=%s" % item for item in params.items())
            gcmd = self.gcode.create_gcode_command(
                "SET_VELOCITY_LIMIT", line, params)
        self.original(gcmd)


def load_config(config):
    return VelocityLimit(config)
