# Which Klipper runs: QIDI's, or Valve Oil's.
#
# Valve Oil's Klipper is upstream Klipper v0.14 with QIDI's changes,
# installed beside QIDI's ~/klipper at ~/vo-klipper, and off until the
# operator chooses it. The launcher in front of klipper.service starts the
# Klipper printer_data/.vo_klipper.json names, starts QIDI's instead when
# Valve Oil's can't start, and records each start in .vo_klipper_status.json.
# This module runs in both trees:
#
#   VO_KLIPPER                     says which Klipper is chosen, which runs,
#                                  and why QIDI's runs when it isn't chosen
#   VO_KLIPPER USE=VALVE_OIL|QIDI  switches, refused during a print. Valve
#                                  Oil's status service writes the choice
#                                  and restarts Klipper; the request runs on
#                                  its own thread and its answer comes back
#                                  through the reactor.
#
# When Klipper is ready it writes .vo_klipper_ready.json with the launch id
# the launcher passes in VO_KLIPPER_LAUNCH_ID, so the launcher knows this
# start came up. A Klipper started without the launcher has no id and writes
# nothing.
#
#   status: selected and running ("valve_oil" or "qidi"), reason (why the
#   running Klipper isn't the chosen one, or null), available (Valve Oil's
#   Klipper is installed), version (Valve Oil's Klipper's, when known)

import json
import logging
import os
import threading
import urllib.error
import urllib.request

CHOICE_FILE = ".vo_klipper.json"
STATUS_FILE = ".vo_klipper_status.json"
READY_FILE = ".vo_klipper_ready.json"
LAUNCH_ID_ENV = "VO_KLIPPER_LAUNCH_ID"
TREE_ROOT = "/home/qidi/vo-klipper"
LAUNCHER = "/usr/local/lib/vo_klipper_launch.py"
# Valve Oil's status service (installer/system/vo_status.py).
SWITCH_URL = "http://127.0.0.1:7133/api/klipper"
SWITCH_TIMEOUT = 30.
POLL = 5.
MAX_BYTES = 8192
MAX_TEXT = 300
CHOICES = {"VALVE_OIL": "valve_oil", "QIDI": "qidi"}
NAMES = {"valve_oil": "Valve Oil's Klipper", "qidi": "QIDI's Klipper"}


def klipper_root():
    """The Klipper tree this module runs in: klippy/extras/<this> -> tree."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def tree_of(path):
    return ("valve_oil" if path == TREE_ROOT
            or path.startswith(TREE_ROOT + "/") else "qidi")


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.loads(handle.read(MAX_BYTES + 1))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _text(value):
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:MAX_TEXT]


def _sentence(text):
    return text.rstrip(". ") + "."


class KlipperSwitch:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        start_args = self.printer.get_start_args()
        config_file = start_args.get("config_file")
        # printer_data/config/printer.cfg -> printer_data
        self.data_dir = (os.path.dirname(os.path.dirname(
            os.path.abspath(config_file))) if config_file else None)
        self.running = tree_of(klipper_root())
        self.launch_id = os.environ.get(LAUNCH_ID_ENV) or None
        self.software_version = _text(start_args.get("software_version"))
        self.print_stats = None
        self.asking = None
        self.read_next = 0.
        self.state = None
        self.printer.register_event_handler("klippy:connect", self._connect)
        self.printer.register_event_handler("klippy:ready", self._ready)
        self.gcode.register_command(
            "VO_KLIPPER", self.cmd_VO_KLIPPER,
            desc="Say which Klipper runs, or switch with USE=VALVE_OIL or"
                 " USE=QIDI")

    def _connect(self):
        self.print_stats = self.printer.lookup_object("print_stats", None)

    def _path(self, name):
        return os.path.join(self.data_dir, name)

    # ------------------------------------------------------------ ready
    def _ready(self):
        if self.launch_id is None or self.data_dir is None:
            return
        # Other modules' ready handlers run after this one and can still
        # stop Klipper, so acknowledge once they have all run and Klipper
        # is still ready.
        reactor = self.printer.get_reactor()
        reactor.register_timer(self._acknowledge, reactor.monotonic() + 1.)

    def _acknowledge(self, eventtime):
        if self.printer.get_state_message()[1] != "ready":
            logging.info("vo_klipper: Klipper didn't stay ready; no ready "
                         "acknowledgement for this start")
            return self.printer.get_reactor().NEVER
        self._write_ready()
        return self.printer.get_reactor().NEVER

    def _write_ready(self):
        path = self._path(READY_FILE)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"launch_id": self.launch_id}) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except OSError:
            logging.exception("vo_klipper: couldn't write %s", path)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # ----------------------------------------------------------- status
    def _refresh(self, eventtime):
        if self.state is not None and eventtime is not None \
                and eventtime < self.read_next:
            return
        self.read_next = (eventtime or 0.) + POLL
        choice = _read(self._path(CHOICE_FILE)) if self.data_dir else {}
        selected = ("valve_oil" if choice.get("use") == "valve_oil"
                    else "qidi")
        launch = _read(self._path(STATUS_FILE)) if self.data_dir else {}
        # Only the launcher's record of this start describes this Klipper.
        if self.launch_id is None or launch.get("launch_id") != self.launch_id:
            launch = {}
        available = (os.path.isfile(LAUNCHER)
                     and os.path.isdir(os.path.join(TREE_ROOT, "klippy")))
        reason = None
        if selected != self.running:
            if selected == "valve_oil" and not available:
                reason = "Valve Oil's Klipper isn't installed"
            elif launch.get("selected") == selected:
                reason = (_text(launch.get("reason"))
                          or "the launcher started %s" % NAMES[self.running])
            elif selected == "valve_oil" and self.launch_id is None:
                reason = "Klipper was started without Valve Oil's launcher"
            else:
                reason = ("Klipper hasn't restarted since %s was chosen"
                          % NAMES[selected])
        version = _text(launch.get("version"))
        if version is None and self.running == "valve_oil":
            version = self.software_version
        self.state = {"selected": selected, "running": self.running,
                      "reason": reason, "available": available,
                      "version": version}

    def get_status(self, eventtime=None):
        self._refresh(eventtime)
        return dict(self.state)

    def fallback(self, eventtime=None):
        """Why QIDI's Klipper runs although Valve Oil's is chosen, or None."""
        state = self.get_status(eventtime)
        if state["selected"] != "valve_oil" or state["running"] != "qidi":
            return None
        return state["reason"] or "QIDI's Klipper started instead"

    # ---------------------------------------------------------- command
    def _printing(self):
        if self.print_stats is None:
            return False
        state = self.print_stats.get_status(
            self.reactor.monotonic()).get("state")
        return state in ("printing", "paused")

    def _report(self):
        state = self.get_status()
        selected, running = state["selected"], state["running"]
        version = state["version"]
        if selected == running == "valve_oil":
            return ("Valve Oil's Klipper%s is running. Go back to QIDI's"
                    " Klipper with VO_KLIPPER USE=QIDI."
                    % (" " + version if version else ""))
        if selected == running == "qidi":
            if not state["available"]:
                return ("QIDI's Klipper is running. Valve Oil's Klipper isn't"
                        " installed.")
            return ("QIDI's Klipper is running. Valve Oil's Klipper is off;"
                    " switch to it with VO_KLIPPER USE=VALVE_OIL.")
        if selected == "valve_oil":
            return ("Valve Oil's Klipper is chosen, but QIDI's Klipper is"
                    " running: %s Try again with VO_KLIPPER USE=VALVE_OIL, or"
                    " stay on QIDI's with VO_KLIPPER USE=QIDI."
                    % _sentence(state["reason"]))
        return ("QIDI's Klipper is chosen, but Valve Oil's Klipper is"
                " running: %s" % _sentence(state["reason"]))

    def cmd_VO_KLIPPER(self, gcmd):
        use = gcmd.get("USE", None)
        if use is None:
            gcmd.respond_info(self._report())
            return
        choice = CHOICES.get(use.strip().upper())
        if choice is None:
            raise gcmd.error("VO_KLIPPER USE= takes VALVE_OIL or QIDI")
        if self._printing():
            raise gcmd.error(
                "Klipper can't be switched during a print. Switch once the"
                " print ends or is cancelled.")
        if self.asking is not None:
            raise gcmd.error("Already switching to %s" % NAMES[self.asking])
        self.asking = choice
        gcmd.respond_info("Switching to %s..." % NAMES[choice])
        threading.Thread(target=self._ask, args=(choice,),
                         name="vo-klipper-switch", daemon=True).start()

    def _ask(self, choice):
        # Runs on its own thread: the status service checks what it can't
        # from here, such as an install running, before Klipper restarts.
        request = urllib.request.Request(
            SWITCH_URL, method="POST",
            data=json.dumps({"use": choice, "source": "console"}).encode(),
            headers={"Content-Type": "application/json",
                     "X-Valve-Oil": "status"})
        failed = True
        try:
            with urllib.request.urlopen(request, timeout=SWITCH_TIMEOUT) as r:
                r.read()
            message = "%s chosen. Klipper restarts now." % NAMES[choice]
            failed = False
        except urllib.error.HTTPError as exc:
            try:
                error = _text(json.loads(exc.read(MAX_BYTES)).get("error"))
            except (OSError, ValueError, AttributeError):
                error = None
            finally:
                exc.close()
            message = "Klipper wasn't switched: %s" % (
                error or "Valve Oil's status service answered %d" % exc.code)
        except (OSError, ValueError) as exc:
            message = ("Klipper wasn't switched: Valve Oil's status service"
                       " didn't answer (%s)" % (exc,))
        if failed:
            logging.warning("vo_klipper: %s", message)
        self.reactor.register_async_callback(
            lambda eventtime: self._answered(message, failed))

    def _answered(self, message, failed):
        self.asking = None
        if failed:
            self.gcode.respond_raw("!! " + message)
        else:
            self.gcode.respond_info(message)


def load_config(config):
    return KlipperSwitch(config)
