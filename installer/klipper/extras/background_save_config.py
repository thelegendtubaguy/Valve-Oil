# Background SAVE_CONFIG_QD for Valve Oil.
#
# QIDI's SAVE_CONFIG_QD writes pending SAVE_CONFIG values to printer.cfg without
# restarting. It re-reads and parses the whole include tree twice and runs
# `sync` on Klipper's main thread, which stalls Klipper for over two seconds at
# print start, longer than a heater holding a steady power can go without a
# refresh before its MCU shuts down.
#
# VO_SAVE_CONFIG_QD writes the same file in a background thread. The backup is
# a hard link and the new file replaces printer.cfg atomically, so a restart
# during the save always finds a complete printer.cfg. SAVE_CONFIG,
# SAVE_CONFIG_QD, and a second VO_SAVE_CONFIG_QD wait for a save in progress
# so two writers never replace printer.cfg at once.
#
# QIDI's SAVE_CONFIG writes the new printer.cfg without fsync: it renames
# printer.cfg to the dated backup, then renames the new file into its place.
# QIDI's image mounts / with `sync`, but Valve Oil's OS optimization takes that
# off, and ext4 then writes the new file's contents up to about 30 s after the
# rename. Since that rename doesn't replace an existing file, ext4 doesn't
# flush the contents first, so a power cut in that time leaves printer.cfg
# empty, as after input shaper calibration from the screen (M901). SAVE_CONFIG
# and SAVE_CONFIG_QD here flush printer.cfg and its folder after QIDI's handler
# has written them, before Klipper restarts.

import logging
import os
import threading
import time

import configfile

WAIT_INTERVAL = 0.05

# Module-level so a save started before a Klipper RESTART is still waited for
# afterwards; RESTART keeps imported modules.
_save_thread = None


class BackgroundSaveConfig:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        for name in ("SAVE_CONFIG", "SAVE_CONFIG_QD"):
            original = self.gcode.register_command(name, None)
            if original is not None:
                self.gcode.register_command(
                    name, self._waiting(original),
                    desc="Overwrite config file (waits for a background save)")
        self.gcode.register_command(
            "VO_SAVE_CONFIG_QD", self.cmd_VO_SAVE_CONFIG_QD,
            desc="Save pending SAVE_CONFIG values without blocking Klipper")

    def _waiting(self, original):
        def handler(gcmd):
            self._wait_for_save()
            result = original(gcmd)
            cfgname = self.printer.get_start_args()["config_file"]
            try:
                _fsync_saved(cfgname)
            except Exception as e:
                logging.exception("vo_background_save_config: flushing"
                                  " '%s' failed", cfgname)
                gcmd.respond_raw("!! Unable to flush %s to storage: %s"
                                 % (cfgname, e))
            return result
        return handler

    def _wait_for_save(self):
        while _save_thread is not None and _save_thread.is_alive():
            self.reactor.pause(self.reactor.monotonic() + WAIT_INTERVAL)

    def cmd_VO_SAVE_CONFIG_QD(self, gcmd):
        global _save_thread
        self._wait_for_save()
        pconfig = self.printer.lookup_object("configfile")
        if not pconfig.autosave.fileconfig.sections():
            return
        autosave_text = _config_api(pconfig).to_text(pconfig.autosave)
        cfgname = self.printer.get_start_args()["config_file"]
        _save_thread = threading.Thread(
            target=self._save, args=(pconfig, autosave_text, cfgname),
            name="vo-save-config")
        _save_thread.start()

    def _save(self, pconfig, autosave_text, cfgname):
        start = time.monotonic()
        try:
            self._write(pconfig, autosave_text, cfgname)
        except Exception as e:
            logging.exception("VO_SAVE_CONFIG_QD failed")
            msg = "!! VO_SAVE_CONFIG_QD: unable to save config: %s" % (e,)
            self.reactor.register_async_callback(
                lambda eventtime: self.gcode.respond_raw(msg))
            return
        logging.info("VO_SAVE_CONFIG_QD: saved '%s' in %.3f s",
                     cfgname, time.monotonic() - start)

    def _write(self, pconfig, autosave_text, cfgname):
        # Same file contents and checks as QIDI's SAVE_CONFIG_QD, against a
        # snapshot of the pending values taken when the command ran.
        lines = [("#*# " + l).strip() for l in autosave_text.split("\n")]
        lines.insert(0, "\n" + configfile.AUTOSAVE_HEADER.rstrip())
        lines.append("")
        autosave_data = "\n".join(lines)
        api = _config_api(pconfig)
        data = api.read(cfgname)
        regular_data, _old_autosave = api.find_autosave(data)
        api.parse(regular_data, cfgname)
        autosave = api.parse(autosave_text, cfgname)
        regular_data = api.strip_duplicates(regular_data, autosave)
        included = api.parse(regular_data, cfgname)
        for section in autosave.sections():
            for option in autosave.options(section):
                if included.has_option(section, option):
                    raise configfile.error(
                        "SAVE_CONFIG section '%s' option '%s' conflicts with"
                        " included value" % (section, option))
        data = regular_data.rstrip() + autosave_data
        datestr = time.strftime("-%Y%m%d_%H%M%S")
        backup_name = cfgname + datestr
        temp_name = cfgname + "_autosave"
        if cfgname.endswith(".cfg"):
            backup_name = cfgname[:-4] + datestr + ".cfg"
            temp_name = cfgname[:-4] + "_autosave.cfg"
        logging.info("VO_SAVE_CONFIG_QD to '%s' (backup in '%s')",
                     cfgname, backup_name)
        with open(temp_name, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if os.path.lexists(backup_name):
            os.unlink(backup_name)
        os.link(cfgname, backup_name)
        os.replace(temp_name, cfgname)
        _fsync_path(os.path.dirname(os.path.abspath(cfgname)))


class _config_api:
    """PrinterConfig's save helpers, on both Klipper layouts: 0.14 moved them
    to ConfigFileReader and ConfigAutoSave and works on fileconfigs, older
    Klipper keeps them on PrinterConfig and works on ConfigWrappers. Parsed
    configs come back as fileconfigs either way."""

    def __init__(self, pconfig):
        reader_cls = getattr(configfile, "ConfigFileReader", None)
        if reader_cls is not None:
            reader, autosave = reader_cls(), pconfig.autosave
            self.to_text = lambda wrapper: reader.build_config_string(
                wrapper.fileconfig)
            self.read = reader.read_config_file
            self.find_autosave = autosave._find_autosave_data
            self.parse = reader.build_fileconfig_with_includes
            self.strip_duplicates = autosave._strip_duplicates
        else:
            self.to_text = pconfig._build_config_string
            self.read = pconfig._read_config_file
            self.find_autosave = pconfig._find_autosave_data
            self.parse = lambda data, filename: pconfig._build_config_wrapper(
                data, filename).fileconfig
            self.strip_duplicates = lambda data, fileconfig: \
                pconfig._strip_duplicates(data, _Wrapped(fileconfig))


class _Wrapped:
    def __init__(self, fileconfig):
        self.fileconfig = fileconfig


def _fsync_path(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_saved(cfgname):
    """Flush a printer.cfg that was renamed into place, and its folder."""
    path = os.path.abspath(cfgname)
    _fsync_path(path)
    _fsync_path(os.path.dirname(path))


def load_config(config):
    return BackgroundSaveConfig(config)
