# vo:moonraker-fixes
# Moonraker fixes for Valve Oil, installed in QIDI's Moonraker virtualenv and
# imported by vo-moonraker-fixes.pth.
#
# Valve Oil keeps the printer off QIDI's servers. QIDI's Moonraker always
# loads QIDI's compiled mqtt component, which connects only to QIDI's cloud
# broker with the account QIDI's cloud gave the printer, and ignores the
# [mqtt] section of moonraker.conf. Here Moonraker loads upstream Moonraker's
# MQTT component instead (vo_moonraker_mqtt.py, beside this module): with an
# [mqtt] section it connects to that broker as Moonraker documents, for
# example for Home Assistant; without one it connects nowhere. QIDI's
# frp_manager acts only when QIDI's mqtt component calls it, so it starts no
# FRP.
#
# A few keys of QIDI's config table are kept at one value (PINNED):
# - QIDI's LAN-only mode (general.lan_mode) stays on: the touchscreen app then
#   shows LAN Only Mode instead of a cloud login, and stops its own cloud
#   requests before they connect.
# - QIDI's AI detection switches (ai.*, which the touchscreen app writes "1" or
#   "0") stay off. Valve Oil keeps QIDI's AI detection service stopped, so a
#   switch turned on would detect nothing.
# Each is written when Moonraker starts and after any config write that
# changed it; a write of a pinned key itself stores the pinned value and
# answers with it, and skips QIDI's handler, which for general.lan_mode would
# otherwise close the operator's MQTT connection or reconnect QIDI's.
#
# QIDI's /api/qidiclient/* endpoints are served by the one agent connected
# and fail when a slicer has registered as an agent too. They prefer the
# touchscreen app's agent here, which is Valve Oil's agent under the app's
# name.
#
# QIDI's Moonraker checks over D-Bus, as it starts, that the touchscreen app
# and Klipper answer, and only logs what they say. Neither call has a time
# limit, so a Klipper that holds its D-Bus name and doesn't answer keeps
# Moonraker from starting at all: no API, no Fluidd, no touchscreen, until the
# printer restarts (issue #390). Here each D-Bus call made while QIDI's D-Bus
# component starts waits at most DBUS_STARTUP_CALL_TIMEOUT, and one that
# doesn't answer by then gets an error in the form QIDI's code returns for a
# failed call. Its calls once Moonraker has started are left as they are.
#
# The .pth file imports this module in every Python started from Moonraker's
# virtualenv, so importing it only registers import hooks, and the modules
# the fixes use are imported when Moonraker loads the components.

import sys

MQTT_MODULE = "moonraker.components.mqtt"
DATABASE_MODULE = "moonraker.components.database"
EXTENSIONS_MODULE = "moonraker.components.extensions"
DBUS_MODULE = "moonraker.components.dbus_manager"
MQTT_FILE_NAME = "vo_moonraker_mqtt.py"
# QIDI's config table key: (the value it is kept at, what the log says when a
# write is answered with it).
PINNED = {
    "general.lan_mode": ("1", "LAN only mode stays on"),
    "ai.noodle_detection": ("0", "QIDI's AI detection stays off"),
    "ai.spool_detection": ("0", "QIDI's AI detection stays off"),
    "ai.foreign_object_detection": ("0", "QIDI's AI detection stays off"),
    "ai.bed_detection": ("0", "QIDI's AI detection stays off"),
}
DBUS_STARTUP_CALL_TIMEOUT = 5.


def _imports():
    global asyncio, inspect, logging, os
    import asyncio
    import inspect
    import logging
    import os


# ---- MQTT -------------------------------------------------------------------

def mqtt_file():
    import os
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        MQTT_FILE_NAME)


class _MqttFinder:
    """Loads upstream Moonraker's MQTT component in place of QIDI's."""

    def find_spec(self, name, path=None, target=None):
        if name != MQTT_MODULE:
            return None
        if self in sys.meta_path:
            sys.meta_path.remove(self)
        import importlib.util
        import logging
        import os
        source = mqtt_file()
        if not os.path.isfile(source):
            logging.info("vo_moonraker_fixes: %s is missing; QIDI's MQTT "
                         "component loads", source)
            return None
        logging.info("vo_moonraker_fixes: Moonraker's MQTT component "
                     "loads in place of QIDI's")
        return importlib.util.spec_from_file_location(name, source)


# ---- Pinned config keys ----------------------------------------------------

def _pinned_key(section, key):
    name = "%s.%s" % (str(section).strip(), str(key).strip())
    return name if name in PINNED else None


async def _pin(database, names):
    items = getattr(database, "config_items", None)
    for name in names:
        value = PINNED[name][0]
        try:
            cursor = await database.get_value_from_config(*name.split(".", 1))
            row = dict(await cursor.fetchone() or {})
            if str(row.get("value")) != value:
                await database.update_value_from_config(*name.split(".", 1),
                                                        value)
                logging.info("vo_moonraker_fixes: %s kept at %s", name, value)
        except Exception:
            logging.exception("vo_moonraker_fixes: keeping %s at %s failed",
                              name, value)
        if isinstance(items, dict) and name in items:
            items[name] = value


def _unpinned(database):
    """The pinned keys a write changed: QIDI's handlers keep config_items as
    they write the table. All of them when it can't tell."""
    items = getattr(database, "config_items", None)
    if not isinstance(items, dict):
        return list(PINNED)
    return [name for name, (value, _) in PINNED.items()
            if str(items.get(name, value)) != value]


def _then_pin(method):
    # Startup reads the table; each later write is checked in memory, as the
    # print state writes many times a minute.
    startup = method.__name__ == "_init_config_items"

    async def pinned(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        finally:
            names = list(PINNED) if startup else _unpinned(self)
            if names:
                await _pin(self, names)
    pinned._vo_pinned = method
    pinned.__name__ = method.__name__
    return pinned


def _pinned_stay(handler):
    async def _handle_config_update_request(self, web_request, *args,
                                            **kwargs):
        try:
            section = web_request.get("section", None)
            key = web_request.get("key", None)
        except Exception:
            section = key = None
        name = _pinned_key(section, key)
        if name is not None:
            pinned, outcome = PINNED[name]
            try:
                value = web_request.get("value", None)
            except Exception:
                value = None
            if str(value) != pinned:
                logging.info("vo_moonraker_fixes: %s set to %r was answered "
                             "with %s; %s", name, value, pinned, outcome)
            await _pin(self, [name])
            return {"status": "success", "result": {name: pinned}}
        try:
            return await handler(self, web_request, *args, **kwargs)
        finally:
            names = _unpinned(self)
            if names:
                await _pin(self, names)
    _handle_config_update_request._vo_pinned = handler
    return _handle_config_update_request


DATABASE_METHODS = ("_init_config_items", "_handle_config_update_request",
                    "_handle_config_update_batch_request",
                    "_handle_config_delete_request")


def apply_database(module):
    """Patch QIDI's moonraker.components.database after it is imported."""
    _imports()
    for component_class in vars(module).values():
        if not isinstance(component_class, type):
            continue
        methods = {name: vars(component_class).get(name)
                   for name in DATABASE_METHODS}
        if not all(inspect.iscoroutinefunction(method)
                   for method in methods.values()):
            continue
        for name, method in methods.items():
            if hasattr(method, "_vo_pinned"):
                continue
            wrap = _pinned_stay \
                if name == "_handle_config_update_request" else _then_pin
            setattr(component_class, name, wrap(method))
        return True
    logging.info("vo_moonraker_fixes: QIDI's LAN-only mode and AI detection "
                 "switches not kept; Moonraker's database code differs from "
                 "the version this fix is for")
    return False


# ---- D-Bus startup checks ---------------------------------------------------

def _dbus_startup_bounded(component_init):
    async def component_init_(self, *args, **kwargs):
        self.__dict__["_vo_dbus_starting"] = True
        try:
            return await component_init(self, *args, **kwargs)
        finally:
            self.__dict__["_vo_dbus_starting"] = False
    component_init_._vo_dbus_startup = component_init
    return component_init_


def _dbus_call_bounded(send_request):
    async def send_request_(self, service, *args, **kwargs):
        call = send_request(self, service, *args, **kwargs)
        if not self.__dict__.get("_vo_dbus_starting"):
            return await call
        try:
            return await asyncio.wait_for(call, DBUS_STARTUP_CALL_TIMEOUT)
        except asyncio.TimeoutError:
            logging.info("vo_moonraker_fixes: %s did not answer Moonraker's "
                         "D-Bus check within %.0f s; Moonraker starts "
                         "without its answer", service,
                         DBUS_STARTUP_CALL_TIMEOUT)
            # As QIDI's send_request returns a call that failed.
            return {"error": "Low-level DBus call failed",
                    "details": "No answer within %.0f s"
                    % DBUS_STARTUP_CALL_TIMEOUT}
    send_request_._vo_dbus_call = send_request
    return send_request_


def apply_dbus(module):
    """Patch QIDI's moonraker.components.dbus_manager after it is
    imported."""
    _imports()
    component_class = getattr(module, "DbusManager", None)
    members = vars(component_class) \
        if isinstance(component_class, type) else {}
    component_init = members.get("component_init")
    send_request = members.get("send_request")
    if callable(getattr(send_request, "_vo_dbus_call", None)):
        return True
    if not inspect.iscoroutinefunction(component_init) \
            or not inspect.iscoroutinefunction(send_request):
        logging.info("vo_moonraker_fixes: D-Bus startup checks left "
                     "without a time limit; Moonraker's D-Bus code differs "
                     "from the version this fix is for")
        return False
    component_class.component_init = _dbus_startup_bounded(component_init)
    component_class.send_request = _dbus_call_bounded(send_request)
    return True


# ---- Agent selection --------------------------------------------------------

def _is_touchscreen_app(connection):
    # QIDI's touchscreen app identifies as an agent named after the printer
    # model, with the URL "local": QD_MAX4 or QD_PLUS5, and S5 on the Smart 5.
    data = getattr(connection, "client_data", None)
    if not isinstance(data, dict):
        return False
    name = data.get("name")
    return data.get("url") == "local" and isinstance(name, str) \
        and (name.startswith("QD_") or name == "S5")


def _app_agent_first(select):
    """ExtensionManager._select_qidiclient: with several agents and none
    named, the touchscreen app's agent rather than an error."""
    def _select_qidiclient(self, web_request, *args, **kwargs):
        agents = getattr(self, "agents", None)
        try:
            named = web_request.get_str("agent", None)
        except Exception:
            named = None
        if named is None and isinstance(agents, dict) and len(agents) > 1:
            for connection in agents.values():
                if _is_touchscreen_app(connection):
                    return connection
        return select(self, web_request, *args, **kwargs)
    _select_qidiclient._vo_select = select
    return _select_qidiclient


def apply_extensions(module):
    """Patch QIDI's moonraker.components.extensions after it is imported."""
    _imports()
    for component_class in vars(module).values():
        select = vars(component_class).get("_select_qidiclient") \
            if isinstance(component_class, type) else None
        if callable(select):
            if not hasattr(select, "_vo_select"):
                component_class._select_qidiclient = _app_agent_first(select)
            return True
    logging.info("vo_moonraker_fixes: agent selection left as it is; "
                 "Moonraker's extensions code differs from the version "
                 "this fix is for")
    return False


# ---- Import hooks -----------------------------------------------------------

class _Finder:
    """Patches one of QIDI's Moonraker modules once, when Moonraker imports
    it."""

    def __init__(self, module_name, patch):
        self.module_name = module_name
        self.patch = patch

    def find_spec(self, name, path=None, target=None):
        if name != self.module_name:
            return None
        sys.meta_path.remove(self)
        import importlib.util
        spec = importlib.util.find_spec(name)
        loader = getattr(spec, "loader", None)
        exec_module = getattr(loader, "exec_module", None)
        if exec_module is None:
            return spec

        def patched_exec_module(module):
            exec_module(module)
            try:
                self.patch(module)
            except Exception:
                import logging
                logging.exception("vo_moonraker_fixes: fix for %s failed",
                                  name)
        try:
            loader.exec_module = patched_exec_module
        except Exception:
            pass
        return spec


def install():
    if not any(isinstance(finder, _MqttFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _MqttFinder())
    installed = {finder.module_name for finder in sys.meta_path
                 if isinstance(finder, _Finder)}
    for module_name, patch in ((DATABASE_MODULE, apply_database),
                               (EXTENSIONS_MODULE, apply_extensions),
                               (DBUS_MODULE, apply_dbus)):
        if module_name not in installed:
            sys.meta_path.insert(0, _Finder(module_name, patch))


install()
