#!/usr/bin/env bash
# Thin redirect → nocturne-working/ops (private operator tooling).
set -euo pipefail
if [ -n "${NOCTURNE_WORKING_ROOT:-}" ]; then
  OPS="$NOCTURNE_WORKING_ROOT/ops"
elif [ -d "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/nocturne-working/ops" ]; then
  OPS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/nocturne-working/ops"
else
  echo "error: set NOCTURNE_WORKING_ROOT to the nocturne-working checkout" >&2
  exit 1
fi
exec "$OPS/wire-contract.sh" "$@"
