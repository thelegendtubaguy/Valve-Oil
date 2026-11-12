from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .errors import InstallerError


# Fixes for QIDI's compiled Moonraker components, which can't be patched as source. The module is installed in
# Moonraker's virtualenv and a .pth file imports it when Moonraker's Python starts; the module then patches QIDI's
# code as Moonraker loads it. Removing both files restores stock Moonraker.
MODULE_OPERATION = "moonraker_fixes_module"
PATH_OPERATION = "moonraker_fixes_path"
SITE_PACKAGES = "/home/qidi/moonraker-env/lib/python3.9/site-packages"
MODULE_NAME = "tltg_moonraker_fixes"
MODULE_FILE = f"{SITE_PACKAGES}/{MODULE_NAME}.py"
PATH_FILE = f"{SITE_PACKAGES}/tltg-moonraker-fixes.pth"
RESTART_SERVICE = "moonraker.service"
MODULE_PAYLOAD = Path(__file__).resolve().parents[1] / f"system/{MODULE_NAME}.py"
# Package-created files start with a marker line so later package versions replace earlier ones.
MODULE_MARKER = "# tltg-optimized:moonraker-fixes\n"
PATH_MARKER = "# tltg-optimized:moonraker-fixes-path\n"
PATH_CONTENT = PATH_MARKER + f"import {MODULE_NAME}\n"


@lru_cache(maxsize=1)
def module_source() -> str:
    try:
        source = MODULE_PAYLOAD.read_text(encoding="utf-8")
    except OSError as exc:
        raise InstallerError("Moonraker fixes module is missing from the package.") from exc
    if not source.startswith(MODULE_MARKER):
        raise InstallerError("Moonraker fixes module is missing its marker line.")
    compile(source, f"{MODULE_NAME}.py", "exec")
    return source


def _patch_created(current: str, content: str, marker: str, path: str) -> str:
    if current == "" or current.startswith(marker):
        return content
    raise InstallerError(f"Existing {path} is unrecognized and was preserved.")


def patch_module(current: str) -> str:
    return _patch_created(current, module_source(), MODULE_MARKER, MODULE_FILE)


def patch_path_file(current: str) -> str:
    return _patch_created(current, PATH_CONTENT, PATH_MARKER, PATH_FILE)
