# Bed soak test for Valve Oil.
#
# Measures how long the printer takes to be ready to print at a bed and
# chamber temperature: it sets up as print start does, measures one full-bed
# mesh with the bed and chamber heaters off, then heats them and measures
# meshes until the bed stops moving or a time limit. Each mesh's height and
# shape change against the cold mesh and its speed of change are reported
# live in printer.tltg_bed_soak, and the run is saved as a JSON Lines file
# under the config folder's tltg_bed_soak folder.
#
#   TLTG_BED_SOAK BED=100 [CHAMBER=60] [PROBE_COUNT=5] [TIME_LIMIT=90]
#                 [STOP_WHEN_SETTLED=1] [KEEP_HEATING=0]
#   TLTG_BED_SOAK_STOP
#
# The test runs one G-code step at a time from its own reactor callback, so
# other commands run between meshes. Its meshes skip bed_mesh's profile save
# so they leave no config save pending, and the mesh loaded before the test
# is loaded again at the end. The tltg_bed_soak/stop endpoint stops the test
# and turns its heaters off without waiting for the mesh in progress.
#
# Problem points: points the probe retried (from tltg_probe_report), points
# whose change since the previous mesh is out of line with the points around
# them, and probe jumps, found by probing the bed centre between meshes. A
# jump is a check-point change that differs from both neighbouring
# intervals' movement by more than the jump limit; its mesh is dropped and
# later meshes are corrected by its size. Out-of-line points and jumps are
# looked for only between meshes taken after the bed reached temperature:
# while it heats, the bed moves too fast and unevenly to tell them apart.

import json
import logging
import os
import statistics
import time

PROFILE = "tltg_bed_soak"
RUN_FOLDER = "tltg_bed_soak"
CHAMBER_HEATER = "heater_generic chamber"
DEFAULT_NOZZLE = 140.
# Stopped-moving limit (mm/min) and the meshes it must hold for.
SPEED_LIMIT = 0.002
SETTLED_MESHES = 3
# A point is out of line when its change differs from the bed's median
# change by more than this (mm).
OUT_OF_LINE = 0.015
# A check-point change this far (mm) beyond the bed's movement is a jump.
JUMP_LIMIT = 0.015
# Print start's waits: bed within 1 C of target, chamber at target - 3 C.
BED_WAIT = 1.0
CHAMBER_WAIT = 3.0
# The chamber has stopped warming when it rises less than this in this time.
CHAMBER_STALL_RISE = 0.5
CHAMBER_STALL_TIME = 600.
DEFAULT_TIME_LIMIT = 90.
MAX_TIME_LIMIT = 240.
SAMPLE_PERIOD = 1.
RECORD_PERIOD = 5.
MIN_PROBE_COUNT = 3
MAX_PROBE_COUNT = 11


class _End(Exception):
    """Ends the test. leave_heaters: something else now owns them."""

    def __init__(self, state, reason, leave_heaters=False):
        Exception.__init__(self, reason)
        self.state = state
        self.reason = reason
        self.leave_heaters = leave_heaters


def _round(value, digits=6):
    return None if value is None else round(value, digits)


def mesh_change(matrix, offset, reference, skip=()):
    """Values of matrix - offset - reference, leaving out (row, col) in
    skip."""
    return [m - offset - r
            for i, (mrow, rrow) in enumerate(zip(matrix, reference))
            for j, (m, r) in enumerate(zip(mrow, rrow))
            if (i, j) not in skip]


def height_and_shape(changes):
    """Whole-bed shift and the mean warp left after removing it."""
    if not changes:
        return None, None
    height = sum(changes) / len(changes)
    shape = sum(abs(c - height) for c in changes) / len(changes)
    return height, shape


def out_of_line(matrix, offset, previous, prev_offset, limit=OUT_OF_LINE):
    """(row, col, change) of points whose change since previous differs from
    the median change of the points around them by more than limit. Warping
    changes neighbouring points alike; a bad reading doesn't."""
    changes = {(i, j): (m - offset) - (p - prev_offset)
               for i, (mrow, prow) in enumerate(zip(matrix, previous))
               for j, (m, p) in enumerate(zip(mrow, prow))}
    flagged = []
    for (i, j), c in sorted(changes.items()):
        around = [changes[(i + di, j + dj)]
                  for di in (-1, 0, 1) for dj in (-1, 0, 1)
                  if (di or dj) and (i + di, j + dj) in changes]
        if around and abs(c - statistics.median(around)) > limit:
            flagged.append((i, j, c))
    return flagged


def speed(matrix, offset, previous, prev_offset, minutes, skip=()):
    """Median absolute change per minute since previous."""
    if minutes <= 0:
        return None
    changes = [abs((m - offset) - (p - prev_offset))
               for i, (mrow, prow) in enumerate(zip(matrix, previous))
               for j, (m, p) in enumerate(zip(mrow, prow))
               if (i, j) not in skip]
    if not changes:
        return None
    return statistics.median(changes) / minutes


def find_jump(prev, cur, nxt, limit=JUMP_LIMIT):
    """Each argument is (check-point change, seconds) for consecutive mesh
    intervals. Returns the jump in cur, or None, when cur's change differs
    from both neighbours' rate by more than limit in the same direction."""
    (dp, tp), (dc, tc), (dn, tn) = prev, cur, nxt
    if tp <= 0 or tc <= 0 or tn <= 0:
        return None
    a = dc - dp / tp * tc
    b = dc - dn / tn * tc
    if abs(a) > limit and abs(b) > limit and (a > 0) == (b > 0):
        return (a + b) / 2.
    return None


def settled_time(meshes, gate, limit=SPEED_LIMIT, count=SETTLED_MESHES):
    """Time of the first mesh of the run of used meshes at the end that are
    under the speed limit and started at or after gate, if at least count
    long; otherwise None."""
    if gate is None:
        return None
    run = []
    for mesh in reversed([m for m in meshes if m["used"]]):
        if (mesh["start"] < gate or mesh["speed"] is None
                or mesh["speed"] >= limit):
            break
        run.append(mesh)
    if len(run) < count:
        return None
    return run[-1]["time"]


def chamber_stalled(samples, wait_temp, rise=CHAMBER_STALL_RISE,
                    window=CHAMBER_STALL_TIME):
    """samples: (time, chamber temp) since the heaters turned on. True when
    the chamber is below wait_temp and rose less than rise over the last
    window seconds."""
    if not samples or samples[-1][0] - samples[0][0] < window:
        return False
    now, temp = samples[-1]
    if temp >= wait_temp:
        return False
    earlier = [t for s, t in samples if s <= now - window]
    return bool(earlier) and temp - earlier[-1] < rise


class BedSoak:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        config_file = self.printer.get_start_args().get("config_file", "")
        self.run_dir = os.path.join(
            os.path.dirname(os.path.abspath(config_file)), RUN_FOLDER)
        self.bed_mesh = self.probe = self.report = None
        self.heaters = {}
        self.mesh_command = "BED_MESH_CALIBRATE"
        self.mesh_min = self.mesh_max = None
        self.horizontal_move_z = 5.
        self.skip_save = False
        self.sample_timer = None
        self._reset()
        self.status = self._build_status()
        self.gcode.register_command(
            "TLTG_BED_SOAK", self.cmd_TLTG_BED_SOAK,
            desc="Measure how long the bed takes to heat and stop moving")
        self.gcode.register_command(
            "TLTG_BED_SOAK_STOP", self.cmd_TLTG_BED_SOAK_STOP,
            desc="Stop the bed soak test")
        self.printer.lookup_object("webhooks").register_endpoint(
            "tltg_bed_soak/stop", self._handle_stop)
        self.printer.register_event_handler("klippy:connect", self._connect)
        self.printer.register_event_handler("klippy:shutdown",
                                            self._shutdown)

    # ------------------------------------------------------------- setup
    def _reset(self):
        self.state = "idle"
        self.step = ""
        self.stop_requested = False
        self.end = None
        self.settings = None
        self.started = None
        self.heaters_on = None
        self.heaters_on_mono = None
        self.elapsed = None
        self.milestones = {"bed": None, "chamber": None, "chamber_max": None,
                           "settled": None, "ready": None}
        self.meshes = []
        self.intervals = []
        self.checks = []
        self.jumps = []
        self.offset = 0.
        self.mesh_started = None
        self.last_duration = None
        self.mesh_report = None
        self.expected = {}
        self.turned_on = set()
        self.temps = []
        self.pending_temps = []
        self.last_record = None
        self.chamber_samples = []
        self.chamber_stall_time = None
        self.prev_mesh = None
        self.check_point = None
        self.file = ""
        self.path = None
        self.reason = ""

    def _connect(self):
        pheaters = self.printer.lookup_object("heaters")
        self.heaters = {
            "extruder": pheaters.lookup_heater("extruder"),
            "bed": pheaters.lookup_heater("heater_bed")}
        try:
            self.heaters["chamber"] = pheaters.lookup_heater("chamber")
        except Exception:
            pass
        self.bed_mesh = self.printer.lookup_object("bed_mesh", None)
        self.probe = self.printer.lookup_object("probe", None)
        self.report = self.printer.lookup_object("tltg_probe_report", None)
        if self.report is not None:
            self.report.add_listener(self._mesh_report)
        handlers = getattr(self.gcode, "ready_gcode_handlers", None) or \
            getattr(self.gcode, "base_gcode_handlers", {})
        if "_BED_MESH_CALIBRATE" in handlers or \
                "_BED_MESH_CALIBRATE" in getattr(
                    self.gcode, "base_gcode_handlers", {}):
            # QIDI wraps Klipper's command in an adaptive macro.
            self.mesh_command = "_BED_MESH_CALIBRATE"
        if self.bed_mesh is not None:
            orig = self.bed_mesh.bmc.orig_config
            self.mesh_min = list(orig["mesh_min"])
            self.mesh_max = list(orig["mesh_max"])
            self.horizontal_move_z = self.bed_mesh.horizontal_move_z
            save_profile = self.bed_mesh.save_profile

            def maybe_save_profile(name):
                if self.skip_save:
                    return
                save_profile(name)
            self.bed_mesh.save_profile = maybe_save_profile
        self._publish()

    def _mesh_report(self, record):
        if self.skip_save:
            self.mesh_report = record

    # ------------------------------------------------------------ status
    def _build_status(self):
        latest = None
        if self.meshes:
            m = self.meshes[-1]
            latest = {"index": m["index"], "time": m["time"],
                      "height": m["height"], "shape": m["shape"],
                      "speed": m["speed"], "used": m["used"]}
        progress = None
        if self.mesh_started is not None and self.last_duration:
            progress = min(.99, (self.reactor.monotonic() - self.mesh_started)
                           / self.last_duration)
            progress = round(progress, 2)
        return {
            "state": self.state,
            "step": self.step,
            "stop_requested": self.stop_requested,
            "chamber_available": "chamber" in self.heaters,
            "settings": dict(self.settings) if self.settings else None,
            "limits": {"speed": SPEED_LIMIT, "out_of_line": OUT_OF_LINE,
                       "jump": JUMP_LIMIT, "bed_wait": BED_WAIT,
                       "chamber_wait": CHAMBER_WAIT},
            "started": self.started,
            "heaters_on": self.heaters_on,
            "elapsed": self.elapsed,
            "milestones": dict(self.milestones),
            "meshes": len(self.meshes),
            "latest": latest,
            "mesh_progress": progress,
            "problems": {"points": len(self._problem_points()),
                         "jumps": len(self.jumps)},
            "file": self.file,
            "reason": self.reason,
        }

    def _publish(self):
        self.status = self._build_status()

    def get_status(self, eventtime=None):
        return self.status

    # ---------------------------------------------------------- commands
    def _printing(self, eventtime):
        stats = self.printer.lookup_object("print_stats", None)
        sdcard = self.printer.lookup_object("virtual_sdcard", None)
        state = stats.get_status(eventtime).get("state") if stats else ""
        return (state in ("printing", "paused")
                or bool(sdcard and sdcard.is_active()))

    def _target_arg(self, gcmd, name, heater, required):
        value = gcmd.get_float(name, None)
        if value is None or (not required and value <= 0.):
            if required:
                raise gcmd.error("TLTG_BED_SOAK needs %s=" % name)
            return None
        if heater is None:
            raise gcmd.error("This printer has no chamber heater")
        if not heater.min_temp < value <= heater.max_temp:
            raise gcmd.error("%s=%g is outside that heater's limits "
                             "(up to %g)" % (name, value, heater.max_temp))
        return value

    def cmd_TLTG_BED_SOAK(self, gcmd):
        eventtime = self.reactor.monotonic()
        if self.state == "running":
            raise gcmd.error("A bed soak test is already running")
        if self._printing(eventtime):
            raise gcmd.error("A bed soak test can't run during a print")
        if self.bed_mesh is None or self.probe is None:
            raise gcmd.error("A bed soak test needs a probe and bed_mesh")
        bed = self._target_arg(gcmd, "BED", self.heaters["bed"], True)
        chamber = self._target_arg(gcmd, "CHAMBER",
                                   self.heaters.get("chamber"), False)
        count = gcmd.get_int("PROBE_COUNT", 5, minval=MIN_PROBE_COUNT,
                             maxval=MAX_PROBE_COUNT)
        limit = gcmd.get_float("TIME_LIMIT", DEFAULT_TIME_LIMIT, minval=10.,
                               maxval=MAX_TIME_LIMIT)
        settle = bool(gcmd.get_int("STOP_WHEN_SETTLED", 1, minval=0,
                                   maxval=1))
        keep = bool(gcmd.get_int("KEEP_HEATING", 0, minval=0, maxval=1))
        nozzle = self._probing_temp()
        self._reset()
        self.settings = {"bed": bed, "chamber": chamber, "nozzle": nozzle,
                         "probe_count": count, "time_limit": limit,
                         "stop_when_settled": settle, "keep_heating": keep}
        self.state = "running"
        self.started = time.time()
        self.prev_mesh = self.bed_mesh.get_mesh()
        self._open_file()
        self._publish()
        self.sample_timer = self.reactor.register_timer(
            self._sample, self.reactor.NOW)
        self.reactor.register_callback(self._run)
        gcmd.respond_info(
            "Bed soak test started: bed %g C%s, %dx%d mesh, up to %g min. "
            "The printer homes, wipes the nozzle, levels, and measures a "
            "cold mesh before heating. TLTG_BED_SOAK_STOP stops it."
            % (bed, ", chamber %g C" % chamber if chamber else "", count,
               count, limit))

    def _probing_temp(self):
        macro = self.printer.lookup_object("gcode_macro _km_globals", None)
        try:
            value = float(macro.variables["start_extruder_probing_temp"])
        except Exception:
            value = 0.
        return value if value > 0 else DEFAULT_NOZZLE

    def cmd_TLTG_BED_SOAK_STOP(self, gcmd):
        if self.state != "running":
            gcmd.respond_info("No bed soak test is running")
            return
        self._request_stop()
        gcmd.respond_info("Bed soak test stopping")

    def _handle_stop(self, web_request):
        running = self.state == "running"
        if running:
            self._request_stop()
        web_request.send({"stopping": running})

    def _request_stop(self):
        if self.stop_requested:
            return
        self.stop_requested = True
        if self.end is None:
            self.end = _End("stopped", "Stopped by the operator")
        if not self.settings["keep_heating"]:
            self._heaters_off(("bed", "chamber"))
        self._publish()

    def _shutdown(self):
        if self.state == "running" and self.end is None:
            self.end = _End("failed", "Klipper shut down", True)

    # ------------------------------------------------------------ heaters
    def _heaters_off(self, names):
        for name in names:
            heater = self.heaters.get(name)
            if heater is None or name not in self.turned_on:
                continue
            self.expected[name] = 0.
            try:
                heater.set_temp(0.)
            except Exception:
                logging.exception("tltg_bed_soak: turning off %s", name)

    def _note_target(self, name):
        heater = self.heaters.get(name)
        if heater is not None:
            self.expected[name] = heater.get_status(
                self.reactor.monotonic())["target"]
            if self.expected[name] > 0:
                self.turned_on.add(name)

    def _check_takeover(self, eventtime):
        if self.end is not None:
            return
        if self._printing(eventtime):
            self.end = _End("failed", "A print started", True)
            return
        for name, target in self.expected.items():
            heater = self.heaters.get(name)
            current = heater.get_status(eventtime)["target"]
            if abs(current - target) > .01:
                label = {"extruder": "nozzle"}.get(name, name)
                self.end = _End("failed", "The %s target was changed "
                                "outside the test" % label, True)
                return

    def _sample(self, eventtime):
        if self.state != "running":
            return self.reactor.NEVER
        self._check_takeover(eventtime)
        bed = self.heaters["bed"].get_status(eventtime)
        chamber = None
        if "chamber" in self.heaters:
            chamber = self.heaters["chamber"].get_status(eventtime)
        if self.heaters_on_mono is not None:
            t = eventtime - self.heaters_on_mono
            self.elapsed = int(t)
            self._update_milestones(t, bed, chamber)
            if self.last_record is None or \
                    t - self.last_record >= RECORD_PERIOD - .01:
                self.last_record = t
                self.pending_temps.append([
                    round(t, 1), round(bed["temperature"], 2),
                    round(chamber["temperature"], 2) if chamber else None])
        self._publish()
        return eventtime + SAMPLE_PERIOD

    def _update_milestones(self, t, bed, chamber):
        ms = self.milestones
        settings = self.settings
        if ms["bed"] is None and bed["target"] > 0 and \
                bed["temperature"] >= bed["target"] - BED_WAIT:
            ms["bed"] = round(t)
        if settings["chamber"] and chamber is not None:
            wait = chamber["target"] - CHAMBER_WAIT
            temp = chamber["temperature"]
            self.chamber_samples.append((t, temp))
            if ms["chamber"] is None and chamber["target"] > 0 \
                    and temp >= wait:
                ms["chamber"] = round(t)
                ms["chamber_max"] = None
            elif ms["chamber"] is None:
                if ms["chamber_max"] is not None:
                    ms["chamber_max"] = round(max(ms["chamber_max"], temp), 1)
                elif chamber_stalled(self.chamber_samples, wait):
                    ms["chamber_max"] = round(
                        max(s for _, s in self.chamber_samples), 1)
                    self.chamber_stall_time = round(t)
        self._update_ready()

    def _chamber_gate(self):
        """When the chamber stopped mattering: reached, stalled, or not
        heated. None while it is still warming."""
        ms = self.milestones
        if not self.settings["chamber"]:
            return 0.
        if ms["chamber"] is not None:
            return ms["chamber"]
        if self.chamber_stall_time is not None:
            return self.chamber_stall_time
        return None

    def _update_ready(self):
        ms = self.milestones
        gate = self._chamber_gate()
        ms["ready"] = None
        if ms["settled"] is not None and gate is not None:
            ms["ready"] = max(ms["settled"], gate)

    # --------------------------------------------------------------- run
    def _check(self):
        if self.end is not None:
            raise self.end

    def _script(self, step, script):
        self._check()
        self.step = step
        self._publish()
        with self.gcode.get_mutex():
            self._check_takeover(self.reactor.monotonic())
            self._check()
            self.gcode.run_script_from_command(script)
        self._check_takeover(self.reactor.monotonic())

    def _run(self, eventtime):
        end = _End("finished", "")
        try:
            self._setup()
            self._loop()
        except _End as e:
            end = e
        except self.printer.command_error as e:
            end = _End("failed", str(e))
        except Exception as e:
            logging.exception("tltg_bed_soak: test failed")
            end = _End("failed", "Internal error: %s" % (e,))
        self._finish(end)

    def _profile_var(self, name, default):
        macro = self.printer.lookup_object("gcode_macro _tltg_printer", None)
        try:
            return float(macro.variables[name])
        except Exception:
            return default

    def _setup(self):
        s = self.settings
        # The cold mesh is measured with the bed and chamber heaters off.
        self._script("nozzle", "M104 S%g\nM140 S0%s" % (
            s["nozzle"], "\nM141 S0" if "chamber" in self.heaters else ""))
        for name in self.heaters:
            self._note_target(name)
        self._script("home", "G28")
        self._script("wipe", "\n".join((
            "G90", "G1 Z20 F480", "OPTIMIZED_MOVE_TO_TRASH",
            "M109 S%g" % s["nozzle"], "M83", "G1 E-0.2 F1800",
            "M106 S255", "OPTIMIZED_WIPE_NOZZLE_DROP", "M106 S0")))
        self._note_target("extruder")
        zx = self._profile_var("z_tilt_staging_x", None)
        zy = self._profile_var("z_tilt_staging_y", None)
        staging = "G1 X%g Y%g F33900" % (zx, zy) if zx is not None \
            and zy is not None else ""
        # The Smart 5 has a single Z motor and no Z tilt.
        if self.printer.lookup_object("z_tilt", None) is not None:
            self._script("z_tilt", "\n".join(
                filter(None, ("G90", staging, "Z_TILT_ADJUST", "M400"))))
        self._script("home_z", "BED_MESH_CLEAR\n"
                     "_OPTIMIZED_HOME_Z_FROM_SAFE_POINT")
        self.checks.append(self._probe_check("cold_mesh"))
        cold = self._mesh("cold_mesh")
        cold["height"] = cold["shape"] = 0.
        # Heaters on.
        self.step = "mesh"
        with self.gcode.get_mutex():
            self._check_takeover(self.reactor.monotonic())
            self._check()
            self.gcode.run_script_from_command(
                "M140 S%g" % s["bed"]
                + ("\nM141 S%g" % s["chamber"] if s["chamber"] else ""))
            self.heaters_on_mono = self.reactor.monotonic()
            self.heaters_on = time.time()
            self.elapsed = 0
            self._note_target("bed")
            if s["chamber"]:
                self._note_target("chamber")
        # The cold mesh's times count back from the heaters turning on.
        for mesh in self.meshes:
            for key in ("start", "end", "time"):
                mesh[key] = round(mesh[key] - self.heaters_on_mono, 1)
        self._write({"type": "heaters_on", "time": 0,
                     "epoch": self.heaters_on})
        self._write_mesh(self.meshes[0])
        self._publish()

    def _probe_check(self, step):
        """Probes the bed centre; returns (z, reactor time)."""
        cx = (self.mesh_min[0] + self.mesh_max[0]) / 2.
        cy = (self.mesh_min[1] + self.mesh_max[1]) / 2.
        self.check_point = [cx, cy]
        z = self.horizontal_move_z
        self._script(step, "G90\nG1 Z%g F600\nG1 X%g Y%g F12000\nPROBE\n"
                     "G1 Z%g F600" % (z, cx, cy, z))
        result = self.probe.get_status(self.reactor.monotonic())
        return (result.get("last_z_result"), self.reactor.monotonic())

    def _mesh(self, step):
        count = self.settings["probe_count"]
        start = self.reactor.monotonic()
        self.mesh_started = start
        self.mesh_report = None
        self.skip_save = True
        try:
            self._script(step, "%s PROFILE=%s MESH_MIN=%g,%g MESH_MAX=%g,%g "
                         "PROBE_COUNT=%d,%d" % (
                             self.mesh_command, PROFILE,
                             self.mesh_min[0], self.mesh_min[1],
                             self.mesh_max[0], self.mesh_max[1],
                             count, count))
        finally:
            self.skip_save = False
            self.mesh_started = None
        zmesh = self.bed_mesh.get_mesh()
        matrix = [list(row) for row in zmesh.get_probed_matrix()]
        params = zmesh.get_mesh_params()
        check = self._probe_check(step)
        self.checks.append(check)
        end = self.reactor.monotonic()
        self.last_duration = end - start
        base = self.heaters_on_mono or 0.
        mesh = {"type": "mesh", "index": len(self.meshes),
                "start": round(start - base, 1), "end": round(end - base, 1),
                "time": round((start + end) / 2. - base, 1),
                "matrix": matrix, "offset": 0., "height": None,
                "shape": None, "speed": None, "used": True, "retried": [],
                "out_of_line": [], "check": {}, "temps": []}
        report = self.mesh_report or {}
        mesh["retried"] = [dict(p, **dict(zip(("row", "col"),
                                              self._grid(p, params))))
                           for p in report.get("points", [])]
        before, after = self.checks[-2][0], self.checks[-1][0]
        mesh["check"] = {"before": before, "after": after, "jump": None}
        if before is not None and after is not None:
            self.intervals.append(
                [after - before, self.checks[-1][1] - self.checks[-2][1]])
        else:
            self.intervals.append(None)
        self.meshes.append(mesh)
        return mesh

    @staticmethod
    def _grid(point, params):
        def index(value, lo, hi, count):
            if count < 2 or hi <= lo:
                return 0
            i = int(round((value - lo) / (hi - lo) * (count - 1)))
            return max(0, min(count - 1, i))
        return (index(point["y"], params["min_y"], params["max_y"],
                      params["y_count"]),
                index(point["x"], params["min_x"], params["max_x"],
                      params["x_count"]))

    def _loop(self):
        s = self.settings
        while True:
            self._check()
            if self.elapsed is not None and self.elapsed >= \
                    s["time_limit"] * 60.:
                raise _End("finished", "Reached the time limit")
            if s["stop_when_settled"] and self._settled_for_stop():
                raise _End("finished", "The bed stopped moving")
            mesh = self._mesh("mesh")
            self._confirm_jump()
            self._analyze(mesh)
            self._write_mesh(mesh)
            self._publish()

    def _settled_for_stop(self):
        gate = self._chamber_gate()
        if gate is None or self.milestones["bed"] is None:
            return False
        return settled_time(self.meshes, max(gate, self.milestones["bed"])) \
            is not None

    def _confirm_jump(self):
        """Judges the mesh before the latest, now that the interval after
        it is known."""
        k = len(self.meshes) - 2
        if k < 1:
            return
        i = next((i for i in range(k - 1, -1, -1)
                  if self.intervals[i] is not None and self.meshes[i]["used"]),
                 None)
        cur, nxt = self.intervals[k], self.intervals[k + 1]
        if i is None or cur is None or nxt is None \
                or not self._at_temperature(self.meshes[i]):
            return
        prev = self.intervals[i]
        size = find_jump(prev, cur, nxt)
        if size is None:
            return
        mesh = self.meshes[k]
        mesh["used"] = False
        mesh["check"]["jump"] = round(size, 6)
        self.offset += size
        jump = {"mesh": k, "time": mesh["time"], "size": round(size, 6)}
        self.jumps.append(jump)
        self._write(dict(jump, type="jump"))
        self.gcode.respond_info(
            "Bed soak: probe readings jumped %.0f um during mesh %d; that "
            "mesh isn't used and later meshes are corrected."
            % (size * 1000., k))

    def _analyze(self, mesh):
        mesh["offset"] = round(self.offset, 6)
        cold = self.meshes[0]
        prev = next((m for m in reversed(self.meshes[:-1]) if m["used"]),
                    cold)
        flagged = set((p["row"], p["col"]) for p in mesh["retried"])
        lines = []
        if self._at_temperature(prev):
            lines = out_of_line(mesh["matrix"], mesh["offset"],
                                prev["matrix"], prev["offset"])
        mesh["out_of_line"] = [{"row": i, "col": j, "change": round(c, 6)}
                               for i, j, c in lines]
        flagged.update((i, j) for i, j, _ in lines)
        height, shape = height_and_shape(mesh_change(
            mesh["matrix"], mesh["offset"], cold["matrix"], flagged))
        mesh["height"] = _round(height)
        mesh["shape"] = _round(shape)
        mesh["speed"] = _round(speed(
            mesh["matrix"], mesh["offset"], prev["matrix"], prev["offset"],
            (mesh["time"] - prev["time"]) / 60., flagged), 7)
        ms = self.milestones
        if ms["bed"] is not None:
            ms["settled"] = settled_time(self.meshes, ms["bed"])
        self._update_ready()

    def _at_temperature(self, mesh):
        bed = self.milestones["bed"]
        return bed is not None and mesh["start"] >= bed

    def _problem_points(self):
        points = {}
        for mesh in self.meshes:
            for p in mesh["retried"]:
                entry = points.setdefault((p["row"], p["col"]), {
                    "x": p["x"], "y": p["y"], "row": p["row"],
                    "col": p["col"], "meshes": [], "retried": 0,
                    "out_of_line": 0})
                entry["retried"] += 1
                if mesh["index"] not in entry["meshes"]:
                    entry["meshes"].append(mesh["index"])
            for p in mesh["out_of_line"]:
                entry = points.setdefault((p["row"], p["col"]), {
                    "x": None, "y": None, "row": p["row"], "col": p["col"],
                    "meshes": [], "retried": 0, "out_of_line": 0})
                entry["out_of_line"] += 1
                if mesh["index"] not in entry["meshes"]:
                    entry["meshes"].append(mesh["index"])
        if points and self.meshes:
            # Positions for out-of-line points from the mesh grid.
            n = self.settings["probe_count"]
            for entry in points.values():
                if entry["x"] is None:
                    entry["x"] = round(self.mesh_min[0] + entry["col"] * (
                        self.mesh_max[0] - self.mesh_min[0]) / (n - 1), 2)
                    entry["y"] = round(self.mesh_min[1] + entry["row"] * (
                        self.mesh_max[1] - self.mesh_min[1]) / (n - 1), 2)
        return [points[k] for k in sorted(points)]

    # ------------------------------------------------------------ finish
    def _finish(self, end):
        self.step = "finishing"
        self.mesh_started = None
        self.skip_save = False
        self._publish()
        if not self.printer.is_shutdown():
            if not end.leave_heaters:
                self._heaters_off(["extruder"])
                keep = self.settings["keep_heating"] and \
                    end.state in ("finished", "stopped")
                if not keep:
                    self._heaters_off(("bed", "chamber"))
            if not self._printing(self.reactor.monotonic()):
                try:
                    with self.gcode.get_mutex():
                        if not self._printing(self.reactor.monotonic()):
                            self.bed_mesh.set_mesh(self.prev_mesh)
                except Exception:
                    logging.exception("tltg_bed_soak: restoring the mesh")
        if self.sample_timer is not None:
            self.reactor.unregister_timer(self.sample_timer)
            self.sample_timer = None
        self.state = end.state
        self.reason = end.reason or ("The bed stopped moving"
                                     if end.state == "finished" else "")
        self.step = ""
        points = self._problem_points()
        warning = bool(self.jumps) or any(len(p["meshes"]) > 1
                                          for p in points)
        self._write({"type": "result", "state": self.state,
                     "reason": self.reason, "ended": time.time(),
                     "milestones": dict(self.milestones),
                     "meshes": len(self.meshes), "jumps": list(self.jumps),
                     "problem_points": points, "warning": warning})
        self._publish()
        self.gcode.respond_info(self._summary(warning))

    def _summary(self, warning):
        ms = self.milestones

        def minutes(value):
            return "-" if value is None else "%.1f min" % (value / 60.)
        lines = ["Bed soak test %s: %s" % (self.state, self.reason),
                 "Bed at temperature: %s" % minutes(ms["bed"])]
        if self.settings["chamber"]:
            if ms["chamber"] is None and ms["chamber_max"] is not None:
                lines.append("Chamber stopped warming at %.1f C"
                             % ms["chamber_max"])
            else:
                lines.append("Chamber at temperature: %s"
                             % minutes(ms["chamber"]))
        lines.append("Bed stopped moving: %s" % minutes(ms["settled"]))
        lines.append("Ready to print: %s" % minutes(ms["ready"]))
        if warning:
            lines.append("Some probe readings can't be trusted; check the "
                         "plate and run the test again.")
        if self.file:
            lines.append("Saved as %s" % self.file)
        return "\n".join(lines)

    # -------------------------------------------------------------- file
    def _open_file(self):
        s = self.settings
        name = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started))
        name += "-bed%g" % s["bed"]
        if s["chamber"]:
            name += "-chamber%g" % s["chamber"]
        name += ".jsonl"
        try:
            os.makedirs(self.run_dir, exist_ok=True)
            self.path = os.path.join(self.run_dir, name)
            self.file = RUN_FOLDER + "/" + name
        except OSError:
            logging.exception("tltg_bed_soak: can't create %s", self.run_dir)
            self.path = None
            self.file = ""
            return
        self._write(dict(
            s, type="settings", version=1, started=self.started,
            mesh_min=self.mesh_min, mesh_max=self.mesh_max,
            limits=self._build_status()["limits"],
            check_point=[(self.mesh_min[0] + self.mesh_max[0]) / 2.,
                         (self.mesh_min[1] + self.mesh_max[1]) / 2.]))

    def _write_mesh(self, mesh):
        mesh["temps"], self.pending_temps = self.pending_temps, []
        self._write(mesh)

    def _write(self, record):
        if self.path is None:
            return
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(record, separators=(",", ":")) + "\n")
        except (OSError, TypeError, ValueError):
            logging.exception("tltg_bed_soak: writing %s", self.path)


def load_config(config):
    return BedSoak(config)
