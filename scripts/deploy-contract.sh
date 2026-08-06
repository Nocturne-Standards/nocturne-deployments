#!/usr/bin/env bash
# Thin redirect → nocturne-working/ops (private operator tooling).
set -euo pipefail
SME_OR_CALLER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Prefer explicit ops root; else sibling nocturne-working/ops
if [ -n "${NOCTURNE_WORKING_ROOT:-}" ]; then
  OPS="$NOCTURNE_WORKING_ROOT/ops"
elif [ -d "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/nocturne-working/ops" ]; then
  OPS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/nocturne-working/ops"
else
  echo "error: set NOCTURNE_WORKING_ROOT to the nocturne-working checkout" >&2
  exit 1
fi
export CALLER_REPO_ROOT="${CALLER_REPO_ROOT:-}"
# If caller unset, do not guess — ops script requires it
exec "$OPS/deploy-contract.sh" "$@"
