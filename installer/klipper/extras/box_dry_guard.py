# QIDI Box drying guard for Valve Oil.
#
# While QIDI's timed Box drying runs, its timer turns every slot of the Box
# every 300 s with RUN_STEPPER and G4 P5000, sent through
# run_script_from_command so they skip the G-code lock. In a print those
# dwells stop the print for about 20 s each time. A turn that lands during a
# homing or probing move flushes the toolhead in the middle of it, and Klipper
# shuts down with an internal error on RUN_STEPPER.
#
# So a print doesn't start while a Box is drying: the commands that start one
# (SDCARD_PRINT_FILE, QIDI's power-loss resume, and M24 when it isn't resuming
# a paused print) stop with an error until drying is stopped. Resuming a
# paused print is left alone. Drying doesn't start during a print either:
# BOX_START_DRYING, and ENABLE_BOX_DRY where QIDI's firmware has it, stop with
# an error from the print start until the print ends.

import logging

START_COMMANDS = ("SDCARD_PRINT_FILE", "SDCARD_POWER_OFF_CONTINUES_PINT_FILE",
                  "M24")
DRY_COMMANDS = ("BOX_START_DRYING", "ENABLE_BOX_DRY")


class BoxDryGuard:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.printer.register_event_handler("klippy:ready", self._ready)

    def _ready(self):
        if self.printer.lookup_object("box_extras", None) is None:
            logging.info("vo_box_dry_guard: inactive: no QIDI Box")
            return
        wrapped = []
        for names, guard in ((START_COMMANDS, self._guarded),
                             (DRY_COMMANDS, self._dry_guarded)):
            for name in names:
                original = self.gcode.register_command(name, None)
                if original is None:
                    continue
                self.gcode.register_command(
                    name, guard(name, original),
                    desc=self.gcode.gcode_help.get(name))
                wrapped.append(name)
        logging.info("vo_box_dry_guard: active on %s", ", ".join(wrapped))

    def drying_boxes(self):
        """Numbers of the Boxes QIDI's drying is running on, from 1."""
        box_extras = self.printer.lookup_object("box_extras", None)
        try:
            eventtime = self.printer.get_reactor().monotonic()
            boxes = box_extras.get_status(eventtime).get("boxes")
        except Exception:
            logging.exception("vo_box_dry_guard: Box status failed")
            return []
        if not isinstance(boxes, list):
            return []
        return [number for number, box in enumerate(boxes, 1)
                if hasattr(box, "get") and box.get("dry_state") == 1]

    def _print_state(self):
        print_stats = self.printer.lookup_object("print_stats", None)
        if print_stats is None:
            return None
        eventtime = self.printer.get_reactor().monotonic()
        return print_stats.get_status(eventtime).get("state")

    def _resuming(self):
        return self._print_state() == "paused"

    def _guarded(self, name, original):
        def handler(gcmd):
            if not (name == "M24" and self._resuming()):
                boxes = self.drying_boxes()
                if boxes:
                    logging.info("vo_box_dry_guard: %s refused while Box %s "
                                 "dries", name,
                                 ", ".join(str(box) for box in boxes))
                    raise gcmd.error(
                        "%s drying. Stop drying before starting a print."
                        % (("Box %d is" % boxes[0]) if len(boxes) == 1
                           else "Boxes %s and %d are" % (
                               ", ".join(str(box) for box in boxes[:-1]),
                               boxes[-1])))
            return original(gcmd)
        return handler

    def _dry_guarded(self, name, original):
        def handler(gcmd):
            # QIDI's print_stats is "extracting" while a 3MF print unpacks,
            # after the print start was let through.
            if self._print_state() in ("extracting", "printing", "paused"):
                logging.info("vo_box_dry_guard: %s refused during a print",
                             name)
                raise gcmd.error(
                    "Box drying can't start during a print. Set the Box "
                    "heater target instead.")
            return original(gcmd)
        return handler


def load_config(config):
    return BoxDryGuard(config)
