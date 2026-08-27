# Pin templates

Operator scaffolding — **not** loaded at runtime. Copy, fill `REPLACE_*` fields, merge into `testnet.json` / `duskds/testnet.json`.

## `chit-tnst-testnet.pin-template.json`

Parallel Chit stack for **tNST** (`cash_asset: drc20`). See plan:

`docs/superpowers/plans/2026-08-27-tnst-chit-t2-lite.md`

### Merge steps

1. Deploy four contracts (fresh nonces): gate, settlement, escrow, receive-demo — same wasm family as prod Chit stack (`chit/scripts/deploy-contract.sh` + ops wrappers).
2. Run wiring in `_wiring_notes.wire_order` via `nocturne-working/ops/wire-contract.sh`.
3. Replace every `REPLACE_*` placeholder in the template with on-chain values.
4. Remove `_template` and `_wiring_notes` top-level keys.
5. Merge `contracts` entries into the manifest root (alongside `tnst-token`, `agent-cash-escrow`, …).
6. Merge `wiring` entries into the manifest `wiring` section.
7. Regenerate flat `testnet.json` projection if your workflow uses `duskds/` as source of truth.
8. Update `nocturne-chain-web/apps/consensus/.env.tnst-chit.example` contract ids.
9. Run `npm run check:chit-parity` (prod) — must stay green.

### Reused ids (do not change without ops review)

| Role | Id / pin |
| --- | --- |
| tNST DRC-20 | `f090528946990f3f2d3291d903327dfd700af6d912388c5f3c64c9bbc2a4edda` (`tnst-token`) |
| Mandate (prod reuse) | `b592983fd7ab9ec51483ca7f3bedbba08ae1531c19a4d065bf82be497bf36475` |
| Revocation (prod reuse) | `5e83f7be75b08e37b5f314aa1cd9b8b49ee95216bc21066603dadf1eb3b03cd6` |

Prod Chit stack (`PUBLIC_CHIT_*`) must **not** be edited for T2.
