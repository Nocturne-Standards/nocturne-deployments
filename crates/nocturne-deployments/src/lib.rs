//! Thin reader for nocturne pin files (flat `testnet.json` and enveloped layouts).

use std::collections::HashMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use serde::de::Error as _;
use serde::Deserialize;

/// Errors loading or querying a deployments pin file.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("deployments file not found (set NOCTURNE_DEPLOYMENTS or place deployments/testnet.json)")]
    NotFound,
    #[error("reading {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("parsing {path}: {source}")]
    Parse {
        path: PathBuf,
        #[source]
        source: serde_json::Error,
    },
    #[error("no `{key}.current.contract_id` in {path}")]
    MissingContractId { key: String, path: PathBuf },
}

pub type Result<T> = std::result::Result<T, Error>;

/// Catalog of pin files under a repo root (`index.json`).
#[derive(Debug, Clone, Deserialize)]
pub struct PinIndex {
    pub schema: String,
    pub files: Vec<PinIndexEntry>,
}

/// One pin file entry in the catalog.
#[derive(Debug, Clone, Deserialize)]
pub struct PinIndexEntry {
    pub layer: String,
    pub network: String,
    pub path: String,
    #[serde(default)]
    pub chain_id: Option<u64>,
    #[serde(default)]
    pub public: bool,
}

/// One recorded deployment (current or history row).
#[derive(Debug, Clone, Deserialize)]
pub struct DeploymentEntry {
    #[serde(default)]
    pub contract_id: Option<String>,
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default)]
    pub tx_id: Option<String>,
    #[serde(default)]
    pub wasm_path: Option<String>,
    #[serde(default)]
    pub dd_wasm_path: Option<String>,
    #[serde(default)]
    pub wasm_sha256: Option<String>,
    #[serde(default)]
    pub deploy_nonce: Option<u64>,
    #[serde(default)]
    pub address: Option<String>,
    #[serde(default)]
    pub proxy: Option<String>,
    #[serde(default)]
    pub implementation: Option<String>,
    #[serde(default)]
    pub deployed_at: Option<String>,
}

/// Per-contract pin: live `current` plus optional history.
#[derive(Debug, Clone, Deserialize)]
pub struct ContractRecord {
    pub current: DeploymentEntry,
    #[serde(default)]
    pub history: Vec<DeploymentEntry>,
}

/// Full pin file (contracts keyed by name, optional envelope metadata).
#[derive(Debug, Clone)]
pub struct DeploymentsFile {
    /// Ops `wire-contract.sh` records; ignored by pin lookups.
    #[allow(dead_code)]
    wiring: Option<serde_json::Value>,
    aliases: HashMap<String, String>,
    pub contracts: HashMap<String, ContractRecord>,
    path: PathBuf,
}

const ENVELOPE_KEYS: &[&str] = &[
    "schema", "layer", "network", "rpc", "explorer", "aliases", "wiring", "chain_id", "contracts",
];

fn is_envelope_key(key: &str) -> bool {
    ENVELOPE_KEYS.contains(&key)
}

fn parse_deployments_value(
    value: serde_json::Value,
) -> std::result::Result<
    (
        HashMap<String, ContractRecord>,
        HashMap<String, String>,
        Option<serde_json::Value>,
    ),
    serde_json::Error,
> {
    let obj = value
        .as_object()
        .ok_or_else(|| serde_json::Error::custom("expected JSON object"))?;

    let aliases: HashMap<String, String> = match obj.get("aliases") {
        Some(v) => serde_json::from_value(v.clone())?,
        None => HashMap::new(),
    };

    let wiring = obj.get("wiring").cloned();

    let contracts = if let Some(contracts_val) = obj.get("contracts") {
        serde_json::from_value(contracts_val.clone())?
    } else {
        let mut contracts: HashMap<String, ContractRecord> = HashMap::new();
        for (key, val) in obj {
            if !is_envelope_key(key) {
                contracts.insert(key.clone(), serde_json::from_value(val.clone())?);
            }
        }
        contracts
    };

    Ok((contracts, aliases, wiring))
}

impl DeploymentsFile {
    /// Path this file was loaded from.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Canonical contract name for `key` (resolves aliases, no chains).
    pub fn resolve_key(&self, key: &str) -> Result<&str> {
        let canonical = self.aliases.get(key).map(|s| s.as_str()).unwrap_or(key);
        self.contracts
            .get_key_value(canonical)
            .map(|(name, _)| name.as_str())
            .ok_or_else(|| Error::MissingContractId {
                key: key.to_string(),
                path: self.path.clone(),
            })
    }

    /// `current` entry for `key`, or error.
    pub fn current(&self, key: &str) -> Result<&DeploymentEntry> {
        let canonical = self.resolve_key(key)?;
        Ok(&self.contracts.get(canonical).unwrap().current)
    }

    /// Native `contract_id`, else `address`, else `proxy`.
    pub fn contract_id(&self, key: &str) -> Result<&str> {
        let entry = self.current(key)?;
        entry
            .contract_id
            .as_deref()
            .or(entry.address.as_deref())
            .or(entry.proxy.as_deref())
            .ok_or_else(|| Error::MissingContractId {
                key: key.to_string(),
                path: self.path.clone(),
            })
    }
}

/// Resolve pin file path for a layer/network (does not read).
fn resolve_layer_path(root: &Path, layer: &str, network: &str) -> Result<PathBuf> {
    let index_path = root.join("index.json");
    if index_path.is_file() {
        if let Ok(index) = load_index(root) {
            if let Some(entry) = index
                .files
                .iter()
                .find(|e| e.layer == layer && e.network == network)
            {
                let path = root.join(&entry.path);
                if path.is_file() {
                    return Ok(path);
                }
            }
        }
    }

    let layered = root.join(layer).join(format!("{network}.json"));
    if layered.is_file() {
        return Ok(layered);
    }

    let fallback = root.join("testnet.json");
    if fallback.is_file() {
        return Ok(fallback);
    }

    Err(Error::NotFound)
}

/// Resolve pin file path (does not read).
///
/// Order: `NOCTURNE_DEPLOYMENTS` → walk-up pin dirs → sibling pin dirs.
pub fn resolve_path(start: &Path) -> Result<PathBuf> {
    if let Ok(raw) = env::var("NOCTURNE_DEPLOYMENTS") {
        let p = PathBuf::from(raw);
        if p.is_dir() {
            if p.join("index.json").is_file() {
                return resolve_layer_path(&p, "duskds", "testnet");
            }
            let file = p.join("testnet.json");
            if file.is_file() {
                return Ok(file);
            }
        } else if p.is_file() {
            return Ok(p);
        }
        return Err(Error::NotFound);
    }

    const PIN_RELS: &[&str] = &[
        "deployments/testnet.json",
        "nocturne-deployments/testnet.json",
    ];
    for dir in start.ancestors() {
        for rel in PIN_RELS {
            let nested = dir.join(rel);
            if nested.is_file() {
                return Ok(nested);
            }
        }
        if let Some(parent) = dir.parent() {
            for rel in PIN_RELS {
                let sibling = parent.join(rel);
                if sibling.is_file() {
                    return Ok(sibling);
                }
            }
        }
    }
    Err(Error::NotFound)
}

/// Load pin catalog from `{root}/index.json`.
pub fn load_index(root: impl AsRef<Path>) -> Result<PinIndex> {
    let root = root.as_ref();
    let path = root.join("index.json");
    let raw = fs::read_to_string(&path).map_err(|source| Error::Io {
        path: path.clone(),
        source,
    })?;
    serde_json::from_str(&raw).map_err(|source| Error::Parse { path, source })
}

/// Load pin file for `layer`/`network` under `root`.
pub fn load_layer(
    root: impl AsRef<Path>,
    layer: &str,
    network: &str,
) -> Result<DeploymentsFile> {
    load(resolve_layer_path(root.as_ref(), layer, network)?)
}

/// Load pin file from an explicit path.
pub fn load(path: impl AsRef<Path>) -> Result<DeploymentsFile> {
    let path = path.as_ref().to_path_buf();
    let raw = fs::read_to_string(&path).map_err(|source| Error::Io {
        path: path.clone(),
        source,
    })?;
    let value: serde_json::Value = serde_json::from_str(&raw).map_err(|source| Error::Parse {
        path: path.clone(),
        source,
    })?;
    let (contracts, aliases, wiring) =
        parse_deployments_value(value).map_err(|source| Error::Parse {
            path: path.clone(),
            source,
        })?;
    Ok(DeploymentsFile {
        contracts,
        aliases,
        wiring,
        path,
    })
}

/// Resolve then load, walking from `start`.
pub fn load_from(start: &Path) -> Result<DeploymentsFile> {
    load(resolve_path(start)?)
}

/// Resolve then load, walking from the current process directory.
pub fn load_default() -> Result<DeploymentsFile> {
    let cwd = env::current_dir().map_err(|source| Error::Io {
        path: PathBuf::from("."),
        source,
    })?;
    load_from(&cwd)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn loads_contract_id() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("testnet.json");
        fs::write(
            &path,
            r#"{
              "knot-registry": {
                "current": {
                  "version": "0.1.5",
                  "contract_id": "abc123"
                },
                "history": []
              }
            }"#,
        )
        .unwrap();
        let file = load(&path).unwrap();
        assert_eq!(file.contract_id("knot-registry").unwrap(), "abc123");
        assert!(file.contract_id("missing").is_err());
    }

    #[test]
    fn resolve_sibling_deployments() {
        let _g = ENV_LOCK.lock().unwrap();
        // SAFETY: serialized by ENV_LOCK; test-only
        unsafe { env::remove_var("NOCTURNE_DEPLOYMENTS") };

        let root = tempfile::tempdir().unwrap();
        let pins = root.path().join("deployments");
        fs::create_dir_all(&pins).unwrap();
        fs::write(
            pins.join("testnet.json"),
            r#"{"k":{"current":{"contract_id":"id1"}}}"#,
        )
        .unwrap();
        let product = root.path().join("knot").join("crates").join("tool");
        fs::create_dir_all(&product).unwrap();

        let resolved = resolve_path(&product).unwrap();
        assert_eq!(resolved, pins.join("testnet.json"));
        assert_eq!(load_from(&product).unwrap().contract_id("k").unwrap(), "id1");
    }

    #[test]
    fn resolve_sibling_nocturne_deployments() {
        let _g = ENV_LOCK.lock().unwrap();
        unsafe { env::remove_var("NOCTURNE_DEPLOYMENTS") };

        let root = tempfile::tempdir().unwrap();
        let pins = root.path().join("nocturne-deployments");
        fs::create_dir_all(&pins).unwrap();
        fs::write(
            pins.join("testnet.json"),
            r#"{"k":{"current":{"contract_id":"id2"}}}"#,
        )
        .unwrap();
        let product = root.path().join("knot").join("crates").join("tool");
        fs::create_dir_all(&product).unwrap();

        let resolved = resolve_path(&product).unwrap();
        assert_eq!(resolved, pins.join("testnet.json"));
        assert_eq!(load_from(&product).unwrap().contract_id("k").unwrap(), "id2");
    }

    #[test]
    fn env_overrides_walk() {
        let _g = ENV_LOCK.lock().unwrap();
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("pins.json");
        fs::write(
            &path,
            r#"{"x":{"current":{"contract_id":"from-env"}}}"#,
        )
        .unwrap();
        // SAFETY: serialized by ENV_LOCK; test-only
        unsafe { env::set_var("NOCTURNE_DEPLOYMENTS", &path) };
        let got = load_default().unwrap();
        unsafe { env::remove_var("NOCTURNE_DEPLOYMENTS") };
        assert_eq!(got.contract_id("x").unwrap(), "from-env");
    }

    #[test]
    fn alias_multisig_registry() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("duskds.json");
        fs::write(
            &path,
            r#"{
              "schema": "nocturne.pins.v1",
              "aliases": { "multisig-registry": "knot-registry" },
              "contracts": {
                "knot-registry": { "current": { "contract_id": "abc123" } }
              }
            }"#,
        )
        .unwrap();
        let file = load(&path).unwrap();
        assert_eq!(file.contract_id("knot-registry").unwrap(), "abc123");
        assert_eq!(file.contract_id("multisig-registry").unwrap(), "abc123");
        assert_eq!(
            file.resolve_key("multisig-registry").unwrap(),
            "knot-registry"
        );
    }

    #[test]
    fn legacy_flat_root_still_loads() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("testnet.json");
        fs::write(
            &path,
            r#"{"knot-registry":{"current":{"contract_id":"id1"}}}"#,
        )
        .unwrap();
        assert_eq!(
            load(&path).unwrap().contract_id("knot-registry").unwrap(),
            "id1"
        );
    }

    #[test]
    fn evm_proxy_as_contract_id() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("evm.json");
        fs::write(
            &path,
            r#"{
              "contracts": {
                "loan-register": {
                  "current": {
                    "proxy": "0xD624d003221446D8a5D4B4AA9b8DAAFE50227858",
                    "implementation": "0x66665c5f2f41122E3E3C51da8b614E55B36B98B2",
                    "address": "0xD624d003221446D8a5D4B4AA9b8DAAFE50227858"
                  }
                }
              }
            }"#,
        )
        .unwrap();
        assert_eq!(
            load(&path).unwrap().contract_id("loan-register").unwrap(),
            "0xD624d003221446D8a5D4B4AA9b8DAAFE50227858"
        );
    }

    #[test]
    fn load_layer_falls_back_to_root_testnet_json() {
        let root = tempfile::tempdir().unwrap();
        fs::write(
            root.path().join("testnet.json"),
            r#"{"knot-registry":{"current":{"contract_id":"from-root"}}}"#,
        )
        .unwrap();
        let file = load_layer(root.path(), "duskds", "testnet").unwrap();
        assert_eq!(file.contract_id("knot-registry").unwrap(), "from-root");
    }
}
