#!/usr/bin/env python3
"""Generate a hash-locked Valve Oil THR firmware (FIFO fix) from its patch manifest without flashing hardware."""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

if __package__:
    from .build_box_firmware import apply_patches
else:
    from build_box_firmware import apply_patches

FIRMWARE = Path(__file__).resolve().parents[1] / "firmware"
# One patch manifest per QIDI THR image: the Max 4 and Plus 5 share 02.02.01.08, the Smart 5 runs 02.02.01.09.
PATCH_MANIFESTS = (FIRMWARE / "max4-thr-valve-oil-v1.json", FIRMWARE / "smart5-thr-valve-oil-v1.json")
PATCH_MANIFEST = PATCH_MANIFESTS[0]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def patch_thr(stock: bytes, manifest: dict) -> bytes:
    return apply_patches(stock, manifest["thr"], "THR")


def build(stock_archive: Path, output_dir: Path, manifest_path: Path = PATCH_MANIFEST) -> None:
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError("Output directory already exists; choose a new destination")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Use the same immutable snapshot for admission and ZIP reads.
    archive_bytes = stock_archive.read_bytes()
    if sha256(archive_bytes) != manifest["stock_archive"]["sha256"]:
        raise ValueError("Unrecognized stock archive checksum; no package generated")

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as source:
        patched = patch_thr(source.read(manifest["thr"]["member"]), manifest)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".thr-fifo-", dir=output_dir.parent) as temporary:
            stage = Path(temporary)
            image_path = stage / manifest["outputs"]["thr"]
            image_path.write_bytes(patched)
            archive_path = stage / manifest["outputs"]["archive"]
            members = []
            with zipfile.ZipFile(archive_path, "w") as target:
                target.comment = source.comment
                for info in source.infolist():
                    digest = hashlib.sha256()
                    with target.open(copy.copy(info), "w") as destination:
                        if info.filename == manifest["thr"]["member"]:
                            destination.write(patched)
                            digest.update(patched)
                        else:
                            with source.open(info) as original:
                                while chunk := original.read(1024 * 1024):
                                    destination.write(chunk)
                                    digest.update(chunk)
                    members.append({"name": info.filename, "sha256": digest.hexdigest()})

            # Reopen the written ZIP and check every payload before exposing it.
            with zipfile.ZipFile(archive_path) as result:
                if result.namelist() != source.namelist():
                    raise ValueError("Generated archive member list changed")
                for member in members:
                    with result.open(member["name"]) as handle:
                        if hashlib.file_digest(handle, "sha256").hexdigest() != member["sha256"]:
                            raise ValueError(f"Generated archive payload mismatch: {member['name']}")

            report = {
                "schema_version": 1,
                "patch_id": manifest["patch_id"],
                "patch_manifest_sha256": sha256(manifest_path.read_bytes()),
                "upstream_fix": manifest["upstream_fix"],
                "stock_archive": manifest["stock_archive"],
                "thr": manifest["thr"],
                "members": members,
                "artifacts": [],
            }
            for path in (archive_path, image_path):
                with path.open("rb") as handle:
                    digest = hashlib.file_digest(handle, "sha256").hexdigest()
                report["artifacts"].append({"name": path.name, "sha256": digest})
                path.with_name(path.name + ".sha256").write_text(
                    f"{digest}  {path.name}\n", encoding="utf-8"
                )
            report_path = stage / manifest["outputs"]["report"]
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            report_path.with_name(report_path.name + ".sha256").write_text(
                f"{sha256(report_path.read_bytes())}  {report_path.name}\n", encoding="utf-8"
            )
            # Exclusive directory creation also prevents concurrent builds overwriting artifacts.
            output_dir.mkdir()
            for path in sorted(stage.iterdir()):
                shutil.move(str(path), output_dir / path.name)

    print(f"Generated {output_dir / manifest['outputs']['archive']}")
    print(f"THR SHA-256: {manifest['thr']['patched_sha256']}")
    print(f"Only the THR FIFO fix and the version {manifest['thr']['patched_version']} are applied. Nothing was flashed.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="new directory; must not exist")
    parser.add_argument("--manifest", default=PATCH_MANIFEST.name, choices=[path.name for path in PATCH_MANIFESTS],
                        help="patch manifest in firmware/ of the THR image to patch (default: Max 4 and Plus 5)")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or newer is required")
    try:
        build(args.stock_archive, args.output_dir, FIRMWARE / args.manifest)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"Firmware generation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
