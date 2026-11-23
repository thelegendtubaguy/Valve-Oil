#!/usr/bin/env python3
from __future__ import annotations

import sys
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.runtime.board_images import validate_board_targets
from installer.runtime.compatibility import load_supported_upgrade_sources, validate_manifest_compatibility
from installer.runtime.manifest import load_manifest


README_OFFICIAL = "## Supported printers"
README_COMMUNITY = "### Community-supported printers"


def _readme_table(text: str, start: str, end: str) -> dict[str, str]:
    """A README section's printer table, printer to firmware column; an empty dict for "None yet."."""
    section = text.split(start, 1)[1].split(end, 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ")][1:]  # without the header row
    return {cells[0]: cells[1] for cells in ([cell.strip() for cell in row.strip("|").split("|")] for row in rows)}


def check_readme_printers(manifest, readme: str) -> None:
    """The README lists each printer in its support level's table, with the QIDI firmware it supports."""
    for heading in (README_OFFICIAL, README_COMMUNITY):
        if heading not in readme:
            raise SystemExit(f"README.md is missing its '{heading}' section")
    tables = {
        "official": _readme_table(readme, README_OFFICIAL, README_COMMUNITY),
        "community": _readme_table(readme, README_COMMUNITY, "\n## "),
    }
    for support, listed in tables.items():
        expected = {printer.display_name: ", ".join(f"`{printer.soc_version(key)}`" for key in printer.firmware)
                    for printer in manifest.printers if printer.support == support}
        if listed != expected:
            raise SystemExit(f"README.md's {support} printers {listed} don't match installer/package.yaml: {expected}")


def main() -> int:
    manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
    check_readme_printers(manifest, (REPO_ROOT / "README.md").read_text(encoding="utf-8"))
    compatibility = load_supported_upgrade_sources(
        REPO_ROOT / "installer/supported_upgrade_sources.yaml"
    )
    validate_manifest_compatibility(manifest, compatibility)
    validate_board_targets(manifest, REPO_ROOT)
    for printer in manifest.printers:
        # A firmware update sends the closed-loop driver image only when its size and SHA-256 are pinned.
        target = printer.target_firmware
        if (target.board_version("closed_loop") is None) != (target.closed_loop_image is None):
            raise SystemExit(f"{printer.id}: target firmware {target.version} closed_loop_image doesn't match its "
                             "closed-loop drivers")
    globals_text = (REPO_ROOT / "installer/klipper/macros/globals.cfg").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^variable_package_version:\s*["\']?([^"\'\n]+)["\']?\s*$', globals_text, re.MULTILINE)
    if match is None:
        raise SystemExit("Missing variable_package_version in Valve Oil's globals.cfg")
    if match.group(1) != manifest.package.version:
        raise SystemExit(
            "Optimized globals package version does not match installer/package.yaml: "
            f"{match.group(1)} != {manifest.package.version}"
        )
    print("installer compatibility metadata validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
