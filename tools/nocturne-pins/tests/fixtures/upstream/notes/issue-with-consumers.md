Package `@dusk-network/networks` `0.3.0` (tag `v0.3.0`, commit `d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1`) compared with the locked `0.2.0`.

| Class | Meaning | Changes | When consumers update |
|---|---|---|---|
| S | safety | 1 | within 48 h, before any deploy |
| A | API or schema | 6 | opt in; never forced |

## Consumers

- [ ] `o/wallet` `package.json` (npm, immediate): S; within 48 h, before any deploy. Uses `routes.services[].payoutAddress`
- [ ] `o/bridge` `package.json` (npm, immediate): S, A; within 48 h, before any deploy. Uses `package:exports["./account"]`, `package:exports["./gas"]`, `routes.services[].payoutAddress`

## Changes

| Class | File | Path | Old | New | Summary |
|---|---|---|---|---|---|
| S | `networks/mainnet.json` | `routes.services[dusk-to-bsc].payoutAddress` | `null` | `0xe6D0c4d8CA2C12b357A5b5470c53E86123Ab16da` | `added: bridge route: account, payout address, fee or running state` |
| A | `package.json` | `description` | `Dusk's networks for the Dusk apps: …red formats.` | `Dusk's networks for the Dusk apps: …red formats.` | `changed: package metadata` |
| A | `package.json` | `exports["./account"]` | `null` | `{"types": "./src/account.d.ts", "de…account.js"}` | `added: exported entry point` |
| A | `package.json` | `exports["./gas"]` | `null` | `{"types": "./src/gas.d.ts", "defaul…src/gas.js"}` | `added: exported entry point` |
| A | `package.json` | `version` | `0.2.0` | `0.3.0` | `changed: 0.x minor version bump (may break the API)` |
| A | `schema.json` | `properties.routes.properties.services.items.properties.payoutAddress` | `null` | `{"description": "The address on the…mAddress"}]}` | `added: new key payoutAddress` |
| A | `schema.json` | `properties.routes.properties.services.items.required` | `["id", "chain", "account", "feeLux"…, "running"]` | `["id", "chain", "account", "payoutA…, "running"]` | `changed: required keys +"payoutAddress"` |

Not compared:

- JavaScript implementation (src/*.js): not compared, only its declared API (.d.ts)
- CHANGELOG.md: quoted for people, never used to classify
- type declarations (*.d.ts): not in both releases' files

## Verification

- verified: tarball sha512 equals npm dist.integrity
- verified: package.json name and version match
- verified: SLSA provenance subject is this tarball (sha512) and package@version
- verified: provenance binds https://github.com/dusk-network/networks, .github/workflows/publish.yml, refs/tags/v0.3.0, commit d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1, GitHub-hosted runner
- verified: signing certificate names the same workflow identity (byte search, not parsed)
- verified: git ls-remote refs/tags/v0.3.0 equals the provenance commit
- verified: byte-equal to GitHub at the commit: networks/mainnet.json, networks/testnet.json, networks/devnet.json, schema.json, fixtures/sozu-vectors.json, LICENSE, package.json
- **not verified**: Sigstore bundle: DSSE signature, Fulcio certificate chain and Rekor inclusion proof (needs X.509 and ECDSA; the tool is standard library only)
- **not verified**: npm registry signatures (npm audit signatures)
- **not verified**: JSR files equal npm (JSR is not read)

Tarball sha256 `0000000000000000000000000000000000000000000000000000000000000000`, integrity `sha512-pinned`.
Rekor log index `42` (recorded, not checked offline).

## Upstream CHANGELOG

Quoted from the package as data. Classes above come from the diff, not from this text.

```text
## [0.3.0]

### Added

- `/duskevm`: `decodeBridgeCall`, what a deposit to DuskEVM or a withdrawal's proof or claim does, from its arguments
- `/duskevm`: `decodeWithdrawal` and `duskRecipientOf`, a withdrawal as DuskEVM recorded it and whom it pays on Dusk
- `/duskevm`: `bridgeEnvelopeOk`, whether a bridge call is sent as the bridge's route expects
- `/duskevm`: `encodeDepositArgs` and `depositCall`, a deposit's arguments
- `/duskevm`: `duskEvmContracts` and `isBridgeContract`, DuskEVM's contracts on Dusk
- `/duskevm`: `evmAddressOf`, the EVM address DuskEVM gives a Dusk contract or account
- `/duskevm`: a withdrawal's claim date in words
- `/account`: Dusk accounts checked as keys, to and from their raw keys, and base58
- `/sozu`: `isSozuCall`, whether a call is a stake or an unstake with Sozu
- `/gas`: gas for public transfers, DuskEVM deposits and withdrawals, beside Sozu's
- `/format`: `aboutTime`, `aboutWords` and `durationWords`
- `routeForAccount`: the route a transfer with one of the bridge's accounts is on
- `routes.services[].payoutAddress`: the address that pays a route out, for BSC on mainnet
```
