# Identity Merkle + tNST airdrop — pin keys (fill after live deploy)

Do **not** merge invented `contract_id` values. After ops deploy + wire, copy live fields into `duskds/testnet.json` (then regenerate flat `testnet.json` if that is still the projection).

## Contract keys (`contracts.<key>.current`)

| Key | Source wasm | Notes |
| --- | --- | --- |
| `merkle-campaign-root` | `nocturne-identity-rewards` `merkle_campaign_root.wasm` | Init `init_owner` |
| `consensus-identity-achievement` | `consensus_identity_achievement.wasm` | Init `init_token`; `set_registry` |
| `tnst-identity-airdrop` | `tnst_identity_airdrop.wasm` | Init `init_owner`; `set_registry`; `set_tnst` |
| `tnst-token` | `tnst-token` `tnst_token.wasm` | **Keep existing pin** until 6a bytecode redeploy. New id only after faucet retarget |

Fill per current entry (same shape as existing pins):

- `version`
- `contract_id` (64 hex)
- `tx_id`
- `wasm_sha256`
- `deploy_nonce`
- `address` (deployer moonlight)
- `deployed_at`
- `wasm_path` / `dd_wasm_path` — omit or null in the public record

## Wiring keys (`wiring.<contract>.<fn>`)

Record after successful calls:

| Wiring key | Args |
| --- | --- |
| `consensus-identity-achievement.set_registry` | merkle-campaign-root id |
| `tnst-identity-airdrop.set_registry` | merkle-campaign-root id |
| `tnst-identity-airdrop.set_tnst` | tnst-token id (current or post-6a) |
| `tnst-token.grant_minter` | `Principal` `{kind: Contract, bytes: airdrop id}` — **only after 6a redeploy** |
| `merkle-campaign-root.set_root` | campaign_id + root (publisher script) |
| `merkle-campaign-root.set_publisher` | optional |

## Out of this template

No sample hex ids. No `REPLACE_*` filled with dummy 32-byte values in `testnet.json`.
