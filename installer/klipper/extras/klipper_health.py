# Klipper main-loop health for Valve Oil.
#
# QIDI's box_extras checks each Box load with the toolhead load cell.
# auto_detect_filament_advance registers _init_probe_data_advance, which
# samples the load cell every 0.2 s for a resting baseline and then stops;
# detect_filament_loaded registers _read_wei_timer, which watches for the
# force jump of filament reaching the nozzle while the extruder pushes, and
# stops it once the load is done or the next check starts. QIDI then drops
# its reference to the stopped timer but never unregisters it, so every load
# leaves its timers in Klipper's list, three per load on the test printer.
# Klipper walks that whole list on every pass of its main loop: after 405
# loads it held 1278 timers, its main thread was busy most of the time even
# while idle, and a Box motor move missed the 50 ms window its endstop sync
# allows ("Communication timeout during homing"), ending a print.
#
# This module unregisters those timers once they can never run again: their
# callback is one of QIDI's two load-cell callbacks, they are stopped, and
# the QIDI object no longer holds them. It also measures how busy Klipper's
# main thread is and how late its timers run, warns when timers keep piling
# up or the main loop stays saturated, and logs that context when an
# endstop sync times out, so the next such failure explains itself. QIDI's
# load-cell probe (probe_air, extras/air) reports a failed endstop sync as
# "WEIGHT sensor error", the timeout included; the THR firmware never sends
# the probe's own sensor-error reason, so that error gets the context too. Its
# status feeds the timer count and main-thread load to Fluidd's System
# Utilization charts.
#
# Endstop syncs: a homing move across boards stays armed only while each
# board's status reports, sent every 30% of the sync timeout (15 ms for
# QIDI's 50 ms), keep reaching Klipper, whose serial thread, not its main
# thread, renews the timeout on the other boards. Each sync here keeps its
# last reports, with the board's clock when it sent each one and Klipper's
# time when its serial thread read it, and its final result. A failed sync
# then says which board timed out, and whether a board sent its reports late
# or they reached Klipper late.
#
# Sync timing: Klipper's C serial code renews each board's timeout from the
# other boards' latest reports, the moment it reads one, to that report's
# send time plus the timeout. From the reports kept here, each sync works out
# the same deadlines: how much time a board had left when the report that
# renews it reached Klipper, and whose report that was, and how long each
# report took from the board to Klipper by the board's clock. Every sync also
# counts each board's retransmits, invalid bytes, and serial-port framing and
# overrun errors. A failed sync adds how often Klipper's threads were
# preempted and where the serial and USB interrupts and softirqs ran.
# Passing syncs go to klippy.log as one line for each run of homing or
# probing, with the least time any board had left, so a printer that comes
# close shows it before a sync fails.
#
# Status copies: QIDI's multi_color_controller status, which Moonraker reads
# every second, deep-copies its flattened Box status, a dict of about 440
# strings, numbers, and lists, which takes about half of the status's 11 ms.
# Klipper's macros deep-copy each object's whole status the first time a
# macro reads it, such as 48 ms for printer.configfile, which QIDI's start,
# end, and startup macros read many times. In both modules copy.deepcopy is
# replaced with a plain copy of the same dicts, lists, tuples, and values,
# several times faster; anything else still goes to copy.deepcopy.
#
# Late MCU commands: a command that takes effect at an MCU clock, such as a
# pin change or an endstop sync's timeout, must reach the MCU before that
# clock. QIDI's Max 4 mainboard runs one that arrives late at once and logs
# "Timer too close" to klippy.log, which names neither the command nor what
# sent it. Each command whose format has a clock is checked as Klipper sends
# it: when its clock is already past, or nearly so, by the MCU's estimated
# time, or by the clock of an earlier command on its queue that it must wait
# for, klippy.log gets a warning naming the MCU, the command, how late it
# is, and the Python calls that sent it. Other commands are not wrapped.
# Steps go out from Klipper's C code and are not checked.

import collections
import copy
import fcntl
import logging
import os
import struct
import sys
import threading
import time
import traceback
import types

# QIDI box_extras / box2base load-cell callbacks whose stopped timers leak.
FINISHED_CALLBACKS = ("_read_wei_timer", "_init_probe_data_advance")
CLEANUP_INTERVAL = 5.
SAMPLE_INTERVAL = 1.
REPORT_INTERVAL = 60.
# Samples kept: enough for the busy window.
HISTORY = 300
# Klipper's timer count settles within a minute of starting; warn when it
# grows this far past that.
TIMER_GROWTH_WARNING = 50
# Warn when the main thread is at least this busy over the whole window.
# Printing and Box tool changes peak near 25% over five minutes on the test
# printer; the leaked timers had it near 50% when a Box unload timed out.
BUSY_LOAD = 0.45
BUSY_WINDOW = 300.
WARNING_REPEAT = 3600.
# Window before an endstop sync timeout reported with it.
TIMEOUT_CONTEXT = 10.
HOMING_TIMEOUT = "Communication timeout during homing"
PROBE_SYNC_ERROR = "WEIGHT sensor error"
PROBE_SYNC_FAILURE = ("Load-cell probe endstop sync failure (QIDI reports it "
                      "as \"%s\")" % (PROBE_SYNC_ERROR,))
# Status reports kept per endstop sync, about a second of QIDI's, and how
# many of them klippy.log gets after a failure.
SYNC_REPORTS = 64
SYNC_REPORTS_LOGGED = 12
# Reports kept per board for a sync's timing, about 75 s of QIDI's.
SYNC_TIMING_REPORTS = 5000
# Klipper renews a board's timeout only when that moves it at least this
# share of the report interval, which is 30% of the timeout.
MIN_EXTEND = .8 * .3
# Passing syncs are logged together once none has run for this long.
SYNC_SUMMARY_IDLE = 3.
# Host counters read for a failed sync; a dispatch's boards share them.
SYSTEM_SNAPSHOT_REUSE = .1
# Linux TIOCGICOUNT and its struct serial_icounter_struct of 20 ints.
TIOCGICOUNT = 0x545D
ICOUNT_FIELDS = ("cts", "dsr", "rng", "dcd", "rx", "tx", "frame",
                 "overrun", "parity", "brk", "buf_overrun")
UART_ERRORS = ("frame", "overrun", "parity", "brk", "buf_overrun")
LINK_ERRORS = ("bytes_retransmit", "bytes_invalid")
# Objects whose modules deep-copy whole statuses: QIDI's Box status, and
# Klipper's macro access to every object's status.
STATUS_COPIERS = ("multi_color_controller", "gcode_macro")
# Command parameters holding the MCU clock a command takes effect at.
CLOCK_PARAMS = ("clock", "report_clock")
# A command less than this ahead of its MCU when sent is reported: the
# mainboard link's round trip alone is about 1 ms.
LATE_COMMAND_MARGIN = 0.005
# An MCU compares times in 32 bits, so a command this many ticks or more
# ahead looks past to it (3.58 s at the Smart 5 mainboard's 600 MHz) and is
# reported too.
MAX_AHEAD_TICKS = 0x80000000
# Each MCU's command is reported at most this often; the next report counts
# the ones in between.
LATE_COMMAND_REPEAT = 10.
# Python calls listed with a late command, innermost first.
LATE_COMMAND_STACK = 8

# The KlipperHealth of the running Klipper; the endstop wrapper outlives a
# RESTART, which creates a new one.
_current = [None]


def _held_ids(owner):
    """Ids of the objects an owner holds in its attributes, one level into
    containers."""
    held = set()
    try:
        values = list(vars(owner).values())
    except TypeError:
        return held
    for value in values:
        held.add(id(value))
        if isinstance(value, dict):
            held.update(id(v) for v in value.values())
        elif isinstance(value, (list, tuple, set)):
            held.update(id(v) for v in value)
    return held


def finished_timers(reactor):
    """QIDI load-cell timers that can never run again."""
    timers = getattr(reactor, "_timers", None)
    if not isinstance(timers, list):
        return []
    found = []
    held = {}
    for timer in timers:
        callback = getattr(timer, "callback", None)
        if getattr(callback, "__name__", None) not in FINISHED_CALLBACKS \
                or getattr(timer, "waketime", None) != reactor.NEVER:
            continue
        owner = getattr(callback, "__self__", None)
        if owner is None:
            continue
        if id(owner) not in held:
            held[id(owner)] = _held_ids(owner)
        if id(timer) not in held[id(owner)]:
            found.append(timer)
    return found


def _wrap_trsync(cls):
    """Make cls, Klipper's MCU_trsync, keep its recent status reports and
    final result for each sync. Returns True when it does."""
    start = getattr(cls, "start", None)
    handle = getattr(cls, "_handle_trsync_state", None)
    stop = getattr(cls, "stop", None)
    if start is None or handle is None or stop is None:
        return False
    if getattr(handle, "_vo_klipper_health", False):
        return True

    def start_sync(trsync, *args, **kwargs):
        timeout = args[3] if len(args) > 3 else kwargs.get("expire_timeout")
        print_time = args[0] if args else kwargs.get("print_time")
        sync = {"timeout": timeout, "result": None, "noted": False,
                "reports": collections.deque(maxlen=SYNC_REPORTS),
                "timing": [], "start": None, "link": None, "system": None}
        try:
            sync["start"] = _start_systime(trsync.get_mcu(), print_time)
            sync["link"] = link_counters(trsync.get_mcu())
            sync["system"] = _system_snapshot()
        except Exception:
            logging.exception("vo_klipper_health: sync start figures")
        trsync._vo_sync = sync
        return start(trsync, *args, **kwargs)

    # Runs on the board's serial thread.
    def handle_report(trsync, params):
        sync = getattr(trsync, "_vo_sync", None)
        if sync is not None:
            report = (params.get("#receive_time"), params.get("clock"),
                      params.get("can_trigger"), params.get("trigger_reason"))
            sync["reports"].append(report)
            if len(sync["timing"]) < SYNC_TIMING_REPORTS:
                sync["timing"].append(report)
        return handle(trsync, params)

    def stop_sync(trsync, *args, **kwargs):
        result = stop(trsync, *args, **kwargs)
        sync = getattr(trsync, "_vo_sync", None)
        if sync is not None:
            sync["result"] = result
        return result

    handle_report._vo_klipper_health = True
    # Lets vo_klipper_fixes find its homing fix under this wrapper.
    start_sync.__wrapped__ = start
    handle_report.__wrapped__ = handle
    stop_sync.__wrapped__ = stop
    cls.start = start_sync
    cls._handle_trsync_state = handle_report
    cls.stop = stop_sync
    return True


def _endstop_trsyncs(endstop):
    """(MCU name, trsync or None) for each board in an endstop's sync."""
    found = []
    dispatch = getattr(endstop, "_dispatch", None)
    for trsync in getattr(dispatch, "_trsyncs", None) or ():
        try:
            found.append((trsync.get_mcu().get_name(), trsync))
        except Exception:
            pass
    if not found:
        try:
            found.append((endstop.get_mcu().get_name(), None))
        except Exception:
            pass
    return found


def send_gaps(reports, freq):
    """Seconds since the board sent its previous status report, for each
    report, or None where either has no send time: the report answering
    Klipper's own trigger request carries clock 0."""
    gaps, previous = [], None
    for _receive_time, clock, can_trigger, _reason in reports:
        if clock is None or not (can_trigger or clock):
            clock = None
        gaps.append(None if clock is None or previous is None
                    else ((clock - previous) & 0xffffffff) / freq)
        previous = clock
    return gaps


def _start_systime(mcu, print_time):
    """Host time of a sync's start, when Klipper sets each board's first
    deadline, or None."""
    clocksync = getattr(mcu, "_clocksync", None)
    if print_time is None or clocksync is None:
        return None
    return clocksync.estimate_clock_systime(mcu.print_time_to_clock(print_time))


def _parse_stats(text):
    values = {}
    for item in (text or "").split():
        name, _, value = item.partition("=")
        try:
            values[name] = float(value)
        except ValueError:
            pass
    return values


def link_counters(mcu):
    """A board's retransmit and invalid byte counts, and its serial port's
    error counts when the port has them, or None."""
    serial = getattr(mcu, "_serial", None)
    if serial is None:
        return None
    counters = {}
    try:
        stats = _parse_stats(serial.stats(time.monotonic()))
    except Exception:
        stats = {}
    for name in LINK_ERRORS:
        if name in stats:
            counters[name] = stats[name]
    try:
        fd = serial.serial_dev.fileno()
        raw = fcntl.ioctl(fd, TIOCGICOUNT, bytes(80))
        values = struct.unpack("20i", raw)
        for name, value in zip(ICOUNT_FIELDS, values):
            if name in UART_ERRORS:
                counters["uart_" + name] = value
    except Exception:
        pass
    return counters


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _per_cpu(text, keep):
    """Per-CPU counts from /proc/interrupts or /proc/softirqs, for each line
    keep(name, label) names."""
    counts = {}
    lines = (text or "").splitlines()
    if not lines:
        return counts
    cpus = len(lines[0].split())
    for line in lines[1:]:
        head, _, rest = line.partition(":")
        fields = rest.split()
        values = []
        for field in fields[:cpus]:
            if not field.isdigit():
                break
            values.append(int(field))
        label = keep(head.strip(), " ".join(fields[cpus:]))
        if label and len(values) == cpus:
            counts[label] = values
    return counts


def _irq_label(name, description):
    for device in description.split():
        if device.startswith("ttyS") or "hcd:usb" in device:
            return device
    return None


def _system_snapshot(cache=[None, None]):
    """How often each Klipper thread has been preempted, and the per-CPU
    counts of the serial and USB interrupts and of softirqs. A snapshot
    taken in the last SYSTEM_SNAPSHOT_REUSE seconds is reused."""
    now = time.monotonic()
    if cache[0] is not None and now - cache[0] < SYSTEM_SNAPSHOT_REUSE:
        return cache[1]
    threads = {}
    try:
        tids = os.listdir("/proc/self/task")
    except OSError:
        tids = []
    for tid in tids:
        for line in (_read("/proc/self/task/%s/status" % (tid,)) or
                     "").splitlines():
            if line.startswith("nonvoluntary_ctxt_switches:"):
                threads[tid] = int(line.split()[1])
    snapshot = {
        "threads": threads,
        "interrupts": _per_cpu(_read("/proc/interrupts"), _irq_label),
        "softirqs": _per_cpu(_read("/proc/softirqs"),
                             lambda name, _rest: name),
    }
    cache[0], cache[1] = now, snapshot
    return snapshot


def _counter_change(before, after):
    return {name: after[name] - before[name]
            for name in after if name in (before or {})}


def sync_timing(trsyncs):
    """Each board's figures for one sync: the least time it had left when
    Klipper read the report that renews its timeout, whose report that was,
    the longest a report took from the board to Klipper, and its link
    counters' change. Klipper renews a board's deadline to the earliest of
    the other boards' latest report send times plus the timeout, as its C
    serial code does; a sync on one board renews from its own reports."""
    boards = []
    for name, trsync in trsyncs:
        sync = getattr(trsync, "_vo_sync", None)
        mcu = trsync.get_mcu() if trsync is not None else None
        clocksync = getattr(mcu, "_clocksync", None)
        if not sync or clocksync is None or sync["start"] is None \
                or not sync["timeout"]:
            continue
        boards.append((name, mcu, clocksync, sync))
    if not boards:
        return {}
    figures = {}
    events = []
    for name, mcu, clocksync, sync in boards:
        figures[name] = {"spare": None, "from": None, "delay": None,
                         "link": _counter_change(sync["link"],
                                                 link_counters(mcu) or {})}
        for receive_time, clock, can_trigger, _reason in sync["timing"]:
            if receive_time is None or clock is None:
                continue
            if not can_trigger:
                events.append((receive_time, name, None))
                continue
            sent = clocksync.estimate_clock_systime(
                clocksync.clock32_to_clock64(clock))
            events.append((receive_time, name, sent))
            delay = receive_time - sent
            if figures[name]["delay"] is None or delay > figures[name]["delay"]:
                figures[name]["delay"] = delay
    events.sort(key=lambda event: event[0])
    status = {name: sync["start"] for name, _m, _c, sync in boards}
    timeouts = {name: sync["timeout"] for name, _m, _c, sync in boards}
    expire = {name: status[name] + timeouts[name] for name in status}
    for receive_time, sender, sent in events:
        if sent is None:
            # A board triggered or timed out; Klipper stops renewing.
            break
        status[sender] = sent
        for name in expire:
            others = [(status[o], o) for o in status if o != name] \
                or [(status[name], name)]
            earliest, source = min(others)
            renewed = earliest + timeouts[name]
            if renewed - expire[name] < MIN_EXTEND * timeouts[name]:
                continue
            spare = expire[name] - receive_time
            board = figures[name]
            if board["spare"] is None or spare < board["spare"]:
                board["spare"], board["from"] = spare, source
            expire[name] = renewed
    return figures


def _ms(seconds):
    return "%.1f" % (seconds * 1000.,)


def _timing_summary(figures):
    """The console sentences for a failed sync's timing."""
    spares = ["%s ms on %s (from %s's reports)"
              % (_ms(f["spare"]), name, f["from"])
              for name, f in sorted(figures.items())
              if f["spare"] is not None]
    delays = ["%s ms (%s)" % (_ms(f["delay"]), name)
              for name, f in sorted(figures.items())
              if f["delay"] is not None]
    errors = []
    for name, f in sorted(figures.items()):
        found = ", ".join("%d %s" % (value, label.replace("uart_", "serial ")
                                     .replace("bytes_", "").replace("_", " "))
                          for label, value in sorted(f["link"].items())
                          if value)
        if found:
            errors.append("%s: %s" % (name, found))
    text = ""
    if spares:
        text += (" Klipper read the reports that renew each board's timeout "
                 "with as little as %s to spare; below 0 means a renewal came "
                 "after the deadline." % (" and ".join(spares),))
    if delays:
        text += (" Reports took up to %s to reach Klipper."
                 % (" and ".join(delays),))
    if errors:
        text += " Link errors during the sync: %s." % ("; ".join(errors),)
    return text


def _system_detail(trsyncs, eventtime_snapshot):
    """klippy.log's host figures over a failed sync: thread preemptions and
    per-CPU interrupt and softirq counts."""
    before = None
    for _name, trsync in trsyncs:
        sync = getattr(trsync, "_vo_sync", None)
        if sync and sync["system"]:
            before = sync["system"]
            break
    after = eventtime_snapshot
    if not before or not after:
        return ""
    main = str(threading.main_thread().native_id) \
        if hasattr(threading.main_thread(), "native_id") else None
    preempted = {tid: after["threads"][tid] - before["threads"][tid]
                 for tid in after["threads"] if tid in before["threads"]}
    others = sorted(((count, tid) for tid, count in preempted.items()
                     if tid != main), reverse=True)
    parts = ["Klipper threads preempted: main +%d, others %s"
             % (preempted.get(main, 0),
                " ".join("%s:+%d" % (tid, count) for count, tid in others
                         if count) or "+0")]
    for key, label in (("interrupts", "interrupts per CPU"),
                       ("softirqs", "softirqs per CPU")):
        changes = []
        for name, values in sorted(after[key].items()):
            old = before[key].get(name)
            if old is None or len(old) != len(values):
                continue
            change = [b - a for a, b in zip(old, values)]
            if any(change):
                changes.append("%s %s" % (name, "/".join(map(str, change))))
        parts.append("%s: %s" % (label, ", ".join(changes) or "none"))
    return "; ".join(parts)


def _timing_detail(figures):
    entries = []
    for name, f in sorted(figures.items()):
        entry = "%s spare %s ms from %s, delay %s ms" % (
            name, _ms(f["spare"]) if f["spare"] is not None else "?",
            f["from"] or "?",
            _ms(f["delay"]) if f["delay"] is not None else "?")
        link = " ".join("%s +%d" % (label, value)
                        for label, value in sorted(f["link"].items()) if value)
        entries.append(entry + ", " + (link or "no link errors"))
    return "; ".join(entries)


def _sync_detail(trsyncs):
    """The console summary of a failed sync's status reports, and the
    klippy.log list of each board's last reports."""
    timed_out, gaps, logged = [], [], []
    syncs = [(name, trsync, getattr(trsync, "_vo_sync", None))
             for name, trsync in trsyncs]
    syncs = [(name, trsync, sync) for name, trsync, sync in syncs if sync]
    if not syncs:
        return "", ""
    times = [r[0] for _, _, sync in syncs for r in sync["reports"]
             if r[0] is not None]
    last = max(times) if times else None
    timeout = None
    for name, trsync, sync in syncs:
        timeout = timeout or sync["timeout"]
        limit = getattr(trsync, "REASON_COMMS_TIMEOUT", None)
        if limit is not None and sync["result"] is not None \
                and sync["result"] >= limit:
            timed_out.append(name)
        reports = list(sync["reports"])
        try:
            freq = float(trsync.get_mcu().seconds_to_clock(1.))
        except Exception:
            continue
        sent = send_gaps(reports, freq)
        if len(reports) >= 2:
            received = [b[0] - a[0] for a, b in zip(reports, reports[1:])
                        if a[0] is not None and b[0] is not None]
            gaps.append("%s sent them up to %d ms apart and Klipper read them "
                        "up to %d ms apart"
                        % (name, round(max([g for g in sent if g] or [0.])
                                       * 1000),
                           round(max(received or [0.]) * 1000)))
        entries = []
        for (receive_time, _clock, can_trigger, reason), gap in zip(
                reports[-SYNC_REPORTS_LOGGED:], sent[-SYNC_REPORTS_LOGGED:]):
            entry = ("%.1f" % ((receive_time - last) * 1000.)
                     if receive_time is not None and last is not None
                     else "?")
            if gap is not None:
                entry += "/%.1f" % (gap * 1000.,)
            if not can_trigger:
                entry += " reason=%s" % (reason,)
            entries.append(entry)
        logged.append("%s (%d reports, result %s): %s"
                      % (name, len(sync["reports"]), sync["result"],
                         " ".join(entries) or "none"))
    summary = ""
    if timed_out:
        summary += " The sync timed out on %s" % (" and ".join(timed_out),)
        if timeout:
            summary += " (%d ms limit)" % (round(timeout * 1000),)
        summary += "."
    if gaps:
        summary += " Of its status reports, %s." % ("; ".join(gaps),)
    detail = ("endstop sync status reports, as ms read before the last one "
              "/ ms since the board sent its previous one: %s"
              % ("; ".join(logged),))
    return summary, detail


def _wrap_home_wait(cls, error, failure):
    """Make cls.home_wait report the context of an endstop sync failure,
    which it raises with error in the message. Returns True when it does."""
    original = getattr(cls, "home_wait", None)
    if original is None:
        return False
    if getattr(original, "_vo_klipper_health", False):
        return True

    def home_wait(endstop, *args, **kwargs):
        try:
            result = original(endstop, *args, **kwargs)
        except Exception as e:
            health = _current[0]
            if health is not None and error in str(e):
                try:
                    health.note_homing_timeout(failure,
                                               _endstop_trsyncs(endstop))
                except Exception:
                    logging.exception("vo_klipper_health: homing timeout "
                                      "report failed")
            raise
        health = _current[0]
        if health is not None:
            try:
                health.note_sync(_endstop_trsyncs(endstop))
            except Exception:
                logging.exception("vo_klipper_health: sync timing failed")
        return result
    home_wait._vo_klipper_health = True
    cls.home_wait = home_wait
    return True


def _late_command(mcu, command, ahead, reach):
    health = _current[0]
    if health is None:
        return
    try:
        # The caller of the command's send.
        health.note_late_command(mcu.get_name(), command, ahead,
                                 sys._getframe(2), reach)
    except Exception:
        logging.exception("vo_klipper_health: late command report failed")


def watch_command(mcu, command, msgformat):
    """Check each send of an MCU command whose format has a clock. Returns
    True when its sends are checked."""
    parts = msgformat.split()
    index = next((i for i, arg in enumerate(parts[1:])
                  if arg.split("=")[0] in CLOCK_PARAMS), None)
    send = getattr(command, "send", None)
    get_clock = getattr(getattr(mcu, "_clocksync", None), "get_clock", None)
    if index is None or send is None or get_clock is None:
        return False
    freq = float(mcu.seconds_to_clock(1.))
    if freq <= 0.:
        return False
    monotonic = mcu.get_printer().get_reactor().monotonic
    margin = LATE_COMMAND_MARGIN * freq
    name = parts[0]

    def send_checked(data=(), minclock=0, reqclock=0):
        try:
            clock = data[index]
        except (IndexError, KeyError, TypeError):
            clock = 0
        # Clock 0 stops a command, such as an endstop check, instead.
        if clock:
            now = get_clock(monotonic())
            # Ticks ahead of the MCU, negative when past. Klipper's clocks
            # are full host clocks; one cut to the 32 bits the MCU receives
            # is compared in 32 bits, as the MCU compares it.
            ahead = clock - now
            if clock <= 0xffffffff < now:
                ahead = ((ahead + 0x80000000) & 0xffffffff) - 0x80000000
            # Klipper holds the command until minclock, a full host clock
            # that can be any time before the command's own.
            ahead -= max(0, minclock - now)
            if ahead < margin or ahead >= MAX_AHEAD_TICKS:
                _late_command(mcu, name, ahead / freq,
                              MAX_AHEAD_TICKS / freq)
        return send(data, minclock, reqclock)
    command.send = send_checked
    return True


def _watch_late_commands(cls):
    """Make cls, Klipper's MCU, check the commands it looks up that have a
    clock. Returns True when it does."""
    lookups = [(name, getattr(cls, name, None))
               for name in ("lookup_command", "lookup_query_command")]
    if any(lookup is None for _name, lookup in lookups):
        return False

    def wrap(lookup):
        if getattr(lookup, "_vo_klipper_health", False):
            return lookup

        def lookup_watched(mcu, msgformat, *args, **kwargs):
            command = lookup(mcu, msgformat, *args, **kwargs)
            try:
                watch_command(mcu, command, msgformat)
            except Exception:
                logging.exception("vo_klipper_health: cannot check %s",
                                  msgformat)
            return command
        lookup_watched._vo_klipper_health = True
        return lookup_watched
    for name, lookup in lookups:
        setattr(cls, name, wrap(lookup))
    return True


_PLAIN_VALUES = (str, int, float, bool, type(None))


def _plain_copy(value):
    kind = type(value)
    if kind in _PLAIN_VALUES:
        return value
    if kind is dict:
        return {key: _plain_copy(item) for key, item in value.items()}
    if kind is list:
        return [_plain_copy(item) for item in value]
    if isinstance(value, tuple):
        # As copy.deepcopy: a tuple, or a named tuple such as Klipper's
        # Coord, whose items copy to themselves is returned as it is.
        items = [_plain_copy(item) for item in value]
        if all(new is old for new, old in zip(items, value)):
            return value
        if kind is tuple:
            return tuple(items)
    raise TypeError(kind)


def _deepcopy(value, memo=None, *args):
    """copy.deepcopy for status trees of plain values, copied directly."""
    if memo is None:
        try:
            return _plain_copy(value)
        except (TypeError, RecursionError):
            pass
    return copy.deepcopy(value, memo, *args)


def _plain_copy_module():
    module = types.ModuleType("copy")
    module.__dict__.update(copy.__dict__)
    module.deepcopy = _deepcopy
    return module


def lighten_status_copy(obj):
    """Give the module that defines obj's class the plain-value deepcopy.
    Returns True when it has it."""
    module = sys.modules.get(getattr(type(obj), "__module__", None))
    current = getattr(module, "copy", None)
    if getattr(current, "deepcopy", None) is _deepcopy:
        return True
    if current is not copy:
        return False
    module.copy = _plain_copy_module()
    return True


class KlipperHealth:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.removed = 0
        self.timer_baseline = None
        self.samples = collections.deque(maxlen=HISTORY)
        self.last_warning = {}
        self.late_commands = {}
        self.sync_summary = None
        self._next_cleanup = 0.
        self._next_report = None
        self._waketime = None
        _current[0] = self
        try:
            import mcu
        except ImportError:
            mcu = None
        if not _wrap_home_wait(getattr(mcu, "MCU_endstop", None),
                               HOMING_TIMEOUT, HOMING_TIMEOUT):
            logging.info("vo_klipper_health: Klipper's endstop layout is "
                         "not as expected; homing timeouts are not reported")
        if not _watch_late_commands(getattr(mcu, "MCU", None)):
            logging.info("vo_klipper_health: Klipper's MCU commands are not "
                         "as expected; late commands are not reported")
        if not _wrap_trsync(getattr(mcu, "MCU_trsync", None)):
            logging.info("vo_klipper_health: Klipper's endstop sync is not "
                         "as expected; failed syncs do not report their "
                         "status reports")
        self.printer.register_event_handler("klippy:connect", self._connect)
        self.printer.register_event_handler("klippy:ready", self._ready)

    def _connect(self):
        air = sys.modules.get("extras.air")
        if air is not None and not _wrap_home_wait(
                getattr(air, "WeighEndstopWrapper", None),
                PROBE_SYNC_ERROR, PROBE_SYNC_FAILURE):
            logging.info("vo_klipper_health: QIDI's load-cell probe is not "
                         "as expected; its endstop sync failures are not "
                         "reported")
        for name in STATUS_COPIERS:
            obj = self.printer.lookup_object(name, None)
            if obj is not None and not lighten_status_copy(obj):
                logging.info("vo_klipper_health: %s does not use the copy "
                             "module as expected; its status copy is "
                             "unchanged", name)

    def _ready(self):
        now = self.reactor.monotonic()
        self._next_cleanup = now + CLEANUP_INTERVAL
        self._next_report = now + REPORT_INTERVAL
        self._waketime = now + SAMPLE_INTERVAL
        self.reactor.register_timer(self._sample, self._waketime)

    # ---------------------------------------------------------------- timers
    def timer_count(self):
        return len(getattr(self.reactor, "_timers", None) or ())

    def cleanup(self):
        """Unregister finished QIDI load-cell timers; returns how many."""
        removed = 0
        for timer in finished_timers(self.reactor):
            try:
                self.reactor.unregister_timer(timer)
            except ValueError:
                continue
            removed += 1
        if removed:
            self.removed += removed
            logging.info("vo_klipper_health: removed %d finished QIDI "
                         "load-cell timers (%d since Klipper started)",
                         removed, self.removed)
        return removed

    # -------------------------------------------------------------- sampling
    def _sample(self, eventtime):
        delay = max(0., eventtime - self._waketime)
        self.samples.append((eventtime, time.thread_time(), delay))
        if eventtime >= self._next_cleanup:
            self._next_cleanup = eventtime + CLEANUP_INTERVAL
            self.cleanup()
        if eventtime >= self._next_report:
            self._next_report = eventtime + REPORT_INTERVAL
            self._report(eventtime)
        summary = self.sync_summary
        if summary is not None \
                and eventtime - summary["last"] >= SYNC_SUMMARY_IDLE:
            self.log_sync_summary()
        self._waketime = eventtime + SAMPLE_INTERVAL
        return self._waketime

    def _window(self, eventtime, seconds):
        """Main-thread load, its peak over one sample, and the largest timer
        delay over the last `seconds`."""
        recent = [s for s in self.samples if s[0] >= eventtime - seconds]
        if len(recent) < 2:
            return None, None, None
        load = ((recent[-1][1] - recent[0][1])
                / max(recent[-1][0] - recent[0][0], 1e-6))
        peak = max((b[1] - a[1]) / max(b[0] - a[0], 1e-6)
                   for a, b in zip(recent, recent[1:]))
        return load, peak, max(s[2] for s in recent[1:])

    def _report(self, eventtime):
        timers = self.timer_count()
        if self.timer_baseline is None:
            self.timer_baseline = timers
        load, peak, delay = self._window(eventtime, REPORT_INTERVAL)
        if load is not None:
            logging.info("vo_klipper_health: timers=%d removed=%d "
                         "main_thread_load=%.2f peak=%.2f max_delay=%.3f",
                         timers, self.removed, load, peak, delay)
        if timers >= self.timer_baseline + TIMER_GROWTH_WARNING:
            self._warn(eventtime, "timers",
                       "Klipper has %d timers, %d more than after it started, "
                       "most of them %s. Klipper slows down as they pile up; "
                       "restart Klipper when the printer is idle, and please "
                       "report this with klippy.log."
                       % (timers, timers - self.timer_baseline,
                          self._top_callbacks()))
        busy, _peak, _delay = self._window(eventtime, BUSY_WINDOW)
        if busy is not None and busy >= BUSY_LOAD \
                and self.samples[0][0] <= eventtime - BUSY_WINDOW + 2.:
            self._warn(eventtime, "busy",
                       "Klipper's main loop has been %d%% busy for %d minutes."
                       " With that little headroom, Box motor moves can fail "
                       "with \"%s\"."
                       % (round(busy * 100), BUSY_WINDOW // 60,
                          HOMING_TIMEOUT))

    def _top_callbacks(self):
        counts = collections.Counter(
            getattr(getattr(t, "callback", None), "__qualname__", "?")
            for t in getattr(self.reactor, "_timers", None) or ())
        return ", ".join("%s (%d)" % item for item in counts.most_common(2))

    def _warn(self, eventtime, kind, message):
        last = self.last_warning.get(kind)
        if last is not None and eventtime - last < WARNING_REPEAT:
            return
        self.last_warning[kind] = eventtime
        logging.warning("vo_klipper_health: %s", message)
        self.gcode.respond_info(message)

    # ------------------------------------------------------- homing timeouts
    def note_homing_timeout(self, failure, trsyncs):
        eventtime = self.reactor.monotonic()
        mcus = [name for name, _trsync in trsyncs]
        summary, detail = _sync_detail(trsyncs)
        figures, system = {}, ""
        if self._take_syncs(trsyncs):
            # The passing syncs before this one, in their own line first.
            self.log_sync_summary()
            figures = sync_timing(trsyncs)
            system = _system_detail(trsyncs, _system_snapshot())
            summary += _timing_summary(figures)
        load, peak, delay = self._window(eventtime, TIMEOUT_CONTEXT)
        if load is None:
            context = "no load figures yet"
        else:
            context = ("Klipper's main thread was %d%% busy (peak %d%%) and "
                       "its timers ran up to %d ms late"
                       % (round(load * 100), round(peak * 100),
                          round(delay * 1000)))
        message = ("%s on %s.%s In the %d s before it, %s, with %d timers."
                   % (failure, " and ".join(mcus) or "an endstop", summary,
                      TIMEOUT_CONTEXT, context, self.timer_count()))
        logging.warning("vo_klipper_health: %s", message)
        if detail:
            logging.info("vo_klipper_health: %s", detail)
        if figures:
            logging.info("vo_klipper_health: endstop sync timing: %s",
                         _timing_detail(figures))
        if system:
            logging.info("vo_klipper_health: during the sync: %s", system)
        self.gcode.respond_info(message)

    # ---------------------------------------------------- passing syncs
    @staticmethod
    def _take_syncs(trsyncs):
        """Mark a sync's boards as reported; False when a wrapper around
        the same sync already did."""
        syncs = [getattr(trsync, "_vo_sync", None)
                 for _name, trsync in trsyncs]
        syncs = [sync for sync in syncs if sync]
        if not syncs or any(sync["noted"] for sync in syncs):
            return False
        for sync in syncs:
            sync["noted"] = True
        return True

    def note_sync(self, trsyncs):
        """Add a passing sync's timing to the summary klippy.log gets once
        homing or probing pauses."""
        if not self._take_syncs(trsyncs):
            return
        figures = sync_timing(trsyncs)
        if not figures:
            return
        eventtime = self.reactor.monotonic()
        summary = self.sync_summary
        if summary is None:
            summary = self.sync_summary = {
                "first": time.time(), "count": 0, "boards": {}}
        summary["last"] = eventtime
        summary["count"] += 1
        for name, f in figures.items():
            board = summary["boards"].setdefault(
                name, {"spare": None, "from": None, "delay": None,
                       "link": collections.Counter()})
            if f["spare"] is not None and (board["spare"] is None
                                           or f["spare"] < board["spare"]):
                board["spare"], board["from"] = f["spare"], f["from"]
            if f["delay"] is not None and (board["delay"] is None
                                           or f["delay"] > board["delay"]):
                board["delay"] = f["delay"]
            board["link"].update(f["link"])

    def log_sync_summary(self):
        summary, self.sync_summary = self.sync_summary, None
        if summary is None:
            return
        logging.info(
            "vo_klipper_health: %d endstop syncs from %s passed; %s",
            summary["count"],
            time.strftime("%H:%M:%S", time.localtime(summary["first"])),
            _timing_detail(summary["boards"]))

    # --------------------------------------------------------- late commands
    def note_late_command(self, mcu_name, command, ahead, frame, reach=None):
        """Log a command sent with its clock past, or nearly, by `ahead`
        seconds, or further ahead than the reach of its MCU's 32-bit clock,
        and the Python calls that sent it from frame."""
        eventtime = self.reactor.monotonic()
        key = (mcu_name, command)
        last, skipped = self.late_commands.get(key, (None, 0))
        if last is not None and eventtime - last < LATE_COMMAND_REPEAT:
            self.late_commands[key] = (last, skipped + 1)
            return
        self.late_commands[key] = (eventtime, 0)
        calls = traceback.StackSummary.extract(
            traceback.walk_stack(frame), limit=LATE_COMMAND_STACK,
            lookup_lines=False)
        kind = "late MCU command"
        if ahead < 0.:
            timing = "%.1f ms late" % (-ahead * 1000.,)
        elif reach is not None and ahead >= reach:
            kind = "MCU command too far ahead"
            timing = ("%.1f ms ahead, beyond the %.1f ms its clock reaches,"
                      % (ahead * 1000., reach * 1000.))
        else:
            timing = "only %.1f ms ahead" % (ahead * 1000.,)
        logging.warning(
            "vo_klipper_health: %s: %s on '%s' %s when sent"
            "%s; called from %s", kind, command, mcu_name, timing,
            " (%d more since the last report)" % (skipped,) if skipped else "",
            " < ".join("%s:%d %s" % (os.path.basename(call.filename),
                                     call.lineno, call.name)
                       for call in calls) or "unknown")

    def get_status(self, eventtime=None):
        """Live figures for Fluidd's System Utilization charts: the timer
        count, and the main thread's load and timer delay over the last
        sample."""
        load = delay = None
        if len(self.samples) >= 2:
            (t0, cpu0, _), (t1, cpu1, delay) = self.samples[-2], self.samples[-1]
            load = round((cpu1 - cpu0) / max(t1 - t0, 1e-6), 3)
            delay = round(delay, 3)
        return {
            "timers": self.timer_count(),
            "removed_timers": self.removed,
            "main_thread_load": load,
            "timer_delay": delay,
        }


def load_config(config):
    return KlipperHealth(config)
