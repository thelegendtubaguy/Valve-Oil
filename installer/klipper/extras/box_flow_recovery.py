# QIDI Box flow recovery for Valve Oil.
#
# Stopping a paused Box flow: QIDI's TaskQueueManager.stop() clears the flow,
# then sends M104 S0 with gcode.run_script(), which waits for Klipper's G-code
# lock. FLOW_STOP already holds that lock, so the command never returned and
# every later command queued behind it, with the nozzle left hot. While
# FLOW_STOP runs, the stop's scripts go through run_script_from_command
# instead; the multi_color_controller/flow_stop endpoint, which doesn't hold
# the lock, keeps run_script. Either way the nozzle is turned off after a stop
# only when no print is running or paused; a print keeps its heat for resume.
#
# A Box flow step that raises, such as a Box motor move ending with
# "Communication timeout during homing", escapes QIDI's flow runner and
# leaves the flow running, not paused, with the nozzle hot: FLOW_RETRY
# refuses it, and Fluidd shows a busy Box with no way out. Such a flow is
# paused on the failed step, as QIDI pauses a step that fails, before the
# error goes on to the command that ran it.
#
# Filament stranded between the Box hub and the toolhead: a load that reaches
# the hub but not the toolhead leaves the hub sensor occupied and the toolhead
# sensor empty. QIDI's unload then runs no steps and reports success, and its
# eject heats the nozzle and pulls back whichever slot last_load_slot names.
# For an idle printer, UNLOAD_FILAMENT_CONTROL and EJECT_FILAMENT_CONTROL of
# the hub's slot pull that slot back with vo_box_slot_jog's motion until
# the hub sensor clears, stopping if the slot's inlet sensor empties first.
# An unload then parks the filament in the Box and saves the slot state
# QIDI's own slot unload saves; an eject hands over to QIDI's eject, which now
# runs as its eject step alone. The hub's slot comes from
# vo_box_loaded_slot's record and last_load_slot, or is the only slot with
# filament at its inlet; anything else is refused with the reason. Whenever
# neither QIDI nor this module would move anything, the console says why.
#
# Tool changes outside a print: T0-T15 call T_MANAGER, QIDI's print-time tool
# change, whose flow cuts, unloads, and loads without heating the nozzle.
# Outside a print, such as from Fluidd's tool buttons, it ran with the nozzle
# cooling and could end in QIDI's PAUSE, leaving the printer paused with no
# print. T_MANAGER now runs only while a print is running or paused; otherwise
# it says to load the slot instead.
#
# Relative positioning left over from an earlier command, such as G91 from a
# script that stopped with an error before its G90, sent QIDI's flows'
# absolute moves off the bed: MOVE_TO_TRASH, which sets no G90, failed with "Move out
# of range" on every BOX_HEAT until Klipper restarted. A flow started or
# retried while no print is running or paused now switches to absolute
# positioning first, as G90 does; a print keeps its own.
#
# Cold unload and eject: UNLOAD_FILAMENT_CONTROL and EJECT_FILAMENT_CONTROL
# with COLD=1, outside a print, run QIDI's flow without heating the nozzle.
# The Max 4's cutter cuts cold filament and the extruder sits above it, so
# the cut and the extruder's pull-back move only filament that never enters
# the hotend. QIDI's flows never read the nozzle temperature: they heat with
# M109 to the filament's highest printing temperature in their heat step, in
# the unload step, and on retry, and their cut and unload pull back with
# G1 E moves that Klipper's cold-extrusion check refuses. A cold flow runs
# inside vo_cold_cut's cold_retracts(), which lets those pull-backs through
# and still refuses extrusion, with every M109 skipped and the flow's
# temperature cleared, so its completion turns the nozzle off without QIDI's
# 5 s dwell and wipe. FLOW_RETRY of a paused cold flow retries it cold. Only
# flows made of Box heat, cut, unload, and eject steps run cold; any other,
# such as an unload of the external spool, heats as usual and says so.
#
# During a print, T_MANAGER runs its steps itself and reports them as flow 13.
# A step that fails pauses that flow (box_extras._pause_print_retry_flow) and
# sends PAUSE, and RESUME's TRY_RESUME_PRINT redoes the tool change from the
# sensors. A step that raises instead, such as a Box motor move ending with
# "Communication timeout during homing", escaped T_MANAGER and the print's
# error handling cancelled the print. It now pauses the flow and the print the
# same way, unless Klipper has shut down.
#
# Filament caught at the toolhead: QIDI's unload from the toolhead
# (box_stepper's extruder_unload_single_func) pulls the cut filament back
# 30 mm and then exl_2, with the slot's motor turning with the extruder, and
# only starts the Box's pull once the toolhead sensor is clear. While the
# sensor still detects filament it pulls 10 mm more at a time, up to seven
# times, then moves or shakes the toolhead and starts again, three times in
# all, before QDE_004_008: about 450 mm and a minute and a half of pulling on
# filament that isn't moving. On any Box code, the first two extruder pulls
# of an unload are its pull-back; once pulls after them add up to
# EXTRA_PULL_MM with the sensor still detecting filament, the next pull is
# refused and the unload stops with an error that says what's wrong: either
# something holds the filament, at the toolhead or in the Box, or Klipper
# didn't move the extruder for the pulls, as inside a cancelled part. Every
# unload from the toolhead logs both sensors before and after, how far the
# extruder was asked to pull and how far it moved, and where the toolhead
# sensor cleared.
#
# The slot motor's step clock: QIDI's Box auto-feed steps a slot on the Box
# itself, saving the slot's step clock when a feed starts and putting it back
# when the feed ends. Re-arming auto-feed while a feed runs, as print start
# does when the purge has just tripped the buffer, drops the feed without
# putting the clock back, so the next feed saves a clock that is off from
# Klipper's by up to 51 s and keeps it for the rest of the print. A load
# homes the slot, which lines the clocks up again, but an unload ties the slot
# to the extruder for its pull-back first, so the slot's steps ran up to 25 s
# late, holding the filament while the extruder slipped on it, or all at once
# as fast as the Box allows. Before the pull-back, with QIDI's auto-feed
# aborted and the slot idle, the slot's step clock is now reset as a homing
# move resets it.
#
# Cancelled parts: Klipper's exclude_object drops every move while the print
# is inside a cancelled part, extruder-only moves included. OrcaSlicer closes
# a part's EXCLUDE_OBJECT_START/END block only when the next part starts, so
# a filament change after a part's last line runs inside that part's block,
# and when that part was cancelled, the cut, the extruder's pull-backs and
# loads, and the purge and wipe were all dropped while the Box motors still
# ran. The print then paused inside the cancelled part, where every move the
# operator made was dropped too. Moves now go through a cancelled part from
# TOOL_CHANGE_START to TOOL_CHANGE_END, while T_MANAGER runs, and while the
# print is paused; the part's own lines are still dropped. A filament change
# that starts inside a cancelled part first raises the nozzle straight up to
# the height the G-code expects, since the slicer's lift before
# TOOL_CHANGE_START was dropped. The next EXCLUDE_OBJECT_START or
# EXCLUDE_OBJECT_END, or a new print, ends a filament change that never sent
# TOOL_CHANGE_END.
#
# Power-loss resume: QIDI's resume (SDCARD_POWER_OFF_CONTINUES_PINT_FILE)
# cancels the saved cancelled parts again and restarts the file 4 KiB before
# where power was lost, without the EXCLUDE_OBJECT_START of the part it
# restarts inside. Klipper then counted the print as outside every part, and
# a cancelled part printed again, in the air, until its EXCLUDE_OBJECT_END.
# When parts were cancelled, the resume now finds the last part label and
# filament change marker before its restart point, once QIDI has put the
# toolhead back, and starts that part again, so a cancelled one stays
# skipped; a restart inside a filament change lets its moves through as
# above.
#
# External spool loads: QIDI's external-spool load flow ends with EXT_WIPE,
# whose ExternalAdapter.wipe runs its purge and chute wipe and then returns
# False whatever happened, in every build listed below, while a G-code error
# in it raises. Every load therefore ended with "Step EXT_WIPE failed. Pausing
# flow." after the wipe had run, and the touchscreen then stopped the flow
# instead of completing it. In those builds the step now reports success when
# its script ran; an error still raises and pauses the flow as above.

import contextlib
import hashlib
import inspect
import logging
import os
import re
import sys

import greenlet

EXTERNAL_SLOT = "slot16"
HEATER_OFF = "M104 S0"
HUB_SLOT_VARIABLE = "vo_box_hub_slot"
# Slot motion for stranded filament, mm and mm/s.
RETRACT_SPEED = 60.
RETRACT_PIECE = 40.
MAX_HUB_RETRACT = 1500.
# QIDI's slot unload retracts 260 mm once the hub sensor clears; the hub can
# clear anywhere in the last piece, so park that much less.
PARK_DISTANCE = 260. - RETRACT_PIECE
SLOT_STATE_LOADED = 1
# Steps a cold flow may run; heat steps only park once M109 is skipped.
COLD_STEPS = ("BOX_HEAT", "BOX_CUT", "BOX_UNLOAD", "BOX_EJECT")
# SHA-256 of QIDI's multi_color_controller.so builds whose external wipe
# returns False after its script runs.
EXTERNAL_WIPE_FALSE_BUILDS = frozenset((
    # Max 4 01.01.07.01, Plus 5 01.01.02.01
    "b7e3bc21156fb9df9f5eea07f2c596a17c7ea5db852a36139ba9773160e43592",
    # Smart 5 01.01.02.01
    "caee552ffb7734e7ef3fd3b861ef5bad6f22e2bdacdf34a586de21fdaf316370",
))
# The pull-back of an unload from the toolhead is its first two extruder
# pulls, 30 mm and then exl_2, in every traced build. Pulls after them may add up to
# EXTRA_PULL_MM while the toolhead sensor detects filament; QIDI's are 10 mm.
PULLBACK_MOVES = 2
EXTRA_PULL_MM = 20.
TOOLHEAD_SENSOR = "filament_switch_sensor filament_switch_sensor"
RETRACT_LINE = re.compile(r"G1 E-(\d+(?:\.\d+)?) F\d+(?:\.\d+)?$")
# QIDI's auto-feed abort puts a running feed's saved step clock back 10 ms
# after it; resetting the clock is only safe once that is done, in seconds.
# SET_LIMIT_A is box_autofeed's buffer signal to the Box.
FEED_ABORT_SETTLE = .1
# QIDI's box_autofeed log line for the Box buffer switch.
BUFFER_EVENT = "[MCB] limit_a_event: raw_state="
# Below this share of the pulls asked for, the extruder counts as not moved.
MOVED_SHARE = .5
# Raising the nozzle out of a cancelled part: a relative move just above the
# G-code's height, which exclude_object makes straight up, then back down.
LIFT_STATE = "_vo_cancelled_part_lift"
LIFT_SCRIPT = ("SAVE_GCODE_STATE NAME=%s\nG91\nG1 Z0.01 F1200\nG1 Z-0.01\n"
               "RESTORE_GCODE_STATE NAME=%s" % (LIFT_STATE, LIFT_STATE))
# SHA-256 of the source, line endings removed, of QIDI's power-loss resume
# methods this was written for: Max 4 01.01.06.05 and 01.01.07.01, Plus 5
# and Smart 5 01.01.02.01.
POWER_LOSS_RESUME = {
    "_set_printer_state_internal":
        "29ac1b6742add751d9afe4ec25ac93487ae9b58d0bc3ebce0f91ef5c0c9bd6ff",
    "cmd_SDCARD_POWER_OFF_CONTINUES_PINT_FILE":
        "b3485fd88961ac598812c08f673d938594739751ff8dc155aab6a125d00fde7f",
}
# Part labels and filament change markers in a print file, and how much of
# the file is read at a time looking back for them.
MARK_LINE = re.compile(
    rb"^[ \t]*(EXCLUDE_OBJECT_START|EXCLUDE_OBJECT_END|TOOL_CHANGE_START|"
    rb"TOOL_CHANGE_END)\b[^\n]*", re.M | re.I)
SCAN_CHUNK = 64 * 1024


def _slot_number(slot):
    if isinstance(slot, str) and slot.startswith("slot") \
            and slot[4:].isdigit():
        return int(slot[4:])
    return None


def source_hash(func):
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        return None
    return hashlib.sha256(source.replace("\r", "").encode()).hexdigest()


def marks_before(fd, pos, pause):
    """Read back from byte POS of the print file FD for the last part label
    before it. Returns that label's EXCLUDE_OBJECT_START line, or None when
    it is an EXCLUDE_OBJECT_END or there is none, and whether a filament
    change is open there. A line POS cuts counts as before it: the print
    restarts at its rest, which can't run as a command."""
    if pos <= 0:
        return None, False
    tail = os.pread(fd, SCAN_CHUNK, pos - 1)
    newline = tail.find(b"\n")
    hi = pos - 1 + (newline if newline >= 0 else len(tail))
    carry = b""
    filament_change = None
    while hi > 0:
        lo = max(0, hi - SCAN_CHUNK)
        data = os.pread(fd, hi - lo, lo) + carry
        if lo:
            # The chunk's first line may start in the one before it.
            cut = data.find(b"\n")
            if cut < 0:
                carry, hi = data, lo
                continue
            carry, data = data[:cut], data[cut + 1:]
        for match in reversed(list(MARK_LINE.finditer(data))):
            mark = match.group(1).upper()
            if filament_change is None:
                filament_change = mark == b"TOOL_CHANGE_START"
            if mark == b"EXCLUDE_OBJECT_START":
                return match.group(0).strip().decode("utf-8", "replace"), \
                    filament_change
            if mark == b"EXCLUDE_OBJECT_END":
                return None, filament_change
        hi = lo
        pause()
    return None, bool(filament_change)


def slot_label(slot):
    """Name a slot the way the QIDI Box card does: slot5 is 2B."""
    number = _slot_number(slot)
    if number is None or number >= 16:
        return "the external spool" if slot == EXTERNAL_SLOT else str(slot)
    return "%d%s" % (number // 4 + 1, "ABCD"[number % 4])


class _StopScripts:
    """QIDI's G-code object for the duration of a flow stop: the heater-off
    line is held back for the caller to decide, and the rest runs without
    taking the G-code lock when the caller already holds it."""

    def __init__(self, gcode, holds_lock):
        self._gcode = gcode
        self.holds_lock = holds_lock

    def _filter(self, script):
        if not isinstance(script, str):
            return script
        lines = [line for line in script.split("\n")
                 if line.strip().upper() != HEATER_OFF]
        return "\n".join(lines)

    def run_script(self, script):
        script = self._filter(script)
        if isinstance(script, str) and not script.strip():
            return None
        if self.holds_lock:
            return self._gcode.run_script_from_command(script)
        return self._gcode.run_script(script)

    def run_script_from_command(self, script):
        script = self._filter(script)
        if isinstance(script, str) and not script.strip():
            return None
        return self._gcode.run_script_from_command(script)

    def __getattr__(self, name):
        return getattr(self._gcode, name)


def _mm(value):
    return "%d" % value if value == int(value) else "%.1f" % value


def _add(total, start, end):
    """TOTAL plus START - END, or None once any of them is unknown."""
    if total is None or start is None or end is None:
        return None
    return total + start - end


class _BufferFeeds(logging.Handler):
    """Counts the QIDI Box buffer switch's feed triggers, which QIDI's
    box_autofeed logs as `[MCB] limit_a_event: raw_state=0 normalized=1`
    and its releases as `normalized=0`; every trigger makes the Box feed
    filament forward on its own."""

    def __init__(self):
        logging.Handler.__init__(self, logging.INFO)
        self.feeds = 0
        self.pressed = None

    def emit(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return
        if not message.startswith(BUFFER_EVENT):
            return
        if message.endswith("normalized=1"):
            self.feeds += 1
            self.pressed = True
        elif message.endswith("normalized=0"):
            self.pressed = False


class _UnloadWatch:
    """A Box slot's G-code object for one unload from the toolhead: it adds
    up the extruder's pulls, notes where the toolhead sensor cleared, and
    raises stop(watch) instead of a pull once the pulls after the pull-back
    reach EXTRA_PULL_MM with the sensor still detecting filament."""

    def __init__(self, gcode, toolhead_filament, extruder_position,
                 slot_position, stop):
        self._gcode = gcode
        self._toolhead_filament = toolhead_filament
        self._extruder_position = extruder_position
        self._slot_position = slot_position
        self._stop = stop
        self.moves = 0
        self.pullback = 0.
        self.extra = 0.
        # How far the extruder moved back for the pulls; None if unknown.
        self.moved = 0.
        # Where Klipper had the slot's motor at the first pull and after the
        # last script before the toolhead sensor cleared: its steps are made
        # when QIDI's M400 after a pull runs, not with the pull.
        self._slot_start = None
        self._slot_end = None
        self.cleared_at = None

    @property
    def pulled(self):
        return self.pullback + self.extra

    def _lines(self, script):
        if not isinstance(script, str):
            return []
        return [line.strip() for line in script.split("\n")]

    def _before(self, script):
        if self.moves < PULLBACK_MOVES or self.extra < EXTRA_PULL_MM:
            return
        if any(RETRACT_LINE.match(line) for line in self._lines(script)) \
                and self._toolhead_filament() is True:
            raise self._stop(self)

    def _run(self, run, script):
        self._before(script)
        retract = any(RETRACT_LINE.match(line)
                      for line in self._lines(script))
        start = self._extruder_position()
        if retract and self.moves == 0:
            self._slot_start = self._slot_position()
        result = run(script)
        if retract:
            self.moved = _add(self.moved, start, self._extruder_position())
        if self.moves and self.cleared_at is None:
            self._slot_end = self._slot_position()
        self._after(script)
        return result

    @property
    def slot_moved(self):
        """How far Klipper moved the slot's motor during the pulls, in mm
        as the slot counts them; None if unknown."""
        return _add(0., self._slot_end, self._slot_start)

    @property
    def skipped(self):
        """Whether Klipper didn't move the extruder for the pulls."""
        return self.moved is not None and self.pulled > 0. \
            and self.moved < self.pulled * MOVED_SHARE

    def _after(self, script):
        for line in self._lines(script):
            retract = RETRACT_LINE.match(line)
            if retract:
                if self.moves < PULLBACK_MOVES:
                    self.pullback += float(retract.group(1))
                else:
                    self.extra += float(retract.group(1))
                self.moves += 1
            elif line == "M400" and self.cleared_at is None \
                    and self._toolhead_filament() is False:
                self.cleared_at = self.pulled

    def run_script_from_command(self, script):
        return self._run(self._gcode.run_script_from_command, script)

    def run_script(self, script):
        return self._run(self._gcode.run_script, script)

    def __getattr__(self, name):
        return getattr(self._gcode, name)


def _buffer_feeds():
    """The one buffer feed counter on Python's root logger, kept across
    Klipper restarts, which reuse this module."""
    root = logging.getLogger()
    for handler in root.handlers:
        if type(handler).__name__ == "_BufferFeeds":
            return handler
    handler = _BufferFeeds()
    root.addHandler(handler)
    return handler


class BoxFlowRecovery:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.controller = None
        self.manager = None
        self.originals = {}
        # Greenlets running FLOW_STOP, which hold Klipper's G-code lock.
        self._locked = set()
        # A COLD=1 unload or eject is starting its flow.
        self._cold_requested = False
        # The flow a cold unload or eject left paused, for FLOW_RETRY.
        self._cold_task = None
        # QIDI's external wipe was checked, and wrapped if its build needs it.
        self._external_wipe_fixed = False
        # T_MANAGER is running a print's tool change.
        self._tool_change = False
        # Klipper's exclude_object and its own test for a cancelled part.
        self._exclude = None
        self._in_cancelled_part = None
        # Between TOOL_CHANGE_START and TOOL_CHANGE_END of a print.
        self._filament_change = False
        # The Box buffer's feed triggers, and their count at the last unload
        # from the toolhead.
        self._buffer = None
        self._feeds_at_unload = 0
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("virtual_sdcard:reset_file",
                                            self._end_filament_change)

    # ----------------------------------------------------------------- setup
    def _handle_ready(self):
        try:
            self._watch_toolhead_unloads()
        except Exception:
            logging.exception("vo_box_flow_recovery: watching unloads from "
                              "the toolhead failed")
        try:
            self._watch_cancelled_parts()
        except Exception:
            logging.exception("vo_box_flow_recovery: watching cancelled "
                              "parts failed")
        controller = self.printer.lookup_object("multi_color_controller", None)
        manager = getattr(controller, "task_queue_manager", None)
        if not callable(getattr(manager, "stop", None)) \
                or not hasattr(manager, "gcode") \
                or not hasattr(manager, "current_task"):
            logging.info("vo_box_flow_recovery: inactive: QIDI Box flow "
                         "controller not found")
            return
        self.controller = controller
        self.manager = manager
        manager.stop = self._wrap_stop(manager.stop)
        for name in ("start_flow", "retry"):
            if callable(getattr(manager, name, None)):
                setattr(manager, name,
                        self._wrap_flow_runner(getattr(manager, name), name))
        self._fix_external_wipe()
        for name, handler in (
                ("FLOW_STOP", self.cmd_FLOW_STOP),
                ("UNLOAD_FILAMENT_CONTROL", self.cmd_UNLOAD_FILAMENT_CONTROL),
                ("EJECT_FILAMENT_CONTROL", self.cmd_EJECT_FILAMENT_CONTROL),
                ("T_MANAGER", self.cmd_T_MANAGER)):
            self._wrap_command(name, handler)
        logging.info("vo_box_flow_recovery: active")

    def _wrap_command(self, name, handler):
        original = self.gcode.register_command(name, None)
        if original is None:
            logging.info("vo_box_flow_recovery: %s not found", name)
            return False
        self.originals[name] = original
        self.gcode.register_command(name, handler,
                                    desc=self.gcode.gcode_help.get(name))
        return True

    # ------------------------------------------------------------------ state
    def _print_state(self):
        print_stats = self.printer.lookup_object("print_stats", None)
        if print_stats is None:
            return None
        return print_stats.get_status(self.reactor.monotonic()).get("state")

    def _task(self):
        return getattr(self.manager, "current_task", None)

    def _flow_paused(self):
        task = self._task()
        return task is not None and bool(getattr(task, "is_paused", False))

    def _tracker(self):
        tracker = self.printer.lookup_object("vo_box_loaded_slot", None)
        return tracker if getattr(tracker, "slots", None) else None

    # ------------------------------------------------------- failed steps
    def _controller_build(self):
        module = sys.modules.get(type(self.controller).__module__)
        path = getattr(module, "__file__", None)
        if not path:
            return None
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def _fix_external_wipe(self):
        # QIDI's adapters fill method_map when Klipper is ready, possibly after
        # this module, so a flow start tries again.
        method_map = getattr(self.manager, "method_map", None)
        wipe = method_map.get("EXT_WIPE") if isinstance(method_map, dict) \
            else None
        if self._external_wipe_fixed or not callable(wipe):
            return
        self._external_wipe_fixed = True
        try:
            build = self._controller_build()
        except OSError:
            logging.exception("vo_box_flow_recovery: reading QIDI's flow "
                              "controller failed")
            return
        if build not in EXTERNAL_WIPE_FALSE_BUILDS:
            logging.info("vo_box_flow_recovery: external wipe left as "
                         "QIDI's (build %s)", build)
            return

        def external_wipe(*args, **kwargs):
            result = wipe(*args, **kwargs)
            return True if result is False else result
        method_map["EXT_WIPE"] = external_wipe
        logging.info("vo_box_flow_recovery: external wipe reports success "
                     "after its script runs")

    def _wrap_flow_runner(self, original, name):
        def run(*args, **kwargs):
            try:
                self._fix_external_wipe()
            except Exception:
                logging.exception("vo_box_flow_recovery: fixing the "
                                  "external wipe failed")
            try:
                self._absolute_positioning()
            except Exception:
                logging.exception("vo_box_flow_recovery: switching to "
                                  "absolute positioning failed")
            if name == "start_flow":
                cold = self._cold_requested and self._cold_flow(
                    args[0] if args else kwargs.get("flow_id"))
                self._cold_task = None
            else:
                cold = self._cold_task is not None \
                    and self._task() is self._cold_task
            try:
                if not cold:
                    return original(*args, **kwargs)
                self.manager.current_flow_temp = None
                with self._cold():
                    return original(*args, **kwargs)
            except Exception as e:
                try:
                    self._pause_after_error(e)
                except Exception:
                    logging.exception("vo_box_flow_recovery: pausing the "
                                      "flow after a step error failed")
                raise
            finally:
                # After a step error is paused, so its retry stays cold too.
                if cold:
                    task = self._task()
                    self._cold_task = task if getattr(
                        task, "is_paused", False) else None
        return run

    # ------------------------------------------- unload from the toolhead
    def _watch_toolhead_unloads(self):
        lookup_objects = getattr(self.printer, "lookup_objects", None)
        if not callable(lookup_objects):
            return
        if self._buffer is None:
            self._buffer = _buffer_feeds()
            self._feeds_at_unload = self._buffer.feeds
        watched = []
        for name, stepper in lookup_objects("box_stepper"):
            original = getattr(stepper, "extruder_unload_single_func", None)
            if not callable(original) or not hasattr(stepper, "gcode"):
                continue
            slot = name.split()[-1]
            stepper.extruder_unload_single_func = self._wrap_toolhead_unload(
                slot, stepper, original)
            watched.append(slot)
        if watched:
            logging.info("vo_box_flow_recovery: watching unloads from the "
                         "toolhead on %s", ", ".join(watched))

    def _extruder_position(self):
        """The toolhead's extruder position, which moves only with the
        extruder; None if unknown."""
        toolhead = self.printer.lookup_object("toolhead", None)
        get_position = getattr(toolhead, "get_position", None)
        if not callable(get_position):
            return None
        position = get_position()
        return position[3] if len(position) > 3 else None

    def _slot_position(self, box_stepper):
        """Where Klipper has moved the slot's motor to, in mm; None if
        unknown."""
        stepper = getattr(box_stepper, "stepper", None)
        try:
            return stepper.get_mcu_position() * stepper.get_step_dist()
        except Exception:
            return None

    def _feeds(self):
        return None if self._buffer is None else self._buffer.feeds

    def _toolhead_filament(self):
        """The toolhead sensor: True with filament, None if unknown."""
        sensor = self.printer.lookup_object(TOOLHEAD_SENSOR, None)
        # QIDI's runout helper starts at False but stores the sensor's 0 or 1
        # once it changes.
        present = getattr(getattr(sensor, "runout_helper", None),
                          "filament_present", None)
        return None if present is None else bool(present)

    def _hub_filament(self):
        """The Box hub sensor: True with filament, None if unknown."""
        extras = self.printer.lookup_object("box_extras", None)
        state = getattr(extras, "b_endstop_state", None)
        return None if state is None else not state

    def _wrap_toolhead_unload(self, slot, stepper, original):
        def extruder_unload_single_func(*args, **kwargs):
            real = stepper.gcode
            if isinstance(real, _UnloadWatch):
                return original(*args, **kwargs)
            watch = _UnloadWatch(
                real, self._toolhead_filament, self._extruder_position,
                lambda: self._slot_position(stepper),
                lambda watch: self._toolhead_stuck(slot, watch))
            before = (self._toolhead_filament(), self._hub_filament(),
                      self._feeds())
            stepper.gcode = watch
            unhook = self._reset_clock_before_sync(slot, stepper)
            outcome = "raised"
            try:
                result = original(*args, **kwargs)
                outcome = "returned %r" % (result,)
                return result
            except Exception as e:
                outcome = "raised %s: %s" % (type(e).__name__, e)
                raise
            finally:
                if stepper.gcode is watch:
                    stepper.gcode = real
                unhook()
                try:
                    self._log_unload(slot, before, watch, outcome)
                except Exception:
                    logging.exception("vo_box_flow_recovery: logging the "
                                      "unload failed")
        return extruder_unload_single_func

    def _reset_clock_before_sync(self, slot, box_stepper):
        """Reset the slot's step clock right before QIDI's unload ties the
        slot to the extruder; returns what undoes the hook."""
        extruder_stepper = getattr(box_stepper, "extruder_stepper", None)
        sync = getattr(extruder_stepper, "sync_to_extruder", None)
        mcu_stepper = getattr(extruder_stepper, "stepper", None)
        autofeed = self.printer.lookup_object("box_autofeed", None)
        abort = getattr(autofeed, "auto_abort", None)
        if not callable(sync) or not callable(abort) or not callable(
                getattr(mcu_stepper, "note_homing_end", None)):
            return lambda: None
        own = "sync_to_extruder" in vars(extruder_stepper)
        done = []

        def sync_to_extruder(extruder_name, *args, **kwargs):
            if extruder_name and not done:
                done.append(True)
                try:
                    self._reset_slot_clock(slot, abort, mcu_stepper)
                except Exception:
                    logging.exception("vo_box_flow_recovery: resetting %s's "
                                      "step clock failed", slot_label(slot))
            return sync(extruder_name, *args, **kwargs)
        extruder_stepper.sync_to_extruder = sync_to_extruder

        def unhook():
            if vars(extruder_stepper).get("sync_to_extruder") \
                    is not sync_to_extruder:
                return
            if own:
                extruder_stepper.sync_to_extruder = sync
            else:
                del extruder_stepper.sync_to_extruder
        return unhook

    def _reset_slot_clock(self, slot, abort, mcu_stepper):
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.wait_moves()
        # QIDI's unload aborts auto-feed just before this. An abort that
        # lands in the Box's pause between two feeds is ignored and the Box
        # starts the next feed itself while the buffer signal says to, so
        # that signal is cleared first, then auto-feed is aborted twice, each
        # time waiting for the Box to put a dropped feed's clock back. No
        # feed can be stepping the slot when its clock is reset.
        self.gcode.run_script_from_command("SET_LIMIT_A STATE=0")
        for _ in range(2):
            abort()
            self.reactor.pause(self.reactor.monotonic() + FEED_ABORT_SETTLE)
        toolhead.flush_step_generation()
        mcu_stepper.note_homing_end()
        logging.info("vo_box_flow_recovery: %s step clock reset before the "
                     "unload's pull-back", slot_label(slot))

    def _log_unload(self, slot, before, watch, outcome):
        def state(value):
            return {True: "filament", False: "clear"}.get(value, "unknown")
        after = (self._toolhead_filament(), self._hub_filament())
        cleared = "" if watch.cleared_at is None else \
            ", cleared after %s mm" % (_mm(watch.cleared_at),)
        def mm(value):
            return "unknown" if value is None \
                else "%s mm" % (_mm(round(value, 1)),)
        feeds = self._feeds()
        if feeds is None or before[2] is None:
            buffer = "unknown"
        else:
            buffer = ("%d feeds before it since the last unload from the "
                      "toolhead, %d during it, switch %s"
                      % (before[2] - self._feeds_at_unload, feeds - before[2],
                         {True: "pressed", False: "released"}.get(
                             self._buffer.pressed, "not seen yet")))
            self._feeds_at_unload = feeds
        logging.info(
            "vo_box_flow_recovery: %s unload from the toolhead: toolhead "
            "sensor %s -> %s%s; hub sensor %s -> %s; extruder pulled back %s "
            "mm, %s of them after its pull-back, and moved %s; slot motor "
            "moved %s during the pulls; Box buffer %s; %s",
            slot_label(slot), state(before[0]), state(after[0]), cleared,
            state(before[1]), state(after[1]), _mm(watch.pulled), _mm(watch.extra),
            mm(watch.moved), mm(watch.slot_moved), buffer, outcome)

    def _toolhead_stuck(self, slot, watch):
        if watch.skipped:
            part = self._cancelled_part()
            message = (
                "Box unload of %s stopped: Klipper didn't move the extruder "
                "for the %s mm of pull-back QIDI's unload asked for%s, so the "
                "toolhead filament sensor still detects filament and the Box "
                "hasn't pulled it. Check that the extruder turns"
                % (slot_label(slot), _mm(watch.pulled),
                   "" if part is None else
                   " because the print is inside %s, a cancelled part, where "
                   "Klipper drops every move" % (part,)))
        else:
            message = (
                "Box unload of %s stopped: the toolhead filament sensor still "
                "detects filament after the extruder pulled it back %s mm, %s "
                "mm more than its %s mm pull-back. Something is holding the "
                "filament, at the toolhead, such as a cutter blade that hasn't "
                "sprung back or a swollen cut end, or in the Box, such as a "
                "slot feeder that won't turn, so the Box hasn't pulled it, and "
                "QIDI's further pulls would only grind it. Free the filament"
                % (slot_label(slot), _mm(watch.pulled), _mm(watch.extra),
                   _mm(watch.pullback)))
        # A print's tool change says how to resume after the message.
        if not self._tool_change:
            if self._task() is not None:
                message += (", then FLOW_RETRY tries the unload again, or "
                            "FLOW_STOP ends it.")
            else:
                message += ", then unload again."
        logging.warning("vo_box_flow_recovery: %s", message)
        return self.printer.command_error(message)

    # ------------------------------------------------------ cancelled parts
    def _watch_cancelled_parts(self):
        exclude = self.printer.lookup_object("exclude_object", None)
        in_cancelled_part = getattr(exclude, "_test_in_excluded_region", None)
        if not callable(in_cancelled_part):
            logging.info("vo_box_flow_recovery: cancelled parts not "
                         "watched: Klipper's exclude_object not found")
            return
        self._exclude = exclude
        self._in_cancelled_part = in_cancelled_part

        def drops_moves():
            return in_cancelled_part() \
                and not self._moves_through_cancelled_parts()
        exclude._test_in_excluded_region = drops_moves
        for name, handler in (
                ("EXCLUDE_OBJECT_START", self.cmd_EXCLUDE_OBJECT_START),
                ("EXCLUDE_OBJECT_END", self.cmd_EXCLUDE_OBJECT_END),
                ("TOOL_CHANGE_START", self.cmd_TOOL_CHANGE_START),
                ("TOOL_CHANGE_END", self.cmd_TOOL_CHANGE_END)):
            self._wrap_command(name, handler)
        logging.info("vo_box_flow_recovery: moves go through cancelled "
                     "parts during filament changes and pauses")
        self._watch_power_loss_resume()

    def _watch_power_loss_resume(self):
        sdcard = self.printer.lookup_object("virtual_sdcard", None)
        set_state = getattr(sdcard, "_set_printer_state_internal", None)
        if not callable(set_state):
            return
        for name, expected in POWER_LOSS_RESUME.items():
            if source_hash(getattr(type(sdcard), name, None)) != expected:
                logging.info("tltg_box_flow_recovery: power-loss resume left "
                             "as QIDI's: its %s differs from the version "
                             "this was written for", name)
                return

        def set_printer_state(*args, **kwargs):
            set_state(*args, **kwargs)
            restore = getattr(sdcard, "_printer_off_power_resume", None)
            if callable(restore):
                sdcard._printer_off_power_resume = \
                    lambda: self._resume_after_power_loss(sdcard, restore)
        set_printer_state.__wrapped__ = set_state
        sdcard._set_printer_state_internal = set_printer_state
        logging.info("tltg_box_flow_recovery: power-loss resume keeps "
                     "cancelled parts skipped")

    def _resume_after_power_loss(self, sdcard, restore):
        """Run QIDI's restore, then put the print back inside the part and
        filament change its file restarts in, when parts were cancelled."""
        restore()
        cancelled = self._exclude.get_status().get("excluded_objects")
        if not cancelled:
            return
        pos = sdcard.file_position
        try:
            part, filament_change = marks_before(
                sdcard.current_file.fileno(), pos,
                lambda: self.reactor.pause(self.reactor.monotonic()))
        except (AttributeError, OSError, ValueError):
            logging.exception("tltg_box_flow_recovery: finding the part a "
                              "power-loss resume restarts in failed")
            return
        logging.info("tltg_box_flow_recovery: power-loss resume restarts at "
                     "byte %d %s%s; cancelled: %s", pos,
                     "after %s" % (part,) if part else "outside every part",
                     ", inside a filament change" if filament_change else "",
                     ", ".join(cancelled))
        if part is not None:
            self._learn_extruder_position()
            self.gcode.run_script_from_command(part)
        if filament_change:
            self._filament_change = True

    def _learn_extruder_position(self):
        """exclude_object lets the first 5 extrusions through once it starts
        watching, to learn where the extruder is, and QIDI's resume starts it
        again. The resume has put the extruder back, so take it as learned
        before a cancelled part's moves would print."""
        exclude = self._exclude
        if getattr(exclude, "initial_extrusion_moves", 0) > 0 \
                and getattr(exclude, "next_transform", None) is not None:
            exclude.max_position_extruded = max(
                exclude.max_position_extruded,
                exclude.last_position_extruded[3])
            exclude.initial_extrusion_moves = 0

    def _moves_through_cancelled_parts(self):
        return self._filament_change or self._tool_change \
            or self._print_state() == "paused"

    def _drops_moves(self):
        """Whether a move now would be dropped in a cancelled part."""
        return self._exclude is not None \
            and bool(self._exclude._test_in_excluded_region())

    def _cancelled_part(self):
        """The cancelled part the print is inside, or None."""
        status = getattr(self._exclude, "get_status", None)
        if not callable(status):
            return None
        status = status()
        current = status.get("current_object")
        return current if current is not None \
            and current in (status.get("excluded_objects") or ()) else None

    def _leave_cancelled_part(self):
        """Raise the nozzle straight up to the G-code's height, from where
        the moves dropped in a cancelled part left it."""
        try:
            self.gcode.run_script_from_command(LIFT_SCRIPT)
        except Exception:
            logging.exception("vo_box_flow_recovery: raising the nozzle "
                              "out of a cancelled part failed")
            try:
                self.gcode.run_script_from_command(
                    "RESTORE_GCODE_STATE NAME=%s" % (LIFT_STATE,))
            except Exception:
                logging.exception("vo_box_flow_recovery: restoring the "
                                  "G-code state failed")

    def _start_moving(self, flag):
        """Set FLAG so moves go through cancelled parts, raising the nozzle
        first if moves were being dropped."""
        part = self._cancelled_part() if self._drops_moves() else None
        setattr(self, flag, True)
        if part is not None:
            logging.info("vo_box_flow_recovery: filament change inside "
                         "cancelled part %s; its moves go through until it "
                         "ends", part)
            self._leave_cancelled_part()

    def _end_filament_change(self):
        self._filament_change = False

    def cmd_EXCLUDE_OBJECT_START(self, gcmd):
        self._end_filament_change()
        return self.originals["EXCLUDE_OBJECT_START"](gcmd)

    def cmd_EXCLUDE_OBJECT_END(self, gcmd):
        self._end_filament_change()
        return self.originals["EXCLUDE_OBJECT_END"](gcmd)

    def cmd_TOOL_CHANGE_START(self, gcmd):
        if self._print_state() in ("printing", "paused"):
            self._start_moving("_filament_change")
        return self.originals["TOOL_CHANGE_START"](gcmd)

    def cmd_TOOL_CHANGE_END(self, gcmd):
        try:
            return self.originals["TOOL_CHANGE_END"](gcmd)
        finally:
            self._end_filament_change()

    # ------------------------------------------------------------- cold flows
    def _cold_flow(self, flow_id):
        """Whether flow FLOW_ID can run cold; says why not."""
        steps = (getattr(self.manager, "FLOW_MAP", None) or {}).get(flow_id)
        if steps is None:
            raise self.printer.command_error(
                "Box flow %s not run: COLD=1 doesn't recognize it. Run the "
                "command without COLD=1 to heat the nozzle." % (flow_id,))
        others = [step for step in steps if step not in COLD_STEPS]
        if others:
            message = ("Box flow %s heats the nozzle: COLD=1 covers only Box "
                       "unload and eject steps, not %s."
                       % (flow_id, ", ".join(others)))
            logging.info("vo_box_flow_recovery: %s", message)
            self.gcode.respond_info(message)
            return False
        logging.info("vo_box_flow_recovery: flow %s runs cold: %s",
                     flow_id, ", ".join(steps) or "no steps")
        return True

    @contextlib.contextmanager
    def _cold(self):
        """Cold retracts allowed and M109 skipped."""
        cold_cut = self.printer.lookup_object("vo_cold_cut", None)
        with cold_cut.cold_retracts():
            heat = self.gcode.register_command("M109", None)

            def skip_heat(gcmd):
                logging.info("vo_box_flow_recovery: cold Box flow skipped "
                             "%s", gcmd.get_commandline())

            if heat is not None:
                self.gcode.register_command("M109", skip_heat)
            try:
                yield
            finally:
                if heat is not None:
                    self.gcode.register_command("M109", None)
                    self.gcode.register_command(
                        "M109", heat, desc=self.gcode.gcode_help.get("M109"))

    def _absolute_positioning(self):
        if self._print_state() in ("printing", "paused"):
            return
        gcode_move = self.printer.lookup_object("gcode_move", None)
        if gcode_move is None or getattr(gcode_move, "absolute_coord", True):
            return
        gcode_move.absolute_coord = True
        logging.info("vo_box_flow_recovery: relative positioning was on; "
                     "switched to absolute for the Box flow")

    def _pause_after_error(self, error):
        task = self._task()
        step = getattr(task, "current_step", None)
        if task is None or not step or self.printer.is_shutdown() \
                or getattr(task, "is_paused", False) \
                or getattr(task, "is_cancelled", False):
            return
        task.is_paused = True
        logging.info("vo_box_flow_recovery: step %s raised %s: %s; flow "
                     "paused", step, type(error).__name__, error)
        # QIDI's own words for a failed step, which Fluidd's Box card reads.
        self.gcode.respond_info("Step %s failed. Pausing flow." % (step,))

    # -------------------------------------------------------------- flow stop
    def _wrap_stop(self, original):
        def stop(*args, **kwargs):
            had_task = self._task() is not None
            state = self._print_state()
            holds_lock = greenlet.getcurrent() in self._locked
            real = self.manager.gcode
            scripts = _StopScripts(real, holds_lock)
            self.manager.gcode = scripts
            try:
                result = original(*args, **kwargs)
            finally:
                if self.manager.gcode is scripts:
                    self.manager.gcode = real
            if had_task:
                try:
                    self._after_stop(scripts, state)
                except Exception:
                    logging.exception("vo_box_flow_recovery: heater "
                                      "handling after a flow stop failed")
            return result
        return stop

    def _after_stop(self, scripts, state):
        if state in ("printing", "paused"):
            message = ("Box flow stopped. The nozzle stays hot because a "
                       "print is %s." % (state,))
        else:
            if scripts.holds_lock:
                self.gcode.run_script_from_command(HEATER_OFF)
            else:
                self.gcode.run_script(HEATER_OFF)
            message = "Box flow stopped. Nozzle heater off."
        logging.info("vo_box_flow_recovery: %s", message)
        self.gcode.respond_info(message)

    def _stop_from_command(self):
        """Stop QIDI's current flow from inside a G-code command."""
        current = greenlet.getcurrent()
        self._locked.add(current)
        try:
            self.manager.stop()
        finally:
            self._locked.discard(current)

    def cmd_FLOW_STOP(self, gcmd):
        if self._task() is None:
            gcmd.respond_info("No Box flow is running or paused, so there's "
                              "nothing to stop.")
        current = greenlet.getcurrent()
        self._locked.add(current)
        try:
            return self.originals["FLOW_STOP"](gcmd)
        finally:
            self._locked.discard(current)

    # ------------------------------------------------------------ tool change
    def cmd_T_MANAGER(self, gcmd):
        if self._print_state() in ("printing", "paused"):
            try:
                self._start_moving("_tool_change")
                return self.originals["T_MANAGER"](gcmd)
            except Exception as e:
                if not self._pause_print_after_error(gcmd, e):
                    raise
                return None
            finally:
                self._tool_change = False
        message = ("T%s not run: QIDI's tool change runs only during a print. "
                   "To load a slot, use Load on the QIDI Box card or "
                   "LOAD_FILAMENT_CONTROL SLOT=<n>."
                   % (gcmd.get("T", ""),))
        logging.info("vo_box_flow_recovery: %s", message)
        gcmd.respond_info(message)
        return None

    def _ran_out_slot(self, error):
        """The slot that ran out when QIDI's tool change hit its auto-reload
        bug, or None for any other error.

        When the loaded slot's inlet is empty and the toolhead sensor still
        detects filament, QIDI's T_MANAGER (box_extras
        filament_printing_manager_box1 on the Max 4 01.01.07.01 and the Plus 5
        and Smart 5 01.01.02.01) purges the rest and then
        calls loaded_stepper.loaded_stepper.switch_next_slot() on the slot
        itself, which raises AttributeError before any flow step runs.
        """
        if not isinstance(error, AttributeError) \
                or "'loaded_stepper'" not in str(error):
            return None
        save_vars = self.printer.lookup_object("save_variables", None)
        variables = getattr(save_vars, "allVariables", None) or {}
        slot = variables.get("last_load_slot")
        return slot if _slot_number(slot) is not None \
            and slot != EXTERNAL_SLOT else None

    def _pause_print_after_error(self, gcmd, error):
        """Pause the print after QIDI's print-time tool change raised, as
        QIDI does after a step that fails; False to let the error end it."""
        if self.printer.is_shutdown():
            return False
        task = self._task()
        step = getattr(task, "current_step", None)
        if task is not None and step \
                and not getattr(task, "is_paused", False) \
                and not getattr(task, "is_cancelled", False):
            extras = self.printer.lookup_object("box_extras", None)
            pause_flow = getattr(extras, "_pause_print_retry_flow", None)
            try:
                if callable(pause_flow):
                    pause_flow(self.manager, step)
                else:
                    task.is_paused = True
            except Exception:
                logging.exception("vo_box_flow_recovery: pausing the tool "
                                  "change flow failed")
        ran_out = self._ran_out_slot(error)
        if ran_out is not None:
            message = (
                "T%s paused the print: %s ran out of filament. QIDI purged "
                "what was left of it at the waste chute, then a bug in "
                "QIDI's Box code stopped the tool change before it could "
                "switch to another slot with the same filament. RESUME loads "
                "T%s's slot and carries on printing. Put filament in %s "
                "before the print needs it again, or the print pauses there."
                % (gcmd.get("T", ""), slot_label(ran_out), gcmd.get("T", ""),
                   slot_label(ran_out)))
        else:
            message = ("T%s failed%s: %s. The print is paused; RESUME tries "
                       "the tool change again."
                       % (gcmd.get("T", ""),
                          " at %s" % (step,) if step else "",
                          str(error).rstrip(".")))
        logging.warning("vo_box_flow_recovery: %s (%s: %s)", message,
                        type(error).__name__, error)
        self.gcode.respond_raw("!! " + message)
        if self._print_state() == "printing":
            self.gcode.run_script_from_command("PAUSE")
        return True

    # --------------------------------------------------- unload and eject
    def cmd_UNLOAD_FILAMENT_CONTROL(self, gcmd):
        self._filament_command("UNLOAD", gcmd)

    def cmd_EJECT_FILAMENT_CONTROL(self, gcmd):
        self._filament_command("EJECT", gcmd)

    def _filament_command(self, action, gcmd):
        original = self.originals[action + "_FILAMENT_CONTROL"]
        cold = gcmd.get_int("COLD", 0, minval=0, maxval=1)
        if cold:
            state = self._print_state()
            if state in ("printing", "paused"):
                raise gcmd.error("Box %s not run: COLD=1 runs only while no "
                                 "print is running or paused, and a print is "
                                 "%s." % (action.lower(), state))
            if self.printer.lookup_object("vo_cold_cut", None) is None:
                raise gcmd.error("Box %s not run: COLD=1 needs "
                                 "vo_cold_cut." % (action.lower(),))
        try:
            plan = self._plan(action, gcmd)
        except Exception:
            logging.exception("vo_box_flow_recovery: %s check failed",
                              action.lower())
            plan = None
        if plan is None:
            if not cold:
                return original(gcmd)
            self._cold_requested = True
            try:
                return original(gcmd)
            finally:
                self._cold_requested = False
        kind, slot, detail = plan
        if kind == "refuse":
            self._refuse(action, slot, detail)
            # QIDI's command would have replaced a paused flow; end it so
            # the nozzle cools and the slot can be jogged.
            if self._flow_paused() \
                    and self._print_state() not in ("printing", "paused"):
                self._stop_from_command()
            return None
        self._retract(action, slot, detail, gcmd, original)
        return None

    def _plan(self, action, gcmd):
        """None to run QIDI's command, ("refuse", slot, reason), or
        ("retract", slot, reason)."""
        adapter = getattr(self.controller, "box_adapter", None)
        tracker = self._tracker()
        if type(adapter).__name__ != "BoxAdapterV1" or tracker is None:
            return None
        number = gcmd.get_int("SLOT", None)
        slot = "slot%d" % number if number is not None else None
        if slot not in tracker.slots:
            return None
        task = self._task()
        if task is not None and not self._flow_paused():
            return None
        toolhead = tracker.toolhead_filament()
        hub = tracker.hub_occupied()
        if toolhead is not False or hub is None:
            return None
        state = self._print_state()
        if not hub:
            if action != "UNLOAD":
                return None
            # QIDI runs no steps here, then parks and reports success.
            return ("refuse", slot,
                    "%s's filament isn't fed past the Box hub, so there's "
                    "nothing to unload. It's already in the Box; Eject "
                    "removes it." % slot_label(slot))
        if state in ("printing", "paused"):
            if action != "UNLOAD":
                return None
            return ("refuse", slot,
                    "filament is stopped between the Box hub and the "
                    "toolhead, but a print is %s. RESUME tries the load "
                    "again, or cancel the print and then unload." % state)
        hub_slot, reason = self._hub_slot(tracker)
        if hub_slot is None:
            return ("refuse", slot, reason)
        if hub_slot != slot:
            return ("refuse", slot,
                    "the filament stopped between the Box hub and the "
                    "toolhead is %s's (%s). %s %s first."
                    % (slot_label(hub_slot), reason,
                       "Unload" if action == "UNLOAD" else "Unload or eject",
                       slot_label(hub_slot)))
        return ("retract", slot, reason)

    def _hub_slot(self, tracker):
        """The slot whose filament is at the hub, and why; or None and why
        it can't be told."""
        variables = tracker.variables()
        inlets = tracker.inlets()
        with_filament = sorted((s for s, present in inlets.items()
                                if present), key=_slot_number)
        recorded = variables.get(HUB_SLOT_VARIABLE, "")
        last = variables.get("last_load_slot", EXTERNAL_SLOT)
        named = []
        for slot, why in ((recorded, "the last slot to feed filament to "
                                     "the hub"),
                          (last, "the slot QIDI last loaded")):
            if inlets.get(slot) and slot not in [s for s, _ in named]:
                named.append((slot, why))
        if len(named) == 1:
            return named[0]
        listed = ", ".join(slot_label(s) for s in with_filament) or "none"
        if len(named) > 1:
            return (None, "the filament at the Box hub could be %s's or "
                          "%s's. Pull the wrong one back with "
                          "VO_BOX_SLOT_JOG, or remove the filament by hand"
                    % (slot_label(named[0][0]), slot_label(named[1][0])))
        if len(with_filament) == 1 and None not in inlets.values():
            return with_filament[0], "the only slot with filament"
        return (None, "no slot is recorded as feeding the filament at the "
                      "Box hub. Slots with filament: %s. Follow the "
                      "filament back to its slot and pull it back with "
                      "VO_BOX_SLOT_JOG SLOT=<n> DISTANCE=-100" % listed)

    def _refuse(self, action, slot, reason):
        verb = "unload" if action == "UNLOAD" else "eject"
        message = "Box %s of %s not run: %s" % (verb, slot_label(slot),
                                                reason)
        if not message.endswith("."):
            message += "."
        logging.info("vo_box_flow_recovery: %s", message)
        self.gcode.respond_info(message)

    def _retract(self, action, slot, reason, gcmd, original):
        tracker = self._tracker()
        jog = self.printer.lookup_object("vo_box_slot_jog", None)
        number = _slot_number(slot)
        label = slot_label(slot)
        if jog is None:
            raise gcmd.error("Box %s of %s not run: vo_box_slot_jog is "
                             "missing." % (action.lower(), label))
        if self._flow_paused():
            self._stop_from_command()
        blocker = jog.blocker()
        if blocker is not None:
            raise gcmd.error("Box %s of %s not run: %s."
                             % (action.lower(), label, blocker))
        message = ("Box: %s's filament is stopped between the Box hub and "
                   "the toolhead (%s). Pulling it back out of the hub."
                   % (label, reason))
        logging.info("vo_box_flow_recovery: %s", message)
        gcmd.respond_info(message)

        def hub_clear_or_inlet_empty():
            return tracker.hub_occupied() is False \
                or tracker.inlets().get(slot) is False

        moved, _ = jog.move(number, -MAX_HUB_RETRACT, RETRACT_SPEED,
                            until=hub_clear_or_inlet_empty,
                            piece=RETRACT_PIECE)
        hub = tracker.hub_occupied()
        inlet = tracker.inlets().get(slot)
        logging.info("vo_box_flow_recovery: %s retracted %.0f mm, hub "
                     "filament=%r inlet filament=%r", slot, moved, hub, inlet)
        if hub is not False:
            if inlet is False:
                raise gcmd.error(
                    "%s's filament left its inlet sensor after %.0f mm while "
                    "the Box hub still detects filament, so the filament at "
                    "the hub isn't %s's, or it broke. Remove it by hand. "
                    "%s's filament is out of its feeder; push it back into "
                    "the inlet to load it again."
                    % (label, abs(moved), label, label))
            raise gcmd.error(
                "Pulled %s back %.0f mm, but the Box hub still detects "
                "filament. Check the tube between the hub and the toolhead "
                "and remove the filament by hand." % (label, abs(moved)))
        variables = tracker.variables()
        if variables.get("last_load_slot") == slot:
            tracker.save("last_load_slot", EXTERNAL_SLOT)
        if action == "EJECT":
            gcmd.respond_info("Box: %s's filament is clear of the hub. "
                              "Ejecting it." % (label,))
            return original(gcmd)
        parked, _ = jog.move(number, -PARK_DISTANCE, RETRACT_SPEED,
                             until=lambda: tracker.inlets().get(slot)
                             is False,
                             piece=RETRACT_PIECE)
        if tracker.inlets().get(slot) is False:
            raise gcmd.error(
                "%s's filament left its inlet sensor %.0f mm after the Box "
                "hub cleared, so it's out of the feeder. Push it back into "
                "the inlet to load it again." % (label, abs(parked)))
        tracker.save(slot, SLOT_STATE_LOADED)
        message = ("Box: unloaded %s. Its filament is parked in the Box."
                   % (label,))
        logging.info("vo_box_flow_recovery: %s", message)
        gcmd.respond_info(message)
        return None


def load_config(config):
    return BoxFlowRecovery(config)
