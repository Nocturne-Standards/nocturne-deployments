# nocturne-deployments

Shared **pin home** for live contract IDs on Dusk testnet.

GitHub: [`aichbindas/nocturne-deployments`](https://github.com/aichbindas/nocturne-deployments)

## What this repo is

- `index.json` — catalog of layer/network pin files (`nocturne.pins.v1`)
- `duskds/testnet.json` — Dusk native (DS) contract pins + wiring envelope
- `duskevm/testnet.json` — Dusk EVM contract pins (live chain-745 public pins)
- `testnet.json` — legacy flat pin file (unchanged during migration)
- `crates/nocturne-deployments` — thin Rust reader for that JSON

Contract IDs are on-chain public data. This repo is the **shared pin file** so
product tools (Knot Lab, etc.) agree on which bytecode is “current” on testnet.

Local machine paths are **not** part of the public pin record (`wasm_path` /
`dd_wasm_path` may be null); use `contract_id`, `wasm_sha256`, and `tx_id`.

## Resolve path (reader order)

1. `NOCTURNE_DEPLOYMENTS` — pin **repo root** (directory containing `index.json`
   and layer pin files), or a file path to `testnet.json`
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

Deploy and wire scripts are **private org tooling** (not in this public tree).
Thin redirects under `scripts/` exec a sibling private ops checkout when present
(`NOCTURNE_WORKING_ROOT` or `../nocturne-working/ops`). Outsiders use the pin
file + their own deploy path.

## Pin keys (Knot)

`knot-registry` / `knot-proposals` (match product crate names and
`knot-tool` `json_key`). Legacy `multisig-*` names are aliased in-file
(`multisig-registry` → `knot-registry`, `multisig-proposals` →
`knot-proposals` in `duskds/testnet.json`).

## Layout

| Path | Role |
|------|------|
| `index.json` | Catalog: layer, network, path, `public`, optional `chain_id` |
| `duskds/testnet.json` | DS pins: `contracts`, `wiring`, `aliases` |
| `duskevm/testnet.json` | EVM pins: `contracts`, `aliases` (live chain-745 public pins) |

Run `python3 scripts/check-aliases.py` to verify alias targets exist in each
pin file's `contracts` map (skipped when `contracts` is `{}`).
