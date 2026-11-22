#!/usr/bin/env python3
"""Rebuild Klipper's C helper library (c_helper.so) with this package's fixes and check it against the installer.

QIDI's klippy/chelper is upstream Klipper at KLIPPER_COMMIT, and its c_helper.so is that tree built with Debian
bullseye's gcc on Makerbase's build host. The script rebuilds it in an arm64 bullseye container with the printer's
compiler and C library packages, checks that the result is byte-identical to QIDI's library, then applies the patches
in chelper/ and builds the patched library the installer ships. See chelper/README.md.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.runtime import klipper_chelper, klipper_tree

KLIPPER_REPOSITORY = "https://github.com/Klipper3d/klipper"
KLIPPER_COMMIT = "ca815f52c8420225f93f90698768a5c4714654a9"
BUILD_IMAGE = "debian:bullseye@sha256:6f519a81440354a85eb592c5f32109ab80605f6b892455983a6f618bf87fabe9"
# The packages QIDI's printers have installed (dpkg -l on firmware 01.01.07.01 and 01.01.02.01).
SNAPSHOT = "20240201T000000Z"
PACKAGES = (
    "gcc-10=10.2.1-6",
    "gcc=4:10.2.1-1",
    "libc6=2.31-13+deb11u7",
    "libc6-dev=2.31-13+deb11u7",
    "libc-dev-bin=2.31-13+deb11u7",
    "linux-libc-dev=5.10.205-2",
)
PATCH_DIR = REPO_ROOT / "chelper"

BUILD_SCRIPT = r"""
set -eu
rm -f /etc/apt/sources.list.d/*
cat > /etc/apt/sources.list <<EOF
deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/$SNAPSHOT bullseye main
deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/$SNAPSHOT bullseye-security main
EOF
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --allow-downgrades --no-install-recommends \
    $PACKAGES >/dev/null
# The library's debug information records the build directory and the source paths, so they are part of the build.
# Klipper ran from /home/mks/klipper on Makerbase's build host and built with absolute source paths.
mkdir -p /home/mks/klipper/klippy
cd /home/mks/klipper
# klippy/chelper/__init__.py builds these, in this order, with these flags.
SOURCES="pyhelper.c serialqueue.c stepcompress.c itersolve.c trapq.c pollreactor.c msgblock.c trdispatch.c
    kin_cartesian.c kin_corexy.c kin_corexz.c kin_delta.c kin_deltesian.c kin_polar.c kin_rotary_delta.c kin_winch.c
    kin_extruder.c kin_shaper.c kin_idex.c"
build() {
    paths=""
    for source in $SOURCES; do paths="$paths /home/mks/klipper/klippy/chelper/$source"; done
    gcc -Wall -g -O2 -shared -fPIC -flto -fwhole-program -fno-use-linker-plugin -o "$1" $paths
}
cp -R /stock klippy/chelper
build /out/qidi_c_helper.so
rm -rf klippy/chelper
cp -R /patched klippy/chelper
build /out/c_helper.so
# Valve Oil's Klipper tree, built where the installer puts it, with its own source list.
mkdir -p "$TREE_ROOT/klippy"
cd "$TREE_ROOT"
cp -R /tree "$TREE_ROOT/klippy/chelper"
paths=""
for source in $TREE_SOURCES; do paths="$paths $TREE_ROOT/klippy/chelper/$source"; done
gcc -Wall -g -O2 -shared -fPIC -flto -fwhole-program -fno-use-linker-plugin -o /out/tree_c_helper.so $paths
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sources() -> list[str]:
    """klipper/klippy/chelper/__init__.py's SOURCE_FILES, in its build order."""
    module = ast.parse((klipper_tree.TREE_SOURCE / "klippy/chelper/__init__.py").read_text())
    for node in module.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "SOURCE_FILES" for t in node.targets):
            return list(ast.literal_eval(node.value))
    raise SystemExit("klipper/klippy/chelper/__init__.py has no SOURCE_FILES")


def fetch_klipper(destination: Path) -> None:
    subprocess.run(["git", "init", "-q", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "fetch", "-q", "--depth", "1", KLIPPER_REPOSITORY, KLIPPER_COMMIT], check=True)
    subprocess.run(["git", "-C", str(destination), "checkout", "-q", "FETCH_HEAD"], check=True)


def build(output_dir: Path) -> None:
    output_dir.mkdir(parents=True)
    work = output_dir / "work"
    fetch_klipper(work / "stock")
    shutil.copytree(work / "stock", work / "patched", symlinks=True)
    for patch in sorted(PATCH_DIR.glob("*.patch")):
        subprocess.run(["git", "-C", str(work / "patched"), "apply", str(patch.resolve())], check=True)
    subprocess.run(
        [
            "docker", "run", "--rm", "--platform", "linux/arm64",
            "-v", f"{(work / 'stock/klippy/chelper').resolve()}:/stock:ro",
            "-v", f"{(work / 'patched/klippy/chelper').resolve()}:/patched:ro",
            "-v", f"{(klipper_tree.TREE_SOURCE / 'klippy/chelper').resolve()}:/tree:ro",
            "-v", f"{output_dir.resolve()}:/out",
            "-e", f"TREE_ROOT={klipper_tree.TREE_ROOT}",
            "-e", f"TREE_SOURCES={' '.join(tree_sources())}",
            "-e", f"SNAPSHOT={SNAPSHOT}",
            "-e", f"PACKAGES={' '.join(PACKAGES)}",
            BUILD_IMAGE, "bash", "-c", BUILD_SCRIPT,
        ],
        check=True,
    )
    shutil.rmtree(work)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path, help="New directory for the built libraries.")
    parser.add_argument(
        "--install",
        action="store_true",
        help=f"Copy the built libraries into installer/{klipper_chelper.PAYLOAD_SUBDIR} after printing their hashes.",
    )
    args = parser.parse_args(argv)
    if args.output_dir.exists() or args.output_dir.is_symlink():
        parser.error(f"{args.output_dir} already exists")
    if shutil.which("docker") is None:
        parser.error("docker is required")
    if len(klipper_chelper.BUILDS) != 1:
        parser.error("installer/runtime/klipper_chelper.py must list exactly the one QIDI build this script reproduces")
    (stock, reviewed), = klipper_chelper.BUILDS.items()
    build(args.output_dir)

    built_stock = args.output_dir / "qidi_c_helper.so"
    built_patched = args.output_dir / "c_helper.so"
    built_tree = args.output_dir / "tree_c_helper.so"
    actual_stock, actual_patched, actual_tree = sha256(built_stock), sha256(built_patched), sha256(built_tree)
    print(f"qidi_c_helper.so {actual_stock}")
    print(f"c_helper.so {actual_patched}")
    print(f"klipper tree c_helper.so {actual_tree} ({built_tree.stat().st_size} bytes,"
          f" sources {klipper_tree.chelper_sources_sha256()})")
    if actual_stock != stock:
        print("The unpatched build is not QIDI's library; the toolchain or sources differ.", file=sys.stderr)
        return 1
    if args.install:
        payloads = klipper_chelper.PAYLOAD_DIR
        payloads.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(built_stock, payloads / reviewed.stock_payload)
        shutil.copyfile(built_patched, payloads / reviewed.patched_payload)
        shutil.copyfile(built_tree, klipper_tree.CHELPER_PAYLOAD)
        return 0
    status = 0
    if actual_patched != reviewed.patched_sha256:
        print("The patched build differs from installer/runtime/klipper_chelper.py.", file=sys.stderr)
        status = 1
    if actual_tree != klipper_tree.CHELPER_SHA256 \
            or klipper_tree.chelper_sources_sha256() != klipper_tree.CHELPER_SOURCES_SHA256:
        print("The Klipper tree's build differs from installer/runtime/klipper_tree.py.", file=sys.stderr)
        status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
