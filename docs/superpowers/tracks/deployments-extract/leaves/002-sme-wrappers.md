---
id: 2
slug: sme-wrappers
status: DONE
owner: task-2-worker
deps:
  - 1
scope:
  - sme_platform/scripts/deploy-contract.sh
  - sme_platform/scripts/wire-contract.sh
  - sme_platform/scripts/lib.sh
acceptance:
  - Thin wrappers exec shared scripts
  - CALLER_REPO_ROOT + DEPLOYMENTS_MIRROR_FILE exported
  - Residual lib.sh sources shared lib without clobbering SCRIPT_DIR
acceptanceDone:
  - true
---
# sme_platform wrappers + residual lib.sh

Task 2: replace full deploy/wire implementations with wrappers that resolve
`NOCTURNE_DEPLOYMENTS_ROOT` (or sibling checkout) and dual-write mirror to
`sme_platform/deployments/testnet.json`.

## Evidence

- Commits `45b205b`, `374e517` on sme_platform `feat/deployments-extract`
- `bash -n` + usage smoke pass; shared script usage text on missing args
- Report: `.superpowers/sdd/task-2-report.md`

## Notes

Worktree users must set `NOCTURNE_DEPLOYMENTS_ROOT` — sibling resolution
under `.worktrees/` does not reach the real checkout.
