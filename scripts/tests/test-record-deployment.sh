#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$SCRIPT_DIR/../lib.sh"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
primary="$tmp/primary.json"
mirror="$tmp/mirror.json"
echo '{}' > "$primary"
echo '{}' > "$mirror"

export DEPLOYMENTS_FILE="$primary"
unset DEPLOYMENTS_MIRROR_FILE || true
entry='{"version":"1.0.0","contract_id":"abc"}'
# Expect function to exist after implementation
record_deployment '["demo"]' "$entry"
test "$(jq -r '.demo.current.contract_id' "$primary")" = "abc"
test "$(jq -r '.demo.current.contract_id // empty' "$mirror")" = ""

export DEPLOYMENTS_MIRROR_FILE="$mirror"
record_deployment '["demo2"]' '{"version":"2.0.0","contract_id":"def"}'
test "$(jq -r '.demo2.current.contract_id' "$primary")" = "def"
test "$(jq -r '.demo2.current.contract_id' "$mirror")" = "def"

# Mirror missing parent dir must fail
export DEPLOYMENTS_MIRROR_FILE="$tmp/missing/mirror.json"
if record_deployment '["demo3"]' '{"contract_id":"ghi"}'; then
  echo "expected mirror failure" >&2
  exit 1
fi
echo "ok"
