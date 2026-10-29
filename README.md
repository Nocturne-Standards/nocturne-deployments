# nocturne-deployments

Contract ids of **our own** Nocturne Standards instances on Dusk testnet, and of the few
contracts we invite people to use. Contract ids are on-chain public data; this repo says
which ones we run and what they are wired to.

## Tiers

| Tier | Meaning | Who reads it |
|------|---------|--------------|
| `public` | Contracts we invite people to use. Changes are announced. | docs, website, everyone |
| `own` | Our own instances, for our products and demos. Not for building on: they may change or disappear without notice. Deploy your own instead. | our tools and CI |
| `experiment` | Never pinned. | nobody |

A `public` entry may only reference `public` entries.

Today: `tnst` is `public`; `atlas`, `knot`, `vigil` and `chit` are `own`.

## Layout

| Path | Role |
|------|------|
| `index.json` | Catalog (`nocturne.index.v1`): entries per product, network and tier, network data |
| `products/<product>/<network>.<tier>.json` | Current file (`nocturne.pins.v2`): live contracts and configuration only |
| `history/<product>/<network>.<tier>.jsonl` | Append-only history (`nocturne.history.v1`), one event per line |
| `schema/` | JSON schemas of the three formats |
| `tools/nocturne-pins` | Resolver and writer (CLI and Python library) |
| `crates/nocturne-deployments` | Rust reader |

The current file is exactly what its history log replays to. CI rebuilds it and fails on any difference,
so nobody edits a current file by hand.

Full name of a contract: `<product>/<name>/<network>/<tier>`, for example `knot/knot-registry/testnet/own`.
Links between products are references by full name (`refs`), checked by CI, including ids baked into
contracts at build time (`atlas_pin`).

## Read

```bash
uv tool install ./tools/nocturne-pins
nocturne-pins resolve knot/knot-registry/testnet/own
nocturne-pins resolve --reader public tnst/tnst-token
nocturne-pins state atlas/testnet/own
```

Without a checkout, the resolver reads this repo's `index.json` from GitHub.
Rust: `nocturne_deployments::Catalog` (see `crates/nocturne-deployments`).

## Write

Only through `nocturne-pins` (our deploy engine calls it). See `tools/nocturne-pins/README.md`.

## Check

```bash
uv run --project tools/nocturne-pins nocturne-pins check --root .
```
