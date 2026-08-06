# nocturne-deployments

Shared **pin home** for live contract IDs (`testnet.json`).

GitHub: [`aichbindas/nocturne-deployments`](https://github.com/aichbindas/nocturne-deployments)

## What this repo is

- `testnet.json` — keyed by contract name; each entry has `current` (+ optional `history`)
- `crates/nocturne-deployments` — thin Rust reader for that JSON

Contract IDs are on-chain public data. This repo is the **shared pin file** so
product tools (Knot Lab, etc.) agree on which bytecode is “current” on testnet.

## Resolve path (reader order)

1. `NOCTURNE_DEPLOYMENTS` — file path, or directory containing `testnet.json`
2. Walk up from the caller: `<dir>/deployments/testnet.json` or
   `<dir>/nocturne-deployments/testnet.json`
3. Sibling of each ancestor: same two names under `<parent>/`

Example for Knot:

```bash
export NOCTURNE_DEPLOYMENTS=/path/to/nocturne-deployments
# or sibling symlink from a knot checkout:
ln -sfn ../nocturne-deployments deployments
```

`knot-tool` loads pins via `NOCTURNE_DEPLOYMENTS` / walk-up (no git dependency).

## Operator deploy / wire

Deploy and wire live in private **`nocturne-working/ops/`** (not this repo).
Thin redirects under `scripts/deploy-contract.sh` and `scripts/wire-contract.sh`
exec those if a sibling `nocturne-working` checkout (or `NOCTURNE_WORKING_ROOT`)
is present.

## Pin keys (Knot)

`multisig-registry` / `multisig-proposals` (stable pin JSON keys; product crates
are `knot-*`).
