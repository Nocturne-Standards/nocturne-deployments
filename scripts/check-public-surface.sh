#!/usr/bin/env bash
# Exit codes: bin/EXIT-CODES.md (0 ok / 1 hard / 2 warn).
# Public-surface / leak gate. Regex SSOT — MCP must exec this, not reimplement.
# See PUBLIC-REPO-STANDARD §5 and TOOLING-AUDIT-LESSONS.md
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" ]]; then
  echo "check-public-surface: not inside a git work tree" >&2
  exit 1
fi
cd "$ROOT"

ALLOWLIST_FILE="${PUBLIC_SURFACE_ALLOWLIST:-.public-surface-allowlist}"
ALLOW_PRIVATE_TIER="${ALLOW_PRIVATE_TIER:-0}"
SELF_NAME="scripts/check-public-surface.sh"
KIT_SELF="bin/check-public-surface.sh"

fail=0
warn=0

# Word-boundary patterns below use \\b. POSIX ERE (git grep -E) does not define
# \\b, so those checks are silent no-ops on common Mac/Linux builds. PCRE (-P)
# does. Fail loud if this git lacks PCRE rather than ship a dead gate.
#
# Probe pathless — do not use `git ls-files | head -1`. Adopt trees often list
# `.cursor/agents/*` symlinks first; `git grep -P` on a symlink exits 1 and
# falsely claims "no PCRE".
if [[ -n "$(git ls-files | head -n1 || true)" ]] && ! git grep -qP '^' 2>/dev/null; then
  echo "check-public-surface: git grep -P (PCRE) required; \\b checks are no-ops under -E" >&2
  exit 1
fi

is_allowlisted() {
  local line="$1"
  [[ -f "$ALLOWLIST_FILE" ]] || return 1
  while IFS= read -r pat || [[ -n "$pat" ]]; do
    [[ -z "$pat" || "$pat" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" == *"$pat"* ]]; then
      return 0
    fi
  done < "$ALLOWLIST_FILE"
  return 1
}

filter_hits() {
  # stdin: git grep lines; stdout: non-allowlisted
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    if is_allowlisted "$line"; then
      continue
    fi
    # Always ignore the gate script itself and allowlist file
    case "$line" in
      "$SELF_NAME":*|"$KIT_SELF":*|*"/$SELF_NAME":*|*"/$KIT_SELF":*|.public-surface-allowlist:*|docs/internal/PUBLIC-REPO-STANDARD.md:*|docs/internal/TOOLING-AUDIT-LESSONS.md:*|FIELD_GUIDE/workflow/context-lifecycle.md:*|*FIELD_GUIDE/workflow/context-lifecycle.md:*)
        continue
        ;;
    esac
    printf '%s\n' "$line"
  done
}

check() {
  # check <severity> <label> <regex>
  # severity: hard | soft  (soft becomes warn when ALLOW_PRIVATE_TIER=1)
  local severity="$1" label="$2" regex="$3"
  local hits
  hits="$(git grep -nIP "$regex" -- \
    ':!.git' \
    ':!target' \
    ':!node_modules' \
    ':!.worktrees' \
    ':!scripts/check-public-surface.sh' \
    ':!bin/check-public-surface.sh' \
    ':!.public-surface-allowlist' \
    2>/dev/null || true)"
  if [[ -z "$hits" ]]; then
    return 0
  fi
  hits="$(printf '%s\n' "$hits" | filter_hits)"
  if [[ -z "$hits" ]]; then
    return 0
  fi
  if [[ "$severity" == soft && "$ALLOW_PRIVATE_TIER" == 1 ]]; then
    echo "WARN ($label) — ALLOW_PRIVATE_TIER=1:"
    printf '%s\n' "$hits"
    warn=1
    return 0
  fi
  echo "BLOCKED: $label"
  printf '%s\n' "$hits"
  fail=1
}

# Credential-looking assignments: hard after public launch; soft while ALLOW_PRIVATE_TIER=1
# (private carves still document testnet wallet pwds in references/ — strip before public)
check soft "possible credential" '(PASSWORD|PWD|SECRET|API_KEY) *= *(['\''"][^'\''".][^'\''"]{5,}|[^'\''"[:space:].$][^[:space:]]{5,})'

# Absolute paths: hard after public launch; soft while ALLOW_PRIVATE_TIER=1
check soft "absolute local path" '/(Users|home)/[a-zA-Z]'

# Soft until private tier is stripped for public launch
check soft "spec section reference" '§[0-9]'
check soft "wave/track/milestone" '\b(Wave|Track|Phase) [0-9A-Z]\b'
check soft "spec number" '\b[Ss]pec [0-9]'
check soft "private doc path" '(docs/superpowers|docs/internal|references/|rusk-experiments|deployments/testnet\.json)'
check soft "internal repo name" '(nocturne-agent-kit|nocturne-mcp-|pituitary)'
check soft "escaping relative link" '\]\(\.\./\.\./\.\./'
check soft "unresolved marker" '\b(TODO|FIXME|XXX|HACK)\b'

# Encrypted provider thinking / CoT envelopes. Hard even with ALLOW_PRIVATE_TIER=1:
# blobs are unreadably secret-bearing (arXiv 2608.09867). Keep the pattern tied
# to a long payload so prose about "thinking" does not trip.
check hard "encrypted thinking envelope" '"(thought_signature|encrypted_content|thinking_blocks|reasoning_details)"[[:space:]]*:[[:space:]]*"[A-Za-z0-9+/=_-]{80,}"'

# Session dumps do not belong in product git even when JSON is clean.
path_hits="$(git ls-files | grep -E '(^|/)agent-transcripts/|(^|/)\.dsh/' || true)"
if [[ -n "$path_hits" ]]; then
  echo "BLOCKED: committed agent transcript dump"
  printf '%s\n' "$path_hits"
  fail=1
fi

if ((fail)); then
  exit 1
fi
if ((warn)); then
  echo "check-public-surface: completed with warnings (ALLOW_PRIVATE_TIER=1)" >&2
  exit 2
fi
exit 0
