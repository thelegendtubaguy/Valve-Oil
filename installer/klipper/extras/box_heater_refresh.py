# QIDI Box heater refresh for Valve Oil.
#
# Klipper resends a heater's unchanged output only every 3.75 s, against the
# 5 s limit after which the heater's MCU shuts down. A QIDI Box heater reads its
# AHT20 sensor from Klipper's main loop, so while that loop is busy (at print
# start and end it stalls for over two seconds) the heater gets no updates at
# all. A Box heater holding a steady output, such as full power while heating,
# can then run out its 5 s and the Box disconnects.
#
# This module makes each Box heater resend an unchanged output every second, so
# it survives stalls of about three seconds. Other heaters, and every heater
# limit and safety check, are unchanged.
#
# It also stops polling a Box heater's AHT20 once Klipper has shut down. A Box
# running the shutdown-fix firmware stays connected in its shutdown state and
# refuses I2C reads; QIDI's aht20_f does not catch the resulting error, which
# would crash Klippy and replace the shutdown reason with a generic error.
#
# The saved variable vo_box_heater_pwm_cycle_time sets how often each Box
# heater switches on and off, as pwm_cycle_time does for the bed and chamber
# heaters. QIDI's box_config builds the Box heaters from a fixed list of
# box.cfg options that leaves pwm_cycle_time out, so they keep Klipper's
# 0.1 s; the Box MCU receives the cycle time when Klippy configures it, so it
# is set here before that. A change applies at Klippy's next start, where the
# changed Box MCU configuration makes Klippy restart the Box MCUs.

import logging
import re

REFRESH_TIME = 1.0
BOX_HEATER_NAME = re.compile(r"^heater_box\d+$")
CYCLE_TIME_VARIABLE = "vo_box_heater_pwm_cycle_time"
# Faster switching only adds work for the Box MCU, which also runs the Box's
# motors and link, so it is held to 100 cycles a second. QIDI's 0.1 s,
# Klipper's default, is the longest allowed.
MIN_CYCLE_TIME = 0.01
STOCK_CYCLE_TIME = 0.1


class BoxHeaterRefresh:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.printer.register_event_handler("klippy:mcu_identify",
                                            self._set_cycle_time)
        self.printer.register_event_handler("klippy:connect", self._connect)
        self.printer.register_event_handler("klippy:ready", self._guard_sensors)

    def _cycle_time(self):
        variables = getattr(self.printer.lookup_object("save_variables", None),
                            "allVariables", None) or {}
        if CYCLE_TIME_VARIABLE not in variables:
            return None
        value = variables[CYCLE_TIME_VARIABLE]
        try:
            cycle_time = float(value)
        except (TypeError, ValueError):
            cycle_time = 0.
        if isinstance(value, bool) or \
                not MIN_CYCLE_TIME <= cycle_time <= STOCK_CYCLE_TIME:
            logging.warning("tltg_box_heater_refresh: %s=%r is not between"
                            " %g and %g s; Box heaters keep %g s",
                            CYCLE_TIME_VARIABLE, value, MIN_CYCLE_TIME,
                            STOCK_CYCLE_TIME, STOCK_CYCLE_TIME)
            return None
        return cycle_time

    def _set_cycle_time(self):
        # Runs before Klippy sends the MCUs their configuration, which carries
        # each heater's cycle time.
        cycle_time = self._cycle_time()
        if cycle_time is None:
            return
        pheaters = self.printer.lookup_object("heaters", None)
        for name, heater in sorted(getattr(pheaters, "heaters", {}).items()):
            if not BOX_HEATER_NAME.match(name):
                continue
            mcu_pwm = getattr(heater, "mcu_pwm", None)
            pwm_delay = getattr(heater, "pwm_delay", None)
            if not callable(getattr(mcu_pwm, "setup_cycle_time", None)) \
                    or not isinstance(pwm_delay, (int, float)):
                logging.warning("tltg_box_heater_refresh: %s has an unexpected"
                                " heater layout; cycle time left unchanged",
                                name)
                continue
            if cycle_time > pwm_delay:
                # Klipper's own limit for pwm_cycle_time.
                logging.warning("tltg_box_heater_refresh: %s cycle time %g s"
                                " is above its sensor's %g s report time; left"
                                " unchanged", name, cycle_time, pwm_delay)
                continue
            mcu_pwm.setup_cycle_time(cycle_time)
            logging.info("tltg_box_heater_refresh: %s switches every %g s",
                         name, cycle_time)

    def _connect(self):
        pheaters = self.printer.lookup_object("heaters")
        for name, heater in sorted(getattr(pheaters, "heaters", {}).items()):
            if not BOX_HEATER_NAME.match(name):
                continue
            if not all(hasattr(heater, attr) for attr in
                       ("set_pwm", "next_pwm_time", "pwm_delay")):
                logging.warning("tltg_box_heater_refresh: %s has an unexpected"
                                " heater layout; refresh left unchanged", name)
                continue
            heater.set_pwm = self._wrap(heater, heater.set_pwm)
            logging.info("tltg_box_heater_refresh: %s resends every %.1f s",
                         name, REFRESH_TIME)

    def _guard_sensors(self):
        reactor = self.printer.get_reactor()
        pheaters = self.printer.lookup_object("heaters")
        for name, heater in sorted(getattr(pheaters, "heaters", {}).items()):
            timer = getattr(getattr(heater, "sensor", None), "sample_timer", None)
            if not BOX_HEATER_NAME.match(name) or not callable(getattr(timer, "callback", None)):
                continue
            timer.callback = self._guarded(name, timer.callback, reactor)

    def _guarded(self, name, callback, reactor):
        def sample(eventtime):
            try:
                return callback(eventtime)
            except Exception:
                if not self.printer.is_shutdown():
                    raise
                logging.info("tltg_box_heater_refresh: %s sensor read failed after"
                             " shutdown; polling stopped", name)
                return reactor.NEVER
        return sample

    def _wrap(self, heater, set_pwm):
        def refreshing_set_pwm(read_time, value):
            previous = heater.next_pwm_time
            set_pwm(read_time, value)
            if heater.next_pwm_time != previous:
                # The value was just sent; resend it sooner if unchanged.
                heater.next_pwm_time = min(
                    heater.next_pwm_time,
                    read_time + heater.pwm_delay + REFRESH_TIME)
        return refreshing_set_pwm


def load_config(config):
    return BoxHeaterRefresh(config)
