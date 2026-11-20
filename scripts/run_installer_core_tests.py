#!/usr/bin/env python3
"""Run the G-code path checker and the installer's tests, one test module per worker process."""
from __future__ import annotations

import argparse
import contextlib
import io
import logging
import multiprocessing
import os
import subprocess
import sys
import traceback
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TESTS_ROOT = REPO_ROOT / "installer/tests"


def test_modules() -> list[str]:
    return sorted(
        ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
        for path in TESTS_ROOT.glob("*/test_*.py")
    )


class _CurrentStderr(logging.StreamHandler):
    """Logs to whatever sys.stderr is now, so each test's log lines land in that test's captured output."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):
        pass


def run_module(module: str) -> tuple[str, int, bool, str]:
    """Run one module's tests. What passing tests print or log is dropped; a failing test's is in its report."""
    root = logging.getLogger()
    if not any(isinstance(handler, _CurrentStderr) for handler in root.handlers):
        handler = _CurrentStderr()
        handler.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
        root.addHandler(handler)
    stream = io.StringIO()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName(module)
    except Exception:  # a module that cannot be imported fails the run like a failing test
        return module, 0, False, traceback.format_exc()
    # unittest also copies a failing test's captured output to the real stdout and stderr; the report has it already.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = unittest.TextTestRunner(stream=stream, verbosity=1, buffer=True).run(suite)
    return module, result.testsRun, result.wasSuccessful(), stream.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1),
                        help="worker processes (default: one per CPU, at most 8)")
    args = parser.parse_args(argv)

    path_check = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/check_gcode_paths.py")],
        cwd=REPO_ROOT,
        check=False,
    )
    if path_check.returncode != 0:
        return path_check.returncode

    os.chdir(REPO_ROOT)
    modules = test_modules()
    # Spawned workers exit as any Python process does, so tests' temporary folders, removed at exit, are removed.
    # Forked ones, Linux's default, exit without that.
    with ProcessPoolExecutor(max(1, args.jobs), mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(run_module, modules))

    failed = [result for result in results if not result[2]]
    for module, _, _, output in failed:
        print(f"{module}\n{output}", file=sys.stderr)
    tests = sum(result[1] for result in results)
    if failed:
        print(f"FAILED: {len(failed)} of {len(modules)} test modules ({tests} tests run)", file=sys.stderr)
        return 1
    print(f"OK: {tests} tests in {len(modules)} modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
