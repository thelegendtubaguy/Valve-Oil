# Printer cooling fan
#
# Copyright (C) 2016-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import pulse_counter, output_pin

class Fan:
    def __init__(self, config, default_shutdown_speed=0.):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.last_fan_value = self.last_req_value = 0.
        # QIDI: fan names for the speed check messages
        self.name = config.get_name().split()[-1]
        self.head_name = config.get_name().split()[0]
        # Read config
        self.max_power = config.getfloat('max_power', 1., above=0., maxval=1.)
        self.kick_start_time = config.getfloat('kick_start_time', 0.1,
                                               minval=0.)
        self.off_below = config.getfloat('off_below', default=0.,
                                         minval=0., maxval=1.)
        cycle_time = config.getfloat('cycle_time', 0.010, above=0.)
        hardware_pwm = config.getboolean('hardware_pwm', False)
        shutdown_speed = config.getfloat(
            'shutdown_speed', default_shutdown_speed, minval=0., maxval=1.)
        # Setup pwm object
        ppins = self.printer.lookup_object('pins')
        self.mcu_fan = ppins.setup_pin('pwm', config.get('pin'))
        self.mcu_fan.setup_max_duration(0.)
        self.mcu_fan.setup_cycle_time(cycle_time, hardware_pwm)
        shutdown_power = max(0., min(self.max_power, shutdown_speed))
        self.mcu_fan.setup_start_value(0., shutdown_power)

        self.enable_pin = None
        enable_pin = config.get('enable_pin', None)
        if enable_pin is not None:
            self.enable_pin = ppins.setup_pin('digital_out', enable_pin)
            self.enable_pin.setup_max_duration(0.)

        # Create gcode request queue
        self.gcrq = output_pin.GCodeRequestQueue(config, self.mcu_fan.get_mcu(),
                                                 self._apply_speed)

        # Setup tachometer
        self.tachometer = FanTachometer(config)
        # QIDI: tachometer speed check (QDE_002_001 after 10 s without rpm)
        self.rpm_error = 0
        if self.tachometer._is_rpm == 1:
            self.rpm_timeout = 10
            self.last_rpm_time = 0
            self.tachometer_heater_names = config.getlist("heater",
                                                          ("extruder",))
            self.rpm_watchdog_timer = self.reactor.register_timer(
                self._rpm_watchdog_callback)

        # Register callbacks
        self.printer.register_event_handler("gcode:request_restart",
                                            self._handle_request_restart)

    def _extruder_heating(self):
        extruder = self.printer.lookup_object('toolhead').get_extruder()
        return extruder.get_heater().target_temp > 0
    def _rpm_watchdog_callback(self, eventtime):
        if (self.last_fan_value > 0 and self.head_name == 'heater_fan'
                and not self._extruder_heating()):
            return self.reactor.NEVER
        if self.last_fan_value > 0:
            if self.get_status(eventtime)['rpm'] > 0:
                self.last_rpm_time = 0
                self.rpm_error = 0
            else:
                self.last_rpm_time += 1
            if self.last_rpm_time == self.rpm_timeout:
                self.rpm_error = 1
                self.last_rpm_time = self.rpm_timeout + 1
                msg = ('Code:QDE_002_001; Message:{"fan_name":"%s"} speed{"%d"}'
                       ' may be error, please check!'
                       % (self.name, self.get_status(eventtime)['rpm']))
                self.printer.lookup_object('gcode').respond_info(msg)
                logging.info(msg)
            return eventtime + 1.0
        self.last_rpm_time = 0
        self.rpm_error = 0
        return self.reactor.NEVER
    def _note_rpm_watch(self, print_time, value, prev_value):
        # QIDI: start the speed check when the fan turns on
        if not self.tachometer._is_rpm:
            return
        if value == 0:
            self.reactor.update_timer(self.rpm_watchdog_timer,
                                      self.reactor.NEVER)
        elif prev_value == 0:
            if self.head_name == 'heater_fan' and not self._extruder_heating():
                self.reactor.update_timer(self.rpm_watchdog_timer,
                                          self.reactor.NEVER)
                return
            curtime = self.reactor.monotonic()
            est = self.get_mcu().estimated_print_time(curtime)
            dif_time = int(print_time) - int(est)
            self.reactor.update_timer(self.rpm_watchdog_timer,
                                      curtime + max(0, dif_time))
    def get_mcu(self):
        return self.mcu_fan.get_mcu()
    def _apply_speed(self, print_time, value):
        if value < self.off_below:
            value = 0.
        value = max(0., min(self.max_power, value * self.max_power))
        if value == self.last_fan_value:
            return "discard", 0.
        self._note_rpm_watch(print_time, value, self.last_fan_value)
        if self.enable_pin:
            if value > 0 and self.last_fan_value == 0:
                self.enable_pin.set_digital(print_time, 1)
            elif value == 0 and self.last_fan_value > 0:
                self.enable_pin.set_digital(print_time, 0)
        if (value and self.kick_start_time
            and (not self.last_fan_value or value - self.last_fan_value > .5)):
            # Run fan at full speed for specified kick_start_time
            self.last_req_value = value
            self.last_fan_value = self.max_power
            self.mcu_fan.set_pwm(print_time, self.max_power)
            return "repeat", print_time + self.kick_start_time
        self.last_fan_value = self.last_req_value = value
        self.mcu_fan.set_pwm(print_time, value)
    def set_speed(self, *args, **kwargs):
        # set_speed(value, print_time=None); QIDI's code and compiled modules
        # call the pre-0.13 set_speed(print_time, value). Callers in this tree
        # pass print_time by keyword, so two positional arguments are always
        # the old form.
        if len(args) == 2:
            # Old form: queue for the time given, sent once step generation
            # reaches it, as Valve Oil's fan fix did on QIDI's Klipper
            print_time, value = args
            self.gcrq._queue_request(print_time, value)
            return
        value = args[0] if args else kwargs['value']
        print_time = kwargs.get('print_time')
        self.gcrq.send_async_request(value, print_time)
    def set_speed_from_command(self, value):
        self.gcrq.queue_gcode_request(value)
    def _handle_request_restart(self, print_time):
        self.set_speed(0., print_time=print_time)

    def get_status(self, eventtime):
        tachometer_status = self.tachometer.get_status(eventtime)
        return {
            'speed': self.last_req_value,
            'rpm': tachometer_status['rpm'],
        }

class FanTachometer:
    def __init__(self, config):
        printer = config.get_printer()
        self._freq_counter = None
        self._is_rpm = 0

        pin = config.get('tachometer_pin', None)
        if pin is not None:
            self._is_rpm = 1
            self.ppr = config.getint('tachometer_ppr', 2, minval=1)
            poll_time = config.getfloat('tachometer_poll_interval',
                                        0.0015, above=0.)
            sample_time = 1.
            self._freq_counter = pulse_counter.FrequencyCounter(
                printer, pin, sample_time, poll_time)

    def get_status(self, eventtime):
        if self._freq_counter is not None:
            rpm = self._freq_counter.get_frequency() * 30. / self.ppr
        else:
            rpm = None
        return {'rpm': rpm}

class PrinterFan:
    def __init__(self, config):
        self.fan = Fan(config)
        # Register commands
        gcode = config.get_printer().lookup_object('gcode')
        gcode.register_command("M106", self.cmd_M106)
        gcode.register_command("M107", self.cmd_M107)
    def get_status(self, eventtime):
        return self.fan.get_status(eventtime)
    def cmd_M106(self, gcmd):
        # Set fan speed
        value = gcmd.get_float('S', 255., minval=0.) / 255.
        self.fan.set_speed_from_command(value)
    def cmd_M107(self, gcmd):
        # Turn fan off
        self.fan.set_speed_from_command(0.)

def load_config(config):
    return PrinterFan(config)
