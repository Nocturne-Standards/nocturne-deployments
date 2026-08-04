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

## Redeploy record

From `sme_platform` (scripts still live there):

```bash
export DEPLOYMENTS_FILE=/Users/leonidas/dev/aichbindas/nocturne-deployments/testnet.json
./scripts/deploy-contract.sh /path/to/knot/crates/multisig-registry
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
