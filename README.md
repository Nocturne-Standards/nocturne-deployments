# nocturne-deployments

Shared **pin home** for live contract IDs (`testnet.json`).

GitHub: [`aichbindas/nocturne-deployments`](https://github.com/aichbindas/nocturne-deployments)

## Layout

- `testnet.json` — keyed by contract name; each entry has `current` (+ optional `history`)
- `crates/nocturne-deployments` — thin Rust reader

## Resolve path (reader order)

1. `NOCTURNE_DEPLOYMENTS` — file path, or directory containing `testnet.json`
2. Walk up: `<dir>/deployments/testnet.json` or `<dir>/nocturne-deployments/testnet.json`
3. Sibling of each ancestor: same two names under `<parent>/`

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

Override primary pin only (advanced):

```bash
export DEPLOYMENTS_FILE=/path/to/custom/testnet.json
```

## Knot / product wiring

In a knot worktree:

```bash
ln -sfn ../../../nocturne-deployments deployments   # from worktree root
```

`multisig-tool` path-deps `deployments/crates/nocturne-deployments` and resolves
pins via walk-up / sibling / `NOCTURNE_DEPLOYMENTS`.

Scoped seed today: `multisig-registry`, `multisig-proposals`. Other products still
pin in `sme_platform/deployments/` until extract leaves land.
