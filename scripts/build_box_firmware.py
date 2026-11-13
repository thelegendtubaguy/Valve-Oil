#!/usr/bin/env python3
"""Generate the hash-locked QIDI Box firmware (shutdown fix and RFID commands) from a stock Box image."""
from __future__ import annotations

import argparse
import hashlib
import json
import signal
import sys
from contextlib import contextmanager
from pathlib import Path

PATCH_MANIFEST = Path(__file__).resolve().parents[1] / "firmware/max4-box-valve-oil-v1.json"


@contextmanager
def terminal_signals_ignored():
    """A board transfer (Box, mainboard, or THR tool) is not stopped half-written by the terminal closing (SIGHUP) or
    Ctrl-C (SIGINT); the installer also runs the tools in their own session."""
    previous = {number: signal.signal(number, signal.SIG_IGN) for number in (signal.SIGHUP, signal.SIGINT)}
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, signal.SIG_DFL if handler is None else handler)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(path: Path = PATCH_MANIFEST) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def apply_patches(stock: bytes, spec: dict, label: str) -> bytes:
    """Apply a manifest's hash-locked patches to the pinned stock image."""
    if len(stock) != spec["size"] or sha256(stock) != spec["stock_sha256"]:
        raise ValueError(f"Unrecognized stock {label} image; no patch applied")
    patched = bytearray(stock)
    for patch in spec["patches"]:
        offset = int(patch["offset"], 0)
        after = bytes.fromhex(patch["after"])
        current = stock[offset:offset + len(after)]
        if "before" in patch:
            before = bytes.fromhex(patch["before"])
            if len(before) != len(after) or current != before:
                raise ValueError(f"{label} preimage mismatch at {patch['offset']}")
        elif sha256(current) != patch["before_sha256"]:
            raise ValueError(f"{label} preimage mismatch at {patch['offset']}")
        patched[offset:offset + len(after)] = after
    if sha256(patched) != spec["patched_sha256"]:
        raise ValueError(f"Patched {label} checksum mismatch; no image generated")
    return bytes(patched)


def board_spec(manifest: dict) -> dict:
    """The image spec of a THR, Box, or mainboard firmware manifest."""
    for key in ("thr", "box", "mcu"):
        if key in manifest:
            return manifest[key]
    raise ValueError("Not a board firmware manifest")


PREDECESSOR = "predecessor"


def known_images(spec: dict) -> dict[str, dict]:
    """Every application a board may hold that the manifest admits, by SHA-256: {"identity", "size", "version"}.

    "stock" is QIDI's image the build is made from and "patched" this project's build; "superseded" entries are earlier
    builds of this project, made from the same stock image. "predecessors" are QIDI's images of an earlier board version,
    as an earlier QIDI package carries them: admitted as what a board runs, never programmed, and replaced by the pinned
    image only once the printer runs the target firmware, whose package carries the stock version.
    """
    known = {entry["sha256"]: {"identity": PREDECESSOR, "size": entry["size"], "version": entry["version"]}
             for entry in spec.get("predecessors", ())}
    known.update({entry["sha256"]: {"identity": "superseded", "size": spec["size"], "version": entry["version"]}
                  for entry in spec.get("superseded", ())})
    for build in ("stock", "patched"):
        known[spec[build + "_sha256"]] = {"identity": build, "size": spec["size"], "version": spec[build + "_version"]}
    return known


def predecessor_versions(spec: dict) -> set[str]:
    return {entry["version"] for entry in spec.get("predecessors", ())}


def predecessor_hashes(spec: dict) -> set[str]:
    return {entry["sha256"] for entry in spec.get("predecessors", ())}


def pinned_image(manifest: dict, firmware: str) -> dict:
    """The application a board must end on when its printer's target QIDI firmware is `firmware`.

    The manifest's `targets` names, per target firmware, "patched" (this project's build) or "stock" (QIDI's image the
    build is made from). Returns that name as `build` with the image's `version`, `size`, and `sha256`.
    """
    build = manifest.get("targets", {}).get(firmware)
    if build not in ("patched", "stock"):
        raise ValueError(f"No image is pinned for QIDI firmware {firmware}")
    spec = board_spec(manifest)
    return {"build": build, "version": spec[build + "_version"], "size": spec["size"], "sha256": spec[build + "_sha256"]}


def programmable_images(manifest: dict, firmware: str) -> frozenset:
    """SHA-256 of the images a transfer may program for target firmware `firmware`: the pinned image and QIDI's stock
    image it is made from, for restore and recovery. A predecessor image is never programmable."""
    return frozenset({pinned_image(manifest, firmware)["sha256"], board_spec(manifest)["stock_sha256"]})


def patch_box(stock: bytes, manifest: dict) -> bytes:
    return apply_patches(stock, manifest["box"], "Box")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-image", type=Path, required=True, help="QD_MAX4_BOX_02.03.01.21 application image")
    parser.add_argument("--output", type=Path, required=True, help="new file; must not exist")
    args = parser.parse_args()
    try:
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("Output already exists; choose a new destination")
        manifest = load_manifest()
        patched = patch_box(args.stock_image.read_bytes(), manifest)
        with args.output.open("xb") as handle:
            handle.write(patched)
    except (OSError, ValueError) as error:
        print(f"Box firmware generation failed: {error}", file=sys.stderr)
        return 1
    print(f"Generated {args.output} ({manifest['box']['patched_version']}, SHA-256 {manifest['box']['patched_sha256']}). Nothing was flashed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
