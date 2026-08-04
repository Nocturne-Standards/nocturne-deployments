---
id: 3
slug: gates-shared-pin
status: DONE
owner: task-3-worker
deps:
  - 1
scope:
  - nocturne-mcp-gates/gates/deploy.js
  - nocturne-mcp-gates/gates.toml.example
acceptance:
  - NOCTURNE_DEPLOYMENTS env pin loaded first in loadDeployments
  - File or directory resolution for testnet.json
  - Tests pass
acceptanceDone:
  - true
---
# Gates shared-pin resolve

Task 3: `loadDeployments` prepends pins from `NOCTURNE_DEPLOYMENTS` before
in-repo policy files so shared SSOT wins on duplicate module keys.

## Evidence

- `gates/deploy.shared-pin.test.js` — file + directory env resolution
- Full vitest suite 19/19 passed
- Report: `.superpowers/sdd/task-3-report.md`
