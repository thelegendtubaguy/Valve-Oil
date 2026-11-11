#!/bin/sh
# Installs, fetches, and removes dev_box_trace.py, a log-only tracer for QIDI's Box code, on a printer.
#
#   scripts/box_trace/box_trace.sh install qidi@<printer-ip>
#   scripts/box_trace/box_trace.sh fetch qidi@<printer-ip> [local-file]
#   scripts/box_trace/box_trace.sh remove qidi@<printer-ip>
#
# install and remove refuse while a print is running, then restart the Klipper service, which leaves X/Y/Z unhomed.
# A service restart is needed because RESTART and FIRMWARE_RESTART keep the old module loaded.
# Installing the package removes the tracer's config file, so run install again after it.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
action=${1:?usage: box_trace.sh install|fetch|remove qidi@<printer-ip> [local-file]}
host=${2:?usage: box_trace.sh install|fetch|remove qidi@<printer-ip> [local-file]}
url="http://${host#*@}"
module=klipper/klippy/extras/dev_box_trace.py
cfg=printer_data/config/tltg-optimized-macros/dev_box_trace.cfg
log=printer_data/logs/dev_box_trace.log

state() {
  curl -s -m 5 "$url/printer/info" | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["result"]["state"])
except Exception: print("unreachable")'
}

require_idle() {
  busy=$(curl -s -m 5 "$url/printer/objects/query?print_stats=state" | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["result"]["status"]["print_stats"]["state"])
except Exception: print("unknown")')
  case $busy in
    printing|paused) echo "The printer is $busy; not restarting Klipper." >&2; exit 1 ;;
  esac
}

restart_klipper() {
  curl -s -m 10 -X POST "$url/machine/services/restart?service=klipper" >/dev/null
  sleep 5
  tries=0
  while :; do
    s=$(state)
    case $s in
      ready) return 0 ;;
      error|shutdown)
        # A Klipper that stopped in shutdown keeps its MCUs shut down across a service restart.
        if [ "${firmware_restarted:-0}" = 0 ]; then
          firmware_restarted=1
          curl -s -m 10 -X POST "$url/printer/firmware_restart" >/dev/null
        fi ;;
    esac
    tries=$((tries + 1))
    if [ $tries -gt 60 ]; then echo "Klipper is $s after restarting." >&2; exit 1; fi
    sleep 2
  done
}

case $action in
  install)
    require_idle
    scp -q -o BatchMode=yes "$here/dev_box_trace.py" "$host:$module"
    ssh -o BatchMode=yes "$host" "printf '# Development Box tracer; remove with scripts/box_trace/box_trace.sh remove.\n[dev_box_trace]\n' > $cfg"
    restart_klipper
    sleep 2
    if ssh -o BatchMode=yes "$host" "tail -n 400 $log | grep -q 'motors sampled'"; then
      echo "Box tracer running; it logs to ~/$log."
    else
      echo "Klipper is ready but the tracer did not start; check ~/printer_data/logs/klippy.log for dev_box_trace." >&2
      exit 1
    fi ;;
  fetch)
    dest=${3:-$(cd "$here/../.." && pwd)/tltg-local/box-trace/dev_box_trace-$(date +%Y%m%d-%H%M%S).log}
    mkdir -p "$(dirname "$dest")"
    scp -q -o BatchMode=yes "$host:$log" "$dest"
    echo "$dest" ;;
  remove)
    require_idle
    ssh -o BatchMode=yes "$host" "rm -f $module $cfg"
    restart_klipper
    echo "Box tracer removed; its log stays at ~/$log." ;;
  *)
    echo "usage: box_trace.sh install|fetch|remove qidi@<printer-ip> [local-file]" >&2
    exit 2 ;;
esac
