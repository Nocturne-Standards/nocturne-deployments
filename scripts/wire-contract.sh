#!/usr/bin/env bash
# Thin redirect → private org ops (NOCTURNE_WORKING_ROOT or sibling nocturne-working/ops).
set -euo pipefail
if [ -n "${NOCTURNE_WORKING_ROOT:-}" ]; then
  OPS="$NOCTURNE_WORKING_ROOT/ops"
elif [ -d "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/nocturne-working/ops" ]; then
  OPS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/nocturne-working/ops"
else
  echo "error: private ops checkout not found — set NOCTURNE_WORKING_ROOT" >&2
  exit 1
fi
exec "$OPS/wire-contract.sh" "$@"
