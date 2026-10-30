# nocturne-pins

Reads and writes nocturne pin repos: one current file per product, network and tier,
an append-only history log behind each, and full names `product/name/network/tier`.

## Install

```bash
uv tool install ./tools/nocturne-pins
```

Inside this repo, `uv run --project tools/nocturne-pins nocturne-pins ...` works without installing.

## Read

```bash
nocturne-pins resolve knot/knot-registry/testnet/own
nocturne-pins resolve knot/knot-registry            # testnet, tier order own then public
nocturne-pins resolve --reader public tnst/tnst-token
nocturne-pins get atlas/atlas/testnet/own
nocturne-pins state atlas/testnet/own knot-warden.set_scheduler
nocturne-pins list
```

Where it looks for repos, first match wins:

1. `--index` (repeatable; a path or an `index.json` URL)
2. `NOCTURNE_PINS`, a `:`-separated list of the same
3. walking up from the current directory for `nocturne-deployments-internal/` and `nocturne-deployments/`
4. the public repo on GitHub

A private repo listed first shadows public entries with the same product, network and tier.
Set `NOCTURNE_PINS_TOKEN` to read a private repo over HTTPS.

Tiers: `own` readers (our tools, CI) read `own`, then `public`. `public` readers (docs, website) read `public` only.
`experiment` is never pinned.

## Dusk's networks

Product `dusk` is reserved: `dusk/<name>/<network>/public` resolves from the base layer
(Dusk's files under `upstream/dusk-networks/`, declared by `index.json` `base.dusk`), never from
catalog entries. `own` and `public` readers both read it. For `dusk`, tier `public` means Dusk
announces changes in their releases. Remote reads take the files from the same base URL as
`index.json`.

```bash
nocturne-pins resolve dusk/evm-l1-cross-domain-messenger/testnet/public
nocturne-pins get dusk/sozu-pool/testnet/public     # layer, id, kind, path, label, fallback
nocturne-pins network testnet                       # Dusk's whole file
nocturne-pins network testnet --layer duskevm --field rpcUrl
nocturne-pins network --chain-id 1 --field urls.node
nocturne-pins labels testnet                        # name, layer, id, label, fallback
```

Names come from a fixed table (`nocturne_pins.dusk_names.TABLE`), one row per JSON path:

| Names | Path | Layer |
|-------|------|-------|
| `transfer`, `stake`, `multicall` | `contracts.*` | duskds |
| `sozu-hub` | `sozu.hub` | duskds |
| `sozu-pool`, `sozu-relayer`, `sozu-stdusk`, `sozu-substrate`, `sozu-vault` (fallback) | `sozu.fallbacks.*` | duskds |
| `evm-<key>` with `_` as `-`, e.g. `evm-l1-cross-domain-messenger`, `evm-optimism-portal`, `evm-tx-router` | `duskEvm.contracts.<key>` (DuskEVM's L1 contracts; `evm_address` alongside) | duskds |
| `evm-dispute-game-factory`; `evm-fault-dispute-game-hub`, `evm-permissioned-dispute-game-hub` (fallback) | `duskEvm.contracts.*` | duskds |
| `evm-predeploy-l1-block`, `evm-predeploy-l2-cross-domain-messenger`, `evm-predeploy-l2-standard-bridge`, `evm-predeploy-l2-to-l1-message-passer` | `duskEvm.predeploys.*` | duskevm |
| `<route>-contract`, `<route>-payout-account` | `routes.migrations[id=<route>]` | external, duskds |
| `<route>-account`, `<route>-payout-address` | `routes.services[id=<route>]` | duskds, external |
| `bsc-token`, `ethereum-token` | `routes.chains.<chain>.token` | external |

`external` is the route's other chain (`chain`, `chain_id` alongside). `label` comes from Dusk's
`display.labels`. A network without a value for a row has no such name. Dusk Domains has no ids in
the files (its manifest is read live), so it has no names. `check` fails on a value of the snapshot
no row names, on a ref to a `fallback` value, and on any use of product `dusk` in an index entry,
current file or history log.

## Write

Only through the tool. It appends to the history log first, then rewrites the current file
from a replay of the whole log, then regenerates the legacy files the index names.
A write is refused when the committed current file no longer equals the replay of its log.

```bash
nocturne-pins record-contract --root . --name knot-registry --layer duskds --entry-json entry.json
nocturne-pins record-call --root . --key atlas.init_guardian --entry-json row.json
nocturne-pins note --root . knot/testnet/own --text "promoted from run X"
```

Calls that do not change configuration (`pay`, `trade`, `mint`, ...) are demo traffic:
they go to the entry's `calls` log when the index gives one (private repo only), otherwise nowhere.
`nocturne-pins classify <contract.fn>` shows how a key is treated.

## Upstream base layer

Dusk publishes its network facts as the npm package `@dusk-network/networks`. We keep a
verified snapshot of one version under `upstream/dusk-networks/` and record it in a lock.

```bash
nocturne-pins upstream sync --version 0.3.0 --root .
nocturne-pins upstream status --root .            # exit 2 when a newer version is published
nocturne-pins upstream status --root . --offline  # lock and file hashes only
```

`sync` writes nothing unless every check passes:

1. registry metadata gives `dist.tarball` (on the registry host) and `dist.integrity`;
   the tarball's sha512 must equal `dist.integrity` before it is opened;
2. the tarball is read in memory: only the listed regular files, no links, no paths
   outside `package/`, no name twice. Nothing from it is run;
3. `package.json` name and version match;
4. the registry has an SLSA provenance attestation (none: refused). Its subject is this
   tarball; it names `github.com/dusk-network/networks`, `.github/workflows/publish.yml`,
   `refs/tags/vX`, a GitHub-hosted runner and one commit; the signing certificate carries
   the same workflow identity;
5. `git ls-remote` of `refs/tags/vX` (peeled if annotated) equals that commit;
6. the network files, `schema.json`, `fixtures/sozu-vectors.json`, `LICENSE` and
   `package.json` are byte-equal to the same paths at that commit on GitHub.

Not verified: the Sigstore bundle itself (DSSE signature, Fulcio certificate chain, Rekor
inclusion proof) and the npm registry signatures. Both need X.509 and ECDSA, which the
standard library lacks, so the attestation is trusted as served by the registry over HTTPS.
The tag binding and the byte comparison with GitHub are the independent checks. JSR is not
read. The lock lists what was and was not verified, with `verified_at`.

Outputs: `upstream/dusk-networks/{mainnet,testnet,devnet}.json`, `schema.json`,
`fixtures/sozu-vectors.json`, `LICENSE`; `upstream/dusk-networks.lock.json`; and
`index.json` `base.dusk = {version, lock}`. A re-sync of the locked version with the same
bytes changes nothing. A version older than the lock needs `--allow-downgrade`.

`nocturne-pins upstream verify --root .` (CI, on every push and PR) fetches the locked tarball
again and checks it against the registry record, the lock and every snapshot file, comparing bytes.
With `--warn-unreachable` (as CI runs it) a registry outage (network error, timeout, 429, 5xx)
is a warning; a wrong answer still fails.

Network data (chain ID, RPC, explorer per layer) comes from the snapshot: DuskDS `chainId`,
`urls.node`, `apps.explorer`; DuskEVM `duskEvm.chainId`, `rpcUrl`, `explorerUrl`. `index.json`
`networks` is optional and may only add keys (an RPC mirror, a network Dusk does not list);
`check` fails on a key that gives another value than Dusk. Legacy files read the merged view.

Code: `nocturne_pins.upstream`, split into a source (`npm`), verifiers (`provenance`,
`content`) and a classifier (`classify`, a placeholder for now), declared per upstream
in `specs`.

## Check (CI)

```bash
nocturne-pins check --root .
```

Schema of index, current files and history lines; replay equals the committed current file;
refs resolve, public entries reference public entries only (`dusk/...` refs are public), baked ids (`atlas_pin`) match;
product `dusk` reserved, no ref to a Dusk `fallback`, every Dusk id named;
contract names unique per network; generated legacy files up to date. Base layer
(`base_check`): the lock validates (`nocturne.upstream-lock.v1`) and matches `base`, every
snapshot file has its locked sha256 and no other file is there, the network files validate
against the vendored `schema.json` (a keyword the validator does not support fails the check),
and `networks` overrides no Dusk fact.
