# Shared helpers for scripts/*.sh — deployment recording, versioning, hashing.
# Source this, don't execute it directly.

DEFAULT_PUBLIC_ADDRESS="zoMdB5Bs5DGrnsALy2NagpHZi7kgzp1dYCcTS43bjBPC2i8zrDaLjKk6Z4WEMjBcDTFr6sZHuLoHKXehkMwVJMhVMWTdGTyvRf3tU4pfwuuePSkRrBQn75v7Lvv9CrmkgYX"

# load_repo_env [repo-root]
# Sources a local env file if present (gitignored). Does not override vars
# already set in the environment. Prefer `.env.testnet`, then `.env.local`,
# then `.env`. See `.env.testnet.example` and `references/testnet-wallet.md`.
load_repo_env() {
  local root="${1:-${REPO_ROOT:-}}"
  if [ -z "$root" ]; then
    root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  fi
  local f=""
  if [ -f "$root/.env.testnet" ]; then
    f="$root/.env.testnet"
  elif [ -f "$root/.env.local" ]; then
    f="$root/.env.local"
  elif [ -f "$root/.env" ]; then
    f="$root/.env"
  else
    return 0
  fi
  # Export only keys that are currently unset/empty.
  local line key val
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key="${line%%=*}"
    val="${line#*=}"
    # strip optional surrounding quotes
    if [[ "$val" == \"*\" && "$val" == *\" ]]; then
      val="${val:1:${#val}-2}"
    elif [[ "$val" == \'*\' && "$val" == *\' ]]; then
      val="${val:1:${#val}-2}"
    fi
    if [ -z "${!key+x}" ] || [ -z "${!key}" ]; then
      export "$key=$val"
    fi
  done < "$f"
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# crate_version <contract-dir>
# Reads the crate's Cargo.toml `version` field. Uses `.manifest_path` to pick
# the exact package rather than `.packages[0]` — for a workspace-root crate
# with test-stub members (e.g. security-token/tests/mock-gate,
# rfq-settlement/tests/mock-oracle), `cargo metadata`'s package order isn't
# guaranteed to put the root crate first, so `[0]` can silently grab a
# member's version instead (found 2026-07-13 redeploying security-token:
# this returned mock-gate's 0.1.0 instead of security-token's own 0.1.1).
crate_version() {
  local contract_dir="$1"
  local manifest_path
  manifest_path="$(cd "$contract_dir" && pwd)/Cargo.toml"
  cargo metadata --no-deps --format-version 1 --manifest-path "$contract_dir/Cargo.toml" \
    | jq -r --arg manifest "$manifest_path" '.packages[] | select(.manifest_path == $manifest) | .version'
}

# record_entry <deployments-file> <jq-path-array-literal, e.g. ["identity-credential"]> <entry-json>
# Pushes the existing "current" (if any) onto "history" and sets the new "current".
# NOTE: the jq-path arg is deliberately NOT named `path` — zsh ties a lowercase
# `path` local to the `$PATH` array, and shadowing it breaks command lookup
# for the rest of the function (silent "command not found" for anything not
# already hashed).
record_entry() {
  local file="$1" key_path="$2" entry="$3"
  local tmp
  tmp="$(mktemp)"
  jq --argjson keypath "$key_path" --argjson entry "$entry" '
    (getpath($keypath) // {"current": null, "history": []}) as $node
    | ($node.history // []) as $hist
    | (if $node.current != null then [$node.current] + $hist else $hist end) as $newHist
    | setpath($keypath; {"current": $entry, "history": $newHist})
  ' "$file" > "$tmp" && mv "$tmp" "$file"
}

# record_deployment <jq-path-array-literal> <entry-json>
# Writes DEPLOYMENTS_FILE (required), then DEPLOYMENTS_MIRROR_FILE if set.
# Mirror failure fails the call (no silent divergence).
record_deployment() {
  local key_path="$1" entry="$2"
  if [ -z "${DEPLOYMENTS_FILE:-}" ]; then
    echo "error: DEPLOYMENTS_FILE is unset" >&2
    return 1
  fi
  mkdir -p "$(dirname "$DEPLOYMENTS_FILE")"
  [ -f "$DEPLOYMENTS_FILE" ] || echo '{}' > "$DEPLOYMENTS_FILE"
  record_entry "$DEPLOYMENTS_FILE" "$key_path" "$entry"
  if [ -n "${DEPLOYMENTS_MIRROR_FILE:-}" ]; then
    [ -f "$DEPLOYMENTS_MIRROR_FILE" ] || echo '{}' > "$DEPLOYMENTS_MIRROR_FILE"
    record_entry "$DEPLOYMENTS_MIRROR_FILE" "$key_path" "$entry" \
      || { echo "error: failed mirroring deployment record to $DEPLOYMENTS_MIRROR_FILE" >&2; return 1; }
  fi
}

# read_field <deployments-file> <jq-path-array-literal> <field-name>
# Prints the requested field of the node's "current" entry, or empty if absent/null.
read_field() {
  local file="$1" key_path="$2" field="$3"
  jq -r --argjson keypath "$key_path" --arg field "$field" \
    '(getpath($keypath).current[$field]) // empty' "$file"
}

# lookup_gas_limit <contract-name> <fn-name> <deployed-wasm-sha256>
#
# Prints a recommended --gas-limit from deployments/gas-limits.json (written
# by rusk-experiments/gas-profiler), or nothing (falls through to
# rusk-wallet's own default) if the file is missing, has no entry for this
# (contract, fn-name), or — the important case — its recorded wasm_sha256
# doesn't match <deployed-wasm-sha256>. That mismatch means the recommended
# numbers were measured against different contract code than what's actually
# deployed; using them anyway could under-recommend a gas limit for logic
# that changed. Warns to stderr in every fallback case so this is never a
# silent "just trust it".
#
# Regenerate after any contract change:
#   cd rusk-experiments/gas-profiler && cargo run --release
lookup_gas_limit() {
  local contract_name="$1" fn_name="$2" deployed_wasm_sha256="$3"
  local gas_limits_file="${NOCTURNE_GAS_LIMITS:-$REPO_ROOT/deployments/gas-limits.json}"

  if [ ! -f "$gas_limits_file" ]; then
    echo "note: $gas_limits_file not found — using rusk-wallet's default gas-limit. Run 'cd rusk-experiments/gas-profiler && cargo run --release' to generate it." >&2
    return 0
  fi

  local recorded_sha256
  recorded_sha256="$(jq -r --arg c "$contract_name" '.contracts[$c].wasm_sha256 // empty' "$gas_limits_file")"

  if [ -z "$recorded_sha256" ]; then
    echo "note: no gas-limits.json entry for '$contract_name' — using rusk-wallet's default gas-limit." >&2
    return 0
  fi

  if [ "$recorded_sha256" != "$deployed_wasm_sha256" ]; then
    echo "warning: gas-limits.json's recorded wasm for '$contract_name' doesn't match the currently deployed contract (wasm changed since gas-profiler last ran) — using rusk-wallet's default gas-limit instead of a possibly-stale recommendation. Re-run 'cd rusk-experiments/gas-profiler && cargo run --release' to refresh." >&2
    return 0
  fi

  local recommended
  recommended="$(jq -r --arg c "$contract_name" --arg f "$fn_name" \
    '.contracts[$c].methods[$f].gas_limit_recommended // empty' "$gas_limits_file")"

  if [ -z "$recommended" ]; then
    echo "note: gas-limits.json has no measurement for ${contract_name}.${fn_name} — using rusk-wallet's default gas-limit." >&2
    return 0
  fi

  # The node's mempool rejects any transaction below `min_gas_limit` (150000
  # on testnet/mainnet, `rusk/*.config.toml`) regardless of actual execution
  # cost — a policy `gas-profiler`'s `VM::ephemeral()` measurement can't see
  # (no mempool involved there), so its recommendation can be a real
  # transaction's execution cost yet still get rejected outright. Confirmed
  # 2026-07-15: `covenant-monitor.submit_financials`'s recommended 35885 and
  # `loan-escrow.deposit_milestone`'s 5888 both fail with "gas limit lower
  # than minimum 150000" — and because `rusk-wallet`'s failure output was
  # being mistaken for a tx id (see this file's wire-contract.sh caller),
  # this failed completely silently until caught by chain-gateway's committed
  # 2026-07-15 origination test not seeing an expected on-chain event.
  local node_min_gas_limit=150000
  if [ "$recommended" -lt "$node_min_gas_limit" ]; then
    recommended="$node_min_gas_limit"
  fi

  echo "$recommended"
}
