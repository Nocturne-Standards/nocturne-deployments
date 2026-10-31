//! pins-v2 reader: `index.json` (`nocturne.index.v1`) and per-product current files
//! (`nocturne.pins.v2`). Read-only; writes go through the `nocturne-pins` tool.
//!
//! Full name of a contract: `product/name/network/tier`. A reader states its tier:
//! [`Reader::Own`] reads `own`, then `public`; [`Reader::Public`] reads `public` only.
//! With several repos, the first one holding an entry wins (private shadows public).

use std::collections::HashMap;
use std::env;
use std::fmt;
use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::{Error, Result};

pub const INDEX_SCHEMA: &str = "nocturne.index.v1";
pub const CURRENT_SCHEMA: &str = "nocturne.pins.v2";
/// Checkout names the walk-up looks for, private first.
pub const SIBLING_NAMES: &[&str] = &["nocturne-deployments-internal", "nocturne-deployments"];
pub const ENV_INDEXES: &str = "NOCTURNE_PINS";

/// Which tiers a reader may see.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reader {
    /// Our tools and CI: `own`, then `public`.
    Own,
    /// Docs, website, anyone else: `public` only.
    Public,
}

impl Reader {
    pub fn tiers(self) -> &'static [&'static str] {
        match self {
            Reader::Own => &["own", "public"],
            Reader::Public => &["public"],
        }
    }
}

/// `product/name/network/tier`; `tier` `None` means "the reader's tier order".
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FullName {
    pub product: String,
    pub name: String,
    pub network: String,
    pub tier: Option<String>,
}

impl FullName {
    /// Parse `product/name[/network[/tier]]`; network defaults to `testnet`.
    pub fn parse(text: &str) -> std::result::Result<Self, String> {
        let parts: Vec<&str> = text.trim().split('/').collect();
        if parts.len() < 2 || parts.len() > 4 || parts.iter().any(|p| p.is_empty()) {
            return Err(format!("not a full name: {text:?} (want product/name/network/tier)"));
        }
        let tier = parts.get(3).map(|t| t.to_string()).filter(|t| t != "*");
        if let Some(t) = &tier {
            if t != "own" && t != "public" {
                return Err(format!("tier {t:?} is not pinned (own or public)"));
            }
        }
        Ok(Self {
            product: parts[0].to_string(),
            name: parts[1].to_string(),
            network: parts.get(2).copied().unwrap_or("testnet").to_string(),
            tier,
        })
    }
}

impl fmt::Display for FullName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let tier = self.tier.as_deref().unwrap_or("*");
        write!(f, "{}/{}/{}/{}", self.product, self.name, self.network, tier)
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct Index {
    pub schema: String,
    pub repo: String,
    pub visibility: String,
    // An index may still carry a `networks` section; it is ignored. Dusk network data
    // comes from the vendored base: `Catalog::network`, `Catalog::network_by_chain_id`.
    pub entries: Vec<IndexEntry>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct IndexEntry {
    pub product: String,
    pub network: String,
    pub tier: String,
    pub current: String,
    pub history: String,
    #[serde(default)]
    pub shadows: Option<String>,
}

/// One live contract in a current file.
#[derive(Debug, Clone, Deserialize)]
pub struct Contract {
    pub layer: String,
    pub id: String,
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default)]
    pub tx: Option<String>,
    pub at: String,
    #[serde(default)]
    pub refs: HashMap<String, String>,
    /// The full v1-shaped row (wasm_sha256, source_commit, atlas_pin, ...).
    pub record: serde_json::Value,
}

#[derive(Debug, Clone, Deserialize)]
pub struct StateValue {
    pub value: serde_json::Value,
    pub since: Option<String>,
    pub at: String,
}

/// `products/<product>/<network>.<tier>.json`.
#[derive(Debug, Clone, Deserialize)]
pub struct Current {
    pub schema: String,
    pub product: String,
    pub network: String,
    pub tier: String,
    pub contracts: HashMap<String, Contract>,
    pub state: HashMap<String, StateValue>,
    pub events: u64,
    pub last_event: Option<String>,
}

/// One checkout.
#[derive(Debug, Clone)]
pub struct Repo {
    pub root: PathBuf,
    pub index: Index,
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T> {
    let raw = fs::read_to_string(path).map_err(|source| Error::Io {
        path: path.to_path_buf(),
        source,
    })?;
    serde_json::from_str(&raw).map_err(|source| Error::Parse {
        path: path.to_path_buf(),
        source,
    })
}

/// True when `dir/index.json` is a pins-v2 index.
pub fn is_v2_root(dir: &Path) -> bool {
    let Ok(raw) = fs::read_to_string(dir.join("index.json")) else {
        return false;
    };
    serde_json::from_str::<serde_json::Value>(&raw)
        .ok()
        .and_then(|v| v.get("schema").and_then(|s| s.as_str()).map(|s| s == INDEX_SCHEMA))
        .unwrap_or(false)
}

impl Repo {
    pub fn open(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref();
        let root = if root.file_name().is_some_and(|n| n == "index.json") {
            root.parent().unwrap_or(root)
        } else {
            root
        };
        let index: Index = read_json(&root.join("index.json"))?;
        if index.schema != INDEX_SCHEMA {
            return Err(Error::NotFound);
        }
        Ok(Self {
            root: root.to_path_buf(),
            index,
        })
    }

    pub fn is_public(&self) -> bool {
        self.index.visibility == "public"
    }

    pub fn entry(&self, product: &str, network: &str, tier: &str) -> Option<&IndexEntry> {
        self.index
            .entries
            .iter()
            .find(|e| e.product == product && e.network == network && e.tier == tier)
    }

    pub fn current(&self, entry: &IndexEntry) -> Result<Current> {
        read_json(&self.root.join(&entry.current))
    }
}

/// Several checkouts in precedence order.
#[derive(Debug, Clone)]
pub struct Catalog {
    pub repos: Vec<Repo>,
}

/// A resolved contract and where it came from.
#[derive(Debug, Clone)]
pub struct Found {
    pub full_name: FullName,
    pub repo: String,
    pub contract: Contract,
}

impl Catalog {
    pub fn open<P: AsRef<Path>>(roots: &[P]) -> Result<Self> {
        let mut repos = roots.iter().map(Repo::open).collect::<Result<Vec<_>>>()?;
        if repos.is_empty() {
            return Err(Error::NotFound);
        }
        // Private shadows public whatever order the roots were given in (stable sort).
        repos.sort_by_key(|r| r.is_public());
        Ok(Self { repos })
    }

    /// `$NOCTURNE_PINS` (path-separator list), else checkouts found walking up from `start`
    /// (the first directory level holding `nocturne-deployments-internal` / `nocturne-deployments`).
    pub fn discover(start: &Path) -> Result<Self> {
        if let Ok(raw) = env::var(ENV_INDEXES) {
            let roots: Vec<PathBuf> = env::split_paths(&raw).filter(|p| !p.as_os_str().is_empty()).collect();
            return Self::open(&roots);
        }
        let mut found: Vec<PathBuf> = Vec::new();
        for dir in start.ancestors() {
            if is_v2_root(dir) && !found.iter().any(|f| f == dir) {
                found.push(dir.to_path_buf());
            }
            let mut sibling_found = false;
            for name in SIBLING_NAMES {
                let candidate = dir.join(name);
                if is_v2_root(&candidate) {
                    sibling_found = true;
                    if !found.iter().any(|f| f == &candidate) {
                        found.push(candidate);
                    }
                }
            }
            if sibling_found {
                break;
            }
        }
        Self::open(&found)
    }

    /// Find a contract by full name; a missing tier follows the reader's order.
    pub fn find(&self, name: &FullName, reader: Reader) -> Result<Found> {
        if name.product == dusk_networks::names::PRODUCT {
            return self.find_dusk(name);
        }
        let tiers: Vec<&str> = match &name.tier {
            Some(t) if reader.tiers().contains(&t.as_str()) => vec![t.as_str()],
            Some(_) => vec![],
            None => reader.tiers().to_vec(),
        };
        for tier in tiers {
            for repo in &self.repos {
                let Some(entry) = repo.entry(&name.product, &name.network, tier) else {
                    continue;
                };
                let current = repo.current(entry)?;
                // The first repo holding the entry decides, even if it lacks the name.
                if let Some(contract) = current.contracts.get(&name.name) {
                    return Ok(Found {
                        full_name: FullName {
                            tier: Some(tier.to_string()),
                            ..name.clone()
                        },
                        repo: repo.index.repo.clone(),
                        contract: contract.clone(),
                    });
                }
                break;
            }
        }
        Err(Error::MissingContractId {
            key: name.to_string(),
            path: self.repos[0].root.clone(),
        })
    }

    /// A Dusk network from the vendored base (`upstream/dusk-networks/`, embedded in the
    /// `dusk-networks` crate), by name: `mainnet`, `testnet`, `devnet`.
    pub fn network(&self, name: &str) -> Option<&'static dusk_networks::Network> {
        dusk_networks::by_name(name)
    }

    /// A Dusk network from the vendored base by chain ID: 1 mainnet, 2 testnet, 3 devnet.
    pub fn network_by_chain_id(&self, chain_id: u64) -> Option<&'static dusk_networks::Network> {
        dusk_networks::by_chain_id(chain_id)
    }

    pub fn resolve(&self, name: &FullName, reader: Reader) -> Result<String> {
        Ok(self.find(name, reader)?.contract.id)
    }

    /// Find a contract by its bare name (`knot-registry`) in any product, own reader order.
    /// For callers that only know the old pin key.
    pub fn find_key(&self, key: &str, network: &str, reader: Reader) -> Result<Found> {
        for tier in reader.tiers() {
            let mut seen: Vec<(String, String)> = Vec::new();
            for repo in &self.repos {
                for entry in repo.index.entries.iter().filter(|e| e.network == network && e.tier == *tier) {
                    let ident = (entry.product.clone(), entry.tier.clone());
                    if seen.contains(&ident) {
                        continue;
                    }
                    seen.push(ident);
                    let current = repo.current(entry)?;
                    if let Some(contract) = current.contracts.get(key) {
                        return Ok(Found {
                            full_name: FullName {
                                product: entry.product.clone(),
                                name: key.to_string(),
                                network: network.to_string(),
                                tier: Some(entry.tier.clone()),
                            },
                            repo: repo.index.repo.clone(),
                            contract: contract.clone(),
                        });
                    }
                }
            }
        }
        Err(Error::MissingContractId {
            key: key.to_string(),
            path: self.repos[0].root.clone(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn checkout(dir: &Path, repo: &str, visibility: &str, product: &str, tier: &str, id: &str) {
        fs::create_dir_all(dir.join("products").join(product)).unwrap();
        let index = serde_json::json!({
            "schema": INDEX_SCHEMA, "repo": repo, "visibility": visibility, "networks": {},
            "entries": [{"product": product, "network": "testnet", "tier": tier,
                         "current": format!("products/{product}/testnet.{tier}.json"),
                         "history": format!("history/{product}/testnet.{tier}.jsonl")}]
        });
        fs::write(dir.join("index.json"), index.to_string()).unwrap();
        let current = serde_json::json!({
            "schema": CURRENT_SCHEMA, "product": product, "network": "testnet", "tier": tier,
            "contracts": {"atlas": {"layer": "duskds", "id": id, "version": "1.0.0", "tx": "t",
                                     "at": "2026-01-01T00:00:00Z", "record": {"contract_id": id}}},
            "state": {}, "events": 1, "last_event": null
        });
        fs::write(dir.join(format!("products/{product}/testnet.{tier}.json")), current.to_string()).unwrap();
    }

    #[test]
    fn parses_full_names() {
        let n = FullName::parse("knot/knot-registry").unwrap();
        assert_eq!(n.network, "testnet");
        assert_eq!(n.tier, None);
        assert!(FullName::parse("knot/x/testnet/experiment").is_err());
        assert!(FullName::parse("knot").is_err());
    }

    #[test]
    fn private_shadows_public_and_public_reader_sees_public_only() {
        let ws = tempfile::tempdir().unwrap();
        checkout(&ws.path().join("nocturne-deployments"), "o/pub", "public", "atlas", "own", "old");
        checkout(&ws.path().join("nocturne-deployments-internal"), "o/priv", "private", "atlas", "own", "new");
        let product = ws.path().join("knot").join("crates");
        fs::create_dir_all(&product).unwrap();
        // SAFETY: test-only; no other test in this module reads the variable.
        unsafe { env::remove_var(ENV_INDEXES) };
        let cat = Catalog::discover(&product).unwrap();
        let name = FullName::parse("atlas/atlas").unwrap();
        assert_eq!(cat.resolve(&name, Reader::Own).unwrap(), "new");
        assert!(cat.resolve(&name, Reader::Public).is_err());
        let public_only = Catalog::open(&[ws.path().join("nocturne-deployments")]).unwrap();
        assert_eq!(public_only.resolve(&name, Reader::Own).unwrap(), "old");
        assert_eq!(cat.find_key("atlas", "testnet", Reader::Own).unwrap().full_name.product, "atlas");
    }
}
