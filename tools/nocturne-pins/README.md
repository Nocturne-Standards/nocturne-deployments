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

## Check (CI)

```bash
nocturne-pins check --root .
```

Schema of index, current files and history lines; replay equals the committed current file;
refs resolve, public entries reference public entries only, baked ids (`atlas_pin`) match;
contract names unique per network; generated legacy files up to date.
