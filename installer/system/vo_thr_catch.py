"""Valve Oil THR catch, for vo-thr-firmware.service, which finishes an interrupted THR flash at boot.

A THR bootloader stays after power-on only if a packet reaches it within its startup window, four to ten seconds
after power-on depending on the build; one that answered an identity request is then held for ten seconds by each
identity request. This runs as root with Python's standard library only (python3 -I -S). It gives the THR's serial port
to the dialout group, as udev does later in boot, so the THR tool can open it as the printer's user; sends only
identity requests, never a start or data packet; and records in CATCH whether and when the bootloader answered and
when it last sent (scripts/flash_thr_firmware.py read_catch).

  --early  started by the generator (vo-thr-catch-generator) about a second after the kernel: catches, and holds an
           answering bootloader until the unit joins it (or HOLD_LIMIT seconds pass).
  (none)   the unit's first step: stops an early catch still holding and keeps its record, or catches now if none ran.

Installed by Valve Oil at /usr/local/lib/vo_thr_catch.py.
"""
import json
import os
import select
import shutil
import signal
import sys
import termios
import time

DEVICE = "/dev/ttyS4"
CATCH = "/run/vo-thr-catch.json"
PID = "/run/vo-thr-catch.pid"
HOLD_INTERVAL = 2.0
HOLD_LIMIT = 60.0
# Frame kind 0, payload 01 00, CRC16: the THR tool's identity request.
IDENTITY = bytes.fromhex("a50000020100af7b")
SECONDS = 1.5


def elapsed():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def crc16(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xA001 if value & 1 else 0)
    return value ^ 0xFFFF


def frames(buffer):
    """Complete, CRC-valid frames at the front of buffer, as (kind, payload); consumes what it reads."""
    while buffer:
        if buffer[0] != 0xA5:
            del buffer[0]
            continue
        if len(buffer) < 4:
            return
        size = int.from_bytes(buffer[2:4], "big")
        if size > 100:
            del buffer[0]
            continue
        if len(buffer) < size + 6:
            return
        raw = bytes(buffer[:size + 6])
        del buffer[:size + 6]
        if int.from_bytes(raw[-2:], "big") == crc16(raw[1:-2]):
            yield raw[1], raw[4:-2]


def raw_115200(fd):
    attributes = termios.tcgetattr(fd)
    attributes[0] = 0  # iflag
    attributes[1] = 0  # oflag
    attributes[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag
    attributes[3] = 0  # lflag
    attributes[4] = attributes[5] = termios.B115200
    attributes[6][termios.VMIN] = 0
    attributes[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attributes)
    termios.tcflush(fd, termios.TCIOFLUSH)


def catch(hold):
    """Catch, and with hold, keep an answering bootloader held until SIGTERM or HOLD_LIMIT."""
    stop = []
    signal.signal(signal.SIGTERM, lambda signum, frame: stop.append(signum))
    result = {"started": elapsed()}
    try:
        shutil.chown(DEVICE, group="dialout")
        os.chmod(DEVICE, 0o660)
        fd = os.open(DEVICE, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        received = bytearray()  # everything that came back, for the record
        try:
            raw_115200(fd)
            buffer = bytearray()
            end = time.monotonic() + SECONDS
            while time.monotonic() < end and "reply" not in result:
                os.write(fd, IDENTITY)
                result.setdefault("first_sent", elapsed())
                result["last_sent"] = elapsed()
                wait = time.monotonic() + 0.2
                while time.monotonic() < wait and "reply" not in result:
                    if select.select([fd], [], [], max(0.0, wait - time.monotonic()))[0]:
                        chunk = os.read(fd, 256)
                        received.extend(chunk)
                        buffer.extend(chunk)
                        for kind, payload in frames(buffer):
                            if kind == 0:
                                result.update(reply=payload.hex(), reply_at=elapsed())
                                break
            # Each identity request holds an answering bootloader for a session's time; it answers no more of them.
            limit = time.monotonic() + HOLD_LIMIT
            while hold and "reply" in result and not stop and time.monotonic() < limit:
                try:
                    select.select([], [], [], HOLD_INTERVAL)
                except InterruptedError:
                    pass
                if not stop:
                    os.write(fd, IDENTITY)
                    result["last_sent"] = elapsed()
                    result["held"] = True
        finally:
            os.close(fd)
            if received:
                result["received"] = bytes(received[:512]).hex()
    except OSError as exc:
        result["error"] = str(exc)
    temporary = CATCH + ".tmp"
    with open(temporary, "w") as handle:
        handle.write(json.dumps(result) + "\n")
    os.chmod(temporary, 0o644)
    os.replace(temporary, CATCH)


def join():
    """Stop an early catch that is holding, once it has written its record. True if an early catch ran this boot."""
    try:
        with open(PID) as handle:
            pid = int(handle.read().strip())
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            running = b"vo_thr_catch.py" in handle.read()
    except (OSError, ValueError):
        running = False
    if running:
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):
            if not os.path.exists(f"/proc/{pid}"):
                break
            time.sleep(0.05)
    return os.path.exists(CATCH)


def main():
    if sys.argv[1:] == ["--early"]:
        with open(PID, "w") as handle:
            handle.write(f"{os.getpid()}\n")
        try:
            catch(hold=True)
        finally:
            os.unlink(PID)
    elif not join():
        catch(hold=False)


if __name__ == "__main__":
    main()
