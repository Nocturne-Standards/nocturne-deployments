# Changelog

All notable changes to this package are listed here. It follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

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

## [0.2.0]

### Added

- `/duskevm`: when a DuskEVM withdrawal can be claimed, whether it can be claimed now, and which dispute games a proof can go against
- `duskEvm.l1AdapterUrl`: the DuskEVM adapter's RPC beside DuskEVM's own

## [0.1.0]

### Added

- Network files for mainnet, testnet and devnet, with a JSON Schema
- Node, archive and prover URLs, the apps' addresses, and the transfer, stake and Multicall contracts
- Sozu's hub, with fallbacks for its pool, relayer, substrate, stDUSK and vault
- A pointer to Dusk Domains' release manifest: version, URL and sha256
- DuskEVM on testnet: its chain, endpoints, predeploys, drivers and every L1 contract, from duskevm-genesis
- Bridge routes: BSC ↔ Dusk, Ethereum → Dusk and Dusk ↔ DuskEVM, with their chains, fees, times and whether each runs
- The retired testnet Sepolia migration contracts
- Dusk as the operator, with its terms and privacy notice
- Labels for every contract and account in the files, and the recipient blocklist
- `networkFor`, `labelFor`, `isBlocked`, `blockedReason`, `routes` and `bridgeLink`
- `/sozu`: Sozu's fee, stDUSK, minimum, unstake and gas rules, and its reads in one query
- `/rates`: native and Sozu reward-rate estimates
- `/format`: amounts, times, contract IDs, accounts and EVM addresses
- Published to npm as `@dusk-network/networks` and to JSR as `@dusk/networks`
