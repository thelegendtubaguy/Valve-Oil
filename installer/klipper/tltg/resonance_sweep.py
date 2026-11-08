# Sweeping resonance test for Valve Oil.
#
# The test generation and moves are adapted from upstream Klipper's
# klippy/extras/resonance_tester.py, Copyright (C) 2020-2024 Dmitry
# Butyugin, and the junction calculation from klippy/toolhead.py, Copyright
# (C) 2016-2024 Kevin O'Connor, distributed under the terms of the GNU GPLv3
# license.
#
# QIDI's resonance test (SHAPER_CALIBRATE, TEST_RESONANCES) is upstream
# Klipper's from before December 2024: the toolhead shakes back and forth
# about the test point and stops to reverse twice per cycle, where friction
# and belt slack can distort what the accelerometer measures. Upstream's test
# since 16b4b6b30 adds a slow back-and-forth sweep, 400 mm/s^2 each way over
# a 1.2 s period, so the toolhead spends most of the test moving one way with
# the shaking on top of it, and wanders about 20 mm either side of the test
# point. This module runs that test, with upstream's later fixes 1c76ed1dc
# (segments with no acceleration) and a2b78ef98 (the final stop), in place of
# QIDI's:
#
# - The sweeping test runs unless the saved variable `tltg_resonance_sweep`
#   is false or a command passes SWEEPING_PERIOD=0; QIDI's own test then runs
#   unchanged.
# - SHAPER_CALIBRATE and TEST_RESONANCES take upstream's SWEEPING_PERIOD,
#   SWEEPING_ACCEL, and ACCEL_PER_HZ for one run. ACCEL_PER_HZ applies to
#   QIDI's test too.
# - The moves of a sweeping run are planned with upstream's junction
#   calculation, for the length of the run only. Each starts no faster than
#   the previous one was planned to end, as upstream's
#   toolhead.limit_next_junction_speed makes it, and QIDI's older calculation
#   (before upstream 847331260) limits a junction between two moves along the
#   same line as if it were a corner. The sweep splits moves where its
#   acceleration changes sign, some only 0.003 mm long at 120 mm/s, and
#   QIDI's calculation slowed those to about 73 mm/s.
# - Calibration data from a sweeping run fades out below 10 Hz instead of
#   being cut off below 5 Hz, as upstream's shaper_calibrate does, so the
#   sweep's own slow motion doesn't count as resonance.
#
# All of it is installed only when the QIDI code it relies on is exactly the
# version it was written for; otherwise QIDI's test is left alone and
# klippy.log says so.

import hashlib
import inspect
import logging
import math

import toolhead
from . import resonance_tester, shaper_calibrate

MARKER = "_tltg_resonance_sweep"
ENABLE_VARIABLE = "tltg_resonance_sweep"
SWEEPING_ACCEL = 400.
SWEEPING_PERIOD = 1.2
FALSE_WORDS = ("0", "false", "no", "off", "disable", "disabled", "")
# Set on a move: the most its next move may start at, in (mm/s)^2.
NEXT_JUNCTION_V2 = "_tltg_next_junction_v2"

# SHA-256 of what this module relies on, line endings removed, as shipped in
# QIDI Max 4 firmware 01.01.06.04 through 01.01.07.01 and Plus 5 01.01.02.01.
ORIGINALS = {
    "VibrationPulseTest":
        "168a542c095be5a96954bc2ec9c79db375bad2ba5f327373e7d59e0f46648137",
    "ResonanceTester._run_test":
        "49e0501b1eba66398deef3bfb34f55dbc2dc6183ee95e3a886ef2dcf3638a726",
    "ResonanceTester._get_max_calibration_freq":
        "3aaebce4f5ce17cb3a5dc6939f42d42b55e36fffbac084937f4c6bbab3f5dab3",
    "Move.calc_junction":
        "f71ea3367159a64eab4a786d34f7fbaf01185f879cb2422c361eee1c0c9ed2e2",
    "ToolHead.cmd_M204":
        "21360df8300836c3321b9e1c75fea343d6b9aa06c12ccaa544bb7aba3fe50cff",
    "CalibrationData.normalize_to_frequencies":
        "4c4d9b817a70ea3d4b72f2ea0d72aff3a96e021320aa5d219ae76e2d5f5fa28a",
}

# Whether the last test prepared was a sweeping one; SHAPER_CALIBRATE
# normalizes its data right after the test, under the same G-code command.
_last_test_swept = [False]


def source_hash(obj):
    try:
        source = inspect.getsource(obj)
    except (OSError, TypeError):
        return None
    return hashlib.sha256(source.replace("\r", "").encode()).hexdigest()


# ------------------------------------------------------------ test sequence
def vibration_test(freq_start, freq_end, accel_per_hz, hz_per_sec):
    """Upstream's vibrations: (end time, acceleration, frequency) for each
    quarter cycle, from freq_start to freq_end."""
    freq = freq_start
    res = []
    sign = 1.
    time = 0.
    while freq <= freq_end + 0.000001:
        t_seg = .25 / freq
        accel = accel_per_hz * freq
        time += t_seg
        res.append((time, sign * accel, freq))
        time += t_seg
        res.append((time, -sign * accel, freq))
        freq += 2. * t_seg * hz_per_sec
        sign = -sign
    return res


def sweeping_test(test_seq, sweeping_accel, sweeping_period):
    """Upstream's sweep: test_seq with sweeping_accel added, changing sign
    every half period, split where it changes."""
    accel_fraction = math.sqrt(2.0) * 0.125
    if sweeping_period:
        t_rem = sweeping_period * accel_fraction
    else:
        t_rem = float('inf')
        sweeping_accel = 0.
    res = []
    last_t = 0.
    sig = 1.
    accel_fraction += 0.25
    for next_t, accel, freq in test_seq:
        t_seg = next_t - last_t
        while t_rem <= t_seg:
            last_t += t_rem
            res.append((last_t, accel + sweeping_accel * sig, freq))
            t_seg -= t_rem
            t_rem = sweeping_period * accel_fraction
            accel_fraction = 0.5
            sig = -sig
        t_rem -= t_seg
        res.append((next_t, accel + sweeping_accel * sig, freq))
        last_t = next_t
    return res


# -------------------------------------------------------------- toolhead
def _limit_next_junction(th, speed):
    move = th.lookahead.get_last()
    if move is None:
        return
    v2 = speed * speed
    limit = getattr(move, NEXT_JUNCTION_V2, None)
    setattr(move, NEXT_JUNCTION_V2, v2 if limit is None else min(limit, v2))


def _sweep_calc_junction(self, prev_move):
    """Upstream's Move.calc_junction as of 16b4b6b30, with the limit
    _limit_next_junction sets."""
    if not self.is_kinematic_move or not prev_move.is_kinematic_move:
        return
    # Allow extruder to calculate its maximum junction
    extruder_v2 = self.toolhead.extruder.calc_junction(prev_move, self)
    max_start_v2 = min(extruder_v2, self.max_cruise_v2,
                       prev_move.max_cruise_v2,
                       prev_move.max_start_v2 + prev_move.delta_v2)
    limit = getattr(prev_move, NEXT_JUNCTION_V2, None)
    if limit is not None:
        max_start_v2 = min(max_start_v2, limit)
    # Find max velocity using "approximated centripetal velocity"
    axes_r = self.axes_r
    prev_axes_r = prev_move.axes_r
    junction_cos_theta = -(axes_r[0] * prev_axes_r[0]
                           + axes_r[1] * prev_axes_r[1]
                           + axes_r[2] * prev_axes_r[2])
    sin_theta_d2 = math.sqrt(max(0.5*(1.0-junction_cos_theta), 0.))
    cos_theta_d2 = math.sqrt(max(0.5*(1.0+junction_cos_theta), 0.))
    one_minus_sin_theta_d2 = 1. - sin_theta_d2
    if one_minus_sin_theta_d2 > 0. and cos_theta_d2 > 0.:
        R_jd = sin_theta_d2 / one_minus_sin_theta_d2
        move_jd_v2 = R_jd * self.junction_deviation * self.accel
        pmove_jd_v2 = R_jd * prev_move.junction_deviation * prev_move.accel
        # Approximated circle must contact moves no further than mid-move
        #   centripetal_v2 = .5 * self.move_d * self.accel * tan_theta_d2
        quarter_tan_theta_d2 = .25 * sin_theta_d2 / cos_theta_d2
        move_centripetal_v2 = self.delta_v2 * quarter_tan_theta_d2
        pmove_centripetal_v2 = prev_move.delta_v2 * quarter_tan_theta_d2
        max_start_v2 = min(max_start_v2, move_jd_v2, pmove_jd_v2,
                           move_centripetal_v2, pmove_centripetal_v2)
    # Apply limits
    self.max_start_v2 = max_start_v2
    self.max_smoothed_v2 = min(
        max_start_v2, prev_move.max_smoothed_v2 + prev_move.smooth_delta_v2)


def run_sweep(printer, test_seq, axis, gcmd):
    """Upstream's ResonanceTestExecutor.run_test on QIDI's toolhead."""
    gcode = printer.lookup_object('gcode')
    reactor = printer.get_reactor()
    th = printer.lookup_object('toolhead')
    X, Y, Z, E = th.get_position()
    status = th.get_status(reactor.monotonic())
    old_max_accel = status['max_accel']
    old_minimum_cruise_ratio = status['minimum_cruise_ratio']
    max_accel = max([abs(a) for _, a, _ in test_seq])
    # QIDI's M204 and SET_VELOCITY_LIMIT clamp to printer.cfg's max_accel,
    # which would leave the moves short of the speeds planned below.
    accel_cap = getattr(th, 'max_accel_cap', None)
    if accel_cap is not None and max_accel > accel_cap:
        raise gcmd.error(
            "The sweeping resonance test needs %.0f mm/s^2, above max_accel"
            " %.0f. Lower ACCEL_PER_HZ, SWEEPING_ACCEL, or FREQ_END."
            % (max_accel, accel_cap))
    gcode.run_script_from_command(
        "SET_VELOCITY_LIMIT ACCEL=%.3f MINIMUM_CRUISE_RATIO=0"
        % (max_accel,))
    input_shaper = printer.lookup_object('input_shaper', None)
    if input_shaper is not None and not gcmd.get_int('INPUT_SHAPING', 0):
        input_shaper.disable_shaping()
        gcmd.respond_info("Disabled [input_shaper] for resonance testing")
    else:
        input_shaper = None
    calc_junction = toolhead.Move.__dict__['calc_junction']
    toolhead.Move.calc_junction = _sweep_calc_junction
    try:
        last_v = last_t = last_freq = 0.
        for next_t, accel, freq in test_seq:
            t_seg = next_t - last_t
            abs_last_v = abs(last_v)
            last_v2 = last_v * last_v
            if abs(accel) < 0.000001:
                v, abs_v = last_v, abs_last_v
                if abs_v < 0.000001:
                    th.dwell(t_seg)
                    last_t, last_freq = next_t, freq
                    continue
                half_inv_accel = 0.
                d = v * t_seg
            else:
                th.cmd_M204(gcode.create_gcode_command(
                    "M204", "M204", {"S": abs(accel)}))
                v = last_v + accel * t_seg
                abs_v = abs(v)
                if abs_v < 0.000001:
                    v = abs_v = 0.
                half_inv_accel = .5 / accel
                d = (v * v - last_v2) * half_inv_accel
            dX, dY = axis.get_point(d)
            nX = X + dX
            nY = Y + dY
            _limit_next_junction(th, abs_last_v)
            if v * last_v < 0:
                # The move first goes to a complete stop, then changes
                # direction
                d_decel = -last_v2 * half_inv_accel
                decel_X, decel_Y = axis.get_point(d_decel)
                th.move([X + decel_X, Y + decel_Y, Z, E], abs_last_v)
                th.move([nX, nY, Z, E], abs_v)
            else:
                th.move([nX, nY, Z, E], max(abs_v, abs_last_v))
            if math.floor(freq) > math.floor(last_freq):
                gcmd.respond_info("Testing frequency %.0f Hz" % (freq,))
                reactor.pause(reactor.monotonic() + 0.01)
            X, Y = nX, nY
            last_t = next_t
            last_v = v
            last_freq = freq
        if last_v:
            # Decelerate to a stop, continuing in the direction of motion
            d_decel = .5 * last_v * abs(last_v) / old_max_accel
            decel_X, decel_Y = axis.get_point(d_decel)
            th.cmd_M204(gcode.create_gcode_command(
                "M204", "M204", {"S": old_max_accel}))
            th.move([X + decel_X, Y + decel_Y, Z, E], abs(last_v))
    finally:
        toolhead.Move.calc_junction = calc_junction
    # Restore the original acceleration values
    gcode.run_script_from_command(
        "SET_VELOCITY_LIMIT ACCEL=%.3f MINIMUM_CRUISE_RATIO=%.3f"
        % (old_max_accel, old_minimum_cruise_ratio))
    # Restore input shaper if it was disabled for resonance testing
    if input_shaper is not None:
        input_shaper.enable_shaping()
        gcmd.respond_info("Re-enabled [input_shaper]")


# ------------------------------------------------------------- calibration
def _normalize_to_frequencies(original):
    def normalize_to_frequencies(self):
        if not _last_test_swept[0]:
            original(self)
            return
        min_freq = shaper_calibrate.MIN_FREQ
        for psd in self._psd_list:
            # Avoid division by zero errors
            psd /= self.freq_bins + .1
            # Remove low-frequency noise
            low_freqs = self.freq_bins < 2. * min_freq
            psd[low_freqs] *= self.numpy.exp(
                -(2. * min_freq / (self.freq_bins[low_freqs] + .1))**2 + 1.)
    setattr(normalize_to_frequencies, MARKER, True)
    return normalize_to_frequencies


# -------------------------------------------------------------------- test
class SweepingTest:
    """Takes the place of QIDI's VibrationPulseTest on the resonance
    tester, and runs it when sweeping is off."""
    def __init__(self, printer, stock):
        self.printer = printer
        self.stock = stock
        self.accel_per_hz = stock.accel_per_hz
        self.test_accel_per_hz = stock.accel_per_hz
        self.sweeping_period = SWEEPING_PERIOD
        self.sweeping_accel = SWEEPING_ACCEL

    def _enabled(self):
        variables = self.printer.lookup_object('save_variables', None)
        if variables is None:
            return True
        try:
            values = variables.get_status(0.).get('variables')
        except Exception:
            values = None
        if not isinstance(values, dict):
            values = getattr(variables, 'allVariables', {}) or {}
        if ENABLE_VARIABLE not in values:
            return True
        value = values[ENABLE_VARIABLE]
        if isinstance(value, str):
            return value.strip().lower() not in FALSE_WORDS
        return bool(value)

    def get_start_test_points(self):
        return self.stock.get_start_test_points()

    def get_max_freq(self):
        return self.stock.get_max_freq()

    def prepare_test(self, gcmd):
        self.stock.prepare_test(gcmd)
        self.test_accel_per_hz = gcmd.get_float(
            "ACCEL_PER_HZ", self.accel_per_hz, above=0.)
        self.sweeping_period = gcmd.get_float(
            "SWEEPING_PERIOD", SWEEPING_PERIOD if self._enabled() else 0.,
            minval=0.)
        self.sweeping_accel = gcmd.get_float(
            "SWEEPING_ACCEL", SWEEPING_ACCEL, above=0.)
        _last_test_swept[0] = bool(self.sweeping_period)
        if self.sweeping_period:
            gcmd.respond_info(
                "Resonance test with sweeping: ACCEL_PER_HZ=%g"
                " SWEEPING_PERIOD=%g SWEEPING_ACCEL=%g"
                % (self.test_accel_per_hz, self.sweeping_period,
                   self.sweeping_accel))
        else:
            gcmd.respond_info(
                "Resonance test without sweeping: ACCEL_PER_HZ=%g"
                % (self.test_accel_per_hz,))

    def run_test(self, axis, gcmd):
        stock = self.stock
        if not self.sweeping_period:
            stock.accel_per_hz = self.test_accel_per_hz
            try:
                stock.run_test(axis, gcmd)
            finally:
                stock.accel_per_hz = self.accel_per_hz
            return
        test_seq = sweeping_test(
            vibration_test(stock.freq_start, stock.freq_end,
                           self.test_accel_per_hz, stock.hz_per_sec),
            self.sweeping_accel, self.sweeping_period)
        run_sweep(self.printer, test_seq, axis, gcmd)


def _differs(tester):
    """Labels of the QIDI code that isn't the version this was written
    for."""
    tester_cls = resonance_tester.ResonanceTester
    normalize = shaper_calibrate.CalibrationData.__dict__.get(
        'normalize_to_frequencies')
    checks = [
        ("VibrationPulseTest", resonance_tester.VibrationPulseTest),
        ("ResonanceTester._run_test", tester_cls.__dict__.get('_run_test')),
        ("ResonanceTester._get_max_calibration_freq",
         tester_cls.__dict__.get('_get_max_calibration_freq')),
        ("Move.calc_junction", toolhead.Move.__dict__.get('calc_junction')),
        ("ToolHead.cmd_M204", toolhead.ToolHead.__dict__.get('cmd_M204')),
    ]
    if not getattr(normalize, MARKER, False):
        checks.append(("CalibrationData.normalize_to_frequencies", normalize))
    differs = [label for label, obj in checks
               if obj is None or source_hash(obj) != ORIGINALS[label]]
    if not isinstance(getattr(tester, 'test', None),
                      resonance_tester.VibrationPulseTest):
        differs.append("ResonanceTester.test")
    return differs


class ResonanceSweep:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.register_event_handler("klippy:connect", self._connect)

    def _connect(self):
        tester = self.printer.lookup_object('resonance_tester', None)
        if tester is None:
            logging.info("tltg_resonance_sweep: no [resonance_tester]")
            return
        try:
            differs = _differs(tester)
            if not differs:
                sweeping_test = SweepingTest(self.printer, tester.test)
                cls = shaper_calibrate.CalibrationData
                normalize = cls.__dict__['normalize_to_frequencies']
                if not getattr(normalize, MARKER, False):
                    cls.normalize_to_frequencies = _normalize_to_frequencies(
                        normalize)
                tester.test = sweeping_test
        except Exception:
            logging.exception("tltg_resonance_sweep: install failed; QIDI's"
                              " resonance test runs")
            return
        if differs:
            logging.info("tltg_resonance_sweep: skipped; Klipper's %s differs"
                         " from the version this is for; QIDI's resonance"
                         " test runs", ", ".join(differs))
            return
        logging.info("tltg_resonance_sweep: sweeping resonance test installed")


def load_config(config):
    return ResonanceSweep(config)
