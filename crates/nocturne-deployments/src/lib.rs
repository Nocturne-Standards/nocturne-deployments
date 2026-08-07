//! Thin reader for nocturne `deployments/testnet.json` pin files.

use std::collections::HashMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

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

/// One recorded deployment (current or history row).
#[derive(Debug, Clone, Deserialize)]
pub struct DeploymentEntry {
    pub contract_id: String,
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
    pub deployed_at: Option<String>,
}

/// Per-contract pin: live `current` plus optional history.
#[derive(Debug, Clone, Deserialize)]
pub struct ContractRecord {
    pub current: DeploymentEntry,
    #[serde(default)]
    pub history: Vec<DeploymentEntry>,
}

/// Full pin file (`testnet.json` object keyed by contract name).
#[derive(Debug, Clone, Deserialize)]
pub struct DeploymentsFile {
    #[serde(flatten)]
    pub contracts: HashMap<String, ContractRecord>,
    #[serde(skip)]
    path: PathBuf,
}

impl DeploymentsFile {
    /// Path this file was loaded from.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// `current` entry for `key`, or error.
    pub fn current(&self, key: &str) -> Result<&DeploymentEntry> {
        self.contracts
            .get(key)
            .map(|r| &r.current)
            .ok_or_else(|| Error::MissingContractId {
                key: key.to_string(),
                path: self.path.clone(),
            })
    }

    /// `current.contract_id` for `key`.
    pub fn contract_id(&self, key: &str) -> Result<&str> {
        Ok(self.current(key)?.contract_id.as_str())
    }
}

/// Resolve pin file path (does not read).
///
/// Order: `NOCTURNE_DEPLOYMENTS` → walk-up pin dirs → sibling pin dirs.
pub fn resolve_path(start: &Path) -> Result<PathBuf> {
    if let Ok(raw) = env::var("NOCTURNE_DEPLOYMENTS") {
        let p = PathBuf::from(raw);
        if p.is_dir() {
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

/// Load pin file from an explicit path.
pub fn load(path: impl AsRef<Path>) -> Result<DeploymentsFile> {
    let path = path.as_ref().to_path_buf();
    let raw = fs::read_to_string(&path).map_err(|source| Error::Io {
        path: path.clone(),
        source,
    })?;
    let mut file: DeploymentsFile = serde_json::from_str(&raw).map_err(|source| Error::Parse {
        path: path.clone(),
        source,
    })?;
    file.path = path;
    Ok(file)
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
}
