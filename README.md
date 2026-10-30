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
| `index.json` | Catalog (`nocturne.index.v1`): entries per product, network and tier, network data, base layer version (`base`) |
| `products/<product>/<network>.<tier>.json` | Current file (`nocturne.pins.v2`): live contracts and configuration only |
| `history/<product>/<network>.<tier>.jsonl` | Append-only history (`nocturne.history.v1`), one event per line |
| `upstream/dusk-networks/` | Snapshot of Dusk's [`@dusk-network/networks`](https://github.com/dusk-network/networks) package (network files, schema, Sozu vectors), MPL-2.0, see its `LICENSE` |
| `upstream/dusk-networks.lock.json` | Version, tag, commit, tarball hashes, per-file sha256 and provenance of that snapshot |
| `schema/` | JSON schemas of the three formats |
| `tools/nocturne-pins` | Resolver and writer (CLI and Python library) |
| `crates/nocturne-deployments` | Rust reader of our pins |
| `crates/dusk-networks` | Rust reader of the Dusk snapshot: typed `Network`, embedded at build time |

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
Rust: `nocturne_deployments::Catalog` (see `crates/nocturne-deployments`). Dusk network data
(chain IDs, endpoints, Dusk contract IDs, DuskEVM, labels, blocklist) comes from
`dusk_networks::by_chain_id` / `by_name`, also reachable as `Catalog::network` /
`Catalog::network_by_chain_id`. A `networks` section in `index.json` is ignored by the Rust reader.
Sozu contract IDs in that data are fallbacks: read the live ones from the Sozu hub.

### Rust crates and tags

Both crates are consumed by git tag, never from crates.io (`publish = false`: `dusk-networks`
embeds `upstream/dusk-networks/` from outside its own directory, which a git dependency
checks out but `cargo package` would not include).

```toml
nocturne-deployments = { git = "https://github.com/Nocturne-Standards/nocturne-deployments", tag = "nocturne-deployments-v0.3.0" }
dusk-networks = { git = "https://github.com/Nocturne-Standards/nocturne-deployments", tag = "nocturne-deployments-v0.3.0" }
```

One repo tag, `nocturne-deployments-vX.Y.Z`, covers every crate in the workspace; `X.Y.Z` is the
`nocturne-deployments` crate version. The Dusk snapshot version a tag embeds is
`dusk_networks::DUSK_NETWORKS_VERSION` (equal to the lock's `version`, checked by test) and is
named in the tag's release notes. A new Dusk snapshot bumps `dusk-networks` and is released under
the next `nocturne-deployments` tag.

| Tag | `nocturne-deployments` | `dusk-networks` | Dusk snapshot |
|-----|------------------------|-----------------|---------------|
| `nocturne-deployments-v0.2.0` | 0.2.0 | none | none |
| `nocturne-deployments-v0.3.0` (next) | 0.3.0 | 0.1.0 | 0.3.0 |

`nocturne-deployments` 0.3.0 drops the untyped `Index.networks` field; read networks through
`Catalog::network` instead.

## Write

Only through `nocturne-pins` (our deploy engine calls it). See `tools/nocturne-pins/README.md`.
The Dusk snapshot under `upstream/` is written only by `nocturne-pins upstream sync --version X`,
never by hand.

## Check

```bash
uv run --project tools/nocturne-pins nocturne-pins check --root .
```
