"""Valve Oil THR catch: the first step of vo-thr-firmware.service, which finishes an interrupted THR flash at boot.

A THR bootloader stays after power-on only if an identity request reaches it within its startup window, four to ten
seconds depending on the build; one that answers is then held for ten seconds by each identity request. This runs as
root with Python's standard library only (python3 -I -S), so it reaches the THR sooner than the THR tool can start.
It gives the THR's serial port to the dialout group, as udev does later in boot, so the THR tool can open it as the
printer's user; sends only identity requests, never a start or data packet; and records in CATCH whether and when the
bootloader answered (scripts/flash_thr_firmware.py read_catch). Installed by Valve Oil at /usr/local/lib/vo_thr_catch.py.
"""
import json
import os
import select
import shutil
import termios
import time

DEVICE = "/dev/ttyS4"
CATCH = "/run/vo-thr-catch.json"
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


def main():
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


if __name__ == "__main__":
    main()
