# Design: nocturne-deployments full extract (cut A)

Date: 2026-08-04  
Status: approved (incl. dual-write mirror)  
Supersedes scoped non-goals in `2026-08-04-nocturne-deployments-reader-design.md` for this track.

## Goal

Make deploy / wire / pin tooling reusable outside `sme_platform` (product soup)
without forcing every product pin key into the shared file on day one.

## Approach (locked)

**A — scripts + pin SSOT in `nocturne-deployments`; thin wrappers in `sme_platform`.**

Not B (full pin migration of every key) or C (scripts-only / dual pin forever).

## Shape

### 1. Repo layout (`aichbindas/nocturne-deployments`)

```
nocturne-deployments/
  testnet.json                 # pin SSOT (grow keys as products record here)
  crates/nocturne-deployments/ # Rust reader (already shipped)
  scripts/
    deploy-contract.sh
    wire-contract.sh
    encode-contract-args.mjs
    sim-proxy.py               # needed by wire --simulate-first
    lib.sh                     # deploy/wire helpers only
  docs/superpowers/specs/…
```

### 2. What moves

| Artifact | From | Notes |
|----------|------|--------|
| `deploy-contract.sh` | sme_platform/scripts | Default `DEPLOYMENTS_FILE` → this repo’s `testnet.json` |
| `wire-contract.sh` | sme_platform/scripts | Same |
| `encode-contract-args.mjs` | sme_platform/scripts | Data-driver encode helper |
| `sim-proxy.py` | sme_platform/scripts | `--simulate-first` path |
| Deploy helpers in `lib.sh` | sme_platform/scripts/lib.sh | `load_repo_env`, `sha256_of`, `crate_version`, `record_entry`, `read_field`, `lookup_gas_limit` |

`load_repo_env` still sources **caller product root** (`.env.testnet` in
sme_platform / knot / wen), not the pin repo. Pass `REPO_ROOT` / explicit arg
from wrappers.

`lookup_gas_limit` keeps reading optional `gas-limits.json` via env
`NOCTURNE_GAS_LIMITS` or path relative to caller `REPO_ROOT` (file can stay
under sme_platform `deployments/` or `rusk-experiments` until migrated).

### 3. sme_platform wrappers

Keep same entrypoints so existing docs/muscle memory work:

```bash
# sme_platform/scripts/deploy-contract.sh  (thin)
exec "$NOCTURNE_DEPLOYMENTS_ROOT/scripts/deploy-contract.sh" "$@"
```

Resolve root: `NOCTURNE_DEPLOYMENTS_ROOT` → sibling `../nocturne-deployments` →
fail with clear error.

Same for `wire-contract.sh`. Do **not** duplicate encode/sim-proxy in
sme_platform after cutover (wrappers only).

### 4. Pin SSOT + migration dual-write

- **Primary (required):** `nocturne-deployments/testnet.json` — every
  `deploy-contract` / `wire-contract` record writes here. Default
  `DEPLOYMENTS_FILE` when unset.
- **Mirror (optional):** if `DEPLOYMENTS_MIRROR_FILE` is set, apply the **same**
  `record_entry` to that path after a successful primary write.
  - sme_platform wrappers set
    `DEPLOYMENTS_MIRROR_FILE=$SME_ROOT/deployments/testnet.json` so local soup
    stays fresh during migration without a big-bang pin move.
  - Products that only care about shared pins leave the mirror unset.
- `sme_platform/deployments/testnet.json` remains readable for unmigrated
  consumers; new SSOT for ops is always the shared file.
- Knot/wen already read shared pins via Rust crate / symlink.

Failure policy: primary write must succeed; mirror failure → non-zero exit +
loud error (do not silently diverge). No partial “primary ok, mirror skipped”
success.

Optional later leaf (out of scope): one-shot import of remaining sme-only keys
into the shared file; eventually drop the mirror.

### 5. Gates / MCP

`nocturne-mcp-gates` policy `deployments.files` should accept shared pin path
(env or absolute / sibling `nocturne-deployments/testnet.json`), not only
in-repo `deployments/testnet.json`. Document in gates example + README.

### 6. Track leaves (consume)

Track id: `deployments-extract` (home: `nocturne-deployments` docs tracks)

Suggested leaves:

1. **Move scripts + lib** into nocturne-deployments; CI/smoke `bash -n` + dry help
2. **sme_platform wrappers** + set `DEPLOYMENTS_MIRROR_FILE` + document
   `NOCTURNE_DEPLOYMENTS_ROOT`
3. **`record_entry` dual-write** — primary always shared; mirror when env set;
   mirror failure fails the script
4. **Gates pin path** resolve shared home
5. **Docs** — README, redeploy notes, pointer from sme_platform references

## Non-goals (this cut)

- Migrating every key out of `sme_platform/deployments/testnet.json`
- History GC / pin schema redesign
- Moving gas-profiler or wallet docs wholesale
- `init_registry` / `init_chain_id` wiring (separate ops)

## Success

- From knot (or any sibling product): can deploy via shared scripts into shared pins
- From sme_platform: `./scripts/deploy-contract.sh` still works via wrapper and
  **dual-writes** shared SSOT + local `deployments/testnet.json`
- Rust reader + gates can resolve shared `testnet.json`
- No second copy of deploy/wire logic in sme_platform

## Risks

- Wrapper root discovery fails on unusual layouts → require env in CI
- Dual pin files during migrate are intentional: shared = SSOT, sme = mirror
  via `DEPLOYMENTS_MIRROR_FILE` until consumers switch
- `lib.sh` split: other sme scripts source full `lib.sh` — keep a residual
  sme `lib.sh` that sources shared helpers **or** only moves helpers used by
  deploy/wire and leaves the rest in sme (prefer: residual sme `lib.sh`
  `source`s nocturne `lib.sh` then keeps any sme-only helpers)

## Open follow-ups (later leaves, not this cut)

- Import remaining sme pin keys
- Publish/version scripts for non-sibling clones (git submodule / release tarball)
