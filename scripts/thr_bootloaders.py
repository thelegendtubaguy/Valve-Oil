"""Exact THR bootloader build identities and their recovered transfer profiles."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib

# What a bootloader does when a data session goes quiet for session_ticks: every build first erases the first application
# page. WAITS then stays in the bootloader, answering identity and start, until power is lost; RESTARTS resets into its
# startup window; HALTS faults in its reset code and does nothing more until power is lost.
WAITS, RESTARTS, HALTS = "waits", "restarts", "halts"


@dataclass(frozen=True)
class BootloaderProfile:
    sha256: str
    identity_reply: bytes
    start_reply: bytes
    end_reply: bytes
    final_padding: bytes
    erase_range: tuple[int, int, int]
    packet_zero_retry_safe: bool
    initialize: tuple[int, ...]
    controller_hooks: tuple[int, ...]
    erase_hook: int
    program_hook: int
    send_hook: int
    jump_hook: int
    dispatch: int
    receive_buffer: int
    receive_flags: tuple[int, ...]
    session_timer: int
    exit_timer: int
    reset_hook: int | None = None
    tick: int | None = None
    startup_ticks: int | None = None
    session_ticks: int | None = None
    exit_ticks: int | None = None
    after_session: str | None = None

    # startup_ticks: after a reset, the bootloader stays only for an identity and a start that both arrive within
    # this many ticks; otherwise it jumps to the application, whatever the application region holds.

    @property
    def recoverable(self) -> bool:
        """Whether its timing is known well enough for recovery mode (Bootloader.transfer_recovering) and rescue."""
        timed = None not in (self.tick, self.startup_ticks, self.session_ticks, self.exit_ticks, self.after_session)
        return timed and (self.after_session != RESTARTS or self.reset_hook is not None)


FIRST = BootloaderProfile(
    sha256="46358f306156edbdee1822dcca17206f78241b01a7e96587215860c4b0d34fc6",
    identity_reply=bytes.fromhex("01020102"),
    start_reply=b"\x01\x01", end_reply=b"\x02\x01", final_padding=b"\xff",
    erase_range=(0x08008000, 0x08020000, 2048), packet_zero_retry_safe=False,
    initialize=(0x08000c10,),
    controller_hooks=(0x08000498, 0x08000508, 0x080004e0),
    erase_hook=0x080005c4, program_hook=0x08000690,
    send_hook=0x080016d4, jump_hook=0x08000b74,
    dispatch=0x080014d8, receive_buffer=0x20000458,
    receive_flags=(0x20000c62, 0x2000003d),
    session_timer=0x20000034, exit_timer=0x20000008,
    tick=0x08000aec, startup_ticks=10000, session_ticks=10000, exit_ticks=1000, after_session=WAITS,
)
SECOND = BootloaderProfile(
    sha256="31f8344db7b091df5429960bc9cd06065e2188c3b8092e43fb65eb3f2833723c",
    identity_reply=bytes.fromhex("0102"),
    start_reply=b"\x01\x01", end_reply=b"\x02\x01", final_padding=b"\x00",
    erase_range=(0x08008000, 0x08020000, 2048), packet_zero_retry_safe=False,
    initialize=(0x08001d04, 0x08001cdc),
    controller_hooks=(0x08000fbc, 0x08000ff4, 0x08001162),
    erase_hook=0x08001018, program_hook=0x080010cc,
    send_hook=0x08000c74, jump_hook=0x08000aac,
    dispatch=0x0800014e, receive_buffer=0x20000084,
    receive_flags=(0x2000088e, 0x2000000b),
    session_timer=0x20000014, exit_timer=0x20000018,
    reset_hook=0x08000130, tick=0x08001bfc, startup_ticks=4000,
    session_ticks=10000, exit_ticks=1000, after_session=RESTARTS,
)
# The Smart 5's: FIRST's code with one more call in main, which sends the identity reply once at startup, unasked; every
# hook and buffer below that call keeps FIRST's address. It identifies as bootloader 1.3, and its startup window is
# half of FIRST's.
THIRD = replace(FIRST, sha256="a21a3019004a4a3e50ec2e07882b8b39b7ff028161e692e83191144c14da7342",
                identity_reply=bytes.fromhex("01020103"), startup_ticks=5000)
# SECOND with one byte changed, at 0x0133: the first instruction of its reset code (0x08000130), SECOND's `dsb sy`, is an
# undefined instruction here, so where SECOND resets after a quiet data session this build faults and stays halted; it
# has no watchdog. Everything else, including every transfer that doesn't go quiet, is SECOND's.
FOURTH = replace(SECOND, sha256="0302f39c204e13a63fdc5c917085afee1f1b5354467c23f8ae2b9eca0dade3f0", after_session=HALTS)
PROFILES = {profile.sha256: profile for profile in (FIRST, SECOND, THIRD, FOURTH)}
# Admission requires matching offline checks and live non-erasing evidence.
ADMITTED_BOOTLOADERS = frozenset((FIRST.sha256, SECOND.sha256, THIRD.sha256, FOURTH.sha256))


def identify_bootloader(data: bytes, *, admitted: bool = True) -> BootloaderProfile:
    identity = hashlib.sha256(data).hexdigest()
    if len(data) != 0x8000 or identity not in PROFILES or (admitted and identity not in ADMITTED_BOOTLOADERS):
        raise ValueError("Unrecognized bootloader")
    return PROFILES[identity]
