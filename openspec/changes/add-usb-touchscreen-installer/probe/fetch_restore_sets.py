"""Fetch QIDI's firmware manifest and board images, not the system package, from each mirrored firmware archive.

Reads only the zip's central directory and the members it needs with HTTP range requests, checks each member's
CRC-32, and refuses member names that aren't plain file names. Output: restore/<printer>/<set>/ with SHA256SUMS.

    python3 -I fetch_restore_sets.py
"""
import hashlib, json, struct, sys, urllib.request, zlib
from pathlib import Path

OWNER = "thelegendtubaguy"
# (printer, set name, repo, tag, archive): the firmware versions installer/package.yaml supports
SETS = [
    ("max4", "01.01.06.03", "Qidi-Max4-Defaults", "qidi-max4-firmware-01.01.06.03", "QD_MAX4_01.01.06.03_20260507_Release.zip"),
    ("max4", "01.01.06.04_20260609", "Qidi-Max4-Defaults", "qidi-max4-firmware-01.01.06.04", "QD_MAX4_01.01.06.04_20260609_Release.zip"),
    ("max4", "01.01.06.04_20260612", "Qidi-Max4-Defaults", "qidi-max4-firmware-01.01.06.04-62357353ac0523f05d4173fefcdd39ae63540cd17047920a68795396ae24b280", "QD_MAX4_01.01.06.04_20260612_Release.zip"),
    ("max4", "01.01.06.05", "Qidi-Max4-Defaults", "qidi-max4-firmware-01.01.06.05-b1826d1aed274c7233b4a23a3a3e5c0b4e9655c5d03188a8b6f3561f0d3f2de7", "QD_MAX4_01.01.06.05_20260804_Release.zip"),
    ("max4", "01.01.07.01", "Qidi-Max4-Defaults", "qidi-max4-firmware-01.01.07.01-0cc97445e77295116adaa665ff463e77608d4929520bd7cd46e732fbb0a6ecf2", "QD_MAX4_01.01.07.01_20261016_Release.zip"),
    ("plus5", "01.01.02.01", "Qidi-Plus5-Defaults", "qidi-plus5-firmware-01.01.02.01-534e5ce959b1e387fc6355f8d78736ddb94432eb9c569ff2a135e103dfeb0871", "QD_PLUS5_01.01.02.01_20261016_Release.zip"),
    ("smart5", "01.01.02.01", "Qidi-Smart5-Defaults", "qidi-smart5-firmware-01.01.02.01-5677f126716aaa0c084c02a787145bc911cd346badd87cca350cecaeebbc041e", "S5_01.01.02.01_20261016_Release.zip"),
]
OUT = Path(__file__).resolve().parent / "restore"


def get(url, start, end):
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent": "valve-oil-usb-probe"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status != 206:
            raise SystemExit(f"{url}: no range support (HTTP {r.status})")
        total = int(r.headers["Content-Range"].rsplit("/", 1)[1])
        return r.read(), total


def members(url):
    tail, total = get(url, 0, 0)
    tail, _ = get(url, max(0, total - 65557), total - 1)
    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise SystemExit(f"{url}: no end of central directory")
    _, _, _, _, count, cd_size, cd_off, _ = struct.unpack_from("<IHHHHIIH", tail, eocd)
    if cd_off == 0xFFFFFFFF or count == 0xFFFF:
        raise SystemExit(f"{url}: zip64 not handled")
    cd, _ = get(url, cd_off, cd_off + cd_size - 1)
    pos, out = 0, []
    for _ in range(count):
        (sig, _, _, flags, method, _, _, crc, csize, usize, nlen, xlen, clen, _, _, _, loff) = struct.unpack_from(
            "<IHHHHHHIIIHHHHHII", cd, pos)
        if sig != 0x02014B50:
            raise SystemExit(f"{url}: bad central directory")
        name = cd[pos + 46:pos + 46 + nlen].decode("utf-8")
        out.append(dict(name=name, method=method, crc=crc, csize=csize, usize=usize, loff=loff, flags=flags))
        pos += 46 + nlen + xlen + clen
    return out


def read_member(url, m):
    head, _ = get(url, m["loff"], m["loff"] + 29)
    nlen, xlen = struct.unpack_from("<HH", head, 26)
    start = m["loff"] + 30 + nlen + xlen
    data, _ = get(url, start, start + m["csize"] - 1)
    if m["method"] == 8:
        data = zlib.decompress(data, -15)
    elif m["method"] != 0:
        raise SystemExit(f"{m['name']}: compression {m['method']} not handled")
    if len(data) != m["usize"] or (zlib.crc32(data) & 0xFFFFFFFF) != m["crc"]:
        raise SystemExit(f"{m['name']}: size or CRC mismatch")
    return data


def plain(name):
    return name and "/" not in name and "\\" not in name and name not in (".", "..") and not name.startswith(".")


for printer, set_name, repo, tag, archive in SETS:
    url = f"https://github.com/{OWNER}/{repo}/releases/download/{tag}/{archive}"
    found = {m["name"]: m for m in members(url)}
    bad = [n for n in found if not plain(n)]
    if bad:
        raise SystemExit(f"{archive}: unexpected member names {bad}")
    manifest = json.loads(read_member(url, found["firmware_manifest.json"]))
    soc_file = manifest.get("SOC", {}).get("file")
    wanted = {"firmware_manifest.json"} | {v["file"] for k, v in manifest.items()
                                           if k != "SOC" and isinstance(v, dict) and v.get("file")}
    missing = sorted(wanted - found.keys())
    if missing:
        raise SystemExit(f"{archive}: manifest names files the zip lacks: {missing}")
    target = OUT / printer / set_name
    target.mkdir(parents=True, exist_ok=True)
    sums = []
    for name in sorted(wanted):
        data = read_member(url, found[name])
        (target / name).write_bytes(data)
        sums.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    (target / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    (target / "SOC_FILE").write_text(f"{soc_file}\n")
    print(f"{printer} {set_name}: {len(wanted)} files, {sum(found[n]['usize'] for n in wanted)} bytes; SOC {manifest['SOC']['version']} {soc_file} not included")
