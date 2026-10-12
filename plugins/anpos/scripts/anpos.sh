#!/bin/sh
# ANPOS planner CLI entry point: sh anpos.sh <command> [options]
exec sh "$(dirname -- "$0")/pyrun.sh" scripts/anpos.py "$@"
