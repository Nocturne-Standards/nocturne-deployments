#!/usr/bin/env bash
# Exit codes: bin/EXIT-CODES.md (0 ok / 1 hard / 2 warn).
# Gate-coverage check — the meta-gate.
#
# WHY THIS EXISTS
# ---------------
# On 2026-08-05 chit re-leaked a credential that knot's public-surface gate had
# been written weeks earlier to catch. Both repos had core.hooksPath pointing at
# this kit. The pre-commit hook fired in both. It caught nothing in chit.
#
# Cause: compose-repo-hooks.sh uses `run_if`, which runs a repo-local gate only
# `if [[ -x "$ROOT/scripts/$script" ]]`. knot had six gate scripts. chit had
# zero. A repo with no gates therefore passed every commit silently — the
# architecture failed open, per repo, with no signal.
#
# This script makes the ABSENCE of a gate a failure. It is the one check that
# cannot itself be forgotten, because it is what notices the forgetting.
#
# Bash 3.2+ compatible (macOS /bin/bash).
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" ]]; then
  echo "check-gate-coverage: not inside a git work tree" >&2
  exit 1
fi
cd "$ROOT"

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Kit is the gate source of truth — scripts/ copies are for adopters only.
if [[ -f "$ROOT/FIELD_GUIDE/SPEC.md" && -x "$ROOT/bin/check-gate-coverage.sh" ]]; then
  echo "ok: check-gate-coverage — kit repo (gates live in bin/)"
  exit 0
fi

# Gates every adopting repo must carry a copy of, in scripts/.
# Keep in sync with bin/ — a gate that exists in the kit but is required
# nowhere is a gate nobody runs.
# Arrays, not here-strings: a here-string needs a temp file, and the Cursor
# sandbox denies that file. The loop then runs zero times and this gate
# used to print ok. Bash 3.2 has no mapfile.
REQUIRED=(
  check-public-surface.sh
  check-audit-sha.sh
  check-repo-rules.sh
  check-gitleaks.sh
  check-bls-insecure.sh
)

# Gates required only when the repo ships Rust contracts.
REQUIRED_IF_CONTRACTS=(
  check-contract-authz.sh
  check-contract-authz-body.sh
  check-contract-init.sh
)

# Test seam only. Empties the lists so the zero-count branch can be forced.
# Unset in production; a real repo never sets this.
if [[ -n "${NOCTURNE_COVERAGE_TEST_EMPTY:-}" ]]; then
  REQUIRED=()
  REQUIRED_IF_CONTRACTS=()
fi

# Opt out per repo, with a reason, in .gate-coverage-waivers:
#   check-repo-rules.sh: no hand-copied mirrors in this repo (reviewed 2026-08-05)
WAIVERS=".gate-coverage-waivers"

fail=0
missing=""
checked=0

is_waived() {
  [[ -f "$WAIVERS" ]] || return 1
  grep -qE "^[[:space:]]*$1[[:space:]]*:" "$WAIVERS"
}

has_contracts() {
  # A dusk-forge contract crate is the trigger for contract-specific gates.
  git grep -lE '#\[dusk_forge::contract\]' -- '*.rs' >/dev/null 2>&1
}

require() {
  local script="$1"
  if [[ -x "scripts/$script" ]]; then
    return 0
  fi
  if [[ -f "scripts/$script" ]]; then
    echo "BLOCKED: scripts/$script exists but is not executable (chmod +x)" >&2
    fail=1
    return 0
  fi
  if is_waived "$script"; then
    echo "waived: $script — $(grep -E "^[[:space:]]*$script[[:space:]]*:" "$WAIVERS" | head -1 | cut -d: -f2-)"
    return 0
  fi
  missing="$missing $script"
  fail=1
}

# Bash 3.2 + set -u treats "${empty[@]}" as unbound, so guard before expand.
if [[ ${#REQUIRED[@]} -gt 0 ]]; then
  for script in "${REQUIRED[@]}"; do
    [[ -z "$script" ]] && continue
    require "$script"
    checked=$((checked + 1))
  done
fi

if has_contracts && [[ ${#REQUIRED_IF_CONTRACTS[@]} -gt 0 ]]; then
  for script in "${REQUIRED_IF_CONTRACTS[@]}"; do
    [[ -z "$script" ]] && continue
    require "$script"
    checked=$((checked + 1))
  done
fi

# Zero iterations is an environment failure, not an empty requirement list.
if [[ "$checked" -eq 0 ]]; then
  echo "BLOCKED: check-gate-coverage checked 0 gates (environment error)" >&2
  exit 1
fi

if [[ -n "$missing" ]]; then
  echo "BLOCKED: this repo is missing required gate scripts:" >&2
  for m in $missing; do
    echo "  scripts/$m" >&2
    if [[ -f "$KIT/bin/$m" ]]; then
      echo "    install: cp \"$KIT/bin/$m\" scripts/ && chmod +x scripts/$m" >&2
    fi
  done
  echo "" >&2
  echo "  Or waive with a reason in $WAIVERS:" >&2
  echo "    <script>: <why this repo does not need it> (reviewed <date>)" >&2
fi

# CI costs money, and GitHub cannot require checks on private aichbindas
# repos (plan returns 403). Private floor is the kit pre-commit hook.
# Public marker: workflows must invoke scripts/check-* (job name kit-gates).
# Missing .github/workflows is a miss, same as a workflow that never calls them.
if [[ -f .nocturne-public ]]; then
  ci_hit=0
  if [[ -d .github/workflows ]]; then
    if grep -Rq 'scripts/check-' .github/workflows --include='*.yml' --include='*.yaml' 2>/dev/null; then
      ci_hit=1
    fi
  fi
  if [[ "$ci_hit" -eq 0 ]]; then
    echo "BLOCKED: public repo (.nocturne-public) does not invoke scripts/check-* in .github/workflows" >&2
    echo "  Required job kit-gates: check-gate-coverage.sh, check-public-surface.sh (ALLOW_PRIVATE_TIER unset), check-gitleaks.sh." >&2
    fail=1
  fi
fi

if ((fail == 0)); then
  echo "ok: check-gate-coverage — $checked gates present and wired"
fi
exit "$fail"
