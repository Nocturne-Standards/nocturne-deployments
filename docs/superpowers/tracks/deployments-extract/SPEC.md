---
planner_model: cursor-grok-4.5-medium
worker_model: composer-2.5
reviewer_model: claude-sonnet-5-thinking-high
---
# SPEC — deployments-extract (cut A)

## Goal

Move deploy/wire scripts into `nocturne-deployments`, keep thin `sme_platform`
wrappers with dual-write mirror, and teach gates to resolve the shared pin file.

## Scope

Per `docs/superpowers/specs/2026-08-04-nocturne-deployments-extract-design.md`:

- Shared repo owns `scripts/{deploy,wire,encode,sim-proxy,lib}` and primary `testnet.json`
- `sme_platform` wrappers `exec` shared scripts with `DEPLOYMENTS_MIRROR_FILE`
- `record_deployment` writes primary then optional mirror (mirror failure = non-zero exit)
- `nocturne-mcp-gates` prepends `NOCTURNE_DEPLOYMENTS` when set
- Operator docs for env vars: `CALLER_REPO_ROOT`, `DEPLOYMENTS_FILE`,
  `DEPLOYMENTS_MIRROR_FILE`, `NOCTURNE_DEPLOYMENTS_ROOT`, `NOCTURNE_GAS_LIMITS`

## Non-goals

- Full migration of every `sme_platform/deployments/testnet.json` key
- `init_registry` / `init_chain_id` wiring
- Live on-chain redeploy as part of extract tooling work

## Acceptance (track-level)

- Scripts live in `nocturne-deployments/scripts/` with dual-write recorder
- `sme_platform` wrappers resolve ND root and mirror local pins
- Gates prefer env pin file over stale in-repo copies
- README + operator docs reflect wrapper / dual-write paths
- Integration smoke (unit test + wrapper resolution) passes without live redeploy
