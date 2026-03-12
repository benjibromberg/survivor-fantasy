#!/bin/bash
# Run scoring analysis — designed to be queued via pueue on the WSL machine.
# Usage: pueue add --label "scoring-run-N" -- ./run_analysis.sh [extra args]
#
# Examples:
#   ./run_analysis.sh                          # Full run (25k samples, 14 cores)
#   ./run_analysis.sh --quick                  # Quick test run
#   ./run_analysis.sh --samples 50000          # Custom sample count
#   ./run_analysis.sh --seasons 41,42,43       # Specific seasons

set -euo pipefail
cd "$(dirname "$0")"

# Activate venv if present
if [ -f venv/bin/activate ]; then
    source venv/bin/activate
elif [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

exec python analyze_scoring.py \
    --cores 14 \
    --export-json app/static/scoring_analysis.json \
    "$@"
