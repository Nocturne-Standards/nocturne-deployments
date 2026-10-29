#!/usr/bin/env bash
# Exit codes: bin/EXIT-CODES.md (0 ok / 1 hard / 2 warn).
# Shared-code ratchet — documented hand-copied mirrors vs .repo-rules-baseline.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" ]]; then
  echo "check-repo-rules: not inside a git work tree" >&2
  exit 1
fi
BASELINE="$ROOT/.repo-rules-baseline"
cd "$ROOT"

# Kit is the SSOT — this ratchet is for adopters (compose kit-first would
# otherwise HARD kit commits on a missing .repo-rules-baseline).
if [[ -f "$ROOT/FIELD_GUIDE/SPEC.md" && -x "$ROOT/bin/check-kit-sync.sh" ]]; then
  echo "ok: check-repo-rules — kit repo (adopter ratchet)"
  exit 0
fi

# Avoid backticks inside single quotes (bash string terminator).
PATTERN='byte-for-byte|byte for byte|[Dd]uplicated rather than|[Ll]ifted from|[Cc]opied (and pared|from)|[Mm]irrors?( +(of|the|from))? '

UPDATE=0
if [[ "${1:-}" == "--update" ]]; then
  UPDATE=1
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

git grep -nIE "$PATTERN" -- \
  ':!.git' \
  ':!.worktrees' \
  ':!target' \
  ':!node_modules' \
  ':!*.lock' \
  ':!Cargo.lock' \
  ':!.repo-rules-baseline' \
  ':!scripts/check-repo-rules.sh' \
  ':!scripts/check-*.sh' \
  ':!bin/check-repo-rules.sh' \
  ':!bin/check-*.sh' \
  ':!FIELD_GUIDE' \
  ':!**/FIELD_GUIDE/**' \
  2>/dev/null | sort >"$tmp" || true

if [[ "$UPDATE" -eq 1 ]]; then
  cp "$tmp" "$BASELINE"
  echo "updated $BASELINE ($(wc -l <"$tmp" | tr -d ' ') hits)"
  exit 0
fi

if [[ ! -f "$BASELINE" ]]; then
  echo "check-repo-rules: missing $BASELINE — run: scripts/check-repo-rules.sh --update" >&2
  exit 1
fi

new="$(comm -13 "$BASELINE" "$tmp" || true)"
if [[ -n "$new" ]]; then
  echo "BLOCKED: new hand-copied type mirrors (shared-code rule):" >&2
  printf '%s\n' "$new" >&2
  echo "Fix by depending on the shared crate, or --update only after review." >&2
  exit 1
fi

echo "ok: check-repo-rules ($(wc -l <"$tmp" | tr -d ' ') hits, no new vs baseline)"
