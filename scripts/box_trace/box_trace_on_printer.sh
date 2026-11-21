#!/bin/bash
# Install, check, or remove the development Box tracer from a shell on the printer itself.
# For printers reached with a password rather than SSH keys, or without the
# tltg-optimized-macros include, such as a QIDI Plus 4.
#
#   bash box_trace_on_printer.sh install   # copy the module, add its include, restart Klipper
#   bash box_trace_on_printer.sh status    # is the tracer running?
#   bash box_trace_on_printer.sh remove    # take it all out again, restart Klipper
#
# install keeps a copy of printer.cfg as printer.cfg.before-box-trace. The tracer only observes;
# see README.md. Both install and remove restart Klipper, so X/Y/Z end up unhomed, and both
# refuse while a print is running or paused.
set -eu

action=${1:?usage: box_trace_on_printer.sh install|status|remove}
here=$(cd "$(dirname "$0")" && pwd)
module=$HOME/klipper/klippy/extras/dev_box_trace.py
config_dir=$HOME/printer_data/config
cfg=$config_dir/dev_box_trace.cfg
printer_cfg=$config_dir/printer.cfg
log=$HOME/printer_data/logs/dev_box_trace.log
include='[include dev_box_trace.cfg]'
source_url=${BOX_TRACE_SOURCE:-https://raw.githubusercontent.com/thelegendtubaguy/valve-oil/dev/scripts/box_trace/dev_box_trace.py}
url=http://127.0.0.1:7125

query() {
  curl -s -m 5 "$url$1" | python3 -c "import json,sys
try:
    print($2)
except Exception:
    print('unknown')" 2>/dev/null || echo unknown
}

require_idle() {
  busy=$(query '/printer/objects/query?print_stats=state' 'json.load(sys.stdin)["result"]["status"]["print_stats"]["state"]')
  case $busy in
    printing|paused) echo "The printer is $busy; not restarting Klipper." >&2; exit 1 ;;
  esac
}

restart_klipper() {
  curl -s -m 10 -X POST "$url/machine/services/restart?service=klipper" >/dev/null
  sleep 5
  tries=0
  restarted_firmware=0
  while :; do
    state=$(query /printer/info 'json.load(sys.stdin)["result"]["state"]')
    case $state in
      ready) return 0 ;;
      error|shutdown)
        if [ $restarted_firmware = 0 ]; then
          restarted_firmware=1
          curl -s -m 10 -X POST "$url/printer/firmware_restart" >/dev/null
        fi ;;
    esac
    tries=$((tries + 1))
    if [ $tries -gt 60 ]; then echo "Klipper is $state after restarting; see ~/printer_data/logs/klippy.log." >&2; exit 1; fi
    sleep 2
  done
}

case $action in
  install)
    require_idle
    if [ -f "$here/dev_box_trace.py" ]; then
      cp "$here/dev_box_trace.py" "$module"
    else
      curl -fsSL -m 30 -o "$module" "$source_url"
    fi
    printf '# Development Box tracer; remove with box_trace_on_printer.sh remove.\n[dev_box_trace]\n' > "$cfg"
    if ! grep -qxF "$include" "$printer_cfg"; then
      cp -p "$printer_cfg" "$printer_cfg.before-box-trace"
      sed -i "1i $include" "$printer_cfg"
    fi
    restart_klipper
    sleep 2
    if tail -n 400 "$log" 2>/dev/null | grep -q 'motors sampled'; then
      echo "Box tracer running; it logs to ~/printer_data/logs/dev_box_trace.log."
    else
      echo "Klipper is ready but the tracer did not start; check ~/printer_data/logs/klippy.log for dev_box_trace." >&2
      exit 1
    fi ;;
  status)
    if [ -f "$module" ] && grep -qxF "$include" "$printer_cfg" && tail -n 400 "$log" 2>/dev/null | grep -q 'motors sampled'; then
      echo "Box tracer installed and logging to ~/printer_data/logs/dev_box_trace.log."
    else
      echo "Box tracer not running."
    fi ;;
  remove)
    require_idle
    sed -i "\\|^\\[include dev_box_trace\\.cfg\\]\$|d" "$printer_cfg"
    rm -f "$module" "$cfg"
    restart_klipper
    echo "Box tracer removed; its log stays at ~/printer_data/logs/dev_box_trace.log."
    echo "The printer.cfg from before the install is kept as printer.cfg.before-box-trace."
    ;;
  *)
    echo "usage: box_trace_on_printer.sh install|status|remove" >&2
    exit 2 ;;
esac
