"""Update now: the run the tltg-status service starts when the operator asks Fluidd or Valve Oil UI to apply what is
waiting, whether or not automatic updates are on.

The service starts it detached, in its own session, as
`python3 -I -S ~/tltg-optimized-macros/installer/runtime/bootstrap.py update-now`, and returns at once. Its progress
and result go to the pending-work status file (valve_oil_status), and its output to the service's job log.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import firmware_update, safety, valve_oil_status
from .auto_update import KIND_ENV, release_archive_url, release_checksum_url, run_latest_installer, write_state, fetch_latest_checksum
from .errors import InstallerError
from .locking import acquire
from .models import RuntimePaths
from .reporter import create_reporter

STOPPED = "The update stopped before it finished."
# The tltg-status service's id for this run's job; the status file's run carries it, so the job names its run.
JOB_ENV = "TLTG_STATUS_JOB"


def run_update_now(*, paths: RuntimePaths, reporter, environ: dict[str, str], urlopen=urllib.request.urlopen, run=subprocess.run) -> bool:
    """Apply everything waiting for this printer; True when it finished.

    This is the one seam between the status service and the update run. A QIDI firmware update that is recorded and
    not finished is continued, or followed while it runs, and nothing else is started. Otherwise it installs the newest
    published release, downloaded and checksum-verified exactly as an enrolled automatic update does, under the
    installer lock and only while the printer is idle; that release's installer brings the printer to the release's
    target QIDI firmware when it can update from the installed one (firmware_update.py, run kind "update_now") and
    does the board work installs do.
    """
    job = environ.get(JOB_ENV) or None
    pending = firmware_update.load_record(paths)
    if pending is not None:
        # The firmware update's own run reports progress; this job is linked to it, or to the run it starts.
        record = None
        if job is not None and not valve_oil_status.attach_job(paths, job):
            record = valve_oil_status.begin_run(paths, kind="update_now", step="resume",
                                                step_label="Continuing the firmware update", job=job,
                                                firmware_update=pending.get("id"))
        result = firmware_update.resume_firmware_update(paths=paths, reporter=reporter, environ=environ, run=run,
                                                        urlopen=urlopen)
        if result.state != firmware_update.NOT_NEEDED:
            if record is not None:  # a no-op once the firmware update's run has replaced it
                valve_oil_status.finish_run(paths, record.id, message=result.message, state=(
                    valve_oil_status.SUCCEEDED if result.state == firmware_update.SUCCEEDED
                    else valve_oil_status.FAILED))
            return result.state == firmware_update.SUCCEEDED
        # Another release's record, dropped as nothing of it was installed: go on with the latest release.
        if record is not None:
            valve_oil_status.finish_run(paths, record.id, state=valve_oil_status.SUCCEEDED, message=result.message)
    record = valve_oil_status.begin_run(paths, kind="update_now", step="check", step_label="Checking that the printer is idle",
                                        job=job)
    try:
        with acquire(paths.lock_path):
            safety.ensure_no_recovery_sentinel(paths.recovery_sentinel_path)
            safety.ensure_printer_idle(paths.moonraker_url, urlopen=urlopen)
            valve_oil_status.set_run_step(paths, record.id, step="download", step_label="Downloading the latest Valve Oil release")
            reporter.line("Downloading the latest Valve Oil release...")
            checksum = fetch_latest_checksum(release_checksum_url(environ), urlopen=urlopen)
            valve_oil_status.set_run_step(paths, record.id, step="install", step_label="Installing the latest Valve Oil release")
            reporter.line("Installing the latest Valve Oil release...")
            run_latest_installer(
                paths=paths, archive_url=release_archive_url(environ), expected_checksum=checksum, urlopen=urlopen, run=run,
                environ={**environ, KIND_ENV: "update_now"},
            )
            write_state(paths, checksum)
    except InstallerError as exc:
        reporter.line(exc.message)
        valve_oil_status.finish_run(paths, record.id, state=valve_oil_status.FAILED, message=exc.message)
        return False
    except BaseException:
        valve_oil_status.finish_run(paths, record.id, state=valve_oil_status.FAILED, message=STOPPED)
        raise
    valve_oil_status.clear_pending(paths, kind=valve_oil_status.RELEASE)
    valve_oil_status.finish_run(paths, record.id, state=valve_oil_status.SUCCEEDED, message="The latest Valve Oil release is installed.")
    reporter.line("The latest Valve Oil release is installed.")
    return True


def main(*, argv: list[str], bundle_root: Path) -> int:
    from .cli import resolve_runtime_paths

    parser = argparse.ArgumentParser(prog="update-now", description="Apply the Valve Oil update waiting for this printer.")
    parser.add_argument("--plain", action="store_true", help="Use plain text output.")
    args = parser.parse_args(argv)
    environ = dict(os.environ)
    paths = resolve_runtime_paths(bundle_root=bundle_root, environ=environ)
    reporter = create_reporter(sys.stdout, prefer_plain=args.plain, bundle_root=bundle_root, environ=environ)
    return 0 if run_update_now(paths=paths, reporter=reporter, environ=environ) else 1
