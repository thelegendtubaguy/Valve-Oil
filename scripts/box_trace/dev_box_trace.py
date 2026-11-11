# Log-only tracer for QIDI's Box code, for development. It changes no motion:
# installed with scripts/box_trace/box_trace.sh, it wraps QIDI's Box objects
# at klippy:ready and writes ~/printer_data/logs/dev_box_trace.log with
#
# - every public method call on QIDI's Box objects, with arguments, result,
#   and duration (rate-limited per method so polling stays quiet)
# - every box_extras.get_value_by_key lookup and its answer
# - every G-code script run from commands, and every console response
# - 10 Hz samples of each Box slot motor, the extruder, and the toolhead,
#   written only while something moves
# - changes in filament switches and in each box_extras status field

import json
import logging
import os
import time

LOG = os.path.expanduser("~/printer_data/logs/dev_box_trace.log")
BOX_PREFIXES = ("box_extras", "box_stepper", "box_autofeed", "box_detect",
                "multi_color_controller", "box_config", "box_heater_fan")
SKIP_METHODS = ("get_status", "get_value_by_key")
# Called continuously while idle (status building, fan callbacks); run muted.
MUTED_METHODS = ("_safe_int", "_get_saved_slot_filament_data",
                 "_clone_filament_info", "_build_filament_info",
                 "_build_empty_box_status", "_slot_label",
                 "_refresh_boxes_object_cache", "_build_boxes_status",
                 "_build_auto_refill", "callback",
                 "_sync_limit_to_active_dev", "_normalize_limit_state",
                 "_get_json_filament_info_item")
RATE_LIMIT = 10  # logged calls per method per second
SAMPLE_INTERVAL = 0.1
STATUS_INTERVAL = 0.5


def _fmt(value):
    try:
        names = [name for _endstop, name in value]
        if names and all(isinstance(n, str) for n in names):
            return "endstops%s" % (names,)
    except Exception:
        pass
    text = repr(value)
    if "object at 0x" in text or len(text) > 300:
        return "<%s>" % (type(value).__name__,)
    return text


class BoxTrace:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.handle = open(LOG, "a", buffering=1)
        self.depth = 0
        self.quiet = 0
        self.rate = {}
        self.motors = []
        self.last_positions = None
        self.last_status = {}
        self.idle_samples = 0
        self.printer.register_event_handler("klippy:ready", self._ready)
        self._write("---- klippy start")

    def _write(self, line):
        now = time.time()
        self.handle.write("%.3f %s.%03d %s\n" % (
            now, time.strftime("%H:%M:%S", time.localtime(now)),
            int(now * 1000) % 1000, line))

    # ------------------------------------------------------------ wrapping
    def _should_log(self, label):
        now = time.time()
        window, count, dropped = self.rate.get(label, (now, 0, 0))
        if now - window >= 1.0:
            if dropped:
                self._write("  (%s: %d more calls not logged)"
                            % (label, dropped))
            window, count, dropped = now, 0, 0
        count += 1
        ok = count <= RATE_LIMIT
        if not ok:
            dropped += 1
        self.rate[label] = (window, count, dropped)
        return ok

    def _wrap(self, label, original):
        def traced(*args, **kwargs):
            log = not self.quiet and self._should_log(label)
            pad = "  " * self.depth
            if log:
                shown = ", ".join([_fmt(a) for a in args]
                                  + ["%s=%s" % (k, _fmt(v))
                                     for k, v in kwargs.items()])
                self._write("%s> %s(%s)" % (pad, label, shown))
            start = time.time()
            self.depth += 1
            try:
                result = original(*args, **kwargs)
            except BaseException as exc:
                self.depth = max(0, self.depth - 1)
                self._write("%s< %s raised %r after %.3fs"
                            % (pad, label, exc, time.time() - start))
                raise
            self.depth = max(0, self.depth - 1)
            if log:
                self._write("%s< %s -> %s after %.3fs"
                            % (pad, label, _fmt(result), time.time() - start))
            return result
        return traced

    def _mute(self, original):
        def muted(*args, **kwargs):
            self.quiet += 1
            try:
                return original(*args, **kwargs)
            finally:
                self.quiet -= 1
        return muted

    def _wrap_object(self, objname, obj):
        getter = getattr(obj, "get_status", None)
        if callable(getter):
            try:
                obj.get_status = self._mute(getter)
            except Exception as exc:
                self._write("cannot mute %s.get_status: %r" % (objname, exc))
        wrapped = []
        for method in dir(obj):
            if method.startswith("__") or method in SKIP_METHODS:
                continue
            try:
                original = getattr(obj, method)
            except Exception:
                continue
            # Only bound methods of this object. cffi pointers (trapq, stepper
            # kinematics) report callable() too and must never be replaced.
            kind = type(original).__name__
            bound = kind in ("method", "builtin_function_or_method") \
                and getattr(original, "__self__", None) is obj
            if not (bound or kind == "function"):
                continue
            if method in MUTED_METHODS:
                try:
                    setattr(obj, method, self._mute(original))
                except Exception:
                    pass
                continue
            try:
                setattr(obj, method,
                        self._wrap("%s.%s" % (objname, method), original))
                wrapped.append(method)
            except Exception as exc:
                self._write("cannot wrap %s.%s: %r" % (objname, method, exc))
        return wrapped

    # ------------------------------------------------------------- setup
    def _ready(self):
        try:
            self._setup()
        except Exception:
            logging.exception("dev_box_trace: setup failed")
            self._write("setup failed; see klippy.log")

    def _setup(self):
        extras = self.printer.lookup_object("box_extras", None)
        lookup = getattr(extras, "get_value_by_key", None)
        if callable(lookup):
            def get_value_by_key(*args, **kwargs):
                value = lookup(*args, **kwargs)
                if not self.quiet:
                        self._write("%s  key %s -> %s" % (
                            "  " * self.depth, _fmt(args), _fmt(value)))
                return value
            extras.get_value_by_key = get_value_by_key

        gcode = self.printer.lookup_object("gcode")
        run = gcode.run_script_from_command

        def run_script_from_command(script):
            self._write("%s  gcode %r" % ("  " * self.depth, script))
            return run(script)
        gcode.run_script_from_command = run_script_from_command
        gcode.register_output_handler(
            lambda msg: self._write("  response %r" % (msg,)))

        for name, obj in self.printer.lookup_objects():
            if not name.startswith(BOX_PREFIXES):
                continue
            short = name.replace("box_stepper ", "")
            attrs = {}
            for attr in dir(obj):
                if attr.startswith("__"):
                    continue
                try:
                    value = getattr(obj, attr)
                except Exception:
                    continue
                if not callable(value):
                    attrs[attr] = _fmt(value)
                if hasattr(value, "get_mcu_position") and name.startswith(
                        "box_stepper"):
                    self.motors.append(("%s.%s" % (short, attr), value))
            self._write("object %s attrs %s" % (name, json.dumps(attrs)))
            wrapped = self._wrap_object(short, obj)
            self._write("object %s wrapped %s" % (name, wrapped))
        self._write("motors sampled: %s" % ([m for m, _ in self.motors],))
        self.reactor.register_timer(self._sample, self.reactor.NOW)
        self.reactor.register_timer(self._status, self.reactor.NOW)
        self._write("ready")

    # ----------------------------------------------------------- sampling
    def _positions(self):
        values = {}
        for label, stepper in self.motors:
            try:
                values[label] = round(stepper.get_commanded_position(), 2)
            except Exception:
                try:
                    values[label] = stepper.get_mcu_position()
                except Exception:
                    pass
        toolhead = self.printer.lookup_object("toolhead")
        pos = toolhead.get_position()
        values["toolhead"] = [round(v, 2) for v in pos[:3]]
        values["extruder_e"] = round(pos[3], 2)
        return values

    def _sample(self, eventtime):
        try:
            values = self._positions()
            if values != self.last_positions:
                self.idle_samples = 0
                self._write("  pos %s" % (json.dumps(values),))
            self.last_positions = values
        except Exception as exc:
            self._write("sample failed: %r" % (exc,))
            return self.reactor.NEVER
        return eventtime + SAMPLE_INTERVAL

    def _status(self, eventtime):
        try:
            current = {}
            for name, obj in self.printer.lookup_objects():
                if name.startswith(("filament_switch_sensor",
                                    "filament_motion_sensor",
                                    "box_autofeed", "box_detect")):
                    getter = getattr(obj, "get_status", None)
                    if callable(getter):
                        current[name] = getter(eventtime)
            extras = self.printer.lookup_object("box_extras", None)
            if extras is not None:
                for key, value in extras.get_status(eventtime).items():
                    current["box_extras." + key] = value
            for key, value in current.items():
                text = json.dumps(value, default=str, sort_keys=True)
                if self.last_status.get(key) != text:
                    self._write("  status %s %s" % (key, text[:600]))
                    self.last_status[key] = text
        except Exception as exc:
            self._write("status failed: %r" % (exc,))
            return self.reactor.NEVER
        return eventtime + STATUS_INTERVAL


def load_config(config):
    return BoxTrace(config)
