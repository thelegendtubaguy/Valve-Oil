#!/usr/bin/env python3
"""Print the installer/package.yaml pins for an add-on release.

Resolves the add-on's Python requirements to wheels for the printers' Python 3.9 on aarch64, downloads the source
archive and every wheel, and prints the `addons.catalog` entry with each artifact's URL and sha256. Paste the output
over the add-on's entry; the bundle builder downloads and verifies the same files.

    python3 scripts/lock_addon.py octoeverywhere 5.4.1
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

PYTHON_VERSION = "3.9"
# QIDI's Debian 11 has glibc 2.31.
UV_PLATFORM = "aarch64-manylinux_2_28"
PLATFORMS = ("manylinux_2_28_aarch64", "manylinux_2_17_aarch64", "manylinux2014_aarch64", "linux_aarch64")
PIP_VERSION = "25.3"

ADDONS = {
    "octoeverywhere": {
        "source_url": "https://github.com/QuinnDamerell/OctoPrint-OctoEverywhere/archive/refs/tags/{version}.tar.gz",
        "requirements": "requirements.txt",
        # The plugin pip-installs zstandard itself when it can't import it; the add-on environment has no pip, so
        # it ships here. The range is the plugin's own Compression.ZStandardPipPackageString.
        "extra": ["zstandard>=0.21.0,<0.26.0"],
    },
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("addon", choices=sorted(ADDONS))
    parser.add_argument("version")
    args = parser.parse_args(argv)
    spec = ADDONS[args.addon]

    source_url = spec["source_url"].format(version=args.version)
    source = _download(source_url)
    requirements = _member_text(source, spec["requirements"]) + "\n" + "\n".join(spec["extra"]) + "\n"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "requirements.in").write_text(requirements, encoding="utf-8")
        # pip evaluates environment markers against the interpreter running it, so uv resolves for the printers'
        # platform and pip only downloads the exact pins.
        subprocess.run(
            [
                "uv", "pip", "compile", "--quiet", "--no-header", "--no-annotate", "--only-binary", ":all:",
                "--python-version", PYTHON_VERSION, "--python-platform", UV_PLATFORM,
                str(tmp_path / "requirements.in"), "-o", str(tmp_path / "requirements.txt"),
            ],
            check=True,
        )
        wheel_dir = tmp_path / "wheels"
        command = [
            sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check", "--no-deps",
            "--only-binary=:all:", "--python-version", PYTHON_VERSION, "--implementation", "cp", "--abi", "cp39",
            "-r", str(tmp_path / "requirements.txt"), "-d", str(wheel_dir),
        ]
        for platform in PLATFORMS:
            command += ["--platform", platform]
        subprocess.run(command, check=True)
        wheels = sorted(wheel_dir.glob("*.whl"), key=lambda path: path.name.lower())
        pins = [_wheel_pin(path, f"addons/{args.addon}/wheels/{path.name}") for path in wheels]

    print(f"    - id: {args.addon}")
    print(f'      version: "{args.version}"')
    print("      source:")
    print(f"        url: {source_url}")
    print(f"        archive: addons/{args.addon}/{args.addon}-{args.version}.tar.gz")
    print(f"        sha256: {hashlib.sha256(source).hexdigest()}")
    print("      wheels:")
    for pin in pins:
        print(f"        - url: {pin['url']}")
        print(f"          archive: {pin['archive']}")
        print(f"          sha256: {pin['sha256']}")
    pip = _pypi_wheel("pip", PIP_VERSION, "py3-none-any")
    print()
    print("  pip_wheel:")
    print(f"    url: {pip['url']}")
    print(f"    archive: addons/{pip['filename']}")
    print(f"    sha256: {pip['sha256']}")
    return 0


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def _member_text(archive: bytes, name: str) -> str:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name.split("/", 1)[-1] == name:
                return tar.extractfile(member).read().decode("utf-8")
    raise SystemExit(f"{name} not found in the source archive")


def _wheel_pin(path: Path, archive: str) -> dict[str, str]:
    match = re.match(r"^([^-]+)-([^-]+)-", path.name)
    if not match:
        raise SystemExit(f"Unrecognized wheel name {path.name}")
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    release = _pypi_release(match.group(1), match.group(2))
    for item in release["urls"]:
        if item["filename"] == path.name and item["digests"]["sha256"] == sha256:
            return {"url": item["url"], "archive": archive, "sha256": sha256}
    raise SystemExit(f"{path.name} with sha256 {sha256} is not a PyPI file")


def _pypi_wheel(name: str, version: str, tag: str) -> dict[str, str]:
    for item in _pypi_release(name, version)["urls"]:
        if item["filename"].endswith(f"-{tag}.whl"):
            return {"url": item["url"], "filename": item["filename"], "sha256": item["digests"]["sha256"]}
    raise SystemExit(f"No {tag} wheel for {name} {version}")


def _pypi_release(name: str, version: str) -> dict:
    return json.loads(_download(f"https://pypi.org/pypi/{name}/{version}/json"))


if __name__ == "__main__":
    raise SystemExit(main())
