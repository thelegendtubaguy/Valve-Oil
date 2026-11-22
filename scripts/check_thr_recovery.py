#!/usr/bin/env python3
"""Check timed THR transfer recovery and rescue against captured bootloaders.

Each run transfers the patched image through the real bootloader code (check_thr_flashing.NativeBoard) on a timed link
and injects one fault:
  dead, lost-reply  the link drops both directions, or only replies, for a while; the transferring process carries on
  killed            the transferring process dies; a rescue (flash_thr_firmware run_rescue) starts some seconds later
  power             the printer loses power; the board restarts from what its flash holds, and at boot the unit's catch
                    (installer/system/vo_thr_catch.py) and then a rescue run
Every run must end with the board running the patched or QIDI's stock image, except where the build makes it
impossible, which the report counts separately:
  - a power loss with the application erased, when the rescue at boot can't take the board inside its startup window
    (SECOND, FOURTH, THIRD; FIRST's window outlasts the boot);
  - on FOURTH, which halts when a session goes quiet: the link dead for longer than a session, or the transferring
    process dying without a packet a rescue could resume from.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

import check_thr_flashing as native
import flash_thr_firmware as tool
from thr_bootloaders import HALTS, RESTARTS, WAITS, identify_bootloader

UART_SECONDS_PER_BYTE = 10 / 115200
# A reply that follows an erase comes this much later per page erased: 48 pages took 0.63 s on a Max 4's THR.
ERASE_SECONDS_PER_PAGE = 0.013
TRIGGERS = ("start", 0, 1, 17, 34, "end")
DURATIONS = (0.5, 3, 6, 9.5, 10.5, 12, 14, 15.5, 17, 25, 40)
QUICK_DURATIONS = (3, 12)
RESCUE_DELAYS = (0.5, 3, 12, 40)
# Seconds from power-on to the catch's first identity request, and to the THR tool's rescue, from cold boots: the kernel
# starts 1.3 s (Smart 5), 1.4 s (Max 4), and 2.0 s (Plus 5) after power-on; the generator's compiled catch sends 1.8 to
# 1.95 s after the kernel, its Python catch (on a printer without gcc) 2.3 to 2.5 s after it, the unit's own catch (when
# the generator didn't run) 3.4 s after it, and the THR tool about 9.6 s after it; then slower boots.
BOOT_DELAYS = ((3.3, 11.0), (3.4, 11.0), (3.7, 11.0), (3.8, 11.0), (3.9, 11.6), (4.5, 11.6), (4.8, 11.0), (8.0, 14.0),
               (20.0, 26.0))
KERNEL_AFTER_POWER = 1.4
HOLD_INTERVAL = 2.0  # installer/system/vo_thr_catch.py
# Seconds a rescue spends finding no application before it waits out any session.
APPLICATION_CHECK = 7.0
# Bootloader SysTick rates relative to the host clock; the startup oscillator is unverified.
SKEWED_RATES = (0.92, 0.97, 1.03, 1.1)


class Killed(Exception):
    """The transferring process died."""


class Journal:
    """flash_thr_firmware.Journal on the link's clock: sends are recorded at most every SENT_INTERVAL seconds."""

    def __init__(self, link):
        self.link, self.record, self.written = link, {}, None

    def packet(self, image_sha256, next_packet):
        self.record.update(image_sha256=image_sha256, next_packet=next_packet)
        self._write()

    def sent(self):
        if self.written is None or self.link.now - self.written >= tool.Journal.SENT_INTERVAL:
            self._write()

    def _write(self):
        self.written = self.link.now
        self.record.update(sent_at=self.written)


class TimedLink:
    def __init__(self, boot, stock, patched, fault=None, rate=1.0):
        self.boot, self.stock, self.patched = boot, stock, patched
        self.board = native.NativeBoard(boot, stock)
        self.now = 0.0
        self.ticks = 0
        self.state = "bootloader"
        self.queue = bytearray()
        self.fault = fault
        self.window = None
        self.resets = []
        self.rate = rate
        self.erased = False
        self.dead_writer = False
        self.pending = []  # (time due, reply bytes)

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.advance(seconds)

    def advance(self, seconds):
        self.now += seconds
        target = int(self.now * 1000 * self.rate)
        while self.ticks < target and self.state == "bootloader":
            self.board.advance(1)
            self.ticks += 1
            self.settle()
        self.ticks = max(self.ticks, target)

    def settle(self):
        if self.board.halts:
            self.state = "halted"
        elif self.board.resets:
            # When it reset, by the board's own ticks: advance() moves the link's clock to the end of its step first.
            self.resets.append(self.ticks / 1000 / self.rate)
            self.board = native.NativeBoard(self.boot, self.board.application(0x18000))
        elif self.board.jumps:
            app = self.board.application(len(self.stock))
            self.state = "stock" if app == self.stock else "patched" if app == self.patched else "bricked"

    def power_cycle(self):
        """Power is lost and comes back: the board starts its bootloader again over what its flash holds."""
        flash = self.board.application(0x18000)
        self.board = native.NativeBoard(self.boot, flash)
        self.state = "bootloader"
        self.queue.clear()
        self.pending.clear()

    def dropping(self, direction):
        if not self.window or not self.window[1] <= self.now < self.window[2]:
            return False
        return self.window[0] == "dead" or direction == "rx"

    def write(self, raw):
        if self.dead_writer:
            raise Killed()
        kind, payload = raw[1], raw[4:-2]
        if self.fault and not self.window:
            mode, trigger, duration = self.fault
            if ((trigger == "start" and kind == 1 and len(payload) == 6)
                    or (trigger == "end" and kind == 1 and len(payload) == 2)
                    or (isinstance(trigger, int) and kind == 2 and int.from_bytes(payload[:4], "big") == trigger)):
                self.window = (mode, self.now, self.now + duration)
                if mode in ("killed", "power"):
                    # The packet that triggers it is never sent.
                    raise Killed()
        self.advance(len(raw) * UART_SECONDS_PER_BYTE)
        if self.state == "bootloader" and not self.dropping("tx"):
            erases = len(self.board.erases)
            replies = self.board.receive(raw)
            pages = len(self.board.erases) - erases
            self.erased = self.erased or pages > 0
            self.settle()
            if not self.dropping("rx"):
                self.pending.append((self.now + pages * ERASE_SECONDS_PER_PAGE, b"".join(replies)))
        return len(raw)

    def flush(self):
        pass

    def read(self, count):
        self.advance(0.002)
        due = [entry for entry in self.pending if entry[0] <= self.now]
        self.pending = [entry for entry in self.pending if entry[0] > self.now]
        for _, data in due:
            if not self.dropping("rx"):
                self.queue.extend(data)
        chunk = bytes(self.queue[:count])
        del self.queue[:count]
        return chunk


def catch(link, profile, powered, until):
    """vo_thr_catch.py: identity requests for 1.5 s, then one every HOLD_INTERVAL until the tool's start at `until`;
    what read_catch returns, on the link's clock."""
    client = tool.Bootloader(link, profile, link.clock, link.sleep)
    if not client._identity_probe(1.5):
        link.advance(max(0.0, until - link.now))
        return None
    reply_at = link.now - powered - KERNEL_AFTER_POWER
    while link.now + HOLD_INTERVAL < until:
        link.advance(HOLD_INTERVAL)
        client._send(0, b"\x01\x00")
    link.advance(max(0.0, until - link.now))
    return profile.identity_reply.hex(), reply_at, client.last_sent


def rescue(link, profile, stock, patched, manifest, journal, *, same_boot, boot_clock=None, caught=None,
           erased=False):
    """What run_rescue does, on the emulated board: the bootloader first, then the application, then a quiet wait."""
    record = dict(journal.record)
    resume = tool.resume_point(record, (patched, stock))
    last_sent = record["sent_at"] + tool.JOURNAL_SLACK if same_boot and "sent_at" in record else None
    client = tool.Bootloader(link, profile, link.clock, link.sleep, journal=journal, boot_clock=boot_clock)
    try:
        result = client.rescue(patched, stock, manifest, resume=resume, last_sent=last_sent, caught=caught,
                               erased=erased)
        if result is None and link.state not in ("stock", "patched"):
            link.advance(APPLICATION_CHECK)
            heard = client.last_sent if client.last_sent is not None else last_sent
            client = tool.Bootloader(link, profile, link.clock, link.sleep, journal=journal, boot_clock=boot_clock)
            client.rescue_quiet(patched, stock, manifest, last_sent=heard)
    except (TimeoutError, tool.RecoveryFailed):
        pass
    return client.events


def run(recovering, boot, stock, patched, manifest, fault, rate=1.0, rescue_delay=None):
    """The state the board ends in, or "bricked-unavoidable" where the build makes recovery impossible."""
    profile = identify_bootloader(boot, admitted=False)
    link = TimedLink(boot, stock, patched, fault, rate)
    journal = Journal(link)
    client = tool.Bootloader(link, profile, link.clock, link.sleep, journal=journal)
    killed = False
    try:
        if recovering:
            client.transfer_recovering(patched, stock, manifest)
        else:
            client.transfer(patched, manifest)
    except (TimeoutError, tool.RecoveryFailed):
        pass
    except Killed:
        killed = True
    unavoidable = False
    if recovering and not killed:
        # The installer reads the board back twelve seconds after the transfer, and rescues it when that fails.
        link.advance(12)
        if link.state not in ("stock", "patched"):
            rescue(link, profile, stock, patched, manifest, journal, same_boot=True)
    if killed:
        mode = fault[0]
        if mode == "power":
            catch_at, tool_at = rescue_delay
            whole = link.board.application(len(stock)) in (stock, patched)
            link.power_cycle()
            powered = link.now
            link.advance(catch_at)
            # Power was lost with the application not whole: only a first contact inside the startup window saves it.
            unavoidable = not whole and catch_at >= profile.startup_ticks / 1000 / rate
            caught = catch(link, profile, powered, powered + tool_at)
            rescue(link, profile, stock, patched, manifest, journal, same_boot=False, caught=caught,
                   erased=tool.erased_point(dict(journal.record), (patched, stock)),
                   boot_clock=lambda: link.now - powered - KERNEL_AFTER_POWER)
        else:
            link.advance(rescue_delay)
            # A FOURTH board in a data session halts once the session goes quiet; a rescue reaches it only through a
            # packet it can take again, before then.
            session = profile.session_ticks / 1000 / rate
            unavoidable = (profile.after_session == HALTS and link.erased
                           and (rescue_delay >= session or tool.resume_point(dict(journal.record), (patched, stock)) is None))
            # A RESTARTS board that was left in a session restarts after it and jumps at the end of its startup window.
            unavoidable = unavoidable or (profile.after_session == RESTARTS and link.erased and rescue_delay
                                          >= (profile.session_ticks + profile.exit_ticks + profile.startup_ticks) / 1000 / rate)
            rescue(link, profile, stock, patched, manifest, journal, same_boot=True)
    if link.state == "bootloader":
        link.advance(30)
    if link.state in ("stock", "patched"):
        return link.state
    window = link.window
    if not killed and profile.after_session == HALTS:
        # Without its first packet's acknowledgement nothing tells whether it was written, and the session can't be
        # waited out; likewise once the link stays dead for a whole session.
        unavoidable = (any(event["event"] == "packet0-unacknowledged" for event in client.events)
                       or bool(window and window[0] == "dead" and window[2] - window[1] >= profile.session_ticks / 1000 / rate))
    if (not killed and window and window[0] == "dead" and link.resets
            and window[2] >= link.resets[-1] + profile.startup_ticks / 1000 / rate):
        unavoidable = True
    return "bricked-unavoidable" if unavoidable else "bricked-avoidable"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-image", required=True, type=Path)
    parser.add_argument("--patched-image", required=True, type=Path)
    parser.add_argument("--bootloader-backup", required=True, type=Path, action="append",
                        help="captured image of a recoverable build; repeat for several")
    parser.add_argument("--manifest", default=tool.MANIFEST.name, choices=tool.MANIFESTS)
    parser.add_argument("--quick", action="store_true", help="3 s and 12 s faults only")
    args = parser.parse_args()
    manifest = json.loads((tool.FIRMWARE / args.manifest).read_text())
    stock, patched = (path.read_bytes() for path in (args.stock_image, args.patched_image))
    assert tool.admitted_image(stock, manifest) == manifest["thr"]["stock_sha256"]
    assert tool.admitted_image(patched, manifest) == manifest["thr"]["patched_sha256"]
    reports, avoidable = [], []
    for path in args.bootloader_backup:
        boot = path.read_bytes()
        profile = identify_bootloader(boot, admitted=False)
        assert profile.recoverable, "Bootloader has no validated recovery profile"

        def link_faults(durations):
            return [None] + [(mode, trigger, duration) for mode in ("dead", "lost-reply")
                             for trigger in TRIGGERS for duration in durations]
        plans = [(1.0, link_faults(QUICK_DURATIONS if args.quick else DURATIONS), True)]
        plans += [(rate, link_faults(QUICK_DURATIONS), False) for rate in SKEWED_RATES]
        report = {"bootloader_sha256": tool.digest(boot), "after_session": profile.after_session, "clock_rates": {}}
        for rate, rate_faults, compare in plans:
            outcomes = {"transfer_recovering": collections.Counter()}
            if compare:
                outcomes["transfer"] = collections.Counter()
            for fault in rate_faults:
                if compare:
                    outcomes["transfer"][run(False, boot, stock, patched, manifest, fault, rate)] += 1
                outcome = run(True, boot, stock, patched, manifest, fault, rate)
                outcomes["transfer_recovering"][outcome] += 1
                if fault is None:
                    assert outcome == "patched", "Uninterrupted recovering transfer did not install the patched image"
                if outcome == "bricked-avoidable":
                    avoidable.append((path.name, rate, fault))
            report["clock_rates"][str(rate)] = {"faults": len(rate_faults),
                                                "outcomes": {name: dict(counter) for name, counter in outcomes.items()}}
        for mode, delays in (("killed", RESCUE_DELAYS), ("power", BOOT_DELAYS)):
            counter = collections.Counter()
            for trigger in TRIGGERS:
                for delay in delays:
                    outcome = run(True, boot, stock, patched, manifest, (mode, trigger, 0), rescue_delay=delay)
                    counter[outcome] += 1
                    if outcome == "bricked-avoidable":
                        avoidable.append((path.name, mode, trigger, delay))
            report[mode] = dict(counter)
        reports.append(report)
    print(json.dumps(reports, indent=2))
    assert not avoidable, f"Recovery left a board unusable where it could have been saved: {avoidable}"


if __name__ == "__main__":
    main()
