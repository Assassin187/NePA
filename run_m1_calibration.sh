#!/usr/bin/env bash
# Run an M1 calibration command with the API keys currently exported by ~/.bashrc.
#
# Usage:
#   ./run_m1_calibration.sh <s4_prompt_development subcommand and arguments>
#
# bashrc returns immediately in non-interactive shells, so invoke an interactive
# Bash before starting Python.  The resulting NEPA_* variables exist only in the
# calibration child process; this script never prints them.
set -euo pipefail

exec bash -ic 'exec uv run python -m nepa.calibration.s4_prompt_development "$@"' bash "$@"
