//! Typed Dusk network data: chain IDs, endpoints, Dusk contract IDs, DuskEVM, routes,
//! labels and blocklist, one [`Network`] per Dusk network.
//!
//! The data is Dusk's [`@dusk-network/networks`](https://github.com/dusk-network/networks)
//! package at version [`DUSK_NETWORKS_VERSION`], vendored under `upstream/dusk-networks/`
//! in this repo (MPL-2.0, see the `LICENSE` there) and embedded at build time. Its
//! provenance and per-file sha256 are in `upstream/dusk-networks.lock.json`; a test
//! recomputes the hashes of the embedded files against that lock.
//!
//! ```
//! let testnet = dusk_networks::by_chain_id(2).unwrap();
//! assert_eq!(testnet.name, "testnet");
//! assert_eq!(testnet.label_for(&testnet.contracts.transfer), Some("Transfer contract"));
//! ```
//!
//! # Sozu
//!
//! [`Sozu::fallbacks`] are the contracts Sozu's hub named when the snapshot was taken.
//! The hub's guardian can name others at any time: read them from the hub
//! ([`Sozu::hub`]) and use the fallbacks only until it answers.
//!
//! # Consuming
//!
//! Depend on this crate by git tag of `nocturne-deployments` (tags
//! `nocturne-deployments-vX.Y.Z`, covering every crate in the workspace). It is not
//! published to crates.io: the embedded files live outside the crate directory.
//!
//! # Parsing
//!
//! Typed fields are required and must have the documented type. Fields this crate does
//! not type are ignored, so a newer upstream file that only adds keys still parses.

pub mod names;

use std::collections::BTreeMap;
use std::sync::OnceLock;

use serde::Deserialize;

/// Version of `@dusk-network/networks` the embedded snapshot is (the lock's `version`).
pub const DUSK_NETWORKS_VERSION: &str = "0.3.0";
/// Upstream commit of that version (the lock's `commit`).
pub const DUSK_NETWORKS_COMMIT: &str = "0bebc7799b99bba1f32e9dc6c24b4e4994bc864a";

/// `upstream/dusk-networks/mainnet.json`, byte for byte.
pub const MAINNET_JSON: &str = include_str!("../../../upstream/dusk-networks/mainnet.json");
/// `upstream/dusk-networks/testnet.json`, byte for byte.
pub const TESTNET_JSON: &str = include_str!("../../../upstream/dusk-networks/testnet.json");
/// `upstream/dusk-networks/devnet.json`, byte for byte.
pub const DEVNET_JSON: &str = include_str!("../../../upstream/dusk-networks/devnet.json");
/// `upstream/dusk-networks/schema.json`: the JSON schema of one network file.
pub const SCHEMA_JSON: &str = include_str!("../../../upstream/dusk-networks/schema.json");
/// `upstream/dusk-networks/fixtures/sozu-vectors.json`: the Sozu test vectors both Dusk
/// wallets run, for parity tests of Sozu math.
pub const SOZU_VECTORS_JSON: &str =
    include_str!("../../../upstream/dusk-networks/fixtures/sozu-vectors.json");

/// The embedded network files, in chain ID order.
const EMBEDDED: [(&str, &str); 3] = [
    ("mainnet.json", MAINNET_JSON),
    ("testnet.json", TESTNET_JSON),
    ("devnet.json", DEVNET_JSON),
];

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("parsing Dusk network {file}: {source}")]
    Parse {
        file: String,
        #[source]
        source: serde_json::Error,
    },
}

/// What the Dusk apps need to know about one network (`schema.json`).
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Network {
    /// The chain ID the node gives in `/on/node/info`: 0 local, 1 mainnet, 2 testnet, 3 devnet.
    pub chain_id: u64,
    /// `local`, `mainnet`, `testnet` or `devnet`.
    pub name: String,
    /// The network's name in the apps.
    pub label: String,
    pub urls: Urls,
    pub apps: Apps,
    pub contracts: Contracts,
    /// `None` where Sozu is not deployed.
    pub sozu: Option<Sozu>,
    /// `None` where Dusk Domains is not published.
    pub dusk_domains: Option<DuskDomains>,
    /// `None` where DuskEVM is not live.
    pub dusk_evm: Option<DuskEvm>,
    pub routes: Routes,
    pub operator: Operator,
    pub display: Display,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Urls {
    pub node: String,
    pub archive: String,
    pub prover: String,
}

/// Where each app is published; `None` where it is not yet.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Apps {
    pub wallet: Option<String>,
    pub explorer: Option<String>,
    pub bridge: Option<String>,
}

/// Dusk contract IDs: lower-case hex, no `0x`.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Contracts {
    pub transfer: String,
    pub stake: String,
    /// `None` where Multicall is not deployed.
    pub multicall: Option<String>,
}

/// Sozu's hub, and the contracts it named when the snapshot was taken.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Sozu {
    /// The hub contract: the authority for the other Sozu contract IDs.
    pub hub: String,
    /// Fallbacks only. The hub's guardian can name other contracts at any time: read
    /// them from [`Sozu::hub`] and use these only until it answers.
    pub fallbacks: SozuFallbacks,
}

/// Sozu contract IDs as the hub named them when the snapshot was taken. Not
/// authoritative: read the live IDs from the hub.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SozuFallbacks {
    pub pool: String,
    pub relayer: String,
    pub st_dusk: String,
    pub substrate: String,
    pub vault: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct DuskDomains {
    pub site: String,
    pub manifest: DuskDomainsManifest,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct DuskDomainsManifest {
    /// The release's source commit.
    pub version: String,
    pub url: String,
    pub sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Currency {
    pub name: String,
    pub symbol: String,
    pub decimals: u8,
}

/// DuskEVM, the EVM chain that settles on this network.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DuskEvm {
    /// The chain's name as wallets list it.
    pub name: String,
    /// The EVM chain ID.
    pub chain_id: u64,
    pub currency: Currency,
    pub rpc_url: String,
    /// The DuskEVM adapter's RPC beside the chain's own.
    pub l1_adapter_url: String,
    pub explorer_url: String,
    pub blockscout_api_url: String,
    pub predeploys: Predeploys,
    /// Every DuskEVM contract on Dusk (the L1 side), by its name in duskevm-genesis.
    pub contracts: BTreeMap<String, L1Contract>,
    /// Data drivers the apps bundle, pinned by sha256. Untyped.
    pub drivers: serde_json::Value,
    /// The duskevm-genesis files this part was generated from. Untyped.
    pub source: serde_json::Value,
}

impl DuskEvm {
    /// A DuskEVM contract on Dusk by its duskevm-genesis name, e.g. `l1_cross_domain_messenger`.
    pub fn l1_contract(&self, name: &str) -> Option<&L1Contract> {
        self.contracts.get(name)
    }
}

/// EIP-55 checksummed addresses of the OP Stack predeploys on DuskEVM.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Predeploys {
    pub l1_block: String,
    pub l2_cross_domain_messenger: String,
    pub l2_standard_bridge: String,
    pub l2_to_l1_message_passer: String,
}

/// A DuskEVM contract deployed on Dusk.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct L1Contract {
    /// Dusk contract ID, lower-case hex.
    pub contract_id: String,
    /// The EVM address it answers to, EIP-55 checksummed.
    pub evm_address: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Routes {
    /// The EVM chains the routes reach, by ID (`ethereum`, `bsc`, ...).
    pub chains: BTreeMap<String, EvmChain>,
    pub migrations: Vec<Migration>,
    pub services: Vec<Service>,
    pub dusk_evm: Vec<DuskEvmRoute>,
    pub retired: Vec<Retired>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EvmChain {
    pub chain_id: u64,
    pub name: String,
    pub currency: Currency,
    pub rpc_url: String,
    pub explorer_url: String,
    /// The DUSK token on this chain.
    pub token: String,
    pub token_label: String,
}

/// DUSK moved from an EVM chain into Dusk.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Migration {
    pub id: String,
    pub chain: String,
    pub contract: String,
    pub payout_account: Option<String>,
    pub typical_seconds: Option<u64>,
    pub running: bool,
}

/// DUSK sent from Dusk to an EVM chain.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Service {
    pub id: String,
    pub chain: String,
    pub account: String,
    pub payout_address: Option<String>,
    /// Lux, as a decimal string.
    pub fee_lux: String,
    pub typical_seconds: Option<u64>,
    pub running: bool,
}

/// Deposits to DuskEVM and withdrawals from it.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DuskEvmRoute {
    pub id: String,
    /// `deposit` or `withdrawal`.
    pub direction: String,
    pub typical_seconds: Option<u64>,
    pub running: bool,
}

/// A migration contract no longer offered.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Retired {
    pub chain: String,
    pub contract: String,
    pub token: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Operator {
    pub name: String,
    pub terms_url: String,
    pub privacy_url: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Display {
    /// Names of contracts (by ID) and accounts (by base58 key).
    pub labels: BTreeMap<String, String>,
    /// Accounts the apps do not send to.
    pub blocklist: Vec<Blocked>,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Blocked {
    /// Base58 public account.
    pub account: String,
    pub reason: BlockReason,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BlockReason {
    Compromised,
    Scam,
    /// A reason added upstream after this crate was written.
    #[serde(other)]
    Other,
}

impl Network {
    /// Parse one network file (`schema.json` shape).
    pub fn from_json(file: &str, text: &str) -> Result<Self, Error> {
        serde_json::from_str(text).map_err(|source| Error::Parse {
            file: file.to_string(),
            source,
        })
    }

    /// The display name of a contract (hex ID) or account (base58 key), if Dusk names it.
    pub fn label_for(&self, id_or_account: &str) -> Option<&str> {
        self.display.labels.get(id_or_account).map(String::as_str)
    }

    /// True when the apps must not send to `account`.
    pub fn is_blocked(&self, account: &str) -> bool {
        self.blocked(account).is_some()
    }

    /// The blocklist entry for `account`, if any.
    pub fn blocked(&self, account: &str) -> Option<&Blocked> {
        self.display.blocklist.iter().find(|b| b.account == account)
    }
}

/// Every embedded network, in chain ID order.
///
/// The embedded files are parsed once. They are fixed at build time and covered by this
/// crate's tests, so a parse failure is a build of a broken snapshot and panics.
pub fn all() -> &'static [Network] {
    static NETWORKS: OnceLock<Vec<Network>> = OnceLock::new();
    NETWORKS.get_or_init(|| {
        EMBEDDED
            .iter()
            .map(|(file, text)| {
                Network::from_json(file, text).unwrap_or_else(|e| panic!("embedded snapshot: {e}"))
            })
            .collect()
    })
}

/// The network with this chain ID (1 mainnet, 2 testnet, 3 devnet).
pub fn by_chain_id(chain_id: u64) -> Option<&'static Network> {
    all().iter().find(|n| n.chain_id == chain_id)
}

/// The network with this name (`mainnet`, `testnet`, `devnet`).
pub fn by_name(name: &str) -> Option<&'static Network> {
    all().iter().find(|n| n.name == name)
}
