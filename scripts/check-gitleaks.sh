#!/usr/bin/env bash
# Optional: run gitleaks if installed. Exit codes: bin/EXIT-CODES.md
# Missing binary: PUBLIC repo = 1 (hard); private/unknown = 2 (warn). Scan finding = 1.
set -euo pipefail

KIT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" ]]; then
  echo "check-gitleaks: not inside a git work tree" >&2
  exit 1
fi
cd "$ROOT"

if ! command -v gitleaks >/dev/null; then
  if [[ -f "$KIT/bin/lib/estate-fail-contract.sh" ]]; then
    # shellcheck source=lib/estate-fail-contract.sh
    source "$KIT/bin/lib/estate-fail-contract.sh"
  else
    estate_gate_line() { echo "GATE:$1 STATUS:$2 SEVERITY:$3" >&2; }
    estate_hint() { echo "HINT: $*" >&2; }
  fi
  vis="$(gh repo view --json visibility -q .visibility 2>/dev/null || echo UNKNOWN)"
  if [[ "$vis" == "PUBLIC" ]]; then
    estate_gate_line gitleaks fail hard
    estate_hint "install gitleaks (public repo requires local secret scan)"
    exit 1
  fi
  estate_gate_line gitleaks warn soft
  estate_hint "install gitleaks for local secret scan"
  exit 2
fi

echo "check-gitleaks: gitleaks detect --source ."
gitleaks detect --source . --verbose
