#!/bin/sh
# Run a plugin Python script with the first Python 3.9+ found (python3, python, py -3).
# Usage: sh pyrun.sh <path relative to plugin root> [args...]
# Works on macOS, Linux and Windows (Git Bash). Exit code and stdin pass through.
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
script="$1"
shift
for candidate in python3 python py; do
  command -v "$candidate" >/dev/null 2>&1 || continue
  if [ "$candidate" = "py" ]; then flag="-3"; else flag=""; fi
  $candidate $flag -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1 || continue
  exec $candidate $flag "$ROOT/$script" "$@"
done
echo "anpos: Python 3.9 or newer was not found (tried python3, python, py -3). Install Python from python.org." >&2
exit 1
