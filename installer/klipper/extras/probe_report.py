# Records the probe points of each bed mesh that needed a retry, for Valve
# Oil.
#
# QIDI's probe reports every sample on the console as "probe at X,Y is
# z=..." and every retry as "Probe samples exceed tolerance. Retrying...".
# While Klipper's bed mesh calibrate probes, this module reads those lines
# and keeps, for each point that needed a retry, its position, how many
# tries it took, and how far apart the samples of its failed tries were.
# printer.tltg_probe_report publishes them for the most recent mesh measured
# since Klipper started, and whether that mesh is the one loaded. Probing is
# unchanged.

import logging
import re
import time

_SAMPLE_RE = re.compile(
    r"^(?://\s*)?probe at (-?[\d.]+),(-?[\d.]+) is z=(-?[\d.]+)\s*$")
_RETRY_TEXT = "Probe samples exceed tolerance. Retrying"
# Samples further apart than this in X or Y belong to different points.
_SAME_POINT = 1.0


class _Collector:
    """Groups console probe samples into points and tries."""

    def __init__(self, offsets):
        self.offsets = offsets
        self.points = []
        self.current = None

    def sample(self, x, y, z):
        cur = self.current
        if (cur is None or abs(cur["x"] - x) > _SAME_POINT
                or abs(cur["y"] - y) > _SAME_POINT):
            self._close()
            cur = self.current = {"x": x, "y": y, "tries": 1,
                                  "spread": 0., "samples": []}
        cur["samples"].append(z)

    def retry(self):
        cur = self.current
        if cur is None:
            return
        if cur["samples"]:
            spread = max(cur["samples"]) - min(cur["samples"])
            cur["spread"] = max(cur["spread"], spread)
        cur["samples"] = []
        cur["tries"] += 1

    def _close(self):
        cur = self.current
        self.current = None
        if cur is not None and cur["tries"] > 1:
            self.points.append({
                "x": round(cur["x"] + self.offsets[0], 2),
                "y": round(cur["y"] + self.offsets[1], 2),
                "tries": cur["tries"],
                "spread": round(cur["spread"], 6)})

    def finish(self):
        self._close()
        return self.points


class ProbeReport:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.bed_mesh = None
        self.collector = None
        self.mesh = None
        self.listeners = []
        self.report = {"measured": None, "profile": None, "mesh_min": None,
                       "mesh_max": None, "probe_count": None, "points": []}
        self.status = dict(self.report, loaded=False)
        self.gcode.register_output_handler(self._output)
        self.printer.register_event_handler("klippy:connect", self._connect)

    def add_listener(self, callback):
        """callback(record) runs after each bed mesh that completes, with
        the same fields as the status."""
        self.listeners.append(callback)

    def _connect(self):
        self.bed_mesh = self.printer.lookup_object("bed_mesh", None)
        helper = getattr(getattr(getattr(self.bed_mesh, "bmc", None),
                                 "probe_mgr", None), "probe_helper", None)
        if helper is None or not hasattr(helper, "start_probe"):
            logging.info("tltg_probe_report: no bed mesh probing to follow")
            return
        start_probe = helper.start_probe

        def wrapped_start_probe(gcmd):
            self._begin()
            try:
                start_probe(gcmd)
            except Exception:
                self.collector = None
                raise
            self._end()
        helper.start_probe = wrapped_start_probe

    def _begin(self):
        offsets = (0., 0.)
        probe = self.printer.lookup_object("probe", None)
        try:
            offsets = tuple(probe.get_offsets()[:2])
        except Exception:
            pass
        self.collector = _Collector(offsets)

    def _end(self):
        collector, self.collector = self.collector, None
        if collector is None:
            return
        points = collector.finish()
        mesh = self.bed_mesh.get_mesh()
        report = {"measured": time.time(), "profile": None, "mesh_min": None,
                  "mesh_max": None, "probe_count": None, "points": points}
        if mesh is not None:
            params = mesh.get_mesh_params()
            report.update(
                profile=mesh.get_profile_name(),
                mesh_min=[params["min_x"], params["min_y"]],
                mesh_max=[params["max_x"], params["max_y"]],
                probe_count=[params["x_count"], params["y_count"]])
        self.mesh = mesh
        self.report = report
        self.status = dict(report, loaded=mesh is not None)
        for callback in self.listeners:
            try:
                callback(dict(report))
            except Exception:
                logging.exception("tltg_probe_report: listener failed")

    def _output(self, message):
        collector = self.collector
        if collector is None:
            return
        for line in message.split("\n"):
            match = _SAMPLE_RE.match(line.strip())
            if match is not None:
                collector.sample(*(float(v) for v in match.groups()))
            elif _RETRY_TEXT in line:
                collector.retry()

    def get_status(self, eventtime=None):
        # A new dict only when something changed, so Klipper's status pass
        # can skip it otherwise.
        loaded = (self.mesh is not None and self.bed_mesh is not None
                  and self.bed_mesh.get_mesh() is self.mesh)
        if loaded != self.status["loaded"]:
            self.status = dict(self.report, loaded=loaded)
        return self.status


def load_config(config):
    return ProbeReport(config)
