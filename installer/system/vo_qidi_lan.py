# vo:qidi-lan
"""The LAN announcements QIDI Studio and QIDI's phone app find the printer by, sent as QIDI's app sent them.

Installed by Valve Oil as /usr/local/lib/vo_qidi_lan.py beside vo_qidi_agent.py, which loads it and runs its loops for
the life of the service. Valve Oil patches these duties out of QIDI's touchscreen app, so they are done here whichever
touchscreen app runs, or none.

While the printer has an IPv4 address, the SSDP sender sends a NOTIFY every 5 seconds from UDP port 1900, alternating
between broadcast to port 5729 and multicast to 239.255.255.250 port 5863, starting with the broadcast. It names the
Wi-Fi address and MAC address when Wi-Fi has an address, else Ethernet's. Like the app, it takes the packet's values
when the network comes up or changes rather than for each packet; it also takes them when the agent connects to
Moonraker. The interfaces are polled every few seconds. Valve Oil keeps the printer off QIDI's cloud, so the packet
says it isn't linked to a QIDI account or connected to QIDI's cloud, as the app said for a printer never linked, with
QIDI's LAN-only mode on.

While Wi-Fi has an IPv4 address, the mkswifi responder listens on UDP port 8989 and answers any datagram containing
"mkswifi" with the app's one-line reply. That reply carries a MAC address QIDI compiled into every app build, not the
printer's, and this keeps it so that QIDI's software sees the same reply.

Sockets are opened again after the network goes down and comes back, and a send or bind that fails is tried again on
the next round.
"""
import asyncio
import logging
import os
import socket
import struct

LOG = logging.getLogger("vo_qidi_lan")

SSDP_PORT = 1900
SSDP_TARGETS = (("255.255.255.255", 5729), ("239.255.255.250", 5863))
SSDP_SECONDS = 5.0
MKSWIFI_PORT = 8989
MKSWIFI_POLL_SECONDS = 3.0
MKSWIFI_REQUEST_BYTES = 1023
MKSWIFI_MAC = "0A:BD:11:88:2F:3C"  # the app's constant, the same in every build and model
NO_IP = "0.0.0.0"
NO_MAC = "00:00:00:00:00:00"
SYS_NET = "/sys/class/net"
WIRELESS = "/proc/net/wireless"
SIOCGIFADDR = 0x8915


# ---- The printer's network ------------------------------------------------------------------------------------------

def ipv4_address(interface):
    """An interface's IPv4 address, or None without one or with only a link-local one."""
    import fcntl
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            packed = fcntl.ioctl(probe.fileno(), SIOCGIFADDR, struct.pack("256s", interface.encode()[:15]))
    except OSError:
        return None
    address = socket.inet_ntoa(packed[20:24])
    if address == NO_IP or address.startswith("169.254."):
        return None
    return address


def interface_mac(interface, sys_net=SYS_NET):
    """An interface's MAC address, upper-cased as the app sends it, or None."""
    try:
        with open(os.path.join(sys_net, interface, "address"), encoding="ascii") as handle:
            address = handle.read().strip().upper()
    except OSError:
        return None
    return address or None


def wifi_signal(wireless=WIRELESS, interface="wlan0"):
    """The app's Wi-Fi level from the signal strength: 3 at -55 dBm or better, 1 at -71 dBm or worse, else 2; 0 without
    a reading."""
    try:
        with open(wireless, encoding="ascii", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return 0
    for line in lines:
        name, _, rest = line.partition(":")
        if name.strip() != interface:
            continue
        try:
            level = float(rest.split()[2].rstrip("."))
        except (IndexError, ValueError):
            return 0
        if level > 0:  # drivers that report an unsigned level
            level -= 256
        if level >= -55:
            return 3
        return 2 if level > -71 else 1
    return 0


def short_model(name):
    """The model as the app shortens it in device names: MAX4, PLUS5, S5."""
    return name[3:] if name.startswith("QD_") else name


def default_alias(serial_number):
    return f"3DP-{serial_number[:3]}-{serial_number[-3:]}"


# ---- SSDP -----------------------------------------------------------------------------------------------------------

def ssdp_packet(host, fields):
    """The app's NOTIFY, for the HOST it is sent to."""
    lines = [
        "NOTIFY * HTTP/1.1",
        f"HOST: {host}",
        "Server: UPnP/1.0",
        f"Location: {fields['ip']}",
        "NT: urn:qiditech-com:device:3dprinter:1",
        f"USN: {fields['serial']}",
        "Cache-Control: max-age=1800",
        f"DevModel.qidi.com: {fields['model']}",
        f"DevName.qidi.com: {fields['name']}",
        f"DevMAC.qidi.com: {fields['mac']}",
        f"DevSignal.qidi.com: {fields['signal']}",
        f"DevConnect.qidi.com: {'true' if fields['connect'] else 'false'}",
        f"DevBind.qidi.com: {'true' if fields['bind'] else 'false'}",
        f"DevStatus.qidi.com: {fields['status']}",
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


class _Quiet(asyncio.DatagramProtocol):
    """Drops what arrives and logs send errors, which come here instead of raising."""

    def __init__(self, name):
        self.name = name

    def error_received(self, exc):
        LOG.debug("%s socket error: %s", self.name, exc)


async def open_ssdp_endpoint(ip):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if ip and ip != NO_IP:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
        sock.bind(("0.0.0.0", SSDP_PORT))
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    transport, _ = await asyncio.get_event_loop().create_datagram_endpoint(lambda: _Quiet("SSDP"), sock=sock)
    return transport


def network_now(ipv4=ipv4_address):
    """(Wi-Fi address, Ethernet address), each None when that interface has none."""
    return ipv4("wlan0"), ipv4("eth0")


class SsdpSender:
    """Sends the app's SSDP NOTIFY every SSDP_SECONDS while the printer has an address."""

    def __init__(self, context, *, network=network_now, mac=interface_mac, signal=wifi_signal,
                 open_endpoint=open_ssdp_endpoint, sleep=asyncio.sleep):
        self.context = context
        self.network, self.mac, self.signal = network, mac, signal
        self.open_endpoint, self.sleep = open_endpoint, sleep
        self.fields = None
        self._stale = False
        context.subscribe({"print_stats": ["state"]})
        context.on_connected(self._moonraker_connected)

    def _moonraker_connected(self):
        self._stale = True

    async def refresh(self, wifi_ip, eth_ip):
        """Take the packet's values, as the app does when a network comes up."""
        if wifi_ip:
            ip, mac, signal = wifi_ip, self.mac("wlan0") or NO_MAC, self.signal()
        elif eth_ip:
            ip, mac, signal = eth_ip, self.mac("eth0") or NO_MAC, 0
        else:
            ip, mac, signal = NO_IP, NO_MAC, 0
        serial = self.context.serial_number
        model = short_model(self.context.name)
        self.fields = {
            "ip": ip, "mac": mac, "signal": signal, "serial": serial or "UNKNOWN", "model": self.context.name,
            "name": f"3DP-{model}-{serial[-3:] if serial else '000'}", "connect": False, "bind": False,
            "status": self.context.print_state() or "standby",
        }
        self._stale = False

    async def run(self):
        network, transport, sent = None, None, 0
        try:
            while True:
                current = self.network()
                wifi_ip, eth_ip = current
                up = bool(wifi_ip or eth_ip)
                if current != network or (up and self._stale):
                    changed_ip = self.fields is not None and (wifi_ip or eth_ip) != self.fields["ip"]
                    network = current
                    if up:
                        await self.refresh(wifi_ip, eth_ip)
                        if transport is not None and changed_ip:
                            transport.close()
                            transport = None
                    elif transport is not None:
                        LOG.info("SSDP notify stopped: no network")
                        transport.close()
                        transport, sent = None, 0
                if up and transport is None:
                    try:
                        transport = await self.open_endpoint(self.fields["ip"])
                        LOG.info("SSDP notify started: ip=%s, model=%s, signal=%s",
                                 self.fields["ip"], self.fields["model"], self.fields["signal"])
                    except OSError as exc:
                        LOG.info("SSDP socket unavailable: %s", exc)
                if transport is not None:
                    host, port = SSDP_TARGETS[sent % len(SSDP_TARGETS)]
                    try:
                        transport.sendto(ssdp_packet(host, self.fields), (host, port))
                    except OSError as exc:
                        LOG.debug("SSDP send failed: %s", exc)
                    sent += 1
                await self.sleep(SSDP_SECONDS)
        finally:
            if transport is not None:
                transport.close()



# ---- mkswifi --------------------------------------------------------------------------------------------------------

class _MkswifiProtocol(asyncio.DatagramProtocol):
    def __init__(self, responder):
        self.responder = responder
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if b"mkswifi" in data[:MKSWIFI_REQUEST_BYTES]:
            LOG.info("Received mkswifi discovery request from %s:%s", addr[0], addr[1])
            asyncio.ensure_future(self.responder.reply(self.transport, addr))

    def error_received(self, exc):
        LOG.debug("mkswifi socket error: %s", exc)


class MkswifiResponder:
    """Answers mkswifi discovery on UDP port 8989 while Wi-Fi has an address, as the app does."""

    def __init__(self, context, *, ipv4=ipv4_address, host="0.0.0.0", port=MKSWIFI_PORT, sleep=asyncio.sleep):
        self.context = context
        self.ipv4, self.host, self.port, self.sleep = ipv4, host, port, sleep
        self.ip = None
        self.address = None

    async def _open(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((self.host, self.port))
            sock.setblocking(False)
        except OSError:
            sock.close()
            raise
        transport, _ = await asyncio.get_event_loop().create_datagram_endpoint(
            lambda: _MkswifiProtocol(self), sock=sock)
        self.address = transport.get_extra_info("sockname")
        return transport

    async def alias(self):
        default = default_alias(self.context.serial_number)
        try:
            alias = await self.context.config_get("user", "alias", default)
        except Exception:
            return default
        return alias if isinstance(alias, str) and alias else default

    async def reply(self, transport, addr):
        alias = await self.alias()
        text = ",".join((
            f"mkswifi:{short_model(self.context.name)}", MKSWIFI_MAC, self.ip or "", "moonraker", "ready", "ASCSDS",
            "wifi", self.context.serial_number, alias))
        if transport.is_closing():
            return
        try:
            transport.sendto(text.encode("utf-8"), addr)
        except OSError as exc:
            LOG.debug("mkswifi reply failed: %s", exc)

    async def run(self):
        transport = None
        try:
            while True:
                self.ip = self.ipv4("wlan0")
                if self.ip and transport is None:
                    try:
                        transport = await self._open()
                        LOG.info("mkswifi discovery listening on port %s", self.address[1])
                    except OSError as exc:
                        LOG.info("mkswifi socket unavailable: %s", exc)
                elif not self.ip and transport is not None:
                    LOG.info("mkswifi discovery stopped: Wi-Fi has no address")
                    transport.close()
                    transport = None
                await self.sleep(MKSWIFI_POLL_SECONDS)
        finally:
            if transport is not None:
                transport.close()


def setup(context):
    ssdp = SsdpSender(context)
    mkswifi = MkswifiResponder(context)
    context.background(ssdp.run)
    context.background(mkswifi.run)
    return ssdp, mkswifi
