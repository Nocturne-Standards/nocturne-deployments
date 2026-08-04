#!/usr/bin/env bash
# Build and deploy a native Dusk contract to testnet via rusk-wallet, recording
# the result (contract + data-driver wasm, versioned) in deployments/testnet.json.
#
# Version-gated by default: the contract's Cargo.toml `version` plus a hash of
# the compiled contract wasm are compared against the last recorded deployment.
#   - same version, same hash  -> already up to date, skip (no gas spent)
#   - same version, diff hash  -> source changed without a version bump, refuse
#                                  (bump Cargo.toml's version, or pass --force)
#   - different version        -> deploy normally
#
# Usage:
#   scripts/deploy-contract.sh <contract-dir> [options]
#
# Options:
#   --init-args <hex>   Hex-encoded rkyv-serialized init function args [default: none]
#   --nonce <u64>        Deploy nonce (contract-id salt) [default: current unix time]
#   --gas-limit <n>       Max gas for the deploy tx [default: rusk-wallet's own default]
#   --gas-price <n>       Gas price in LUX [default: rusk-wallet's own default]
#   --address <addr>      Public/Moonlight address paying gas + becoming contract
#                          owner [default: references/testnet-wallet.md's funded profile]
#   --record-as <key>     Record under this deployments/testnet.json key instead of
#                          the crate directory basename (for product instances of
#                          shared crates, e.g. atlas → prediction-market-atlas)
#   --force               Redeploy even if version/hash unchanged
#   -y, --yes             Skip the confirmation prompt
#
# Requires RUSK_WALLET_PWD to be set (see references/testnet-wallet.md) and a
# funded testnet wallet.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  echo "Usage: $0 <contract-dir> [--init-args <hex>] [--nonce <u64>] [--gas-limit <n>] [--gas-price <n>] [--address <addr>] [--record-as <key>] [--force] [-y|--yes]" >&2
  exit 1
}

[ $# -ge 1 ] || usage
CONTRACT_ARG="$1"; shift

INIT_ARGS=""
NONCE=""
GAS_LIMIT=""
GAS_PRICE=""
ADDRESS=""
RECORD_AS=""
FORCE=""
ASSUME_YES=""

while [ $# -gt 0 ]; do
  case "$1" in
    --init-args) INIT_ARGS="$2"; shift 2 ;;
    --nonce) NONCE="$2"; shift 2 ;;
    --gas-limit) GAS_LIMIT="$2"; shift 2 ;;
    --gas-price) GAS_PRICE="$2"; shift 2 ;;
    --address) ADDRESS="$2"; shift 2 ;;
    --record-as) RECORD_AS="$2"; shift 2 ;;
    --force) FORCE="1"; shift ;;
    -y|--yes) ASSUME_YES="1"; shift ;;
    *) usage ;;
  esac
done

PIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# Wrapper exports CALLER_REPO_ROOT=sme_platform (or knot). Fallback: parent of contract dir workspace is unreliable — require CALLER_REPO_ROOT when not set via env file load from PIN_ROOT only for defaults.
CALLER_REPO_ROOT="${CALLER_REPO_ROOT:-}"
if [ -z "$CALLER_REPO_ROOT" ]; then
  echo "error: CALLER_REPO_ROOT unset — set it to the product repo root (wrappers do this)" >&2
  exit 1
fi
REPO_ROOT="$CALLER_REPO_ROOT"

if [ -d "$CONTRACT_ARG" ]; then
  CONTRACT_DIR="$(cd "$CONTRACT_ARG" && pwd)"
elif [ -d "$REPO_ROOT/$CONTRACT_ARG" ]; then
  CONTRACT_DIR="$(cd "$REPO_ROOT/$CONTRACT_ARG" && pwd)"
else
  echo "error: contract dir not found: $CONTRACT_ARG" >&2
  exit 1
fi

[ -f "$CONTRACT_DIR/Makefile" ] || { echo "error: no Makefile in $CONTRACT_DIR (not a contract crate?)" >&2; exit 1; }
CONTRACT_NAME="$(basename "$CONTRACT_DIR")"
RECORD_KEY="${RECORD_AS:-$CONTRACT_NAME}"

command -v rusk-wallet >/dev/null 2>&1 || { echo "error: rusk-wallet not on PATH (see references/testnet-wallet.md)" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "error: jq not on PATH (run 'mise install')" >&2; exit 1; }
command -v cargo >/dev/null 2>&1 || { echo "error: cargo not on PATH" >&2; exit 1; }
load_repo_env "$REPO_ROOT"
DEPLOYMENTS_FILE="${DEPLOYMENTS_FILE:-$PIN_ROOT/testnet.json}"
mkdir -p "$(dirname "$DEPLOYMENTS_FILE")"
[ -f "$DEPLOYMENTS_FILE" ] || echo '{}' > "$DEPLOYMENTS_FILE"

: "${RUSK_WALLET_PWD:?RUSK_WALLET_PWD must be set — see references/testnet-wallet.md}"

ADDRESS="${ADDRESS:-$DEFAULT_PUBLIC_ADDRESS}"
VERSION="$(crate_version "$CONTRACT_DIR")"
[ -n "$VERSION" ] || { echo "error: could not read crate version from $CONTRACT_DIR/Cargo.toml" >&2; exit 1; }

echo "Building $CONTRACT_NAME WASM (contract)..."
make -C "$CONTRACT_DIR" wasm

WASM_PATH="$(make -C "$CONTRACT_DIR" echo)"
[ -f "$WASM_PATH" ] || { echo "error: build did not produce $WASM_PATH" >&2; exit 1; }

# The data-driver build is best-effort, not required for on-chain deploy: some
# contracts (currently identity-credential — see its README's "make wasm-dd
# doesn't build yet" note) don't have a working data-driver build yet. Don't
# block the actual deploy on that.
DD_WASM_PATH=""
echo "Building $CONTRACT_NAME WASM (data-driver)..."
if make -C "$CONTRACT_DIR" wasm-dd; then
  DD_WASM_PATH="$(make -C "$CONTRACT_DIR" echo-dd)"
  [ -f "$DD_WASM_PATH" ] || { echo "warning: wasm-dd reported success but $DD_WASM_PATH is missing — continuing without a data-driver" >&2; DD_WASM_PATH=""; }
else
  echo "warning: $CONTRACT_NAME's data-driver build failed — deploying the contract anyway, but wire-contract.sh/sync-frontend-contracts.sh won't have a driver for it until this is fixed." >&2
fi

WASM_SHA256="$(sha256_of "$WASM_PATH")"

CONTRACT_KEY_PATH="[\"$RECORD_KEY\"]"
PREV_VERSION="$(read_field "$DEPLOYMENTS_FILE" "$CONTRACT_KEY_PATH" version)"
PREV_SHA256="$(read_field "$DEPLOYMENTS_FILE" "$CONTRACT_KEY_PATH" wasm_sha256)"

if [ -z "$FORCE" ] && [ -n "$PREV_VERSION" ] && [ "$VERSION" = "$PREV_VERSION" ] && [ "$WASM_SHA256" = "$PREV_SHA256" ]; then
  echo "$RECORD_KEY is already deployed at version $VERSION (unchanged wasm) — skipping. Use --force to redeploy anyway."
  exit 0
fi

if [ -z "$FORCE" ] && [ -n "$PREV_VERSION" ] && [ "$VERSION" = "$PREV_VERSION" ] && [ -n "$PREV_SHA256" ] && [ "$WASM_SHA256" != "$PREV_SHA256" ]; then
  echo "error: $RECORD_KEY's version ($VERSION) is unchanged but the compiled wasm changed (source edited without a version bump)." >&2
  echo "       Bump the 'version' field in $CONTRACT_DIR/Cargo.toml before redeploying, or pass --force to deploy anyway." >&2
  exit 1
fi

NONCE="${NONCE:-$(date +%s)}"

DEPLOY_ARGS=(--network testnet contract-deploy --address "$ADDRESS" --code "$WASM_PATH" --deploy-nonce "$NONCE")
[ -n "$INIT_ARGS" ] && DEPLOY_ARGS+=(--init-args "$INIT_ARGS")
[ -n "$GAS_LIMIT" ] && DEPLOY_ARGS+=(--gas-limit "$GAS_LIMIT")
[ -n "$GAS_PRICE" ] && DEPLOY_ARGS+=(--gas-price "$GAS_PRICE")

echo ""
echo "About to deploy to testnet:"
echo "  contract:     $CONTRACT_NAME"
echo "  record-as:    $RECORD_KEY"
echo "  version:      $VERSION"
echo "  wasm:         $WASM_PATH"
echo "  data-driver:  ${DD_WASM_PATH:-<unavailable, see warning above>}"
echo "  address:      $ADDRESS"
echo "  deploy-nonce: $NONCE"
[ -n "$INIT_ARGS" ] && echo "  init-args:    $INIT_ARGS"
[ -n "$GAS_LIMIT" ] && echo "  gas-limit:    $GAS_LIMIT"
[ -n "$GAS_PRICE" ] && echo "  gas-price:    $GAS_PRICE"
echo ""

if [ -z "$ASSUME_YES" ]; then
  read -r -p "Proceed? [y/N] " REPLY
  case "$REPLY" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

STDOUT_FILE="$(mktemp)"
trap 'rm -f "$STDOUT_FILE"' EXIT

rusk-wallet "${DEPLOY_ARGS[@]}" | tee "$STDOUT_FILE"

CONTRACT_ID="$(sed -n '1s/^Deploying //p' "$STDOUT_FILE")"
TX_ID="$(sed -n '2p' "$STDOUT_FILE")"

[ -n "$CONTRACT_ID" ] || { echo "error: could not parse contract id from rusk-wallet output" >&2; exit 1; }

ENTRY_JSON="$(jq -n \
  --arg version "$VERSION" \
  --arg id "$CONTRACT_ID" \
  --arg tx "$TX_ID" \
  --arg wasm "$WASM_PATH" \
  --arg dd_wasm "$DD_WASM_PATH" \
  --arg wasm_sha256 "$WASM_SHA256" \
  --arg nonce "$NONCE" \
  --arg address "$ADDRESS" \
  --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{version: $version, contract_id: $id, tx_id: $tx, wasm_path: $wasm, dd_wasm_path: (if $dd_wasm == "" then null else $dd_wasm end), wasm_sha256: $wasm_sha256, deploy_nonce: ($nonce | tonumber), address: $address, deployed_at: $ts}')"

record_deployment "$CONTRACT_KEY_PATH" "$ENTRY_JSON"

echo ""
echo "Deployed $CONTRACT_NAME v$VERSION -> $CONTRACT_ID (recorded as $RECORD_KEY in deployments/testnet.json)"
