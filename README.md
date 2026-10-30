# nocturne-deployments

Contract ids of **our own** Nocturne Standards instances on Dusk testnet, and of the few
contracts we invite people to use. Contract ids are on-chain public data; this repo says
which ones we run and what they are wired to.

## Tiers

| Tier | Meaning | Who reads it |
|------|---------|--------------|
| `public` | Contracts we invite people to use. Changes are announced. For product `dusk`, Dusk announces changes (their releases), not us. | docs, website, everyone |
| `own` | Our own instances, for our products and demos. Not for building on: they may change or disappear without notice. Deploy your own instead. | our tools and CI |
| `experiment` | Never pinned. | nobody |

A `public` entry may only reference `public` entries.

Today: `tnst` is `public`; `atlas`, `knot`, `vigil` and `chit` are `own`.

Product `dusk` is reserved for Dusk's own contracts and accounts: `dusk/<name>/<network>/public`,
for example `dusk/sozu-hub/testnet/public`. Its entries come only from the Dusk snapshot under
`upstream/`, through a fixed name table in `nocturne-pins` (`nocturne-pins labels` lists it); no
index entry, current file or history log may use it. Values Dusk says to read live (Sozu's
contracts behind its hub, DuskEVM's dispute game hubs) are marked `fallback`, and a ref to one fails.

## Layout

| Path | Role |
|------|------|
| `index.json` | Catalog (`nocturne.index.v1`): entries per product, network and tier, base layer version (`base`). Chain IDs, RPCs and explorers come from the base; `networks` may only add keys |
| `products/<product>/<network>.<tier>.json` | Current file (`nocturne.pins.v2`): live contracts and configuration only |
| `history/<product>/<network>.<tier>.jsonl` | Append-only history (`nocturne.history.v1`), one event per line |
| `upstream/dusk-networks/` | Snapshot of Dusk's [`@dusk-network/networks`](https://github.com/dusk-network/networks) package (network files, schema, Sozu vectors), MPL-2.0, see its `LICENSE` |
| `upstream/dusk-networks.lock.json` | Version, tag, commit, tarball hashes, per-file sha256 and provenance of that snapshot |
| `schema/` | JSON schemas of the formats (index, current file, history line, upstream lock) |
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
nocturne-pins resolve dusk/sozu-hub/testnet/public
nocturne-pins network --chain-id 1 --field urls.node
```

Without a checkout, the resolver reads this repo's `index.json` from GitHub.
Rust: `nocturne_deployments::Catalog` (see `crates/nocturne-deployments`).

## Write

Only through `nocturne-pins` (our deploy engine calls it). See `tools/nocturne-pins/README.md`.
The Dusk snapshot under `upstream/` is written only by `nocturne-pins upstream sync --version X`,
never by hand.

## Check

```bash
uv run --project tools/nocturne-pins nocturne-pins check --root .
```
