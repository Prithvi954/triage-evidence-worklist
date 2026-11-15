#!/usr/bin/env bash
# Start the synthetic demo. Usage:
#   ./scripts/run_demo.sh            # replay mode (no network, no credentials)
#   ./scripts/run_demo.sh live       # live synthetic mode (needs ANTHROPIC_API_KEY in your environment)
#   ./scripts/run_demo.sh reset      # delete the demo database, then start in replay mode
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-replay}"
if [ "$MODE" = "reset" ]; then
  rm -f data/demo.db data/demo.db-wal data/demo.db-shm
  MODE=replay
fi
if [ "$MODE" = "live" ] && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "Set ANTHROPIC_API_KEY in your shell first (never commit it). Falling back is not automatic." >&2
  exit 1
fi
unset ANTHROPIC_LOG   # never run with SDK debug logging (it can log request bodies)
TRIAGE_MODE="$MODE" TRIAGE_DEMO_CONTROLS=1 exec .venv/bin/python -m app.server
