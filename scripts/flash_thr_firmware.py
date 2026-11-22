#!/usr/bin/env python3
"""Restricted Max 4 THR snapshots, bootloader probes, and confirmed transfers."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FIRMWARE = Path(__file__).resolve().parents[1] / "firmware"
# One manifest per QIDI THR image; the installer passes the one pinned for the printer's target firmware.
MANIFESTS = ("max4-thr-valve-oil-v1.json", "smart5-thr-valve-oil-v1.json")
MANIFEST = FIRMWARE / MANIFESTS[0]
if __package__:
    from . import thr_bootloaders
    from .build_box_firmware import programmable_images, terminal_signals_ignored
    from .klipper_debug_read import read_bytes
else:
    import thr_bootloaders
    from build_box_firmware import programmable_images, terminal_signals_ignored
    from klipper_debug_read import read_bytes

identify_bootloader = thr_bootloaders.identify_bootloader
WAITS, HALTS = thr_bootloaders.WAITS, thr_bootloaders.HALTS

DEVICE = "/dev/ttyS4"
UNSUPPORTED_BOOTLOADER = 3
FELL_BACK = 4
END_UNCONFIRMED = 5
# The update check's timer, under its name before 26.11.22.2 too; either may be absent.
SERVICES = ("klipper.service", "qidi-client.service", "vo-auto-update.timer", "tltg-optimized-auto-update.timer")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def admitted_image(data: bytes, manifest: dict) -> str:
    thr = manifest["thr"]
    value = digest(data)
    known = {thr["stock_sha256"], thr["patched_sha256"], *(entry["sha256"] for entry in thr.get("superseded", ()))}
    if len(data) != thr["size"] or value not in known:
        raise ValueError("Unrecognized THR image")
    return value


def load_backup(directory: Path, manifest: dict) -> tuple[bytes, bytes]:
    boot = (directory / "bootloader.bin").read_bytes()
    app = (directory / "application.bin").read_bytes()
    identify_bootloader(boot)
    admitted_image(app, manifest)
    return boot, app


def verify_readback(image: bytes, directory: Path, manifest: dict, *, expected_bootloader: bytes | None = None) -> dict:
    expected = admitted_image(image, manifest)
    boot, app = load_backup(directory, manifest)
    if expected_bootloader is not None and boot != expected_bootloader:
        raise ValueError("Bootloader readback differs from the original backup")
    if app != image:
        raise ValueError("Application readback does not match the selected image")
    return {"verified": True, "application_sha256": expected, "bootloader_sha256": digest(boot)}


def require_ownership(idle_confirmed: bool, *, at_boot: bool = False) -> None:
    """at_boot: run by vo-thr-firmware.service, which systemd orders before Klipper, the touchscreen, and the update
    service the update timer starts, so none of them can hold the UART; the port is also opened exclusively. Nothing is
    asked of systemd then, which early in boot can take seconds a board's startup window doesn't have."""
    if not idle_confirmed:
        raise ValueError("Confirm idle state and zero heater targets with --printer-idle-confirmed")
    if at_boot:
        return
    for service in SERVICES:
        result = subprocess.run(
            ["systemctl", "show", service, "--property=ActiveState", "--value"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode and service.endswith(".timer"):
            load = subprocess.run(
                ["systemctl", "show", service, "--property=LoadState", "--value"],
                capture_output=True, text=True, timeout=5,
            )
            if load.stdout.strip() == "not-found":
                continue
        if result.returncode or result.stdout.strip() != "inactive":
            raise ValueError(f"Stop {service} before UART access")


def crc16(data: bytes) -> int:
    value = 0xffff
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0xa001 if value & 1 else 0)
    return value ^ 0xffff


def encode(kind: int, payload: bytes) -> bytes:
    if kind not in (0, 1, 2) or len(payload) > 1030:
        raise ValueError("Invalid THR packet")
    body = bytes([kind]) + len(payload).to_bytes(2, "big") + payload
    return b"\xa5" + body + crc16(body).to_bytes(2, "big")


class RetryPacket(Exception):
    pass


class RecoveryFailed(Exception):
    pass


class Bootloader:
    HOLD_SECONDS = 120.0
    PAIR_INTERVAL = 0.2
    PACKET0_REJECTIONS = 5
    MIN_CLOCK_RATE = 0.9  # slowest assumed bootloader SysTick relative to the host clock
    # A bootloader answers start after erasing the application region: 0.63 s on a Max 4's THR (46358f30...).
    START_REPLY_SECONDS = 2.0
    MAX_CLOCK_RATE = 1.1  # fastest
    RESTART_ROUNDS = 4
    # The THR powers up with the printer. On a Max 4 it was 1.4 s ahead of the kernel's start; this bounds it for the
    # start a rescue sends a WAITS board that answers early in boot, which may be in its startup window.
    POWER_TO_KERNEL_SECONDS = 2.5
    START_MARGIN_SECONDS = 0.5

    def __init__(self, port, profile, clock=time.monotonic, sleep=time.sleep, *, journal=None, boot_clock=None):
        self.port = port
        self.profile = profile
        self.clock = clock
        self.sleep = sleep
        self.buffer = bytearray()
        self.last_sent = None
        self.last_reply = None
        self.events = []
        self.discarded = 0  # received bytes that weren't part of a valid frame
        # journal.packet(image_sha256, next_packet), before start (next_packet None) and before each data packet, and
        # journal.sent() after each send: what a rescue in another process needs to know (Journal).
        self.journal = journal
        # boot_clock(): seconds since the kernel started, for a rescue early in boot.
        self.boot_clock = boot_clock

    def receive(self, deadline: float):
        while self.clock() < deadline:
            while self.buffer:
                if self.buffer[0] != 0xa5:
                    del self.buffer[0]
                    self.discarded += 1
                    continue
                if len(self.buffer) < 4:
                    break
                size = int.from_bytes(self.buffer[2:4], "big")
                if size > 100:
                    del self.buffer[0]
                    self.discarded += 1
                    continue
                end = size + 6
                if len(self.buffer) < end:
                    break
                raw = bytes(self.buffer[:end])
                if int.from_bytes(raw[-2:], "big") != crc16(raw[1:-2]):
                    del self.buffer[0]
                    self.discarded += 1
                    continue
                del self.buffer[:end]
                kind, payload = raw[1], raw[4:-2]
                if kind not in (0, 1, 2) or (kind == 2 and len(payload) == 7 and payload[6] != 1):
                    # An error frame, or a data packet the bootloader rejected: kept for the record.
                    self._log("frame", kind=kind, payload=payload.hex())
                yield kind, payload
            chunk = self.port.read(100)
            if chunk:
                self.buffer.extend(chunk)

    def exchange(self, kind: int, payload: bytes, accept, *, attempts: int = 3,
                 timeout: float = 5, retry_timeout: bool = True):
        raw = encode(kind, payload)
        for _ in range(attempts):
            if self.port.write(raw) != len(raw):
                raise OSError("Incomplete UART write")
            self.port.flush()
            try:
                for reply_kind, reply in self.receive(self.clock() + timeout):
                    if accept(reply_kind, reply):
                        return reply
            except RetryPacket:
                continue
            if not retry_timeout:
                break
        raise TimeoutError("Expected THR acknowledgement not received; transfer is not verified")

    def probe(self) -> bytes:
        def accept(kind, reply):
            if kind != 0:
                return False
            if reply != self.profile.identity_reply:
                raise ValueError("Unrecognized THR bootloader identity; no erase command sent")
            return True
        return self.exchange(0, b"\x01\x00", accept)

    def transfer(self, image: bytes, manifest: dict) -> dict:
        image_hash = admitted_image(image, manifest)
        self.probe()
        # A start acknowledgement can be lost after erase; do not repeat that boundary.
        self.exchange(1, b"\x01\x00" + len(image).to_bytes(4, "big"),
                      lambda kind, reply: kind == 1 and reply == self.profile.start_reply, attempts=1)
        for index, offset in enumerate(range(0, len(image), 1024)):
            data = image[offset:offset + 1024]
            header = index.to_bytes(4, "big") + len(data).to_bytes(2, "big")
            def accepted(kind, reply):
                if kind != 2 or len(reply) != 7 or reply[:6] != header:
                    return False
                if reply[6] == 2:
                    raise RetryPacket()
                if reply[6] != 1:
                    raise ValueError("Unexpected THR data acknowledgement status")
                return True

            # Only a valid NACK proves an unsafe-to-repeat packet was rejected.
            self.exchange(2, header + data, accepted,
                          retry_timeout=index != 0 or self.profile.packet_zero_retry_safe)
        self.exchange(1, b"\x02\x01", lambda kind, reply: kind == 1 and reply == self.profile.end_reply,
                      attempts=1)
        return {"transfer_acknowledged": True, "verified": False, "application_sha256": image_hash}

    # Recovery relies on the recoverable profile's measured session, exit and startup timers and on what it does when
    # a session goes quiet (thr_bootloaders); see the THR entries in openspec/observations/qidi-platform.md.
    def transfer_recovering(self, image: bytes, fallback: bytes, manifest: dict) -> dict:
        image_hash = self._recovery_images(image, fallback, manifest)
        self.probe()
        self.last_reply = self.clock()
        return self._recover(image, fallback, image_hash, started=False)

    def _recovery_images(self, image: bytes, fallback: bytes, manifest: dict) -> str:
        if not self.profile.recoverable:
            raise ValueError("Transfer recovery is not validated for this bootloader")
        image_hash = admitted_image(image, manifest)
        if admitted_image(fallback, manifest) != manifest["thr"]["stock_sha256"] or len(fallback) != len(image):
            raise ValueError("The recovery fallback must be the admitted stock application")
        return image_hash

    def _recover(self, image: bytes, fallback: bytes, image_hash: str, *, started: bool, first_index: int = 0) -> dict:
        target = image
        for _ in range(4):
            outcome = self._program(target, started, first_index)
            first_index = 0
            if outcome in ("done", "unconfirmed-end"):
                return {"transfer_acknowledged": outcome == "done", "verified": False,
                        "application_sha256": digest(target), "requested_sha256": image_hash,
                        "fell_back": target is not image, "events": self.events}
            target, started = fallback, False
            if outcome == "ambiguous":
                if self.profile.after_session == HALTS:
                    # Waiting out the session would halt the board; nothing can tell where its data went.
                    raise RecoveryFailed("THR session state is uncertain on a bootloader that halts after a quiet "
                                         "session; THR state is unknown")
                # Start into a session that may hold data would program the fallback offset,
                # so an unconfirmed restart waits out any session again instead.
                for _ in range(self.RESTART_ROUNDS):
                    if self._await_reset(len(fallback)):
                        break
                else:
                    raise RecoveryFailed("THR bootloader restart was not confirmed; THR state is unknown")
                started = True
        raise RecoveryFailed("Fallback transfer failed repeatedly; THR state is unknown")

    # ---- rescue: finishing a transfer another process started --------------------------------------------------------
    # A board can be in its startup window (after power-on), in a live data session (the transferring process died
    # moments ago), waiting after a quiet session (WAITS), halted (HALTS), or running an application. Data packets are
    # written at a running position: a later packet sent again is skipped by a live session and ignored without one,
    # but the first packet sent again would be written after itself.

    def rescue(self, image: bytes, fallback: bytes, manifest: dict, *, resume: tuple[bytes, int] | None = None,
               last_sent: float | None = None, caught: tuple[str, float, float] | None = None,
               erased: bool = False) -> dict | None:
        """Finish an interrupted transfer through the bootloader, if one answers. Returns the transfer result, or None
        when no bootloader takes it: the application may be running, or the board halted or locked up (see
        rescue_quiet).

        resume: the image and packet from the transfer's journal (resume_point). last_sent: when that transfer last sent
        anything, on this clock, when it ran since the last boot; the board's state then follows from the time.
        caught: the boot unit's catch (read_catch): the bootloader's identity reply, when it came (seconds since the
        kernel started), and when the catch last sent identity, on this clock, which holds the bootloader.
        erased: the journal shows the application erased and not yet whole again (journal_erased). A start then can't
        leave the board worse off: if the bootloader isn't resident it jumps into the erased application either way."""
        image_hash = self._recovery_images(image, fallback, manifest)
        self._log("rescue-start", boot_elapsed=self.boot_clock() if self.boot_clock else None)
        ticks = self.profile.session_ticks + self.profile.exit_ticks + self.profile.startup_ticks
        if last_sent is not None and self.clock() >= last_sent + ticks / 1000 / self.MIN_CLOCK_RATE + 1:
            # Long enough ago that any session, and the startup window a restart after it opened, have ended.
            last_sent = None
        if last_sent is not None:
            self.last_sent = last_sent
            if resume is not None and self.clock() < last_sent + self.profile.session_ticks / 1000 / self.MAX_CLOCK_RATE - 1:
                result = self._resume(resume, fallback, image_hash)
                if result is not None:
                    return result
            if self.profile.after_session == HALTS:
                return None
            # Wait the session out: a WAITS board then waits, and a RESTARTS board's new startup window is caught if
            # it hasn't passed.
            for _ in range(self.RESTART_ROUNDS):
                if self._await_reset(len(image)):
                    self._log("rescue-restarted")
                    return self._recover(image, fallback, image_hash, started=True)
            return None
        if caught is not None and bytes.fromhex(caught[0]) == self.profile.identity_reply:
            reply_elapsed, held_since = caught[1], caught[2]
            # The catch's identity requests hold a bootloader they reached in its startup window until a session's time
            # after the last of them; a held bootloader takes a start then, and answers no more identity requests.
            if self.clock() < held_since + self.profile.session_ticks / 1000 / self.MAX_CLOCK_RATE - 1:
                self.last_sent = held_since
                self._log("rescue-caught", boot_elapsed=reply_elapsed, erased=erased)
                return self._after_identity(image, fallback, image_hash, reply_elapsed, erased)
        if self._identity_probe(1.0):
            elapsed = self.boot_clock() if self.boot_clock else None
            self._log("rescue-identity", boot_elapsed=elapsed, erased=erased)
            return self._after_identity(image, fallback, image_hash, elapsed, erased)
        if resume is not None:
            return self._resume(resume, fallback, image_hash)
        return None

    def _resume(self, resume: tuple[bytes, int], fallback: bytes, image_hash: str) -> dict | None:
        # A live session answers the packet it was given last, or was about to be given, without writing it twice;
        # without a session the packet is ignored.
        target, next_packet = resume
        # With every data packet acknowledged, the last one asks whether the session is still open.
        header_data = self._packet(target, min(next_packet, (len(target) + 1023) // 1024 - 1))
        for _ in range(3):
            self._send(2, header_data)
            # A rejection can echo a damaged byte count (seen on 46358f30...), so only the index is matched.
            got = self._wait(1.0, lambda kind, reply: kind == 2 and len(reply) == 7 and reply[:4] == header_data[:4]
                             and reply[6] in (1, 2))
            if got and got[1][6] == 1:
                self._log("rescue-resumed", at=next_packet)
                self.last_reply = self.clock()
                return self._recover(target, fallback, image_hash, started=True,
                                     first_index=int.from_bytes(header_data[:4], "big") + 1)
            if got:
                # Rejected: not the session's last packet or the next one. Nothing was written.
                self._log("rescue-resume-rejected", at=next_packet)
                return None
        return None

    def _after_identity(self, image: bytes, fallback: bytes, image_hash: str, elapsed: float | None,
                        erased: bool = False) -> dict | None:
        """A bootloader answered identity, elapsed seconds after the kernel started."""
        if erased or (self.profile.after_session == WAITS and self._start_allowed(elapsed)):
            self.last_reply = self.clock()
            return self._recover(image, fallback, image_hash, started=False)
        # A startup window of unknown age: a start now could erase and then jump. Once any window has ended, only a
        # WAITS board in its wait after a quiet session answers.
        self.sleep(self._quiet_seconds())
        if self.profile.after_session == WAITS and self._identity_probe(1.0):
            self._log("rescue-waiting")
            self.last_reply = self.clock()
            return self._recover(image, fallback, image_hash, started=False)
        return None

    def rescue_quiet(self, image: bytes, fallback: bytes, manifest: dict, *, last_sent: float | None = None) -> dict | None:
        """With no bootloader answering and no application running: let any session end, then take a WAITS board in
        its wait or a RESTARTS board in its next startup window. A HALTS board is left alone: a quiet session halts it,
        and halted it can't be reached until power is lost."""
        image_hash = self._recovery_images(image, fallback, manifest)
        if self.profile.after_session == HALTS:
            return None
        # When the board last heard anything that could keep a session open.
        self.last_sent = self.clock() if last_sent is None else last_sent
        for _ in range(self.RESTART_ROUNDS):
            if self._await_reset(len(image)):
                self._log("rescue-restarted")
                return self._recover(image, fallback, image_hash, started=True)
        return None

    def _start_allowed(self, boot_elapsed: float | None) -> bool:
        if boot_elapsed is None:
            return False
        deadline = self.profile.startup_ticks / 1000 / self.MAX_CLOCK_RATE
        return boot_elapsed + self.POWER_TO_KERNEL_SECONDS + self.START_MARGIN_SECONDS <= deadline

    def _quiet_seconds(self) -> float:
        """Long enough for any session or startup window to end, at the slowest clock."""
        return (self.profile.session_ticks + self.profile.exit_ticks) / 1000 / self.MIN_CLOCK_RATE + 0.5

    @staticmethod
    def _packet(image: bytes, index: int) -> bytes:
        data = image[index * 1024:(index + 1) * 1024]
        return index.to_bytes(4, "big") + len(data).to_bytes(2, "big") + data

    def _log(self, event, **extra):
        self.events.append({"t": round(self.clock(), 3), "event": event, **extra})

    def _send(self, kind: int, payload: bytes) -> None:
        raw = encode(kind, payload)
        if self.port.write(raw) != len(raw):
            raise OSError("Incomplete UART write")
        self.port.flush()
        self.last_sent = self.clock()
        if self.journal is not None:
            self.journal.sent()

    def _wait(self, seconds: float, accept):
        for kind, reply in self.receive(self.clock() + seconds):
            self.last_reply = self.clock()
            if accept(kind, reply):
                return kind, reply
        return None

    def _is_identity(self, kind, reply):
        return kind == 0 and reply == self.profile.identity_reply

    def _is_start(self, kind, reply):
        return kind == 1 and reply == self.profile.start_reply

    def _identity_probe(self, seconds: float) -> bool:
        end = self.clock() + seconds
        while self.clock() < end:
            self._send(0, b"\x01\x00")
            if self._wait(self.PAIR_INTERVAL, self._is_identity):
                return True
        return False

    def _await_reset(self, size: int) -> bool:
        session, exit_delay = self.profile.session_ticks / 1000, self.profile.exit_ticks / 1000
        remaining = self.last_sent + session + exit_delay + 0.2 - self.clock()
        if remaining > 0:
            self.sleep(remaining)
        # Start is only sent once the board has certainly reset: in a session that
        # already holds data it re-erases without rewinding the write position.
        # A dying session still acknowledges start during its exit delay. Until even a
        # slow bootloader clock must have reset, only an identity reply, which no
        # session answers, proves the startup window; after that, a start reply can
        # only come from a new session whose identity reply was lost.
        reset_by = self.last_sent + (session + exit_delay) / self.MIN_CLOCK_RATE
        end = self.clock() + 2 * self.profile.startup_ticks / 1000
        start = b"\x01\x00" + size.to_bytes(4, "big")
        while self.clock() < end:
            self._send(0, b"\x01\x00")
            got = self._wait(self.PAIR_INTERVAL / 2, lambda kind, reply: self._is_identity(kind, reply)
                             or (self.clock() >= reset_by and self._is_start(kind, reply)))
            if got and self._is_start(*got):
                # The late answer to a start sent earlier, which erased first.
                self._log("restarted")
                return True
            if not got and self.clock() < reset_by:
                continue
            self._send(1, start)
            # A bootloader answers start after erasing; wait that long only for one that just answered identity, so
            # probing stays quick enough to catch a startup window.
            if self._wait(self.START_REPLY_SECONDS if got else self.PAIR_INTERVAL / 2, self._is_start):
                self._log("restarted")
                return True
        self._log("start-unconfirmed")
        return False

    def _late_ack(self, accept, hold):
        """Keep a session alive with identity requests, which a session neither answers nor programs, until a late
        acknowledgement arrives; for a HALTS board, which halts if its session goes quiet."""
        deadline = self.clock() + hold
        while self.clock() < deadline:
            self._send(0, b"\x01\x00")
            got = self._wait(1.0, lambda kind, reply: accept(kind, reply) or self._is_identity(kind, reply))
            if got and self._is_identity(*got):
                return "fresh"
            if got:
                self._log("late-ack")
                return got
        return None

    def _hold(self, kind, payload, accept, hold, label, probe=None):
        probe = probe or (kind, payload, accept)
        stale = self.profile.session_ticks / 1000 - 1.0
        deadline = self.clock() + hold
        tries = quiet = 0
        heard = self.last_reply
        while self.clock() < deadline:
            silent = heard is not None and self.clock() - heard >= stale
            use = (kind, payload, accept)
            if silent:
                quiet += 1
                use = probe if quiet % 5 == 1 else None
            if use is not None:
                k, p, acc = use
                self._send(k, p)
                got = self._wait(2.0 if tries == 0 else 0.2,
                                 lambda a, b, acc=acc: acc(a, b) or self._is_identity(a, b))
                tries += 1
                if got and self._is_identity(*got):
                    self._log("fresh-during-hold", at=label)
                    return "fresh"
                if got and not (got[0] == 2 and got[1][6] == 2):
                    # A HALTS board can't be waited out; its session is resumed as long as it answers.
                    if self.profile.after_session != HALTS and (
                            silent or (heard is not None and self.last_reply - heard >= stale)):
                        # In the gap between a session timeout and its reset the
                        # bootloader still acknowledges; only identity tells.
                        self._log("late-ack", at=label)
                        if self._identity_probe(self.profile.exit_ticks / 1000 + 1.0):
                            return "fresh"
                        return "ambiguous"
                    if not accept(*got):
                        # The probe was answered, not what is waited for: the session is open, so the wait goes on.
                        heard = self.last_reply
                        continue
                    if tries > 1:
                        self._log("resumed", at=label, tries=tries)
                    return got
                if got:
                    heard = self.last_reply
            self._send(0, b"\x01\x00")
            if self._wait(0.2, self._is_identity):
                self._log("fresh-during-hold", at=label)
                return "fresh"
        return None

    def _note(self, image: bytes, next_packet: int | None) -> None:
        if self.journal is not None:
            self.journal.packet(digest(image), next_packet)

    def _program(self, image: bytes, started: bool, first_index: int = 0) -> str:
        if not started:
            self._note(image, None)
            start = b"\x01\x00" + len(image).to_bytes(4, "big")
            for attempt in range(3):
                self._send(1, start)
                if self._wait(5 if attempt == 0 else 1, self._is_start):
                    break
            else:
                self._log("start-unacknowledged")
        header = data = None
        for index, offset in enumerate(range(0, len(image), 1024)):
            data = image[offset:offset + 1024]
            header = index.to_bytes(4, "big") + len(data).to_bytes(2, "big")

            def ack(kind, reply, header=header):
                return kind == 2 and len(reply) == 7 and reply[:6] == header and reply[6] in (1, 2)
            if index < first_index:
                continue
            self._note(image, index)
            if index == 0:
                # Only a CRC rejection proves packet zero was not programmed.
                for _ in range(self.PACKET0_REJECTIONS):
                    self._send(2, header + data)
                    got = self._wait(5, ack)
                    if got is None and self.profile.after_session == HALTS:
                        got = self._late_ack(ack, self.HOLD_SECONDS)
                        if got == "fresh":
                            return "fresh"
                    if got is None:
                        self._log("packet0-unacknowledged")
                        return "ambiguous"
                    if got[1][6] == 1:
                        break
                else:
                    self._log("packet0-rejected")
                    return "ambiguous"
                continue
            got = self._hold(2, header + data, ack, self.HOLD_SECONDS, index)
            if got in ("fresh", "ambiguous"):
                return got
            if got is None:
                raise RecoveryFailed(f"No reply for {self.HOLD_SECONDS:.0f} s at packet {index}; THR state is unknown")
        last = (2, header + data, ack)
        # Every data packet is written: the application is whole again, whether or not end arrives.
        self._note(image, (len(image) + 1023) // 1024)
        got = self._hold(1, b"\x02\x01", lambda kind, reply: kind == 1 and reply == self.profile.end_reply,
                         2 * (self.profile.session_ticks + self.profile.exit_ticks) / 1000, "end", probe=last)
        if got in ("fresh", "ambiguous"):
            return got
        return "done" if got else "unconfirmed-end"


def application_session(manifest: dict, *, reset: bool = False,
                        expected_boot: bytes | None = None, capture_root: Path | None = None):
    # Reuse the installed vendor transport without its unrelated AVR reset preamble.
    sys.path.insert(0, "/home/qidi/klipper/klippy")
    import reactor
    import serial
    import serialhdl

    loop = reactor.Reactor()
    reader = serialhdl.SerialReader(loop)
    result = {}

    def run(eventtime):
        try:
            port = serial.Serial(DEVICE, 500000, timeout=0, exclusive=True)
            if not reader._start_session(port):
                raise TimeoutError("THR application identify timed out")
            constants = reader.get_msgparser().get_constants()
            if constants.get("MCU") != "stm32f103xe" or int(constants["SERIAL_BAUD"]) != 500000:
                raise ValueError("Unexpected application identity")
            def read(address, order=2):
                response = reader.send_with_response(
                    "debug_read order=%d addr=%d" % (order, address), "debug_result"
                )
                return int(response["val"])

            version, build_versions = reader.get_msgparser().get_version_info()
            metadata = {"version": version, "build_versions": build_versions,
                        "constants": {key: constants[key] for key in ("MCU", "CLOCK_FREQ", "SERIAL_BAUD") if key in constants},
                        "id_registers": {name: hex(read(address, order)) for name, address, order in (
                            ("cpuid", 0xe000ed00, 2), ("dbgmcu_idcode", 0xe0042000, 2),
                            ("flash_size_kib", 0x1ffff7e0, 1))}}
            boot = read_bytes(reader, 0x08000000, 0x08008000)
            supported = len(boot) == 0x8000 and digest(boot) in thr_bootloaders.ADMITTED_BOOTLOADERS
            length = manifest["thr"]["size"] if supported else 0x18000
            region = read_bytes(reader, 0x08008000, 0x08008000 + length)
            app = region[:manifest["thr"]["size"]]
            result["snapshot"] = boot, app, metadata, region
            if reset:
                if not supported:
                    if capture_root is None:
                        raise ValueError("Unrecognized installed bootloader; no reset sent")
                    directory = Path(tempfile.mkdtemp(prefix="unsupported-thr-", dir=capture_root))
                    save_snapshot(directory, boot, app, metadata=metadata, application_region=region)
                    raise ValueError("Unrecognized installed bootloader; no reset sent. Capture: " + str(directory))
                identify_bootloader(boot)
                admitted_image(app, manifest)
                if expected_boot is None or boot != expected_boot:
                    raise ValueError("Installed bootloader differs from backup; no reset sent")
                reader.send("reset")
                loop.pause(loop.monotonic() + 0.050)
        except Exception as exc:
            result["error"] = exc
        finally:
            reader.disconnect()
            loop.end()

    def timed_out(signum, frame):
        raise TimeoutError("THR application operation exceeded 900 seconds")

    previous_handler = signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(900)
    loop.register_callback(run)
    try:
        loop.run()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        loop.finalize()
    if "error" in result:
        raise result["error"]
    return result["snapshot"]


def save_snapshot(directory: Path, boot: bytes, app: bytes, *, metadata=None,
                  application_region: bytes | None = None) -> dict:
    report = {"bootloader_sha256": digest(boot), "application_sha256": digest(app),
              "supported_bootloader": len(boot) == 0x8000 and digest(boot) in thr_bootloaders.ADMITTED_BOOTLOADERS,
              "bootloader_page_sha256": [digest(boot[offset:offset + 2048]) for offset in range(0, len(boot), 2048)],
              **(metadata or {})}
    files = {"bootloader.bin": boot, "application.bin": app}
    if application_region is not None:
        files["application-region.bin"] = application_region
        report.update(application_region_sha256=digest(application_region), application_region_bytes=len(application_region))
    files["snapshot.json"] = (json.dumps({key: report[key] for key in ("bootloader_sha256", "application_sha256")}, indent=2) + "\n").encode()
    files["report.json"] = (json.dumps(report, indent=2) + "\n").encode()
    for name, data in files.items():
        with os.fdopen(os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    return report


def boot_elapsed() -> float:
    # CLOCK_BOOTTIME is Linux's, as is every printer; elsewhere (tests) the monotonic clock stands in.
    return time.clock_gettime(getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC))


def boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class Journal:
    """A transfer's progress on disk, for a rescue in another process: the image being written and the data packet about
    to be sent, in `path`, and when the board last heard from the transfer (CLOCK_BOOTTIME, with this boot's id), in
    `path` with `.sent` added. A packet change reaches storage, the rename included, before that packet is sent, so
    after any crash the board holds the journal's packet or the one before it, both of which an open session takes
    again safely; a packet older than both would be written again at the running position by some builds. A send is
    recorded at most every SENT_INTERVAL seconds, which a rescue allows for (JOURNAL_SLACK); it matters only within the
    same boot, so it is never synced."""
    SENT_INTERVAL = 0.5

    def __init__(self, path: Path):
        self.path = path
        self.sent_path = path.with_name(path.name + ".sent")
        # A rescue continues the transfer's journal: what was being written stays until it writes something else.
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = None
        self.record = {key: record[key] for key in ("image_sha256", "next_packet") if key in record} \
            if isinstance(record, dict) else {}
        self.written = None

    def packet(self, image_sha256: str, next_packet: int | None) -> None:
        self.record = {"image_sha256": image_sha256, "next_packet": next_packet}
        temporary = self.path.with_name(self.path.name + ".tmp")
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
            handle.write(json.dumps(self.record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        self.sent(force=True)

    def sent(self, *, force: bool = False) -> None:
        if not force and self.written is not None and boot_elapsed() - self.written < self.SENT_INTERVAL:
            return
        self.written = boot_elapsed()
        temporary = self.sent_path.with_name(self.sent_path.name + ".tmp")
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
            handle.write(json.dumps({"boot_id": boot_id(), "sent_at": self.written}) + "\n")
        os.replace(temporary, self.sent_path)


# How much later than its journal says a transfer may have last sent.
JOURNAL_SLACK = Journal.SENT_INTERVAL + 0.1


def read_journal(path: Path | None, images) -> tuple[tuple[bytes, int] | None, float | None]:
    """resume_point of the journal, and when the transfer last sent if it ran since the last boot (CLOCK_BOOTTIME,
    later by JOURNAL_SLACK)."""
    def load(target):
        try:
            record = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return record if isinstance(record, dict) else {}
    if path is None:
        return None, None
    record, sent = load(path), load(path.with_name(path.name + ".sent"))
    sent_at = sent.get("sent_at")
    if sent.get("boot_id") != boot_id() or boot_id() == "" or not isinstance(sent_at, (int, float)) \
            or isinstance(sent_at, bool):
        sent_at = None
    return resume_point(record, images), None if sent_at is None else sent_at + JOURNAL_SLACK


def journal_erased(path: Path | None, images) -> bool:
    """Whether the journal shows the application erased and not yet whole again: a start was acknowledged (the journal
    names a data packet) and not every data packet was."""
    try:
        record = json.loads(path.read_text(encoding="utf-8")) if path is not None else None
    except (OSError, ValueError):
        return False
    return erased_point(record, images)


def erased_point(record, images) -> bool:
    if not isinstance(record, dict):
        return False
    packet = record.get("next_packet")
    if not isinstance(packet, int) or isinstance(packet, bool) or packet < 0:
        return False
    return any(record.get("image_sha256") == digest(image) and packet * 1024 < len(image) for image in images)


def resume_point(record, images) -> tuple[bytes, int] | None:
    """The image a transfer was writing and the packet it was about to send, when a live session of it could take that
    packet again: any but the first."""
    if not isinstance(record, dict):
        return None
    packet = record.get("next_packet")
    if not isinstance(packet, int) or isinstance(packet, bool) or packet < 1:
        return None
    for image in images:
        # Up to the packet count itself: every data packet acknowledged, end not yet.
        if record.get("image_sha256") == digest(image) and packet <= (len(image) + 1023) // 1024:
            return image, packet
    return None


CATCH = Path("/run/vo-thr-catch.json")  # installer/system/vo_thr_catch.py


def read_catch(path: Path = CATCH) -> tuple[str, float, float] | None:
    """The boot unit's catch, when a bootloader answered it: its reply, when (seconds since the kernel started), and
    when the catch last sent identity, on time.monotonic's clock. The file is on /run, so it is from this boot."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        reply, reply_at, last_sent = record["reply"], float(record["reply_at"]), float(record["last_sent"])
        bytes.fromhex(reply)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return reply, reply_at, time.monotonic() - (boot_elapsed() - last_sent)


def run_rescue(image: bytes, backup: tuple[bytes, bytes], manifest: dict, output_dir: Path,
               progress_path: Path | None, *, caught: tuple[str, float, float] | None = None) -> int:
    """Finish an interrupted transfer, then read back the result into output_dir. Returns 0 when the THR holds image,
    FELL_BACK when it holds another admitted application (QIDI's stock image, or one the transfer never replaced)."""
    import serial

    boot_backup, stock = backup
    profile = identify_bootloader(boot_backup)
    if not profile.recoverable:
        raise ValueError("Rescue is not validated for this bootloader")
    resume, sent_at = read_journal(progress_path, (image, stock))
    erased = journal_erased(progress_path, (image, stock))
    journal = Journal(progress_path) if progress_path is not None else None
    events = []

    heard = {}

    def bootloader(step):
        print(f"THR rescue opens {DEVICE} {boot_elapsed():.2f} s after the kernel", file=sys.stderr, flush=True)
        with serial.Serial(DEVICE, 115200, timeout=0.05, write_timeout=2, exclusive=True) as port:
            client = Bootloader(port, profile, journal=journal, boot_clock=boot_elapsed)
            try:
                return step(client)
            finally:
                events.extend(client.events)
                if client.discarded:
                    events.append({"event": "discarded", "bytes": client.discarded})
                if client.last_sent is not None:
                    heard["last_sent"] = client.last_sent

    def read(label):
        boot, app, metadata, region = application_session(manifest)
        output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        save_snapshot(output_dir, boot, app, metadata=metadata, application_region=region)
        if boot != boot_backup:
            raise ValueError("THR bootloader readback differs from the backup")
        value = admitted_image(app, manifest)
        print(json.dumps({"rescue": label, "application_sha256": value, "events": events}, indent=2))
        return 0 if value == digest(image) else FELL_BACK

    # The journal's time, on the Bootloader's clock.
    last_sent = None if sent_at is None else time.monotonic() - (boot_elapsed() - sent_at)
    result = bootloader(lambda client: client.rescue(image, stock, manifest, resume=resume, last_sent=last_sent,
                                                     caught=caught, erased=erased))
    if result is None:
        try:
            # Nothing in a bootloader took the transfer; a running application is read as it is.
            return read("application")
        except (TimeoutError, OSError) as exc:
            events.append({"event": "no-application", "error": str(exc)})
        result = bootloader(lambda client: client.rescue_quiet(image, stock, manifest,
                                                               last_sent=heard.get("last_sent", last_sent)))
    if result is None:
        print(json.dumps({"rescued": False, "events": events}, indent=2))
        raise RecoveryFailed("No THR bootloader took the transfer; THR state is unknown")
    # The bootloader hands over to the application within twelve seconds of its last packet.
    time.sleep(12)
    return read("transferred")


RESCUE_RESULT = "rescue-result.json"


def rescue_from_record(args) -> int:
    """rescue --record: the boot unit's run, and the installer's (installer/runtime/thr_firmware.py writes the record).
    Every outcome, and every failure after the record is read, is written to RESCUE_RESULT in the record's backup
    directory."""
    print(f"THR rescue started {boot_elapsed():.2f} s after the kernel", file=sys.stderr, flush=True)
    try:
        record = json.loads(args.record.read_text(encoding="utf-8"))
        job = record["rescue"]
        directory = Path(record["backup_directory"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"THR operation failed: unreadable THR record: {exc}", file=sys.stderr)
        return 1
    readback = directory / f"rescue-{int(time.time())}-{os.getpid()}"
    code = 1
    try:
        code = main(["rescue", "--image", job["image"], "--backup-dir", job["backup"], "--progress", job["progress"],
                     "--output-dir", str(readback), "--manifest", job["manifest"], "--firmware", job["firmware"],
                     *(["--printer-idle-confirmed"] if args.printer_idle_confirmed else []),
                     *(["--at-boot"] if args.at_boot else [])])
    except (KeyError, TypeError) as exc:
        print(f"THR operation failed: incomplete THR record: {exc}", file=sys.stderr)
    finally:
        result = {"exit": code, "readback": str(readback), "at": time.time()}
        temporary = directory / (RESCUE_RESULT + ".tmp")
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
            handle.write(json.dumps(result) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / RESCUE_RESULT)
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("snapshot", "probe", "transfer", "verify", "rescue"))
    parser.add_argument("--image", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--original-backup-dir", type=Path, help="verify bootloader unchanged from this pre-transfer snapshot")
    parser.add_argument("--printer-idle-confirmed", action="store_true")
    parser.add_argument("--reset", action="store_true", help="read and admit the running application before THR reset")
    parser.add_argument("--confirm-write", action="store_true", help="allow application erase and programming")
    parser.add_argument("--recover-to-stock", action="store_true",
                        help="on a failed exchange, resume, or program the backup's stock application")
    parser.add_argument("--firmware", help="the printer's target QIDI firmware; transfer then programs only the image "
                                           "pinned for it or QIDI's stock image")
    parser.add_argument("--progress", type=Path, help="transfer and rescue: the transfer's journal (Journal), which "
                                                     "lets a rescue resume a live session")
    parser.add_argument("--at-boot", action="store_true", help="rescue: run by vo-thr-firmware.service at boot, "
                                                                "ordered before the update service the update timer starts")
    parser.add_argument("--record", type=Path, help="rescue: take the image, backup, journal, manifest, and target "
                                                   "firmware from the installer's pending THR record, and write the "
                                                   "outcome beside its backups (RESCUE_RESULT)")
    parser.add_argument("--manifest", default=MANIFEST.name, choices=MANIFESTS,
                        help="THR manifest in firmware/ for the printer's THR image (default: Max 4 and Plus 5)")
    args = parser.parse_args(argv)
    if args.record is not None:
        if args.operation != "rescue":
            print("THR operation failed: --record is only used by rescue", file=sys.stderr)
            return 1
        return rescue_from_record(args)
    try:
        manifest = json.loads((FIRMWARE / args.manifest).read_text(encoding="utf-8"))
        if args.recover_to_stock and args.operation != "transfer":
            raise ValueError("--recover-to-stock is only used by transfer")
        if args.reset and args.operation not in ("probe", "transfer"):
            raise ValueError("--reset is only used by probe or transfer")
        if args.confirm_write and args.operation != "transfer":
            raise ValueError("--confirm-write is only used by transfer")
        if args.image is not None and args.operation not in ("transfer", "verify", "rescue"):
            raise ValueError("--image is only used by transfer, verify, or rescue")
        if args.progress is not None and args.operation not in ("transfer", "rescue"):
            raise ValueError("--progress is only used by transfer or rescue")
        if args.original_backup_dir is not None and args.operation != "verify":
            raise ValueError("--original-backup-dir is only used by verify")
        image = None
        writes = args.operation in ("transfer", "rescue")
        if args.operation in ("transfer", "verify", "rescue"):
            if args.image is None:
                raise ValueError("--image is required")
            image = args.image.read_bytes()
            admitted_image(image, manifest)
            if writes and digest(image) not in (manifest["thr"]["stock_sha256"], manifest["thr"]["patched_sha256"]):
                raise ValueError("Transfer only the stock application or the current patched image")
            if writes and args.firmware is not None and digest(image) not in programmable_images(manifest, args.firmware):
                raise ValueError(f"The image is not pinned for QIDI firmware {args.firmware}")
        if args.firmware is not None and not writes:
            raise ValueError("--firmware is only used by transfer or rescue")
        if args.operation == "verify":
            if args.backup_dir is None:
                raise ValueError("--backup-dir must identify the readback snapshot")
            original_boot = load_backup(args.original_backup_dir, manifest)[0] if args.original_backup_dir else None
            print(json.dumps(verify_readback(image, args.backup_dir, manifest, expected_bootloader=original_boot), indent=2))
            return 0
        if args.operation == "transfer" and not args.confirm_write:
            raise ValueError("Transfer requires --confirm-write; application erase was not requested")
        backup = None
        if args.operation in ("probe", "transfer", "rescue"):
            if args.backup_dir is None:
                raise ValueError("--backup-dir with validated stock application and bootloader is required")
            backup = load_backup(args.backup_dir, manifest)
            if digest(backup[1]) != manifest["thr"]["stock_sha256"]:
                raise ValueError("Keep an admitted stock application as the recovery backup")
            if args.recover_to_stock and not identify_bootloader(backup[0]).recoverable:
                raise ValueError("Transfer recovery is not validated for this bootloader")
        if args.operation == "rescue" and not identify_bootloader(backup[0]).recoverable:
            raise ValueError("Rescue is not validated for this bootloader")
        if args.operation in ("snapshot", "rescue"):
            if args.output_dir is None or args.output_dir.exists() or args.output_dir.is_symlink():
                raise ValueError(args.operation.capitalize() + " needs a new --output-dir")
        elif args.output_dir is not None:
            raise ValueError("--output-dir is only used by snapshot or rescue")
        if args.at_boot and args.operation != "rescue":
            raise ValueError("--at-boot is only used by rescue")
        require_ownership(args.printer_idle_confirmed, at_boot=args.at_boot)
        if args.operation == "snapshot":
            args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
            boot, app, metadata, region = application_session(manifest)
            report = save_snapshot(args.output_dir, boot, app, metadata=metadata, application_region=region)
            if not report["supported_bootloader"]:
                print(json.dumps({"unsupported_bootloader": True, "firmware_changed": False,
                                  "summary": str(args.output_dir / "report.json")}, indent=2))
                return UNSUPPORTED_BOOTLOADER
            identify_bootloader(boot)
            admitted_image(app, manifest)
            print(json.dumps({"application_sha256": digest(app), "bootloader_sha256": digest(boot)}, indent=2))
            return 0
        if args.operation == "rescue":
            with terminal_signals_ignored():
                return run_rescue(image, backup, manifest, args.output_dir, args.progress,
                                  caught=read_catch() if args.at_boot else None)
        # From the reset to the end of the transfer.
        with terminal_signals_ignored():
            profile = identify_bootloader(backup[0])
            if args.reset:
                application_session(manifest, reset=True, expected_boot=backup[0], capture_root=args.backup_dir.parent)
                time.sleep(1)
            import serial
            with serial.Serial(DEVICE, 115200, timeout=0.05, write_timeout=2, exclusive=True) as port:
                client = Bootloader(port, profile, journal=Journal(args.progress) if args.progress else None)
                if args.operation == "probe":
                    reply = client.probe()
                    print(json.dumps({"bootloader_reply": reply.hex(), "erase_requested": False}, indent=2))
                elif args.recover_to_stock:
                    try:
                        result = client.transfer_recovering(image, backup[1], manifest)
                    except RecoveryFailed:
                        print(json.dumps({"recovered": False, "events": client.events}, indent=2))
                        raise
                    print(json.dumps(result, indent=2))
                else:
                    print(json.dumps(client.transfer(image, manifest), indent=2))
            print("Keep services stopped until the application returns; use snapshot and verify for readback.", file=sys.stderr)
            if args.recover_to_stock and result["fell_back"]:
                return FELL_BACK
            if args.recover_to_stock and not result["transfer_acknowledged"]:
                return END_UNCONFIRMED
            return 0
    except Exception as exc:
        print(f"THR operation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(main())
