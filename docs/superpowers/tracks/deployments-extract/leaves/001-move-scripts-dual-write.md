---
id: 1
slug: move-scripts-dual-write
status: DONE
owner: task-1-worker
deps: []
scope:
  - scripts/
  - crates/nocturne-deployments/
acceptance:
  - Deploy/wire/encode/sim/lib live under nocturne-deployments/scripts/
  - record_deployment dual-writes primary + optional mirror
  - test-record-deployment.sh passes
acceptanceDone:
  - true
---
# Move scripts + dual-write recorder

Task 1: copy sme_platform deploy artifacts into shared repo; add
`record_deployment`; default primary pin to `$PIN_ROOT/testnet.json`;
require `CALLER_REPO_ROOT` for product `.env` / gas-limits.

## Evidence

- Commit `b1e5544` on `feat/deployments-extract`
- `scripts/tests/test-record-deployment.sh` prints `ok`
- Report: `.superpowers/sdd/task-1-report.md`
