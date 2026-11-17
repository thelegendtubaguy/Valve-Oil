"""Build USB probe 2: one drive image for the Max 4, Plus 5, and Smart 5.

Layout (copy everything under out2/ to the root of a FAT32 drive):

    QD_Update/QD_MAX4_ValveOil_USB_Probe2.zip   each printer's touchscreen lists only its own prefix
    QD_Update/QD_PLUS5_ValveOil_USB_Probe2.zip
    QD_Update/S5_ValveOil_USB_Probe2.zip
    valve-oil/payload.bin, payload.sha256       stands in for the installer, stored once

Each zip holds a manifest whose only entry is SOC version 99.99.99.99 naming a small Debian package, that package,
and valve-oil-restore/: QIDI's manifest and board images for each firmware the printer supports (fetch_restore_sets.py).
The package installs no files. Its postinst puts QIDI's update folder back from the set matching the installed system
package, before anything else, then times a copy of the payload from the drive and writes a report to
~/printer_data/logs/valve-oil-usb-probe.log.
"""
import hashlib, io, json, os, shutil, tarfile, time, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEB = "valve-oil-usb-probe_0.2_all.deb"
SOC_VERSION = "99.99.99.99"
PRINTERS = {"max4": "QD_MAX4_", "plus5": "QD_PLUS5_", "smart5": "S5_"}
PAYLOAD_MB = 25

CONTROL = """Package: valve-oil-usb-probe
Version: 0.2
Architecture: all
Maintainer: Valve Oil
Description: Valve Oil USB probe 2. Restores QIDI's update folder and writes a report; installs no files.
"""
POSTINST = r"""#!/bin/sh
# Valve Oil USB probe 2. First put QIDI's update folder back from the set this zip carried, then report.
U=/home/qidi/update
R=$U/valve-oil-restore
USB=/home/qidi/printer_data/gcodes/USB
OUT=/home/qidi/printer_data/logs/valve-oil-usb-probe.log
{
  echo "== valve-oil-usb-probe 0.2 $(date '+%Y-%m-%d %H:%M:%S') args: $*"
  PRINTER=$(cat "$R/printer" 2>/dev/null)
  case "$PRINTER" in
    max4) SYS=qd-max4-system ;;
    plus5) SYS=qd-plus5-system ;;
    smart5) SYS=s5-system ;;
    *) SYS=unknown ;;
  esac
  VER=$(dpkg-query -W -f='${Version}' "$SYS" 2>/dev/null)
  echo "-- printer: $PRINTER, system package: $SYS $VER"
  echo "-- /home/qidi/update as the update left it"; ls -la "$U"
  SET=; N=0
  for d in "$R/$VER" "$R/$VER"_*; do
    if [ -n "$VER" ] && [ -d "$d" ]; then SET=$d; N=$((N + 1)); fi
  done
  [ "$N" -gt 1 ] && echo "-- $N sets match $VER; using the last, $(basename "$SET")"
  if [ -n "$SET" ] && (cd "$SET" && sha256sum -c --quiet SHA256SUMS); then
    for f in "$U"/*; do
      case "$(basename "$f")" in qidi.deb|valve-oil-restore) ;; *) rm -rf "$f" ;; esac
    done
    for f in "$SET"/*; do
      case "$(basename "$f")" in SHA256SUMS|SOC_FILE) ;; *) cp "$f" "$U/" && chown root:root "$U/$(basename "$f")" && chmod 644 "$U/$(basename "$f")" ;; esac
    done
    if (cd "$U" && sha256sum -c --quiet "$SET/SHA256SUMS"); then
      echo "-- RESTORED QIDI's update folder from set $(basename "$SET") (QIDI's system package file $(cat "$SET/SOC_FILE") is not on the drive)"
    else
      echo "-- RESTORE CHECK FAILED"
    fi
    rm -rf "$R"
  else
    echo "-- NO RESTORE: no verified set for '$PRINTER' '$VER'; /home/qidi/update was NOT restored"
  fi
  echo "-- /home/qidi/update now"; ls -la "$U"
  if [ -d /home/qidi/update.bak ]; then
    echo "-- difference from update.bak (the system package file and qidi.deb are expected)"
    diff -rq "$U" /home/qidi/update.bak 2>&1
  fi
  echo "-- id"; id
  echo "-- process chain"
  pid=$$
  while [ "$pid" -gt 1 ] 2>/dev/null; do
    echo "$pid $(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null)"
    pid=$(awk '{print $4}' /proc/$pid/stat 2>/dev/null)
  done
  echo "-- services"
  for unit in klipper moonraker qidi-client; do echo "$unit: $(systemctl is-active $unit 2>&1)"; done
  echo "-- system packages"; dpkg-query -W -f='${Package} ${Version} ${Status}\n' qd-max4-system qd-plus5-system s5-system 2>&1
  echo "-- shared folder on the drive"; ls -la "$USB/valve-oil" 2>&1
  if [ -f "$USB/valve-oil/payload.bin" ]; then
    T0=$(date +%s.%N)
    cp "$USB/valve-oil/payload.bin" /home/qidi/.valve-oil-usb-probe-payload
    T1=$(date +%s.%N)
    echo "copy took $(echo "$T1 $T0" | awk '{printf "%.2f", $1 - $2}') s"
    echo "expected $(cat "$USB/valve-oil/payload.sha256" 2>&1)"
    echo "got      $(sha256sum /home/qidi/.valve-oil-usb-probe-payload | cut -d' ' -f1)"
    rm -f /home/qidi/.valve-oil-usb-probe-payload
  fi
  echo "-- USB mount"; mount | grep -e "$USB" 2>&1
  echo "-- display"; cat /sys/class/graphics/fb0/virtual_size /sys/class/graphics/fb0/bits_per_pixel 2>&1
  echo "-- Valve Oil"; grep -h package_version /home/qidi/printer_data/config/tltg-optimized-macros/globals.cfg 2>&1 | head -1
  echo "-- qidi-client log (last 80 lines)"; tail -n 80 "$(ls -t /root/qidi-client/qidi-client_*.log 2>/dev/null | head -1)" 2>&1
} > "$OUT" 2>&1
chown qidi:qidi "$OUT" 2>/dev/null
exit 0
"""


def tar_gz(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        for name, data, mode in members:
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), mode, int(time.time())
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def ar(members):
    out = b"!<arch>\n"
    for name, data in members:
        out += f"{name:<16}{int(time.time()):<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode() + data
        if len(data) % 2:
            out += b"\n"
    return out


deb = ar([
    ("debian-binary", b"2.0\n"),
    ("control.tar.gz", tar_gz([("./control", CONTROL.encode(), 0o644), ("./postinst", POSTINST.encode(), 0o755)])),
    ("data.tar.gz", tar_gz([])),
])
manifest = json.dumps({"SOC": {"file": DEB, "version": SOC_VERSION, "region": "NA"}}, indent=2) + "\n"

out = HERE / "out2"
if out.exists():
    shutil.rmtree(out)
(out / "QD_Update").mkdir(parents=True)
for printer, prefix in PRINTERS.items():
    sets = sorted(p for p in (HERE / "restore" / printer).iterdir() if p.is_dir())
    target = out / "QD_Update" / f"{prefix}ValveOil_USB_Probe2.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("firmware_manifest.json", manifest)
        z.writestr(DEB, deb)
        z.writestr("valve-oil-restore/printer", printer + "\n")
        for s in sets:
            for f in sorted(s.iterdir()):
                z.write(f, f"valve-oil-restore/{s.name}/{f.name}")
    print(f"{target.name}: {target.stat().st_size} bytes, sets {[s.name for s in sets]}")

payload = os.urandom(PAYLOAD_MB * 1024 * 1024)
(out / "valve-oil").mkdir()
(out / "valve-oil" / "payload.bin").write_bytes(payload)
(out / "valve-oil" / "payload.sha256").write_text(hashlib.sha256(payload).hexdigest() + "\n")
print(f"valve-oil/payload.bin: {PAYLOAD_MB} MiB")
