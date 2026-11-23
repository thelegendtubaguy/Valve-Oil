#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
if [ ! -f installer/runtime/bootstrap.py ]; then
  cd "$(dirname -- "$(dirname -- "$SCRIPT_DIR")")"
fi
command -v python3 >/dev/null 2>&1 || {
  echo "Missing required tool: python3" >&2
  exit 1
}
# Valve Oil is built for Python 3.9, which every supported printer has; the QIDI Plus 4's 3.7 can't run it.
python3 -I -S -c 'import sys; sys.exit(sys.version_info < (3, 9))' || {
  echo "Valve Oil needs Python 3.9 on the printer; this printer has Python $(python3 -I -S -c 'import platform; print(platform.python_version())'). See https://github.com/thelegendtubaguy/valve-oil#supported-printers" >&2
  exit 1
}
exec python3 -I -S installer/runtime/bootstrap.py install "$@"
