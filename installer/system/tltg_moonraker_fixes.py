# tltg-optimized:moonraker-fixes
# Moonraker fixes for Valve Oil, installed in QIDI's Moonraker virtualenv and
# imported by tltg-moonraker-fixes.pth.
#
# QIDI's Moonraker connects to QIDI's cloud MQTT broker from its event loop.
# The TCP connect is awaited, but the TLS handshake that follows runs inside
# ExtPahoClient.reconnect with the socket's timeout set to the MQTT keepalive,
# 60 s. A broker that accepts the connection and then doesn't answer the
# handshake stops Moonraker's event loop for that long: no API, no websocket,
# and no Klipper state until it times out. At boot the touchscreen then
# reports "Printer service failed to start".
#
# Here the handshake runs in a worker thread, for at most 10 s, before
# QIDI's code gets the socket. QIDI's reconnect then receives a socket that
# is already through its handshake, so its own wrap and handshake return at
# once. Errors reach QIDI's retry loop as they did. Plain MQTT connections
# and anything this module doesn't recognize are left as they are.
#
# QIDI's code decrypts the saved account, password, and broker address
# through the touchscreen app, which connects to Moonraker as an extension
# agent, and it asks whichever agent registered first. At boot the first
# connect runs before the touchscreen app has connected, so it goes to QIDI's
# fallback broker with no account, is refused with "Not authorized", and
# leaves that warning in Fluidd. A slicer that registers as an agent before
# the touchscreen app gets the decrypt requests instead. Here the first
# connect after Moonraker starts waits for the touchscreen app, for at most
# APP_WAIT_TIMEOUT, and every decrypt goes to the touchscreen app when it is
# connected.
#
# The .pth file imports this module in every Python started from Moonraker's
# virtualenv, so importing it only registers an import hook for
# moonraker.components.mqtt, and the modules the fix uses are imported when
# Moonraker loads that component.

import sys

MQTT_MODULE = "moonraker.components.mqtt"
HANDSHAKE_TIMEOUT = 10.
APP_WAIT_TIMEOUT = 120.
APP_POLL_INTERVAL = 0.5


class _HandshakenContext:
    """Stands in for the client's SSL context for one reconnect.

    QIDI's reconnect wraps the socket it is given with the client's SSL
    context. For the socket that already finished its handshake this returns
    it as it is and puts the real context back; anything else goes to the
    real context.
    """

    def __init__(self, client, context, tls):
        object.__setattr__(self, "_tltg", (client, context, tls))

    def __getattr__(self, name):
        return getattr(self._tltg[1], name)

    def __setattr__(self, name, value):
        setattr(self._tltg[1], name, value)

    def wrap_socket(self, sock, *args, **kwargs):
        client, context, tls = self._tltg
        if client._ssl_context is self:
            client._ssl_context = context
        if sock is tls:
            return tls
        return context.wrap_socket(sock, *args, **kwargs)


def _handshake(sock, context, host, timeout):
    # As ExtPahoClient.reconnect does it, apart from the timeout.
    sock.settimeout(timeout)
    try:
        tls = context.wrap_socket(sock, server_hostname=host,
                                  do_handshake_on_connect=False)
    except ssl.CertificateError:
        raise
    except ValueError:
        tls = context.wrap_socket(sock, do_handshake_on_connect=False)
    tls.settimeout(timeout)
    tls.do_handshake()
    return tls


def _is_touchscreen_app(connection):
    # QIDI's touchscreen app identifies as an agent named after the printer
    # model, such as QD_MAX4 or QD_PLUS5, with the URL "local".
    data = getattr(connection, "client_data", None)
    if not isinstance(data, dict):
        return False
    name = data.get("name")
    return data.get("url") == "local" and isinstance(name, str) \
        and name.startswith("QD_")


def _agents(component):
    """QIDI's registry of extension agents, or None if it can't be read."""
    try:
        extensions = component.server.lookup_component("extensions", None)
    except Exception:
        return None
    agents = getattr(extensions, "agents", None)
    return agents if isinstance(agents, dict) else None


def _touchscreen_app_first(agents):
    """Puts the touchscreen app's agent first, where QIDI's decrypt looks.

    Returns whether the touchscreen app is connected.
    """
    for name, connection in agents.items():
        if _is_touchscreen_app(connection):
            break
    else:
        return False
    if next(iter(agents)) != name:
        others = [(key, value) for key, value in agents.items()
                  if key != name]
        agents.clear()
        agents[name] = connection
        agents.update(others)
    return True


def _decrypt_through_touchscreen_app(decrypt):
    # Returns QIDI's coroutine without running it, so none of QIDI's log
    # lines come from inside this module.
    def _qidiclient_decrypt_hex(self, *args, **kwargs):
        agents = _agents(self)
        if agents:
            _touchscreen_app_first(agents)
        return decrypt(self, *args, **kwargs)
    _qidiclient_decrypt_hex._tltg_decrypt = decrypt
    return _qidiclient_decrypt_hex


async def _after_touchscreen_app(loop, component, reconnect):
    """Runs QIDI's first MQTT connect once the touchscreen app is connected."""
    try:
        agents = _agents(component)
        if agents is not None and not _touchscreen_app_first(agents):
            logging.info("tltg_moonraker_fixes: MQTT waiting for the "
                         "touchscreen app before connecting")
            deadline = time.monotonic() + APP_WAIT_TIMEOUT
            while True:
                await asyncio.sleep(APP_POLL_INTERVAL)
                agents = _agents(component)
                if agents is None:
                    break
                if _touchscreen_app_first(agents):
                    logging.info("tltg_moonraker_fixes: touchscreen app "
                                 "connected; MQTT connecting")
                    break
                if time.monotonic() >= deadline:
                    logging.info("tltg_moonraker_fixes: touchscreen app not "
                                 "connected after %.0f s; MQTT connecting "
                                 "without it", APP_WAIT_TIMEOUT)
                    break
    except BaseException:
        reconnect.close()
        raise
    # QIDI's reconnect runs in a task of its own, so its log lines keep their
    # own origin. Cancelling this task cancels it too.
    return await loop.create_task(reconnect)


class _EventLoop:
    """The MQTT component's event loop, with TLS handshakes off the loop and
    the first connect held for the touchscreen app."""

    def __init__(self, loop, component):
        self._tltg_loop = loop
        self._tltg_component = component
        self._tltg_hold = True

    def __getattr__(self, name):
        return getattr(self._tltg_loop, name)

    def create_task(self, coro, *args, **kwargs):
        loop = self._tltg_loop
        if self._tltg_hold \
                and getattr(coro, "__name__", None) == "_do_reconnect":
            self._tltg_hold = False
            coro = _after_touchscreen_app(loop, self._tltg_component, coro)
        return loop.create_task(coro, *args, **kwargs)

    async def create_socket_connection(self, address, timeout=None):
        loop = self._tltg_loop
        sock = await loop.create_socket_connection(address, timeout)
        client = getattr(self._tltg_component, "client", None)
        context = getattr(client, "_ssl_context", None)
        if isinstance(context, _HandshakenContext):
            # An earlier connection attempt ended before reconnect used it.
            context = client._ssl_context = context._tltg[1]
        if context is None or not getattr(client, "_ssl", False) \
                or not isinstance(getattr(client, "_host", None), str):
            return sock
        try:
            tls = await loop.run_in_thread(
                _handshake, sock, context, client._host, HANDSHAKE_TIMEOUT)
        except BaseException:
            # Also ends a handshake still waiting in its thread.
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
            raise
        client._ssl_context = _HandshakenContext(client, context, tls)
        return tls


class _EventLoopAttribute:
    """MQTTClient.eventloop: what QIDI's code assigns comes back wrapped.

    A descriptor on the class, so none of QIDI's code runs inside this
    module and its log lines keep their own origin.
    """

    def __set__(self, component, loop):
        if not isinstance(loop, _EventLoop):
            if inspect.iscoroutinefunction(
                    getattr(loop, "create_socket_connection", None)) \
                    and callable(getattr(loop, "run_in_thread", None)):
                loop = _EventLoop(loop, component)
                logging.info("tltg_moonraker_fixes: MQTT TLS handshake "
                             "moved off the event loop; first connect waits "
                             "for the touchscreen app")
            else:
                logging.info("tltg_moonraker_fixes: MQTT TLS handshake left "
                             "on the event loop; Moonraker's event loop "
                             "differs from the version this fix is for")
        component.__dict__["eventloop"] = loop

    def __get__(self, component, owner=None):
        if component is None:
            return self
        try:
            return component.__dict__["eventloop"]
        except KeyError:
            raise AttributeError("eventloop") from None


def apply(module):
    """Patch QIDI's moonraker.components.mqtt after it is imported."""
    global asyncio, inspect, logging, socket, ssl, time
    import asyncio
    import inspect
    import logging
    import socket
    import ssl
    import time
    component_class = getattr(module, "MQTTClient", None)
    if not isinstance(component_class, type) \
            or "eventloop" in vars(component_class) \
            or not callable(getattr(component_class, "_do_reconnect", None)):
        logging.info("tltg_moonraker_fixes: MQTT TLS handshake left on the "
                     "event loop; Moonraker's MQTT code differs from the "
                     "version this fix is for")
        return False
    component_class.eventloop = _EventLoopAttribute()
    decrypt = vars(component_class).get("_qidiclient_decrypt_hex")
    if callable(decrypt) and not hasattr(decrypt, "_tltg_decrypt"):
        component_class._qidiclient_decrypt_hex = \
            _decrypt_through_touchscreen_app(decrypt)
    return True


class _Finder:
    """Patches moonraker.components.mqtt once, when Moonraker imports it."""

    def find_spec(self, name, path=None, target=None):
        if name != MQTT_MODULE:
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
                apply(module)
            except Exception:
                import logging
                logging.exception("tltg_moonraker_fixes: MQTT fix failed")
        try:
            loader.exec_module = patched_exec_module
        except Exception:
            pass
        return spec


def install():
    if not any(isinstance(finder, _Finder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _Finder())


install()
