"""The files the printer needs to start: merged /usr's /sbin, /bin, and /lib links, and the /sbin/init they lead to.

QIDI's system image is merged /usr: /sbin, /bin, and /lib are relative links to usr/sbin, usr/bin, and usr/lib, and the
kernel starts /sbin/init, a link to /lib/systemd/systemd. QIDI's touchscreen app breaks that. Its Format External
Storage button (usbFormattingHandler) looks for mkfs.vfat or mkfs.fat in /usr/sbin, /sbin, /usr/bin, and /bin, and
when neither is there it runs `dpkg-deb -x <its dosfstools package> /`, which unpacks the package like a tarball and
puts a real /sbin directory, holding only dosfstools, where the link was. Everything keeps running until the next
start, which then stops at the boot logo: there is no /sbin/init, so systemd, the network, SSH, and ADB never start.

Every install checks these files before it changes anything else and puts back a link a directory replaced; every
reboot Valve Oil starts checks them first and refuses while they are broken. Install also installs QIDI's own dosfstools
package with dpkg, so the app finds its tool and never unpacks it. Uninstall undoes neither: both leave the system as it
should be, and removing dosfstools would let the app break /sbin again.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import messages
from .errors import InstallerError
from .naming import REPOSITORY_URL
from .sudo import authenticate_sudo, run_sudo

SYSTEM_ROOT_ENV = "TLTG_OPTIMIZED_SYSTEM_ROOT"  # system_optimizations.SYSTEM_ROOT_ENV: a test's stand-in for /
DEFAULT_PRINTER_DATA_ROOT = Path("/home/qidi/printer_data")
RECOVERY_DOC_URL = f"{REPOSITORY_URL}/blob/main/docs/recovering-an-unbootable-printer.md"
# Each top-level link, where it points on merged /usr, and a file only a working directory there holds: an unmerged
# system keeps real directories that hold these files themselves, and is left alone.
LINKS = (("sbin", "usr/sbin", "init"), ("bin", "usr/bin", "sh"), ("lib", "usr/lib", "systemd/systemd"))
INIT = "/sbin/init"
DOSFSTOOLS_DEB = "/home/qidi/QIDI_Client/resource/mkfs_tools/dosfstools_4.2-1_arm64.deb"
DOSFSTOOLS_PACKAGE = "dosfstools"
DOSFSTOOLS_ARCHITECTURE = "arm64"
# The app's tool check (qidiclient 0x7010f0 on Max 4 01.01.07.01): either name, in any of these directories.
FORMAT_TOOLS = ("mkfs.vfat", "mkfs.fat")
TOOL_DIRS = ("/usr/sbin", "/sbin", "/usr/bin", "/bin")
MAX_LINK_HOPS = 40


class BootPathError(InstallerError):
    pass


@dataclass(frozen=True)
class Problem:
    name: str
    repairable: bool
    detail: str


def root_for(paths, environ: dict[str, str]) -> Path | None:
    """The system root to check: a test's stand-in, or / on a printer. None when neither applies (a test without one)."""
    if environ.get(SYSTEM_ROOT_ENV):
        return Path(environ[SYSTEM_ROOT_ENV])
    if paths.printer_data_root == DEFAULT_PRINTER_DATA_ROOT:
        return Path("/")
    return None


def _map(root: Path, absolute: str) -> Path:
    return Path(absolute) if root == Path("/") else root / absolute.lstrip("/")


def resolve(root: Path, absolute: str) -> Path | None:
    """Where an absolute path leads inside root, following links the way the kernel would there; None when it leads
    nowhere."""
    parts = [part for part in PurePosixPath(absolute).parts if part != "/"]
    current = PurePosixPath("/")
    hops = 0
    while parts:
        part = parts.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            current = current.parent
            continue
        candidate = current / part
        mapped = _map(root, str(candidate))
        if mapped.is_symlink():
            hops += 1
            if hops > MAX_LINK_HOPS:
                return None
            target = PurePosixPath(os.readlink(mapped))
            parts = [part for part in (target if target.is_absolute() else current / target).parts if part != "/"] + parts
            current = PurePosixPath("/")
            continue
        if not mapped.exists():
            return None
        current = candidate
    return _map(root, str(current))


def _executable(root: Path, absolute: str) -> bool:
    found = resolve(root, absolute)
    return found is not None and found.is_file() and os.access(found, os.X_OK)


def check(root: Path) -> tuple[Problem, ...]:
    """What keeps the printer from starting, in order; nothing when it can. A root without /usr/sbin isn't a printer's
    (a test's stand-in for something else) and has nothing to check."""
    if not _map(root, "/usr/sbin").is_dir():
        return ()
    problems = []
    for name, target, key in LINKS:
        top = _map(root, f"/{name}")
        if top.is_symlink():
            link = os.readlink(top).rstrip("/")
            if link not in (target, f"/{target}"):
                problems.append(Problem(name, False, f"/{name} points at {link}, not {target}"))
        elif top.is_dir():
            if resolve(root, f"/{name}/{key}") is not None:
                continue  # an unmerged system's real directory
            if resolve(root, f"/{target}/{key}") is not None:
                problems.append(Problem(name, True, f"/{name} is a directory instead of a link to {target}"))
            else:
                problems.append(Problem(name, False, f"/{name} is a directory and /{target} has no {key}"))
        elif not os.path.lexists(top):
            problems.append(Problem(name, _map(root, f"/{target}").is_dir(), f"/{name} is missing"))
        else:
            problems.append(Problem(name, False, f"/{name} is not a directory or a link"))
    if not problems and not _executable(root, INIT):
        problems.append(Problem("init", False, f"{INIT} doesn't lead to a program"))
    return tuple(problems)


def describe(problems) -> str:
    return "; ".join(problem.detail for problem in problems)


def boot_files_allow_reboot(paths, *, reporter, environ: dict[str, str]) -> bool:
    """Whether the printer can start again: never reboot one that would stop at the boot logo."""
    root = root_for(paths, environ)
    if root is None:
        return True
    problems = check(root)
    if not problems:
        return True
    reporter.debug(event="host_reboot.boot_files_broken", detail=describe(problems))
    reporter.line(messages.HOST_REBOOT_REFUSED_BOOT_FILES.format(detail=describe(problems), doc=RECOVERY_DOC_URL))
    return False


def protect(paths, *, reporter, environ: dict[str, str], dry_run: bool, run=subprocess.run, input_stream=None) -> None:
    """Before an install changes anything: refuse a printer whose start files are broken in a way this can't repair,
    put back a link a directory replaced, and install QIDI's dosfstools so its app doesn't break /sbin again."""
    root = root_for(paths, environ)
    if root is None:
        return
    problems = check(root)
    unrepairable = [problem for problem in problems if not problem.repairable]
    if unrepairable:
        reporter.debug(event="install.boot_files.unrepairable", detail=describe(problems))
        raise BootPathError(messages.BOOT_FILES_UNREPAIRABLE.format(detail=describe(unrepairable), doc=RECOVERY_DOC_URL))
    password: list[str] = []

    def sudo_password() -> str:
        if not password:
            password.append(authenticate_sudo(run=run, environ=environ, reporter=reporter, input_stream=input_stream))
        return password[0]

    if dry_run:
        for problem in problems:
            reporter.line(messages.BOOT_FILES_WOULD_REPAIR.format(detail=problem.detail))
    elif problems:
        _repair(root, problems, run=run, sudo_password=sudo_password, reporter=reporter)
    _ensure_format_tools(root, run=run, sudo_password=sudo_password, reporter=reporter, dry_run=dry_run)


def _repair(root: Path, problems, *, run, sudo_password, reporter) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    real = root == Path("/")

    def sudo(command: list[str]) -> None:
        if run_sudo(command, run=run, password=sudo_password(), timeout=120).returncode != 0:
            raise BootPathError(messages.BOOT_FILES_REPAIR_FAILED.format(command=" ".join(command), doc=RECOVERY_DOC_URL))

    for problem in problems:
        name = problem.name
        target = dict((link, to) for link, to, _key in LINKS)[name]
        top = _map(root, f"/{name}")
        moved = None
        copied: list[str] = []
        kept: list[str] = []
        if top.is_dir() and not top.is_symlink():
            # The directory's own files go where the link leads first, without replacing any there, so nothing it
            # held is lost; then it moves aside, out of the way of the link.
            for entry in sorted(os.listdir(top)):
                source = top / entry
                if source.is_dir() and not source.is_symlink():
                    kept.append(entry)
                    continue
                if os.path.lexists(_map(root, f"/{target}/{entry}")):
                    continue
                if real:
                    sudo(["cp", "-a", "-n", "--", f"/{name}/{entry}", f"/{target}/{entry}"])
                else:
                    shutil.copy2(source, _map(root, f"/{target}/{entry}"), follow_symlinks=False)
                copied.append(entry)
            moved = f"/{name}.valve-oil-{stamp}"
            if real:
                sudo(["mv", "-T", f"/{name}", moved])
            else:
                os.rename(top, _map(root, moved))
        temporary = f"/.{name}.valve-oil-{stamp}"
        try:
            if real:
                sudo(["ln", "-s", "-T", target, temporary])
                sudo(["mv", "-T", temporary, f"/{name}"])
                sudo(["sync"])
            else:
                os.symlink(target, _map(root, temporary))
                os.rename(_map(root, temporary), top)
        except (BootPathError, OSError):
            # Better the directory it was than nothing at all.
            if moved is not None:
                if real:
                    run_sudo(["mv", "-T", moved, f"/{name}"], run=run, password=sudo_password(), timeout=120)
                else:
                    os.rename(_map(root, moved), top)
            raise
        reporter.debug(event="install.boot_files.repaired", link=f"/{name}", target=target, moved_to=moved or "",
                       copied=",".join(copied), kept=",".join(kept))
        reporter.line(messages.BOOT_FILES_REPAIRED.format(name=f"/{name}", target=target,
                                                          moved=f" (the directory is now {moved})" if moved else ""))
    remaining = check(root)
    if remaining:
        raise BootPathError(messages.BOOT_FILES_UNREPAIRABLE.format(detail=describe(remaining), doc=RECOVERY_DOC_URL))


def format_tool_present(root: Path) -> bool:
    return any(_executable(root, f"{directory}/{tool}") for directory in TOOL_DIRS for tool in FORMAT_TOOLS)


def dosfstools_registered(root: Path) -> bool:
    """Whether dpkg lists dosfstools as installed: a repair copies the tools into /usr/sbin without dpkg knowing."""
    try:
        status = _map(root, "/var/lib/dpkg/status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for stanza in status.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in stanza.splitlines() if ": " in line and not line.startswith(" "))
        if fields.get("Package") == DOSFSTOOLS_PACKAGE and fields.get("Status") == "install ok installed":
            return True
    return False


def _ensure_format_tools(root: Path, *, run, sudo_password, reporter, dry_run: bool) -> None:
    deb = _map(root, DOSFSTOOLS_DEB)
    if format_tool_present(root) and (dosfstools_registered(root) or not deb.is_file()):
        reporter.debug(event="install.dosfstools", result="already_current")
        return
    if not deb.is_file():
        # This printer's touchscreen app carries no package to unpack, so it has nothing to break /sbin with.
        reporter.debug(event="install.dosfstools", result="no_package", path=DOSFSTOOLS_DEB)
        return
    try:
        fields = run(["dpkg-deb", "-f", str(deb), "Package", "Architecture"], capture_output=True, text=True,
                     timeout=120)
    except (OSError, subprocess.SubprocessError):
        fields = None
    values = {}
    for line in (getattr(fields, "stdout", "") or "").splitlines():
        key, _, value = line.partition(":")
        values[key.strip()] = value.strip()
    if fields is None or fields.returncode or values.get("Package") != DOSFSTOOLS_PACKAGE \
            or values.get("Architecture") != DOSFSTOOLS_ARCHITECTURE:
        reporter.debug(event="install.dosfstools", result="unrecognized_package", fields=values)
        reporter.line(messages.DOSFSTOOLS_NOT_INSTALLED)
        return
    if dry_run:
        reporter.line(messages.DOSFSTOOLS_WOULD_INSTALL)
        return
    command = ["dpkg", "-i", DOSFSTOOLS_DEB] if root == Path("/") else ["dpkg", f"--root={root}", "-i", str(deb)]
    try:
        installed = run_sudo(command, run=run, password=sudo_password(), timeout=300).returncode == 0
    except (OSError, subprocess.SubprocessError, InstallerError):
        installed = False
    if not installed or not format_tool_present(root) or check(root):
        reporter.debug(event="install.dosfstools", result="failed")
        reporter.line(messages.DOSFSTOOLS_NOT_INSTALLED)
        return
    reporter.debug(event="install.dosfstools", result="installed")
    reporter.line(messages.DOSFSTOOLS_INSTALLED)
