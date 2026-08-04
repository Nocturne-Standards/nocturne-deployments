---
id: 4
slug: docs-track-scaffold
status: DONE
owner: task-4-worker
deps:
  - 1
  - 2
  - 3
scope:
  - README.md
  - docs/superpowers/tracks/deployments-extract/
  - knot/docs/internal/redeploy-2026-08-domains.md
  - sme_platform/references/testnet-wallet.md
acceptance:
  - Operator-facing env var table in nocturne-deployments README
  - Knot redeploy doc uses wrapper path as primary
  - sme_platform testnet-wallet pointer to shared scripts
  - Track SPEC/STATUS + leaves 001–005 scaffolded
acceptanceDone:
  - true
---
# Docs + track scaffold

Task 4: document operator truth for deploy env vars; scaffold
`deployments-extract` track; update knot redeploy checklist and sme_platform
wallet reference.

## Evidence

- README Scripts section replaces legacy DEPLOYMENTS_FILE-only recipe
- Track files under `docs/superpowers/tracks/deployments-extract/`
- Report: `.superpowers/sdd/task-4-report.md`
