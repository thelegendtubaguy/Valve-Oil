# Printer warnings for Valve Oil.
#
# Publishes the problems an operator should fix before printing as Klipper
# status, so Fluidd shows them on every page until they are gone, and the
# optimized macros print the same text in the console at startup and after
# every home. Problems: a missing input shaper calibration, or one saved
# before the installer last flashed the THR, an X/Y motor slip reported by
# tltg_step_loss_monitor (until X and Y are homed again), and a board the
# installer can't flash because its bootloader or firmware isn't supported
# yet. The installer leaves that board's shareable report, with a prefilled
# support-issue link, in Klipper's log folder as tltg_thr_firmware.json,
# tltg_mainboard_firmware.json, or tltg_box_firmware_box<N>.json, and the
# calibration a THR flash found as tltg_input_shaper_thr_flash.json; the
# folder is read when Klipper starts.
#
# Also lists the Valve Oil work waiting for the printer (a newer release, or
# the installed one installed again, a QIDI firmware update, a board's
# firmware) and an update run in progress or interrupted, from the
# pending-work status file the update check and update runs write
# (installer/runtime/valve_oil_status.py). That file sits in
# printer_data beside the automatic-update enrollment marker, and is checked
# while Klipper runs, at most every PENDING_POLL seconds and re-read when it
# changes (or every PENDING_REREAD seconds, in case two quick writes looked
# alike), so its warnings come and go without a Klipper restart.
#
#   warnings  a list of {"id", "level", "title", "message"}, with level
#             "warning" or "error", and "url" for a board's prefilled
#             support issue; empty when nothing is wrong

import json
import logging
import os
import re

BOARD_REPORT = re.compile(
    r"^tltg_(?:(thr|mainboard)_firmware|box_firmware_box(\d+))\.json$")
THR_FLASH = "tltg_input_shaper_thr_flash.json"
SUPPORT_ISSUE_URL = ("https://github.com/thelegendtubaguy/valve-oil/issues/new"
                     "?template=printer-support.yml")
# What a board report in the log folder may add to a warning: a prefilled issue
# link on this project's form, and a short note. Anything else is dropped.
ISSUE_URL_PREFIX = "https://github.com/thelegendtubaguy/valve-oil/issues/new?"
ISSUE_URL_MAX = 7000
NOTE_MAX = 300
REPORT_MAX_BYTES = 65536
PENDING_FILE = ".tltg_valve_oil_status.json"
ENROLLMENT_FILE = ".tltg_optimized_auto_update_enrolled"
PENDING_SCHEMA = 1
PENDING_POLL = 5.
PENDING_REREAD = 60.
PENDING_MAX_BYTES = 262144
PENDING_MAX_TEXT = 200
FAILED_MAX_TEXT = 600
# The firmware update's pending record (installer/runtime/firmware_update.py).
FIRMWARE_UPDATE_RECORD = ".tltg_firmware_update_pending.json"
# Records of a board operation or install that has to be recovered before an
# update can continue (installer/runtime/safety.py).
RECOVERY_FILES = (".tltg_optimized_recovery_required",
                  ".tltg_thr_firmware_pending.json",
                  ".tltg_box_firmware_pending.json",
                  ".tltg_mainboard_firmware_pending.json")
FINISH_IT = "Finish it with Update now or by running the Valve Oil installer."
CONTINUE_IT = ("Continue it with Update now or by running the Valve Oil"
               " installer.")
BOOT_ID = "/proc/sys/kernel/random/boot_id"


class PrinterWarnings:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.failed = False
        log_file = self.printer.get_start_args().get("log_file")
        self.log_dir = os.path.dirname(log_file) if log_file else None
        self.box_reports = []
        self.board_reports = []
        self.thr_flash_shaper = {}
        self.printer.register_event_handler("klippy:ready", self._find_box_reports)
        # printer_data is the parent of the folder holding printer.cfg.
        config_file = self.printer.get_start_args().get("config_file")
        self.data_dir = (os.path.dirname(os.path.dirname(
            os.path.abspath(config_file))) if config_file else None)
        self.pending_next = 0.
        self.pending_read_at = None
        self.pending_stamp = None
        self.pending = {}
        self.enrolled = False
        self.run_active = False

    def _read(self, name):
        try:
            with open(os.path.join(self.log_dir, name)) as handle:
                value = json.loads(handle.read(REPORT_MAX_BYTES + 1))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _find_box_reports(self):
        try:
            names = os.listdir(self.log_dir) if self.log_dir else []
        except OSError:
            names = []
        reports, boards = [], []
        for name in names:
            match = BOARD_REPORT.match(name)
            if match and match.group(2):
                reports.append((int(match.group(2)), name, self._read(name)))
            elif match:
                boards.append((match.group(1), name, self._read(name)))
        self.box_reports = sorted(reports, key=lambda report: report[0])
        self.board_reports = sorted(boards, key=lambda report: report[1])
        self.thr_flash_shaper = {}
        if THR_FLASH in names:
            for axis in ("x", "y"):
                try:
                    self.thr_flash_shaper[axis] = float(
                        self._read(THR_FLASH)["shaper_freq_" + axis])
                except (KeyError, TypeError, ValueError):
                    pass

    def _unshaped_accel(self, macro_status):
        optimized = self.printer.lookup_object(
            "gcode_macro _tltg_optimized_globals", None)
        variables = getattr(optimized, "variables", {})
        try:
            return min(macro_status.max_accel,
                       float(variables.get("unshaped_accel", 10000)))
        except (TypeError, ValueError):
            return macro_status.max_accel

    def _input_shaper(self):
        macro_status = self.printer.lookup_object("tltg_macro_status", None)
        if macro_status is None:
            return []
        shaper = macro_status.input_shaper()
        if shaper["saved"] and not shaper["unshaped_axes"]:
            saved = getattr(macro_status, "saved_shaper_freq", None)
            if (saved is None or not self.thr_flash_shaper or any(
                    abs(saved(axis) - freq) > 1e-6
                    for axis, freq in self.thr_flash_shaper.items())):
                return []
            return [{
                "id": "input_shaper_after_thr_firmware",
                "level": "warning",
                "title": "Run input shaper calibration.",
                "message": "The installer updated the THR firmware after"
                           " your input shaper calibration was saved. The"
                           " saved calibration stays in effect until you"
                           " run input shaper calibration again.",
            }]
        limit = ("limiting acceleration during homing and meshing to %d mm/s²."
                 " Run input shaper calibration when possible."
                 % self._unshaped_accel(macro_status))
        if shaper["unshaped_axes"]:
            return [{
                "id": "input_shaper_off",
                "level": "error",
                "title": "Input shaping is off.",
                "message": "TLTG Optimizations is " + limit,
            }]
        return [{
            "id": "input_shaper_uncalibrated",
            "level": "error",
            "title": "No input shaper calibration is saved.",
            "message": "TLTG Optimizations is utilizing Qidi's default shaper"
                       " and " + limit,
        }]

    def _step_loss(self):
        monitor = self.printer.lookup_object("tltg_step_loss_monitor", None)
        message = getattr(monitor, "message", None)
        if not message:
            return []
        return [{
            "id": "xy_step_loss",
            "level": "error",
            "title": "X/Y lost position.",
            "message": message,
        }]

    def _support(self, warning, name, report):
        # The prefilled link is kilobytes long, so it goes in "url" rather
        # than in the message the console prints after every home.
        note, url = report.get("note"), report.get("issue_url")
        warning["message"] += (
            " To get it supported, open an issue at " + SUPPORT_ISSUE_URL
            + " and attach " + name + " from Fluidd's Logs.")
        if isinstance(note, str) and 0 < len(note) <= NOTE_MAX and note.isprintable():
            warning["message"] += " " + note
        if (isinstance(url, str) and url.startswith(ISSUE_URL_PREFIX)
                and len(url) <= ISSUE_URL_MAX and url.isprintable()
                and " " not in url):
            warning["url"] = url
        return warning

    def _box_firmware(self):
        warnings = [self._support({
            "id": "%s_firmware_unsupported" % board,
            "level": "warning",
            "title": "The %s can't get Valve Oil's firmware yet."
                     % ("THR" if board == "thr" else "mainboard"),
            "message": "Its bootloader or firmware isn't supported yet, so"
                       " Valve Oil doesn't change the THR or mainboard"
                       " firmware and leaves QIDI's firmware updates to the"
                       " touchscreen.",
        }, name, report) for board, name, report in self.board_reports]
        return warnings + [self._support({
            "id": "box_firmware_unsupported_box%d" % number,
            "level": "warning",
            "title": "Box %d can't get the Valve Oil Box firmware yet." % number,
            "message": "Its bootloader or firmware isn't supported yet, so it"
                       " keeps the firmware it has.",
        }, name, report) for number, name, report in self.box_reports]

    def _refresh_pending(self, eventtime):
        if self.data_dir is None:
            return
        if eventtime is not None:
            if eventtime < self.pending_next:
                return
            self.pending_next = eventtime + PENDING_POLL
        marker = os.path.join(self.data_dir, ENROLLMENT_FILE)
        self.enrolled = os.path.isfile(marker) and not os.path.islink(marker)
        path = os.path.join(self.data_dir, PENDING_FILE)
        try:
            info = os.stat(path)
            stamp = (info.st_mtime_ns, info.st_size, info.st_ino)
        except OSError:
            stamp = None
        if stamp != self.pending_stamp or eventtime is None or (
                self.pending_read_at is None
                or eventtime >= self.pending_read_at + PENDING_REREAD):
            self.pending_read_at = eventtime
            # The writer replaces the file whole; a file that can't be read
            # counts as nothing waiting.
            self.pending_stamp = stamp
            self.pending = {}
            if stamp is not None and stamp[1] <= PENDING_MAX_BYTES:
                try:
                    with open(path, encoding="utf-8") as handle:
                        value = json.load(handle)
                except (OSError, ValueError):
                    value = None
                if (isinstance(value, dict)
                        and value.get("schema") == PENDING_SCHEMA):
                    self.pending = value
        self.run_active = self._run_active(self._pending_run())

    def _pending_run(self):
        run = self.pending.get("run")
        return run if isinstance(run, dict) else {}

    def _run_active(self, run):
        if run.get("state") != "running":
            return False
        pid = run.get("pid")
        try:
            with open(BOOT_ID) as handle:
                boot_id = handle.read().strip()
        except OSError:
            boot_id = None
        if run.get("boot_id") != boot_id or not isinstance(pid, int):
            return False
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _can_continue(self, run):
        # As the status service's run.can_continue, and nothing to rescue.
        if any(os.path.exists(os.path.join(self.data_dir, name))
               for name in RECOVERY_FILES):
            return False
        path = os.path.join(self.data_dir, FIRMWARE_UPDATE_RECORD)
        try:
            if os.path.islink(path):
                return False
            with open(path, encoding="utf-8") as handle:
                record = json.loads(handle.read(PENDING_MAX_BYTES + 1))
        except (OSError, ValueError):
            return False
        return (isinstance(record, dict) and record.get("schema") == 1
                and isinstance(run.get("firmware_update"), str)
                and record.get("id") == run.get("firmware_update")
                and record.get("state") == "failed"
                and record.get("continuable") is not False)

    def _installed_release(self):
        optimized = self.printer.lookup_object(
            "gcode_macro _tltg_optimized_globals", None)
        return getattr(optimized, "variables", {}).get("package_version")

    def _pending_text(self, value):
        if not isinstance(value, str):
            return ""
        return value.strip()[:PENDING_MAX_TEXT]

    def _waiting_title(self, kind, items):
        version = self._pending_text(items[0].get("version"))
        if kind == "release" and items[0].get("reinstall") is True:
            return "Valve Oil needs to be installed again."
        if kind == "release":
            return ("Valve Oil %s is waiting." % version if version
                    else "A Valve Oil update is waiting.")
        if kind == "firmware":
            return ("QIDI firmware %s is waiting." % version if version
                    else "A QIDI firmware update is waiting.")
        names = []
        for item in items:
            board = self._pending_text(item.get("board"))
            if board == "thr":
                name = "the THR"
            elif board == "mcu":
                name = "the mainboard"
            elif board.startswith("box") and board[3:].isdigit():
                name = "Box " + board[3:]
            else:
                name = board or "a board"
            if name not in names:
                names.append(name)
        if len(names) > 1:
            names[-2:] = [names[-2] + " and " + names[-1]]
        return "New firmware is waiting for %s." % ", ".join(names)

    def _pending_work(self, eventtime):
        self._refresh_pending(eventtime)
        warnings = []
        run = self._pending_run()
        step = self._pending_text(run.get("step_label"))
        if self.run_active:
            warnings.append({
                "id": "valve_oil_update_running",
                "level": "warning",
                "title": "A Valve Oil update is running.",
                "message": (step + ". " if step else "")
                           + "Don't turn the printer off until it finishes.",
            })
        elif run.get("state") in ("running", "interrupted"):
            warnings.append({
                "id": "valve_oil_update_interrupted",
                "level": "error",
                "title": "A Valve Oil update was interrupted.",
                "message": ("It stopped at: " + step + ". " if step else "")
                           + FINISH_IT,
            })
        elif run.get("state") == "failed" and self.data_dir is not None and (
                os.path.exists(os.path.join(self.data_dir,
                                            FIRMWARE_UPDATE_RECORD))):
            # A firmware update that stopped partway waits for the operator;
            # its message says what to run, and Update now continues it too
            # unless a rescue has to run first or another release has to
            # finish it.
            message = run.get("message")
            message = (message.strip()[:FAILED_MAX_TEXT]
                       if isinstance(message, str) else "")
            if self._can_continue(run):
                message = (message + " " + CONTINUE_IT).strip()
            warnings.append({
                "id": "valve_oil_firmware_update_failed",
                "level": "error",
                "title": "A QIDI firmware update didn't finish.",
                "message": ("It stopped at: " + step + ". " if step else "")
                           + message,
            })
        items = self.pending.get("pending")
        items = [item for item in items if isinstance(item, dict)] if (
            isinstance(items, list)) else []
        installed = self._installed_release()
        for kind in ("release", "firmware", "board"):
            # A release Klipper already runs is installed, unless the check
            # found it needs installing again (its installed state is missing
            # or has drifted).
            group = [item for item in items if item.get("kind") == kind
                     and not (kind == "release" and installed
                              and item.get("version") == installed
                              and item.get("reinstall") is not True)]
            if not group:
                continue
            reasons = []
            for item in group:
                blockers = item.get("blocked_by")
                for blocker in blockers if isinstance(blockers, list) else []:
                    if not isinstance(blocker, dict):
                        continue
                    reason = (self._pending_text(blocker.get("message"))
                              or self._pending_text(blocker.get("code")))
                    if reason and reason not in reasons:
                        reasons.append(reason)
            if not self.enrolled:
                message = ("Automatic updates are off, so run the Valve Oil"
                           " installer to install it.")
            elif reasons:
                message = ("Automatic updates are on, but it hasn't been"
                           " installed yet: " + "; ".join(reasons) + ".")
            else:
                message = ("Automatic updates are on, so it will be installed"
                           " at the next update check while the printer is"
                           " idle.")
                if kind != "release":
                    message += (" The printer can't be used while it"
                                " installs.")
            warnings.append({
                "id": "valve_oil_%s_waiting" % kind,
                "level": "warning",
                "title": self._waiting_title(kind, group),
                "message": message,
            })
        return warnings

    def get_status(self, eventtime=None):
        try:
            warnings = (self._step_loss() + self._input_shaper()
                        + self._box_firmware() + self._pending_work(eventtime))
        except Exception:
            if not self.failed:
                logging.exception("tltg_printer_warnings: check failed")
            self.failed = True
            return {"warnings": []}
        self.failed = False
        return {"warnings": warnings}


def load_config(config):
    return PrinterWarnings(config)
