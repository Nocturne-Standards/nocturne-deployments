# nocturne-deployments extract (cut A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move deploy/wire scripts into `nocturne-deployments`, keep thin sme_platform wrappers with dual-write mirror, and teach gates to resolve the shared pin file.

**Architecture:** Shared repo owns `scripts/{deploy,wire,encode,sim-proxy,lib}` and primary `testnet.json`. sme_platform wrappers `exec` into those scripts with `DEPLOYMENTS_MIRROR_FILE` pointing at local `deployments/testnet.json`. `record_deployment` writes primary then optional mirror (mirror failure fails the script). Gates prepend `NOCTURNE_DEPLOYMENTS` when set.

**Tech Stack:** bash, jq, node (encode), Python (sim-proxy), existing Rust `nocturne-deployments` crate, nocturne-mcp-gates (JS).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-04-nocturne-deployments-extract-design.md` (cut A + dual-write)
- Primary pin path default: `<nocturne-deployments>/testnet.json`
- Mirror only when `DEPLOYMENTS_MIRROR_FILE` is set; mirror failure = non-zero exit
- `load_repo_env` uses **caller product** `REPO_ROOT` (wallet secrets), not the pin repo
- Do not migrate every sme pin key in this plan
- Do not implement `init_registry` / `init_chain_id` wiring
- Commits: separate per task; no secrets on command lines

## File map

| Path | Role |
|------|------|
| `nocturne-deployments/scripts/lib.sh` | Moved helpers + `record_deployment` dual-write |
| `nocturne-deployments/scripts/deploy-contract.sh` | Moved deploy; default primary pin |
| `nocturne-deployments/scripts/wire-contract.sh` | Moved wire; default primary pin |
| `nocturne-deployments/scripts/encode-contract-args.mjs` | Moved encode helper |
| `nocturne-deployments/scripts/sim-proxy.py` | Moved simulate proxy |
| `nocturne-deployments/scripts/resolve-root.sh` | Optional tiny helper sourced by wrappers (or inline) |
| `nocturne-deployments/scripts/tests/test-record-deployment.sh` | Dual-write regression |
| `sme_platform/scripts/deploy-contract.sh` | Thin wrapper |
| `sme_platform/scripts/wire-contract.sh` | Thin wrapper |
| `sme_platform/scripts/lib.sh` | Residual: source shared lib, keep any sme-only bits |
| `nocturne-mcp-gates/gates/deploy.js` | Load shared pin via env |
| Docs READMEs | Operator paths |

---

### Task 1: Move scripts + add `record_deployment`

**Files:**
- Create: `/Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/lib.sh`
- Create: `/Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/deploy-contract.sh`
- Create: `/Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/wire-contract.sh`
- Create: `/Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/encode-contract-args.mjs`
- Create: `/Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/sim-proxy.py`
- Create: `/Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/tests/test-record-deployment.sh`
- Modify: (none in sme yet)

**Interfaces:**
- Consumes: current sme_platform copies of the five artifacts
- Produces:
  - `record_entry <file> <jq-path> <entry-json>` — single-file write (unchanged semantics)
  - `record_deployment <jq-path> <entry-json>` — writes `$DEPLOYMENTS_FILE` then optional `$DEPLOYMENTS_MIRROR_FILE`
  - Scripts live under `nocturne-deployments/scripts/`; `SCRIPT_DIR` is that directory; pin-repo root is `SCRIPT_DIR/..`

- [ ] **Step 1: Copy artifacts into nocturne-deployments**

```bash
ND=/Users/leonidas/dev/aichbindas/nocturne-deployments
SME=/Users/leonidas/dev/aichbindas/sme_platform
mkdir -p "$ND/scripts/tests"
cp "$SME/scripts/lib.sh" "$ND/scripts/lib.sh"
cp "$SME/scripts/deploy-contract.sh" "$ND/scripts/deploy-contract.sh"
cp "$SME/scripts/wire-contract.sh" "$ND/scripts/wire-contract.sh"
cp "$SME/scripts/encode-contract-args.mjs" "$ND/scripts/encode-contract-args.mjs"
cp "$SME/scripts/sim-proxy.py" "$ND/scripts/sim-proxy.py"
chmod +x "$ND/scripts/"*.sh "$ND/scripts/sim-proxy.py"
```

- [ ] **Step 2: Write failing dual-write test**

Create `scripts/tests/test-record-deployment.sh`:

```bash
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
```

- [ ] **Step 3: Run test — expect fail (no `record_deployment`)**

```bash
bash /Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/tests/test-record-deployment.sh
```

Expected: fail with `record_deployment: command not found` (or similar).

- [ ] **Step 4: Implement `record_deployment` in `scripts/lib.sh`**

Append after `record_entry`:

```bash
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
    mkdir -p "$(dirname "$DEPLOYMENTS_MIRROR_FILE")"
    [ -f "$DEPLOYMENTS_MIRROR_FILE" ] || echo '{}' > "$DEPLOYMENTS_MIRROR_FILE"
    record_entry "$DEPLOYMENTS_MIRROR_FILE" "$key_path" "$entry" \
      || { echo "error: failed mirroring deployment record to $DEPLOYMENTS_MIRROR_FILE" >&2; return 1; }
  fi
}
```

Also update `lookup_gas_limit` gas file resolution near the top of that function:

```bash
  local gas_limits_file="${NOCTURNE_GAS_LIMITS:-$REPO_ROOT/deployments/gas-limits.json}"
```

(Keep existing behavior when `REPO_ROOT` is the product root.)

- [ ] **Step 5: Point moved deploy/wire at pin-repo defaults + `record_deployment`**

In `nocturne-deployments/scripts/deploy-contract.sh`:

1. After `REPO_ROOT` is set for the **caller product**, introduce pin root:

```bash
PIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# Product root for .env / gas-limits: prefer CALLER_REPO_ROOT from wrapper, else cwd walk.
CALLER_REPO_ROOT="${CALLER_REPO_ROOT:-$REPO_ROOT}"
```

Change meaning carefully: today `REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"` becomes wrong once scripts live in nocturne-deployments (that would be the pin repo). Replace with:

```bash
PIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# Wrapper exports CALLER_REPO_ROOT=sme_platform (or knot). Fallback: parent of contract dir workspace is unreliable — require CALLER_REPO_ROOT when not set via env file load from PIN_ROOT only for defaults.
CALLER_REPO_ROOT="${CALLER_REPO_ROOT:-}"
if [ -z "$CALLER_REPO_ROOT" ]; then
  echo "error: CALLER_REPO_ROOT unset — set it to the product repo root (wrappers do this)" >&2
  exit 1
fi
REPO_ROOT="$CALLER_REPO_ROOT"
load_repo_env "$REPO_ROOT"
DEPLOYMENTS_FILE="${DEPLOYMENTS_FILE:-$PIN_ROOT/testnet.json}"
mkdir -p "$(dirname "$DEPLOYMENTS_FILE")"
[ -f "$DEPLOYMENTS_FILE" ] || echo '{}' > "$DEPLOYMENTS_FILE"
```

Replace the final `record_entry "$DEPLOYMENTS_FILE" ...` with:

```bash
record_deployment "$CONTRACT_KEY_PATH" "$ENTRY_JSON"
```

Apply the same `PIN_ROOT` / `CALLER_REPO_ROOT` / default `DEPLOYMENTS_FILE` pattern in `wire-contract.sh`, and replace its `record_entry` with `record_deployment`.

Keep `source "$SCRIPT_DIR/lib.sh"` (local).

- [ ] **Step 6: Re-run dual-write test — expect pass**

```bash
bash /Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/tests/test-record-deployment.sh
```

Expected: prints `ok`.

Also:

```bash
bash -n /Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/lib.sh
bash -n /Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/deploy-contract.sh
bash -n /Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/wire-contract.sh
```

Expected: no output, exit 0.

- [ ] **Step 7: Commit (nocturne-deployments)**

```bash
cd /Users/leonidas/dev/aichbindas/nocturne-deployments
git add scripts docs/superpowers/specs/2026-08-04-nocturne-deployments-extract-design.md
git commit -m "$(cat <<'EOF'
feat: move deploy/wire scripts + dual-write recorder

Shared scripts own primary testnet.json writes; optional
DEPLOYMENTS_MIRROR_FILE keeps product-local pins in sync.
EOF
)"
```

---

### Task 2: sme_platform wrappers + residual `lib.sh`

**Files:**
- Modify: `/Users/leonidas/dev/aichbindas/sme_platform/scripts/deploy-contract.sh` (replace with wrapper)
- Modify: `/Users/leonidas/dev/aichbindas/sme_platform/scripts/wire-contract.sh` (replace with wrapper)
- Modify: `/Users/leonidas/dev/aichbindas/sme_platform/scripts/lib.sh` (source shared + keep local if any unique content — after move, prefer thin source)
- Delete or leave stubs: do **not** keep full encode/sim-proxy copies; wrappers must not depend on local copies

**Interfaces:**
- Consumes: Task 1 scripts; `record_deployment`
- Produces: `resolve_nocturne_deployments_root` behavior via env/sibling; wrappers export `CALLER_REPO_ROOT` + `DEPLOYMENTS_MIRROR_FILE`

- [ ] **Step 1: Write wrapper helper snippet used by both scripts**

Replace `sme_platform/scripts/deploy-contract.sh` entirely with:

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SME_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -n "${NOCTURNE_DEPLOYMENTS_ROOT:-}" ]; then
  ND_ROOT="$NOCTURNE_DEPLOYMENTS_ROOT"
elif [ -d "$SME_ROOT/../nocturne-deployments/scripts" ]; then
  ND_ROOT="$(cd "$SME_ROOT/../nocturne-deployments" && pwd)"
else
  echo "error: set NOCTURNE_DEPLOYMENTS_ROOT to the nocturne-deployments checkout" >&2
  exit 1
fi

export CALLER_REPO_ROOT="$SME_ROOT"
export DEPLOYMENTS_FILE="${DEPLOYMENTS_FILE:-$ND_ROOT/testnet.json}"
export DEPLOYMENTS_MIRROR_FILE="${DEPLOYMENTS_MIRROR_FILE:-$SME_ROOT/deployments/testnet.json}"

exec "$ND_ROOT/scripts/deploy-contract.sh" "$@"
```

Replace `wire-contract.sh` the same way (`exec .../wire-contract.sh`).

- [ ] **Step 2: Make residual `sme_platform/scripts/lib.sh` source shared lib**

Replace body with:

```bash
# Residual sme_platform lib — shared helpers live in nocturne-deployments.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SME_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [ -n "${NOCTURNE_DEPLOYMENTS_ROOT:-}" ]; then
  ND_ROOT="$NOCTURNE_DEPLOYMENTS_ROOT"
elif [ -d "$SME_ROOT/../nocturne-deployments/scripts" ]; then
  ND_ROOT="$(cd "$SME_ROOT/../nocturne-deployments" && pwd)"
else
  echo "error: set NOCTURNE_DEPLOYMENTS_ROOT (needed to source shared lib.sh)" >&2
  return 1 2>/dev/null || exit 1
fi
# shellcheck disable=SC1091
source "$ND_ROOT/scripts/lib.sh"
```

(Other sme scripts that `source lib.sh` keep working.)

- [ ] **Step 3: Smoke wrapper resolution (no chain call)**

```bash
cd /Users/leonidas/dev/aichbindas/sme_platform
bash -n scripts/deploy-contract.sh scripts/wire-contract.sh scripts/lib.sh
# Help/usage path without wallet: missing args should print Usage and exit 1
./scripts/deploy-contract.sh 2>&1 | head -5 || true
```

Expected: usage line from **shared** script (mentions deploy-contract options), not “NOCTURNE_DEPLOYMENTS_ROOT” error.

- [ ] **Step 4: Commit (sme_platform)**

```bash
cd /Users/leonidas/dev/aichbindas/sme_platform
git add scripts/deploy-contract.sh scripts/wire-contract.sh scripts/lib.sh
# If encode/sim-proxy remain as duplicates, delete them only if nothing else imports by path:
# git rm scripts/encode-contract-args.mjs scripts/sim-proxy.py
# Prefer: keep files as one-line stubs that exec/explain moved — or delete after rg shows no references.
git commit -m "$(cat <<'EOF'
refactor: wrap deploy/wire via nocturne-deployments

Wrappers set CALLER_REPO_ROOT and DEPLOYMENTS_MIRROR_FILE so new
records hit shared SSOT and keep local testnet.json in sync.
EOF
)"
```

Before delete of encode/sim-proxy: `rg -n 'encode-contract-args|sim-proxy' scripts` — only delete if zero remaining references outside the moved scripts.

---

### Task 3: Gates shared-pin resolve

**Files:**
- Modify: `/Users/leonidas/dev/aichbindas/nocturne-mcp-gates/gates/deploy.js`
- Modify: `/Users/leonidas/dev/aichbindas/nocturne-mcp-gates/gates/deploy` tests if present (`gates/*.test.js`)
- Modify: `/Users/leonidas/dev/aichbindas/nocturne-mcp-gates/gates.toml.example`

**Interfaces:**
- Consumes: existing `loadDeployments(root)`
- Produces: also loads file from `process.env.NOCTURNE_DEPLOYMENTS` (file or dir+`/testnet.json`), with `sourceFile` labeled for clarity

- [ ] **Step 1: Write failing test**

In existing test file (or create `gates/deploy.shared-pin.test.js` matching project runner):

```js
import { deployCurrent } from "./deploy.js";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

test("deployCurrent reads NOCTURNE_DEPLOYMENTS pin file", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "nd-"));
  const pin = path.join(dir, "testnet.json");
  fs.writeFileSync(
    pin,
    JSON.stringify({
      "multisig-registry": { current: { contract_id: "deadbeef", version: "0.1.6" } },
    }),
  );
  const emptyRoot = fs.mkdtempSync(path.join(os.tmpdir(), "ws-"));
  process.env.NOCTURNE_DEPLOYMENTS = pin;
  try {
    const got = deployCurrent(emptyRoot, "multisig-registry");
    expect(got.error).toBeUndefined();
    expect(got.current.contract_id).toBe("deadbeef");
  } finally {
    delete process.env.NOCTURNE_DEPLOYMENTS;
  }
});
```

Adapt `test`/`expect` to the repo’s runner (node:test or vitest — match neighbors).

- [ ] **Step 2: Run test — expect fail**

Run the package test command used by nocturne-mcp-gates (check `package.json` scripts). Expected: fail finding module / missing shared load.

- [ ] **Step 3: Implement env load in `deploy.js`**

Extend `loadDeployments`:

```js
function resolveEnvDeploymentsFile() {
  const raw = process.env.NOCTURNE_DEPLOYMENTS;
  if (!raw) return null;
  if (fs.existsSync(raw) && fs.statSync(raw).isDirectory()) {
    const file = path.join(raw, "testnet.json");
    return fs.existsSync(file) ? file : null;
  }
  return fs.existsSync(raw) ? raw : null;
}

function loadDeployments(root) {
  const loaded = [];
  const envFile = resolveEnvDeploymentsFile();
  if (envFile) {
    try {
      const data = JSON.parse(fs.readFileSync(envFile, "utf8"));
      loaded.push({ rel: envFile, data });
    } catch {
      // skip unreadable
    }
  }
  for (const rel of deploymentFiles(root)) {
    // ... existing body ...
  }
  return loaded;
}
```

Prefer env file **first** so shared SSOT wins over stale in-repo pins for the same module key.

- [ ] **Step 4: Re-run tests — expect pass**

- [ ] **Step 5: Document in `gates.toml.example`**

Add comment:

```toml
# Optional: export NOCTURNE_DEPLOYMENTS=/path/to/nocturne-deployments/testnet.json
# (or the repo directory). Gates prepend that pin file when looking up modules.
```

- [ ] **Step 6: Commit (nocturne-mcp-gates)**

```bash
cd /Users/leonidas/dev/aichbindas/nocturne-mcp-gates
git add gates/deploy.js gates.toml.example gates/*.test.js
git commit -m "$(cat <<'EOF'
feat: resolve shared pins via NOCTURNE_DEPLOYMENTS

Gates prefer the env pin file so extract SSOT wins over stale
in-repo deployments/testnet.json copies.
EOF
)"
```

---

### Task 4: Docs + track scaffold

**Files:**
- Modify: `/Users/leonidas/dev/aichbindas/nocturne-deployments/README.md`
- Modify: `/Users/leonidas/dev/aichbindas/knot/.worktrees/audit-2026-08-full/docs/internal/redeploy-2026-08-domains.md` (wrapper path / dual-write note)
- Create: `/Users/leonidas/dev/aichbindas/nocturne-deployments/docs/superpowers/tracks/deployments-extract/SPEC.md`
- Create: `/Users/leonidas/dev/aichbindas/nocturne-deployments/docs/superpowers/tracks/deployments-extract/STATUS.md`
- Create: leaf files `001`–`005` matching this plan’s tasks (or merge Task 4 docs into leaves done checklist)
- Modify: `/Users/leonidas/dev/aichbindas/sme_platform/references/testnet-wallet.md` — one short pointer that deploy scripts now live in nocturne-deployments (wrappers remain)

**Interfaces:**
- Consumes: Tasks 1–3 behavior
- Produces: operator-facing truth for `DEPLOYMENTS_FILE` / `DEPLOYMENTS_MIRROR_FILE` / `NOCTURNE_DEPLOYMENTS_ROOT` / `CALLER_REPO_ROOT`

- [ ] **Step 1: Rewrite nocturne-deployments README “Redeploy record” section**

```markdown
## Scripts

`scripts/deploy-contract.sh` and `scripts/wire-contract.sh` are the SSOT
implementations.

Env:

| Var | Role |
|-----|------|
| `CALLER_REPO_ROOT` | Product repo (`.env.testnet`, gas-limits) — required |
| `DEPLOYMENTS_FILE` | Primary pin file (default: this repo `testnet.json`) |
| `DEPLOYMENTS_MIRROR_FILE` | Optional second pin write (sme wrappers set this) |
| `NOCTURNE_DEPLOYMENTS_ROOT` | Used by product wrappers to find this checkout |
| `NOCTURNE_GAS_LIMITS` | Optional path to `gas-limits.json` |

From sme_platform (recommended):

```bash
./scripts/deploy-contract.sh /path/to/contract   # wrapper → dual-write
```

From knot directly:

```bash
export CALLER_REPO_ROOT=/path/to/knot
export NOCTURNE_DEPLOYMENTS_ROOT=/path/to/nocturne-deployments
$NOCTURNE_DEPLOYMENTS_ROOT/scripts/deploy-contract.sh crates/multisig-registry -y
```
```

- [ ] **Step 2: Update knot redeploy doc** to use wrappers / dual-write (drop manual `DEPLOYMENTS_FILE=`-only recipe as the primary path; keep as override).

- [ ] **Step 3: Scaffold track STATUS with leaves DONE/TODO matching completion**

After implementation, mark leaves done in STATUS as tasks land.

- [ ] **Step 4: Commits**

Separate commits per repo touched (nocturne-deployments docs, knot doc, sme reference).

---

### Task 5: Integration check (no surprise live redeploy)

**Files:** none required beyond verification commands

- [ ] **Step 1: Confirm dual paths exist**

```bash
test -x /Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/deploy-contract.sh
test -x /Users/leonidas/dev/aichbindas/sme_platform/scripts/deploy-contract.sh
head -20 /Users/leonidas/dev/aichbindas/sme_platform/scripts/deploy-contract.sh | rg -n 'NOCTURNE_DEPLOYMENTS_ROOT|DEPLOYMENTS_MIRROR'
```

Expected: wrapper mentions both.

- [ ] **Step 2: Dry dual-write via `record_deployment` only** (already covered by unit test); optionally:

```bash
bash /Users/leonidas/dev/aichbindas/nocturne-deployments/scripts/tests/test-record-deployment.sh
```

Expected: `ok`

- [ ] **Step 3: Do not redeploy contracts unless operator explicitly asks** — this plan’s success is tooling extract, not another on-chain deploy.

- [ ] **Step 4: Final STATUS rollup** — all extract leaves DONE in `deployments-extract/STATUS.md`.

---

## Spec coverage (self-review)

| Spec item | Task |
|-----------|------|
| Move deploy/wire/encode/sim/lib | 1 |
| Default primary `testnet.json` | 1 |
| Dual-write mirror + fail on mirror error | 1 |
| sme wrappers + `NOCTURNE_DEPLOYMENTS_ROOT` | 2 |
| Residual sme `lib.sh` sources shared | 2 |
| Gates shared pin | 3 |
| Docs + leaves track | 4 |
| Success verification | 5 |
| Non-goals (full pin migrate, init_registry) | excluded |

## Placeholder scan

None intentional. Adapt gates test runner syntax to match repo (Step 1 Task 3).
