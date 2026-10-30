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
`content`) and a classifier (`classify`), declared per upstream in `specs`; `release`
(check-release) and `notes` (PR and issue bodies) sit on top.

### New releases: `upstream check-release`

```bash
nocturne-pins upstream check-release --root .                       # summary; exit 0, 1 or 2
nocturne-pins upstream check-release --root . --json                # the JSON report instead
nocturne-pins upstream check-release --root . --report-out r.json   # also write the report
nocturne-pins upstream check-release --root . --from 0.2.0          # dry run against a registry version
nocturne-pins upstream release-notes --report r.json --kind issue   # or pr, title, summary
```

It reads three release signals: npm `dist-tags.latest`, JSR `latest`
(`https://jsr.io/@dusk/networks/meta.json`) and the repository's `v*` tags (`git ls-remote`).
Only npm is the source; JSR or a tag ahead of npm is a warning. When npm has a newer plain
release, it is fetched and verified exactly as `sync` does (steps 1-6 above), the locked
tarball is fetched again and must match the lock, and the two are classified. Nothing is
written.

| Exit | Meaning |
|------|---------|
| 0 | up to date (or, with `--warn-unreachable`, a registry, JSR or GitHub outage: warning) |
| 2 | a newer release, verified and classified; the report says what changed |
| 1 | verification failed, the locked tarball no longer matches the lock, npm `latest` went back below the lock, or an outage without `--warn-unreachable` |

`--from X` compares with version X from the registry instead of the lock (checked against
the registry's `dist.integrity` only): a dry run, for example 0.2.0 against 0.3.0.

### Change classes

The classifier compares the network files, `schema.json`, `package.json` and the `.d.ts`
files of both tarballs, read in memory. The upstream CHANGELOG is quoted, never used to
classify; JavaScript implementation files are not compared (the report says so).

| Class | What changed | Consumers |
|-------|--------------|-----------|
| S safety | `chainId`, `urls.*`, `contracts.*`, `sozu.hub`, `display.blocklist`, `duskDomains.manifest`, DuskEVM chain ID, endpoints, currency, L1/bridge contracts, predeploys and drivers, `routes.*` except estimates and display names (accounts, payout addresses, fees, contracts, running) | update within 48 h, before any deploy |
| D data | `label`, `display.labels`, `apps.*`, `operator`, `sozu.fallbacks` (the hub decides), estimates (`typicalSeconds`), DuskEVM name, explorer and genesis source hashes, route chain names and explorers | next release; at most 30 days or one minor version behind |
| A API or schema | anything in `schema.json` (required keys, enums, `additionalProperties`, new keys), package `exports` and other manifest fields, `.d.ts` files, a 0.x minor or any major version bump | opt in; never forced |

A path the table does not name is S, and the change says "not in the class table, treated as
safety". A whole object added or removed takes the worst class of the values in it. Output
is sorted by file and path; array items are named by `id` (or `account`, `contract`), e.g.
`routes.services[dusk-to-bsc].payoutAddress`. Each change also has a `ref` with items as `[]`
and a scope prefix outside the network files (`schema:`, `package:`, `types:`, `file:`).

### Consumers

`release-notes` lists, in the issue's checklist, only consumers whose `uses` overlap the
change. They come from `upstream/dusk-networks.consumers.json` when it exists: a JSON list
(or `{"consumers": [...]}`) of `{repo, path, kind: npm|jsr|rust-crate|vendored, policy:
immediate|next-release, uses}`. Each `uses` entry is a class (`S`, `D`, `A`), `*`, or a
prefix of a change's `ref` (`routes.services`, `package:exports`, `schema:`). Without the
file the checklist says the registry is not set up yet.

### Workflow `.github/workflows/dusk-networks.yml`

- **detect**, daily 06:23 UTC and on `workflow_dispatch`: `check-release --warn-unreachable`.
  Exit 2 on the default branch: `upstream sync --version X` on `chore/dusk-networks-vX.Y.Z`,
  the pins CI checks, a commit as `github-actions[bot]`, one tracking issue and one PR whose bodies come
  from `release-notes`. Idempotent: a PR for the branch in any state (a closed one included)
  ends the run; an existing branch or an issue with the same title is reused. Exit 1 fails
  the job. The JSON report is kept as a run artifact. Permissions: `contents`,
  `pull-requests` and `issues` write for this job only.
- **sweep**, Mondays 07:00 UTC: `upstream verify --warn-unreachable` and `upstream status
  --offline`.

A PR opened with the workflow's `GITHUB_TOKEN` starts no other workflow, so `pins` does not
run on it by itself; the job runs the same checks before pushing (tool tests, `check`,
`upstream verify`, `cargo test --locked`). A PR closed without merging is not reopened.

## Check (CI)

```bash
nocturne-pins check --root .
```

Schema of index, current files and history lines; replay equals the committed current file;
refs resolve, public entries reference public entries only, baked ids (`atlas_pin`) match;
contract names unique per network; generated legacy files up to date. Base layer
(`base_check`): the lock validates (`nocturne.upstream-lock.v1`) and matches `base`, every
snapshot file has its locked sha256 and no other file is there, the network files validate
against the vendored `schema.json` (a keyword the validator does not support fails the check),
and `networks` overrides no Dusk fact.
