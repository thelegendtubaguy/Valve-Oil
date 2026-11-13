"""The hourly update check and automatic updates.

Every install schedules the check (tltg-optimized-auto-update.timer and its service), as a guarded host change in
system_optimizations.py, and only uninstall removes it. Each run finds what is waiting for the printer (a newer release,
the release's QIDI firmware update, boards not on their pinned firmware) and publishes it to the status file
(update_check.py). The enrollment marker in printer_data is the only switch: without it a run only checks and
publishes; with it, a run on an idle printer installs the newer release, or starts the waiting firmware update, or
flashes the boards that need it. Switching writes or removes the marker, without sudo, and takes effect at the next
check.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .box_enablement import (
    SavedVariablePersistenceError,
    maybe_repair_saved_variables,
    verify_pending_saved_variable_expectations,
)
from . import messages, safety
from .errors import (
    ActivePrintError,
    FirmwareDetectionError,
    InstallerError,
    PreflightTargetsError,
    PrinterStateError,
)
from .firmware import detect_firmware, supported_printer
from .fs_atomic import atomic_write_text
from .models import RuntimePaths
from .path_safety import ensure_external_path_has_no_symlink_components
from .postflight import verify_install_postflight
from .process_restart import ProcessRestartError, restart_entries, restart_pending
from .manifest import ManifestValidationError, load_manifest
from .state_file import StateValidationError, load_installed_state
from .naming import BUNDLE_ROOT_NAME, REPOSITORY_URL
from .sudo import run_sudo_ignore_failure as _run_sudo_ignore_failure

DEFAULT_ARCHIVE_URL = f"{REPOSITORY_URL}/releases/latest/download/tltg-optimized-macros.tar.gz"
DEFAULT_CHECKSUM_URL = f"{DEFAULT_ARCHIVE_URL}.sha256"
LOCK_HELD_ENV = "TLTG_OPTIMIZED_INSTALLER_LOCK_HELD"
# Who asked for an install's QIDI firmware update, as its status-file run kind: "automatic", "update_now", or by default
# "install" (firmware_update.py).
KIND_ENV = "TLTG_FIRMWARE_UPDATE_KIND"
STATE_FILE = "config/tltg_optimized_auto_update_state.json"
ENROLLMENT_FILE = ".tltg_optimized_auto_update_enrolled"
SERVICE_NAME = "tltg-optimized-auto-update.service"
TIMER_NAME = "tltg-optimized-auto-update.timer"
SYSTEMD_DIR = Path("/etc/systemd/system")
SYSTEM_ROOT_ENV = "TLTG_OPTIMIZED_SYSTEM_ROOT"
URL_OVERRIDE_ENVIRONMENT = (
    "TLTG_AUTO_UPDATE_ARCHIVE_URL",
    "TLTG_AUTO_UPDATE_CHECKSUM_URL",
    "TLTG_INSTALLER_ARCHIVE_URL",
    "TLTG_INSTALLER_CHECKSUM_URL",
)
# A release bundle is downloaded and unpacked here, in the printer user's home and never /tmp, which QIDI's firmware
# package empties; one directory per run, removed when the run's bundle is in place or the run fails.
STAGING_DIRECTORY = ".cache/tltg-auto-update"
STAGING_PREFIX = "run-"
UrlOpenFn = Callable[..., object]
RunFn = Callable[..., subprocess.CompletedProcess]

# ---- the units --------------------------------------------------------------------------------------------------------
# Host changes every install makes (system_optimizations.GUARDED_FILE_PATCHES), like the status service's. They run the
# installed bundle in the printer user's home, which installs keep current (settle_installed_bundle).
SERVICE_OPERATION = "auto_update_service"
TIMER_OPERATION = "auto_update_timer"
SERVICE_FILE = str(SYSTEMD_DIR / SERVICE_NAME)
TIMER_FILE = str(SYSTEMD_DIR / TIMER_NAME)
INSTALLED_BUNDLE = Path("/home/qidi") / BUNDLE_ROOT_NAME
SERVICE_MARKER = "# tltg-optimized:auto-update-service\n"
TIMER_MARKER = "# tltg-optimized:auto-update-timer\n"
SERVICE_CONTENT = SERVICE_MARKER + f"""\
# Valve Oil's update check: shows what is waiting, and installs it only when automatic updates are on. Installed by
# Valve Oil.
[Unit]
Description=Valve Oil update check
Wants=network-online.target
After=network-online.target
ConditionPathExists={INSTALLED_BUNDLE}/auto-update.sh

[Service]
Type=oneshot
User=qidi
WorkingDirectory={INSTALLED_BUNDLE}
UnsetEnvironment={' '.join(URL_OVERRIDE_ENVIRONMENT)}
ExecStart={INSTALLED_BUNDLE}/auto-update.sh --run
"""
TIMER_CONTENT = TIMER_MARKER + """\
# Runs Valve Oil's update check 5 minutes after the printer starts or Valve Oil is installed, and then hourly.
# OnActiveSec, not OnBootSec: a timer started later than OnBootSec after boot, with a run recorded from an earlier
# boot, would not run its service again until the next boot. Installed by Valve Oil.
[Unit]
Description=Run Valve Oil's update check hourly

[Timer]
OnActiveSec=5min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
"""
# The units releases before this one wrote, without a marker, only when automatic updates were turned on.
LEGACY_SERVICE = re.compile(
    r"\[Unit\]\nDescription=[^\n]*auto-update\nWants=network-online\.target\nAfter=network-online\.target\n\n"
    r"\[Service\]\nType=oneshot\nUser=\S+\nWorkingDirectory=[^\n]+\n(?:UnsetEnvironment=[^\n]*\n)?"
    r"ExecStart=[^\n]*/auto-update\.sh --run\n"
)
LEGACY_TIMER = re.compile(
    r"\[Unit\]\nDescription=Run [^\n]*auto-update hourly\n\n\[Timer\]\nOnBootSec=\d+min\nOnUnitActiveSec=1h\n"
    r"Persistent=true\n\n\[Install\]\nWantedBy=timers\.target\n"
)


class AutoUpdateError(InstallerError):
    pass


@dataclass(frozen=True)
class AutoUpdateRunResult:
    action: str
    checksum: str | None = None


def patch_service(current: str) -> str:
    return _patch_unit(current, SERVICE_CONTENT, SERVICE_MARKER, LEGACY_SERVICE, SERVICE_FILE)


def patch_timer(current: str) -> str:
    return _patch_unit(current, TIMER_CONTENT, TIMER_MARKER, LEGACY_TIMER, TIMER_FILE)


def _patch_unit(current: str, desired: str, marker: str, legacy: re.Pattern, name: str) -> str:
    if current == "" or current.startswith(marker) or legacy.fullmatch(current):
        return desired
    raise InstallerError(f"Existing {name} is unrecognized and was preserved.")


def _ours(text: str, marker: str, legacy: re.Pattern) -> bool:
    return text.startswith(marker) or legacy.fullmatch(text) is not None


def systemd_dir(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    root = env.get(SYSTEM_ROOT_ENV)
    return Path(root) / "etc/systemd/system" if root else SYSTEMD_DIR


def check_scheduled(environ: dict[str, str] | None = None) -> bool:
    """Whether this package's timer is installed."""
    try:
        return _ours((systemd_dir(environ) / TIMER_NAME).read_text(encoding="utf-8"), TIMER_MARKER, LEGACY_TIMER)
    except OSError:
        return False


def auto_updates_configured(environ: dict[str, str] | None = None) -> bool:
    directory = systemd_dir(environ)
    return (directory / TIMER_NAME).exists() or (directory / SERVICE_NAME).exists()


def adopt_legacy_enrollment(paths: RuntimePaths, environ: dict[str, str] | None = None) -> bool:
    """Before an install replaces them: units an earlier release wrote only when automatic updates were turned on,
    without the enrollment marker it wrote alongside, mean automatic updates are on. Returns True when it enrolled."""
    if auto_update_enrolled(paths):
        return False
    try:
        legacy = LEGACY_TIMER.fullmatch((systemd_dir(environ) / TIMER_NAME).read_text(encoding="utf-8"))
    except OSError:
        return False
    if legacy is None:
        return False
    _write_enrollment(paths)
    return True


def remove_auto_update_units(*, environ: dict[str, str] | None, run: RunFn, password: str | None) -> None:
    """Uninstall: stop and remove the check's units where the install's own restore left them, such as an earlier
    release's units or a run that found nothing installed. Units this package doesn't recognize are left alone."""
    directory = systemd_dir(environ)
    fake_root = directory != SYSTEMD_DIR
    owned = []
    for name, marker, legacy in ((TIMER_NAME, TIMER_MARKER, LEGACY_TIMER), (SERVICE_NAME, SERVICE_MARKER, LEGACY_SERVICE)):
        try:
            if _ours((directory / name).read_text(encoding="utf-8"), marker, legacy):
                owned.append(name)
        except OSError:
            continue
    if not owned:
        return
    if fake_root:
        for name in owned:
            (directory / name).unlink()
        return
    if TIMER_NAME in owned:
        _run_sudo_ignore_failure(["systemctl", "disable", "--now", TIMER_NAME], run=run, password=password or "")
    for name in owned:
        _run_sudo_ignore_failure(["rm", "-f", str(directory / name)], run=run, password=password or "")
    _run_sudo_ignore_failure(["systemctl", "daemon-reload"], run=run, password=password or "")


def settle_installed_bundle(paths: RuntimePaths, environ: dict[str, str]) -> None:
    """On the printer, make the bundle this install runs from the one the check and "update now" run, in the printer
    user's home, when the install was started from anywhere else."""
    if environ.get(SYSTEM_ROOT_ENV) or paths.printer_data_root != INSTALLED_BUNDLE.parent / "printer_data":
        return
    target = paths.printer_data_root.parent / BUNDLE_ROOT_NAME
    try:
        if paths.bundle_root.resolve() == target.resolve():
            return
        from .bootstrap import copy_bundle

        copy_bundle(paths.bundle_root, target, pwd.getpwuid(os.getuid()))
    except (OSError, KeyError):
        pass


# ---- the switch -------------------------------------------------------------------------------------------------------


def maybe_prompt_enable_auto_updates(*, paths: RuntimePaths, reporter, input_stream, environ=None) -> bool:
    """At the end of an interactive install on a printer that isn't enrolled, offer to turn automatic updates on."""
    from .interaction import confirm_yes

    if input_stream is None or auto_update_enrolled(paths):
        return False
    if not confirm_yes(
        reporter=reporter,
        input_stream=input_stream,
        question=messages.AUTO_UPDATE_PROMPT,
        instruction=messages.AUTO_UPDATE_PROMPT_INSTRUCTION,
        cancel_message=messages.AUTO_UPDATE_NOT_ENABLED,
    ):
        return False
    enable_auto_updates(paths=paths, reporter=reporter, environ=environ)
    return True


def enable_auto_updates(*, paths: RuntimePaths, reporter, environ: dict[str, str] | None = None) -> None:
    _write_enrollment(paths)
    for line in auto_update_status_lines(paths, environ):
        reporter.line(line)


def disable_auto_updates(*, paths: RuntimePaths, reporter, environ: dict[str, str] | None = None) -> None:
    _remove_enrollment(paths)
    for line in auto_update_status_lines(paths, environ):
        reporter.line(line)


def auto_update_status_lines(paths: RuntimePaths, environ: dict[str, str] | None = None) -> list[str]:
    """What is true now: whether automatic updates are on, whether the check is scheduled, and what it last found."""
    from . import valve_oil_status

    lines = [messages.AUTO_UPDATE_ON if auto_update_enrolled(paths) else messages.AUTO_UPDATE_OFF]
    if not check_scheduled(environ):
        lines.append(messages.AUTO_UPDATE_NOT_SCHEDULED)
    status = valve_oil_status.load_status(paths)
    if status.check.at:
        lines.append(f"Last update check: {status.check.at}"
                     + (f" (failed: {status.check.message})" if status.check.result == "failed" else "") + ".")
        names = {valve_oil_status.RELEASE: "Valve Oil", valve_oil_status.FIRMWARE: "QIDI firmware"}
        for item in status.pending:
            what = (f"{names[item.kind]} {item.version or ''}".strip() if item.kind in names
                    else f"board firmware for {item.board}")
            if item.reinstall:
                what += ", installed again"
            reasons = "; ".join(blocker.message for blocker in item.blocked_by)
            lines.append(f"Waiting: {what}" + (f" (not yet: {reasons})" if reasons else "") + ".")
        if not status.pending and status.check.result == "ok":
            lines.append("Nothing is waiting.")
    return lines


# ---- the check --------------------------------------------------------------------------------------------------------


def run_auto_update_check(
    *,
    paths: RuntimePaths,
    reporter,
    environ: dict[str, str] | None = None,
    urlopen: UrlOpenFn = urllib.request.urlopen,
    run: RunFn = subprocess.run,
    enrolled: bool | None = None,
    recovery_required: bool = False,
    **seams,
) -> AutoUpdateRunResult:
    """One update check: publish what is waiting, then, on an enrolled idle printer, do it.

    A printer that is not enrolled is only checked: nothing is installed, reconciled, flashed, stopped, or rebooted,
    and its checksum state records the latest release. `recovery_required` publishes and stops. `seams` reach
    update_check (firmware update start and board tools).
    """
    from . import update_check, valve_oil_status

    env = os.environ if environ is None else environ
    enrolled = auto_update_enrolled(paths) if enrolled is None else enrolled
    if not update_check.installed(paths, enrolled=enrolled):
        reporter.line(messages.AUTO_UPDATE_NOT_INSTALLED)
        return AutoUpdateRunResult(action="not-installed")
    if enrolled and not recovery_required:
        _complete_pending_activation(paths, reporter=reporter, urlopen=urlopen)
    try:
        checksum, latest_version = fetch_latest_release(release_checksum_url(env), urlopen=_release_opener(urlopen))
    except AutoUpdateError:
        reporter.line(messages.AUTO_UPDATE_SKIPPED_CHECKSUM_UNAVAILABLE)
        update_check.publish_failed(paths, enrolled=enrolled, message=messages.AUTO_UPDATE_CHECKSUM_FETCH_FAILED)
        return AutoUpdateRunResult(action="skipped-checksum-unavailable")
    state = _read_state(paths)
    try:
        reconciliation_required = _installation_requires_reconciliation(paths)
    except AutoUpdateError:
        update_check.publish_failed(paths, enrolled=enrolled, message=messages.AUTO_UPDATE_STATE_UNREADABLE)
        raise
    found = update_check.discover(
        paths, enrolled=enrolled, latest_version=latest_version, reconciliation_required=reconciliation_required,
        release_changed=state.get("latest_checksum") != checksum, urlopen=urlopen, recovery_required=recovery_required,
        **seams)
    update_check.publish(paths, found, urlopen=urlopen)

    if not enrolled:
        write_state(paths, checksum)
        reporter.line(messages.AUTO_UPDATE_CHECKED_NOT_ENROLLED)
        return AutoUpdateRunResult(action="checked", checksum=checksum)
    if recovery_required:
        return AutoUpdateRunResult(action="recovery-required", checksum=checksum)

    if found.release_waiting:
        if found.printer_blockers:
            return _skipped(reporter, found, checksum)
        # A new release, or the current one installed again: missing checksum state or drifted installation state.
        updating = state.get("latest_checksum") not in (None, checksum) or found.newer_release
        reporter.line(messages.AUTO_UPDATE_AVAILABLE if updating else messages.AUTO_UPDATE_RECONCILING)
        record = valve_oil_status.begin_run(paths, kind="automatic", step="install",
                                            step_label=f"Installing Valve Oil {found.latest_version or ''}".strip())
        try:
            run_latest_installer(
                paths=paths,
                archive_url=release_archive_url(env),
                expected_checksum=checksum,
                urlopen=urlopen,
                run=run,
                environ={**env, KIND_ENV: "automatic"},
            )
        except BaseException as exc:
            message = getattr(exc, "message", None) or messages.AUTO_UPDATE_INSTALLER_FAILED
            valve_oil_status.finish_run(paths, record.id, state=valve_oil_status.FAILED, message=message)
            update_check.mark_failed(paths, valve_oil_status.RELEASE, message)
            raise
        write_state(paths, checksum)
        valve_oil_status.clear_pending(paths, kind=valve_oil_status.RELEASE)
        valve_oil_status.finish_run(paths, record.id, state=valve_oil_status.SUCCEEDED, message=messages.AUTO_UPDATE_COMPLETE)
        reporter.line(messages.AUTO_UPDATE_COMPLETE)
        return AutoUpdateRunResult(action="updated" if updating else "reconciled", checksum=checksum)

    if found.printer_blockers:
        return _skipped(reporter, found, checksum)
    try:
        maybe_repair_saved_variables(paths=paths, reporter=reporter, urlopen=urlopen)
    except SavedVariablePersistenceError:
        reporter.line(messages.AUTO_UPDATE_SKIPPED_UNKNOWN_STATE)
        return AutoUpdateRunResult(action="skipped-unknown-printer-state", checksum=checksum)
    applied = update_check.apply(paths, found, reporter=reporter, environ=dict(env), urlopen=urlopen, run=run, **seams)
    if applied is None:
        reporter.line(messages.AUTO_UPDATE_ALREADY_CURRENT)
        return AutoUpdateRunResult(action="already-current", checksum=checksum)
    return AutoUpdateRunResult(action=applied, checksum=checksum)


def _skipped(reporter, found, checksum: str) -> AutoUpdateRunResult:
    if any(blocker.code in ("printing", "heaters_on") for blocker in found.printer_blockers):
        reporter.line(messages.AUTO_UPDATE_SKIPPED_ACTIVE_PRINT)
        return AutoUpdateRunResult(action="skipped-active-print", checksum=checksum)
    reporter.line(messages.AUTO_UPDATE_SKIPPED_UNKNOWN_STATE)
    return AutoUpdateRunResult(action="skipped-unknown-printer-state", checksum=checksum)


def _complete_pending_activation(paths: RuntimePaths, *, reporter, urlopen: UrlOpenFn) -> None:
    """Enrolled printers only: finish a Klipper restart an earlier install left pending while the printer was busy."""
    try:
        verify_pending_saved_variable_expectations(paths, urlopen=urlopen)
    except SavedVariablePersistenceError as exc:
        raise AutoUpdateError("Pending saved-variable verification could not be verified.") from exc
    if not paths.restart_marker_path.exists():
        return
    try:
        manifest = load_manifest(paths.installer_root / "package.yaml")
        safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen)
        restart_pending(
            paths,
            allowed_entries=restart_entries(manifest),
            urlopen=urlopen,
        )
        verify_pending_saved_variable_expectations(paths, urlopen=urlopen)
        reporter.line("Klipper service process restarted and verified.")
    except (
        ActivePrintError,
        PrinterStateError,
        ProcessRestartError,
        ManifestValidationError,
        SavedVariablePersistenceError,
    ) as exc:
        raise AutoUpdateError("Pending Klipper process activation could not be verified.") from exc


# ---- the latest release -----------------------------------------------------------------------------------------------

# GitHub answers releases/latest/download/<asset> with a redirect to releases/download/v<version>/<asset>.
_RELEASE_TAG = re.compile(r"/releases/download/v?(\d+(?:\.\d+)+)/")


class _RecordedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.urls.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def release_urlopen(url, timeout=20):
    """urlopen that keeps the redirects it followed, which name the latest release's tag."""
    handler = _RecordedRedirects()
    response = urllib.request.build_opener(handler).open(url, timeout=timeout)
    response.tltg_redirects = tuple(handler.urls)
    return response


def _release_opener(urlopen: UrlOpenFn) -> UrlOpenFn:
    return release_urlopen if urlopen is urllib.request.urlopen else urlopen


def fetch_latest_release(checksum_url: str, *, urlopen: UrlOpenFn = release_urlopen) -> tuple[str, str | None]:
    """The latest release's archive checksum, and its version when the download's redirects name it."""
    try:
        with urlopen(checksum_url, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
            geturl = getattr(response, "geturl", None)
            urls = (*getattr(response, "tltg_redirects", ()), geturl() if callable(geturl) else "")
    except (OSError, urllib.error.URLError, UnicodeDecodeError) as exc:
        raise AutoUpdateError(messages.AUTO_UPDATE_CHECKSUM_FETCH_FAILED) from exc
    first = body.strip().split()[0] if body.strip() else ""
    if len(first) != 64 or any(char not in "0123456789abcdefABCDEF" for char in first):
        raise AutoUpdateError(messages.AUTO_UPDATE_CHECKSUM_INVALID)
    version = next((match.group(1) for match in map(_RELEASE_TAG.search, (url or "" for url in urls)) if match), None)
    return first.lower(), version


def fetch_latest_checksum(checksum_url: str, *, urlopen: UrlOpenFn = urllib.request.urlopen) -> str:
    return fetch_latest_release(checksum_url, urlopen=urlopen)[0]


def version_key(version: str | None) -> tuple[int, ...] | None:
    if not version or not re.fullmatch(r"\d+(?:\.\d+)*", version):
        return None
    return tuple(int(part) for part in version.split("."))


def newer_release(latest: str | None, installed: str | None) -> bool:
    latest_key, installed_key = version_key(latest), version_key(installed)
    return latest_key is not None and installed_key is not None and latest_key > installed_key


# ---- installing a release ---------------------------------------------------------------------------------------------


def staging_root(paths: RuntimePaths) -> Path:
    return paths.printer_data_root.parent / STAGING_DIRECTORY


def run_latest_installer(
    *,
    paths: RuntimePaths,
    archive_url: str,
    expected_checksum: str,
    urlopen: UrlOpenFn,
    run: RunFn,
    environ: dict[str, str],
) -> None:
    """Download, verify, and unpack the latest release under the installer's cache in the printer user's home, put it
    in place of the installed bundle, remove the download, and run the bundled installer noninteractively.

    The cache is outside /tmp, which a QIDI firmware update the installer starts empties, and its run directory is gone
    before the installer starts, whether the bundle was put in place or not.
    """
    if paths.bundle_root.name != BUNDLE_ROOT_NAME:
        raise AutoUpdateError(messages.AUTO_UPDATE_INSTALLER_FAILED)
    stage = _new_stage(paths)
    try:
        archive_path = stage / "bundle.tar.gz"
        extract_root = stage / "extract"
        _download_archive(archive_url, archive_path, urlopen=urlopen)
        _verify_archive_checksum(archive_path, expected_checksum)
        _extract_validated_archive(archive_path, extract_root)
        staged_bundle = extract_root / BUNDLE_ROOT_NAME
        if not (staged_bundle / "install.sh").is_file():
            raise AutoUpdateError(messages.AUTO_UPDATE_INSTALLER_FAILED)
        _replace_bundle(staged_bundle, paths.bundle_root, previous=stage / "previous")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    result = run(
        ["/bin/sh", str(paths.bundle_root / "install.sh"), "--yes", "--plain"],
        cwd=str(paths.bundle_root),
        text=True,
        env={**os.environ, **environ, LOCK_HELD_ENV: "1"},
    )
    if result.returncode != 0:
        raise AutoUpdateError(messages.AUTO_UPDATE_INSTALLER_FAILED)


def _new_stage(paths: RuntimePaths) -> Path:
    """A fresh run directory under the cache, after removing what an interrupted run left there."""
    root = staging_root(paths)
    home = paths.printer_data_root.parent
    try:
        ensure_external_path_has_no_symlink_components(root=home, target=root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for leftover in root.iterdir():
            if leftover.name.startswith(STAGING_PREFIX) and not leftover.is_symlink():
                shutil.rmtree(leftover, ignore_errors=True)
        return Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=root))
    except (OSError, InstallerError) as exc:
        raise AutoUpdateError(messages.AUTO_UPDATE_INSTALLER_FAILED) from exc


def _replace_bundle(staged: Path, bundle_root: Path, *, previous: Path) -> None:
    """Put the staged bundle in place of the installed one in one rename where the kernel can swap two directories;
    otherwise move the old one aside first. The old bundle ends up under the run directory, which is removed."""
    from .qidi_package import exchange_paths

    try:
        if bundle_root.exists() and not bundle_root.is_symlink() and exchange_paths(staged, bundle_root):
            return  # the old bundle is at `staged` now
    except OSError:
        pass  # not on the same filesystem
    if bundle_root.exists() or bundle_root.is_symlink():
        shutil.move(str(bundle_root), previous)
    shutil.move(str(staged), bundle_root)


def _download_archive(archive_url: str, archive_path: Path, *, urlopen: UrlOpenFn) -> None:
    try:
        with urlopen(archive_url, timeout=60) as response, archive_path.open("wb") as handle:
            shutil.copyfileobj(response, handle, 1 << 20)
    except (OSError, urllib.error.URLError) as exc:
        raise AutoUpdateError(messages.AUTO_UPDATE_INSTALLER_FETCH_FAILED) from exc


def _verify_archive_checksum(archive_path: Path, expected_checksum: str) -> None:
    digest = hashlib.sha256()
    with archive_path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest().lower() != expected_checksum.lower():
        raise AutoUpdateError(messages.AUTO_UPDATE_CHECKSUM_INVALID)


def _extract_validated_archive(archive_path: Path, extract_root: Path) -> None:
    extract_root.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            _validate_archive_members(members)
            archive.extractall(extract_root, members=members)
    except (tarfile.TarError, OSError) as exc:
        raise AutoUpdateError(messages.AUTO_UPDATE_INSTALLER_FAILED) from exc


def _validate_archive_members(members: list[tarfile.TarInfo]) -> None:
    has_install_sh = False
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise AutoUpdateError(messages.AUTO_UPDATE_INSTALLER_FAILED)
        if not path.parts or path.parts[0] != BUNDLE_ROOT_NAME:
            raise AutoUpdateError(messages.AUTO_UPDATE_INSTALLER_FAILED)
        if not (member.isfile() or member.isdir()):
            raise AutoUpdateError(messages.AUTO_UPDATE_INSTALLER_FAILED)
        if member.name == f"{BUNDLE_ROOT_NAME}/install.sh" and member.isfile():
            has_install_sh = True
    if not has_install_sh:
        raise AutoUpdateError(messages.AUTO_UPDATE_INSTALLER_FAILED)


def release_checksum_url(environ: dict[str, str]) -> str:
    return environ.get("TLTG_AUTO_UPDATE_CHECKSUM_URL") or environ.get(
        "TLTG_INSTALLER_CHECKSUM_URL", DEFAULT_CHECKSUM_URL
    )


def release_archive_url(environ: dict[str, str]) -> str:
    return environ.get("TLTG_AUTO_UPDATE_ARCHIVE_URL") or environ.get(
        "TLTG_INSTALLER_ARCHIVE_URL", DEFAULT_ARCHIVE_URL
    )


def current_user() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return os.environ.get("USER", "qidi")


# ---- state ------------------------------------------------------------------------------------------------------------


def _read_state(paths: RuntimePaths) -> dict[str, str]:
    path = state_path(paths)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_state(paths: RuntimePaths, checksum: str) -> None:
    path = state_path(paths)
    atomic_write_text(
        path,
        json.dumps({"latest_checksum": checksum}, sort_keys=True, indent=2) + "\n",
        mode=0o644,
    )


def remove_state(paths: RuntimePaths) -> None:
    path = state_path(paths)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _write_enrollment(paths: RuntimePaths) -> None:
    atomic_write_text(enrollment_path(paths), "1\n", mode=0o600)


def _remove_enrollment(paths: RuntimePaths) -> None:
    try:
        enrollment_path(paths).unlink()
    except FileNotFoundError:
        return


def auto_update_enrolled(paths: RuntimePaths) -> bool:
    path = enrollment_path(paths)
    return path.is_file() and not path.is_symlink()


def installed_release(paths: RuntimePaths, manifest=None) -> str | None:
    """The release the installed state records, or None when there is none (never installed, or QIDI's firmware
    package deleted the config folder)."""
    try:
        manifest = manifest or load_manifest(paths.installer_root / "package.yaml")
        state_path_ = paths.printer_data_root / manifest.state_file
        if not state_path_.is_file() or state_path_.is_symlink():
            return None
        return load_installed_state(state_path_).package_version
    except (ManifestValidationError, StateValidationError, OSError):
        return None


def _installation_requires_reconciliation(paths: RuntimePaths) -> bool:
    try:
        manifest = load_manifest(paths.installer_root / "package.yaml")
        detected = detect_firmware(paths.firmware_manifest_path)
    except (ManifestValidationError, FirmwareDetectionError) as exc:
        raise AutoUpdateError(messages.AUTO_UPDATE_INSTALLER_FAILED) from exc

    detected_firmware = detected.version
    installed_state_path = paths.printer_data_root / manifest.state_file
    if not installed_state_path.is_file() or installed_state_path.is_symlink():
        return True
    try:
        installed_state = load_installed_state(installed_state_path)
    except StateValidationError:
        return True
    if (
        installed_state.package_id != manifest.package.id
        or installed_state.package_version != manifest.package.version
        or installed_state.runtime_firmware != detected_firmware
        or supported_printer(manifest, detected) is None
    ):
        return True
    try:
        verify_install_postflight(
            paths=paths,
            manifest=manifest,
            detected_firmware=detected_firmware,
        )
    except PreflightTargetsError:
        return True
    return False


def current_auto_update_checksum(paths: RuntimePaths) -> str | None:
    value = _read_state(paths).get("latest_checksum")
    return value if isinstance(value, str) and len(value) == 64 else None


def state_path(paths: RuntimePaths) -> Path:
    return paths.printer_data_root / STATE_FILE


def enrollment_path(paths: RuntimePaths) -> Path:
    return paths.printer_data_root / ENROLLMENT_FILE
