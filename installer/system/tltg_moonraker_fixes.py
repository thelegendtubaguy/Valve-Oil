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
# The .pth file imports this module in every Python started from Moonraker's
# virtualenv, so importing it only registers an import hook for
# moonraker.components.mqtt, and the modules the fix uses are imported when
# Moonraker loads that component.

import sys

MQTT_MODULE = "moonraker.components.mqtt"
HANDSHAKE_TIMEOUT = 10.


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


class _EventLoop:
    """The MQTT component's event loop, with TLS handshakes off the loop."""

    def __init__(self, loop, component):
        self._tltg_loop = loop
        self._tltg_component = component

    def __getattr__(self, name):
        return getattr(self._tltg_loop, name)

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
                             "moved off the event loop")
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
    global inspect, logging, socket, ssl
    import inspect
    import logging
    import socket
    import ssl
    component_class = getattr(module, "MQTTClient", None)
    if not isinstance(component_class, type) \
            or "eventloop" in vars(component_class) \
            or not callable(getattr(component_class, "_do_reconnect", None)):
        logging.info("tltg_moonraker_fixes: MQTT TLS handshake left on the "
                     "event loop; Moonraker's MQTT code differs from the "
                     "version this fix is for")
        return False
    component_class.eventloop = _EventLoopAttribute()
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
