#!/usr/bin/env bash
# Call an owner-gated wiring function on an already-deployed contract (e.g.
# compliance-gate's init_identity_contract), encoding args via the target
# contract's own data-driver wasm and recording the call in
# deployments/testnet.json.
#
# JSON arg shapes (confirmed empirically against the actual data-driver wasm,
# not guessed): ContractId is a bare hex string, no "0x" prefix (matches
# rusk-wallet's own contract-id output format exactly, and deployments/testnet.json's
# recorded contract_id). BlsPublicKey is the base58 string form (matches a
# rusk-wallet "Public (Moonlight)" address exactly, e.g. references/testnet-wallet.md's).
#
# Usage:
#   scripts/wire-contract.sh <target-contract-name> <fn-name> <json-args> [options]
#
# Options:
#   --gas-limit <n>    [default: rusk-experiments/gas-profiler's recommended
#                       limit for this (contract, fn-name), if
#                       deployments/gas-limits.json has one measured against
#                       the currently-deployed wasm — see lib.sh's
#                       lookup_gas_limit for the freshness check, falls back
#                       to rusk-wallet's own default otherwise]
#   --gas-price <n>
#   --deposit <n>      LUX to attach to this call via rusk-wallet's own
#                       --deposit flag (e.g. loan-escrow.deposit_milestone,
#                       blind-auction's commit/reveal calls) — passed straight
#                       through, not validated here
#   --address <addr>   [default: references/testnet-wallet.md's funded profile]
#   --force            Re-send even if this exact call already succeeded against
#                       the target's current contract ID
#   --simulate-first    Dry-run the real signed transaction against the node's
#                       `/on/transactions/simulate` (real execute() against
#                       live state, no broadcast) before ever calling the real
#                       preverify/propagate. Routes rusk-wallet through
#                       scripts/sim-proxy.py, a local transparent proxy that
#                       intercepts the preverify POST rusk-wallet already makes
#                       internally, replays those same signed bytes against
#                       simulate, and — if simulate reports an execution error
#                       — returns a synthetic preverify rejection so
#                       rusk-wallet aborts on its own before ever propagating
#                       (zero gas spent). If simulate succeeds, forwards
#                       through to the real preverify/propagate unmodified and
#                       prints the real measured gas-spent. Implies a large
#                       default --gas-limit (500,000,000) unless one is passed
#                       explicitly, since simulate's own gas metering treats
#                       --gas-limit as a hard ceiling — a too-small one
#                       produces an out-of-gas simulate result instead of the
#                       true cost. gas-limit is a ceiling, not a mandatory
#                       spend, so this costs nothing extra; you're still only
#                       charged for gas actually used. Caveat: simulate runs
#                       against chain state *at that moment* — on a congested
#                       network, real inclusion can land much later, so a
#                       passing simulate is a strong signal, not an absolute
#                       guarantee (same caveat any dry-run/estimateGas has).
#   -y, --yes           Skip the confirmation prompt
#
# Requires RUSK_WALLET_PWD to be set and deployments/testnet.json to already
# have a "current" entry (with a data-driver) for <target-contract-name> — run
# deploy-contract.sh first.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  echo "Usage: $0 <target-contract-name> <fn-name> <json-args> [--gas-limit <n>] [--gas-price <n>] [--address <addr>] [--force] [--simulate-first] [-y|--yes]" >&2
  exit 1
}

[ $# -ge 3 ] || usage
TARGET="$1"; FN_NAME="$2"; JSON_ARGS="$3"; shift 3

GAS_LIMIT=""
GAS_PRICE=""
DEPOSIT=""
ADDRESS=""
FORCE=""
ASSUME_YES=""
SIMULATE_FIRST=""

while [ $# -gt 0 ]; do
  case "$1" in
    --gas-limit) GAS_LIMIT="$2"; shift 2 ;;
    --gas-price) GAS_PRICE="$2"; shift 2 ;;
    --deposit) DEPOSIT="$2"; shift 2 ;;
    --address) ADDRESS="$2"; shift 2 ;;
    --force) FORCE="1"; shift ;;
    --simulate-first) SIMULATE_FIRST="1"; shift ;;
    -y|--yes) ASSUME_YES="1"; shift ;;
    *) usage ;;
  esac
done

PIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CALLER_REPO_ROOT="${CALLER_REPO_ROOT:-}"
if [ -z "$CALLER_REPO_ROOT" ]; then
  echo "error: CALLER_REPO_ROOT unset — set it to the product repo root (wrappers do this)" >&2
  exit 1
fi
REPO_ROOT="$CALLER_REPO_ROOT"
DEPLOYMENTS_FILE="${DEPLOYMENTS_FILE:-$PIN_ROOT/testnet.json}"
mkdir -p "$(dirname "$DEPLOYMENTS_FILE")"
[ -f "$DEPLOYMENTS_FILE" ] || { echo "error: $DEPLOYMENTS_FILE not found — deploy contracts first" >&2; exit 1; }

command -v rusk-wallet >/dev/null 2>&1 || { echo "error: rusk-wallet not on PATH" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "error: jq not on PATH" >&2; exit 1; }
command -v node >/dev/null 2>&1 || { echo "error: node not on PATH" >&2; exit 1; }
load_repo_env "$REPO_ROOT"

: "${RUSK_WALLET_PWD:?RUSK_WALLET_PWD must be set — see references/testnet-wallet.md}"

ADDRESS="${ADDRESS:-$DEFAULT_PUBLIC_ADDRESS}"

TARGET_KEY_PATH="[\"$TARGET\"]"
CONTRACT_ID="$(read_field "$DEPLOYMENTS_FILE" "$TARGET_KEY_PATH" contract_id)"
DD_WASM_PATH="$(read_field "$DEPLOYMENTS_FILE" "$TARGET_KEY_PATH" dd_wasm_path)"
[ -n "$CONTRACT_ID" ] || { echo "error: no deployed contract_id recorded for '$TARGET' — run 'scripts/deploy-contract.sh $TARGET' first" >&2; exit 1; }
[ -n "$DD_WASM_PATH" ] || { echo "error: no data-driver wasm recorded for '$TARGET' (its wasm-dd build may have failed at deploy time — check the deploy output/warnings)" >&2; exit 1; }
[ -f "$DD_WASM_PATH" ] || { echo "error: recorded data-driver wasm not found on disk: $DD_WASM_PATH (rebuild via 'make -C $TARGET wasm-dd')" >&2; exit 1; }

CALL_KEY="${TARGET}.${FN_NAME}"
WIRING_KEY_PATH="[\"wiring\",\"$CALL_KEY\"]"

FN_ARGS_HEX="$(node "$SCRIPT_DIR/encode-contract-args.mjs" "$DD_WASM_PATH" "$FN_NAME" "$JSON_ARGS")"
[ -n "$FN_ARGS_HEX" ] || { echo "error: failed to encode args for $FN_NAME" >&2; exit 1; }

PREV_CONTRACT_ID="$(read_field "$DEPLOYMENTS_FILE" "$WIRING_KEY_PATH" target_contract_id)"
PREV_FN_ARGS_HEX="$(read_field "$DEPLOYMENTS_FILE" "$WIRING_KEY_PATH" fn_args_hex)"

if [ -z "$FORCE" ] && [ -n "$PREV_CONTRACT_ID" ] && [ "$CONTRACT_ID" = "$PREV_CONTRACT_ID" ] && [ "$FN_ARGS_HEX" = "$PREV_FN_ARGS_HEX" ]; then
  echo "$CALL_KEY already sent against $TARGET's current contract ID ($CONTRACT_ID) — skipping. Use --force to re-send."
  exit 0
fi

if [ -z "$GAS_LIMIT" ]; then
  if [ -n "$SIMULATE_FIRST" ]; then
    # gas-profiler's numbers are measured under VM::ephemeral(), which
    # (confirmed 2026-07-15) misses the mempool's real gas floor, BLS
    # hard-fork signature version, and real transaction-envelope value
    # transfer (TRANSFER_CONTRACT deposits/releases) — unreliable as an
    # actual ceiling. --simulate-first doesn't need a tight guess: gas-limit
    # is a ceiling, not a mandatory spend, so start generous and let
    # simulate report the true gas-spent.
    GAS_LIMIT=500000000
    echo "Using --simulate-first's default gas-limit ceiling: $GAS_LIMIT (gas-profiler's recommendation is not used in this mode — see option doc comment)"
  else
    TARGET_WASM_SHA256="$(read_field "$DEPLOYMENTS_FILE" "$TARGET_KEY_PATH" wasm_sha256)"
    GAS_LIMIT="$(lookup_gas_limit "$TARGET" "$FN_NAME" "$TARGET_WASM_SHA256")"
    [ -n "$GAS_LIMIT" ] && echo "Using gas-profiler's recommended gas-limit for ${TARGET}.${FN_NAME}: $GAS_LIMIT"
  fi
fi

CALL_ARGS=(--network testnet contract-call --address "$ADDRESS" --contract-id "$CONTRACT_ID" --fn-name "$FN_NAME" --fn-args "$FN_ARGS_HEX")
[ -n "$GAS_LIMIT" ] && CALL_ARGS+=(--gas-limit "$GAS_LIMIT")
[ -n "$GAS_PRICE" ] && CALL_ARGS+=(--gas-price "$GAS_PRICE")
if [ -n "$DEPOSIT" ]; then
  # This script's own --deposit is documented (and gas-limits.json's notes
  # assume) LUX, matching every other on-chain amount in this repo — but
  # rusk-wallet's own --deposit flag is denominated in DUSK (confirmed via
  # `rusk-wallet contract-call --help`: "Amount of DUSK to deposit"), 1e9
  # LUX apart. Passing LUX straight through (as this did until 2026-07-15)
  # sends a billion-fold-too-large deposit, which the contract correctly
  # rejects: "The value to deposit doesn't match the value in the
  # transaction" — caught live testing loan-escrow.deposit_milestone.
  DEPOSIT_DUSK="$(awk -v lux="$DEPOSIT" 'BEGIN { printf "%.9f", lux / 1000000000 }')"
  CALL_ARGS+=(--deposit "$DEPOSIT_DUSK")
fi

echo ""
echo "About to call on testnet:"
echo "  target:     $TARGET"
echo "  contract:   $CONTRACT_ID"
echo "  function:   $FN_NAME"
echo "  args:       $JSON_ARGS"
echo "  fn-args:    $FN_ARGS_HEX"
echo "  address:    $ADDRESS"
[ -n "$DEPOSIT" ] && echo "  deposit:    $DEPOSIT LUX"
[ -n "$SIMULATE_FIRST" ] && echo "  mode:       --simulate-first (dry-run against live state before any real send)"
echo ""

if [ -z "$ASSUME_YES" ]; then
  read -r -p "Proceed? [y/N] " REPLY
  case "$REPLY" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

STDOUT_FILE="$(mktemp)"
CLEANUP_FILES=("$STDOUT_FILE")
PROXY_PID=""
trap 'rm -f "${CLEANUP_FILES[@]}"; [ -n "$PROXY_PID" ] && kill "$PROXY_PID" 2>/dev/null; true' EXIT

if [ -n "$SIMULATE_FIRST" ]; then
  command -v python3 >/dev/null 2>&1 || { echo "error: python3 not on PATH — required for --simulate-first's local proxy (scripts/sim-proxy.py)" >&2; exit 1; }
  command -v lsof >/dev/null 2>&1 || { echo "error: lsof not on PATH — required for --simulate-first's port selection" >&2; exit 1; }
  # Fixed testnet endpoint per CLAUDE.md's "always use testnet, never
  # mainnet" rule — rusk-wallet --network testnet's own default.
  UPSTREAM_URL="https://testnet.nodes.dusk.network"
  PROXY_PORT=8899
  while lsof -i ":$PROXY_PORT" >/dev/null 2>&1; do
    PROXY_PORT=$((PROXY_PORT + 1))
  done
  PROXY_LOG="$(mktemp)"
  PROXY_RESULT="$(mktemp)"
  CLEANUP_FILES+=("$PROXY_LOG" "$PROXY_RESULT")

  python3 "$SCRIPT_DIR/sim-proxy.py" "$UPSTREAM_URL" "$PROXY_PORT" "$PROXY_RESULT" > "$PROXY_LOG" 2>&1 &
  PROXY_PID=$!

  for _ in $(seq 1 30); do
    grep -q "listening on" "$PROXY_LOG" 2>/dev/null && break
    sleep 0.2
  done
  grep -q "listening on" "$PROXY_LOG" 2>/dev/null || { echo "error: sim-proxy.py failed to start — see $PROXY_LOG" >&2; cat "$PROXY_LOG" >&2; exit 1; }

  CALL_ARGS=(--state "http://127.0.0.1:$PROXY_PORT" --allow-insecure "${CALL_ARGS[@]}")
fi

rusk-wallet "${CALL_ARGS[@]}" | tee "$STDOUT_FILE"
RUSK_WALLET_STATUS="${PIPESTATUS[0]}"

if [ -n "$SIMULATE_FIRST" ] && [ -s "$PROXY_RESULT" ]; then
  echo ""
  echo "simulate result: $(cat "$PROXY_RESULT")"
fi

[ "$RUSK_WALLET_STATUS" -eq 0 ] || { echo "error: rusk-wallet exited $RUSK_WALLET_STATUS — see output above (e.g. insufficient gas, preverify rejection, or --simulate-first's simulate result above)" >&2; exit 1; }

# rusk-wallet's real tx id is a hex string; a failure that still exits 0
# (or whose last printed line isn't the tx id — e.g. an ANSI cursor-show
# escape sequence trailing an error message) must not be mistaken for
# success. Confirmed 2026-07-15: a "Not enough gas"/mempool-preverify
# failure left `tail -n 1` grabbing a `\x1b[?25h` control sequence, which
# is non-empty and passed the old check, silently recording a failed call
# as wired.
TX_ID="$(tail -n 1 "$STDOUT_FILE")"
case "$TX_ID" in
  [0-9a-fA-F]*[0-9a-fA-F])
    if [ "${#TX_ID}" -lt 32 ]; then
      echo "error: last line of rusk-wallet output doesn't look like a real tx id: '$TX_ID' — see full output above" >&2
      exit 1
    fi
    ;;
  *)
    echo "error: last line of rusk-wallet output doesn't look like a real tx id: '$TX_ID' — see full output above" >&2
    exit 1
    ;;
esac

ENTRY_JSON="$(jq -n \
  --arg target_contract_id "$CONTRACT_ID" \
  --arg fn_name "$FN_NAME" \
  --arg args "$JSON_ARGS" \
  --arg fn_args_hex "$FN_ARGS_HEX" \
  --arg tx "$TX_ID" \
  --arg address "$ADDRESS" \
  --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{target_contract_id: $target_contract_id, fn_name: $fn_name, args: $args, fn_args_hex: $fn_args_hex, tx_id: $tx, address: $address, called_at: $ts}')"

record_deployment "$WIRING_KEY_PATH" "$ENTRY_JSON"

echo ""
echo "Called $CALL_KEY -> tx $TX_ID (recorded in deployments/testnet.json)"
