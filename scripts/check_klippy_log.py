#!/usr/bin/env python3
"""Check a klippy.log someone sent in for Valve Oil's known fault signatures: the check Valve Oil UI's Diagnostics runs
on the printer (installer/runtime/diagnostics.py), without its default size limit.

    python3 scripts/check_klippy_log.py klippy.log [klippy.log.2026-11-21 ...] [--since start|day|all] [--json]
    python3 scripts/check_klippy_log.py valve-oil-support-max4-20261122T101500Z.zip

Logs are given newest first. A support bundle's klippy logs are read straight from the zip into a new temporary folder,
as data only.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from installer.runtime import diagnostics  # noqa: E402

KLIPPY_LOG = "logs/klippy.log"
WHOLE_LOG_MB = 4096


def _from_bundle(bundle: Path, folder: Path) -> list[str]:
    paths = []
    with zipfile.ZipFile(bundle) as archive:
        names = [name for name in archive.namelist() if name == KLIPPY_LOG or name.startswith(KLIPPY_LOG + ".")]
        for name in sorted(names, key=lambda name: (name != KLIPPY_LOG, name), reverse=False):
            target = folder / Path(name).name
            with archive.open(name) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            paths.append(str(target))
    # klippy.log first, then the rotated logs newest first.
    return paths[:1] + sorted(paths[1:], reverse=True)


def main(argv: list[str]) -> int:
    args = [arg for arg in argv if not arg.endswith(".zip")]
    bundles = [Path(arg) for arg in argv if arg.endswith(".zip")]
    if "--budget-mb" not in argv:
        args += ["--budget-mb", str(WHOLE_LOG_MB)]
    if not bundles:
        return diagnostics.main(args)
    folder = Path(tempfile.mkdtemp(prefix="vo-check-klippy-log-"))
    try:
        for bundle in bundles:
            args += _from_bundle(bundle, folder)
        return diagnostics.main(args)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
