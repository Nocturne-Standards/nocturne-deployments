//! Names of Dusk's contracts, accounts and chain IDs: `dusk/<name>/<network>/public`.
//!
//! The names come from one fixed table, one row per JSON path in a network file. The table
//! is kept in `nocturne-pins` (`nocturne_pins.dusk_names.TABLE`) and written as JSON to
//! `crates/dusk-networks/dusk-names.json` by `nocturne-pins name-table --write`; this module
//! embeds that file, so Python and Rust read the same table. `nocturne-pins check` fails when
//! the file differs from the Python table.
//!
//! ```
//! let messenger = dusk_networks::names::entry("testnet", "evm-l1-cross-domain-messenger").unwrap();
//! assert_eq!(messenger.layer, "duskds");
//! assert!(messenger.evm_address.is_some());
//! assert_eq!(dusk_networks::names::entry("testnet", "chain-id").unwrap().id, "2");
//! ```
//!
//! Rows marked [`NameRow::fallback`] are values Dusk says to read live (Sozu's contracts
//! behind its hub, DuskEVM's dispute game hubs). They resolve, but a pinned reference to one
//! is a mistake: read them from the chain.

use std::sync::OnceLock;

use serde::Deserialize;
use serde_json::Value;

/// The embedded `crates/dusk-networks/dusk-names.json`.
pub const NAMES_JSON: &str = include_str!("../dusk-names.json");
/// The `schema` field this module reads.
pub const NAMES_SCHEMA: &str = "nocturne.dusk-names.v1";
/// Product of every Dusk name.
pub const PRODUCT: &str = "dusk";
/// Tier of every Dusk name. For `dusk`, Dusk announces changes, not us.
pub const TIER: &str = "public";

#[derive(Debug, thiserror::Error)]
pub enum NamesError {
    #[error("parsing the Dusk name table: {0}")]
    Parse(#[from] serde_json::Error),
    #[error("Dusk name table: {0}")]
    Invalid(String),
}

/// One row of the table.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NameRow {
    /// Kebab-case name, e.g. `sozu-hub`, `evm-l1-cross-domain-messenger`.
    pub name: String,
    /// Dotted path in the network file; `key[id=X]` picks the array item whose `id` is `X`.
    pub path: String,
    /// `duskds`, `duskevm`, or `external` (a route's other chain).
    pub layer: String,
    /// `contract`, `account` (Dusk account), `address` (EVM address) or `chain-id`.
    pub kind: String,
    /// Dusk says to read this value live; the file's value is only a starting point.
    pub fallback: bool,
    /// For `external` values: the key under `routes.chains`.
    pub chain: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TableFile {
    schema: String,
    #[allow(dead_code)]
    note: Option<String>,
    product: String,
    tier: String,
    names: Vec<NameRow>,
}

/// A parsed name table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NameTable {
    pub rows: Vec<NameRow>,
}

/// One name on one network, as the network file gives it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DuskEntry {
    pub name: String,
    pub network: String,
    pub layer: String,
    pub kind: String,
    /// Contract ID (hex, no `0x`), account, EVM address, or for `chain-id` the number as text.
    pub id: String,
    /// The EVM address of a DuskEVM L1 contract, alongside its contract ID.
    pub evm_address: Option<String>,
    /// For `external` values: the chain key and its chain ID.
    pub chain: Option<String>,
    pub chain_id: Option<u64>,
    /// Dusk's display label for `id`, if any.
    pub label: Option<String>,
    pub fallback: bool,
    pub path: String,
}

impl DuskEntry {
    /// `dusk/<name>/<network>/public`.
    pub fn full_name(&self) -> String {
        format!("{PRODUCT}/{}/{}/{TIER}", self.name, self.network)
    }
}

impl NameTable {
    /// Parse a table file (`nocturne.dusk-names.v1`).
    pub fn from_json(text: &str) -> Result<Self, NamesError> {
        let file: TableFile = serde_json::from_str(text)?;
        if file.schema != NAMES_SCHEMA {
            return Err(NamesError::Invalid(format!(
                "schema {:?}, want {NAMES_SCHEMA:?}",
                file.schema
            )));
        }
        if file.product != PRODUCT || file.tier != TIER {
            return Err(NamesError::Invalid(format!(
                "product/tier {}/{}, want {PRODUCT}/{TIER}",
                file.product, file.tier
            )));
        }
        let mut seen = std::collections::BTreeSet::new();
        for row in &file.names {
            if !seen.insert(row.name.as_str()) {
                return Err(NamesError::Invalid(format!("name {:?} twice", row.name)));
            }
        }
        Ok(Self { rows: file.names })
    }

    pub fn row(&self, name: &str) -> Option<&NameRow> {
        self.rows.iter().find(|r| r.name == name)
    }

    /// The value of `name` in one network file (`doc`), or `None` where it has none.
    pub fn entry(&self, doc: &Value, name: &str) -> Option<DuskEntry> {
        entry_of(doc, self.row(name)?)
    }

    /// Every name the network file has a value for, in table order.
    pub fn entries(&self, doc: &Value) -> Vec<DuskEntry> {
        self.rows
            .iter()
            .filter_map(|row| entry_of(doc, row))
            .collect()
    }
}

fn get<'a>(doc: &'a Value, path: &str) -> Option<&'a Value> {
    let mut value = doc;
    for part in path.split('.') {
        value = match part.split_once("[id=") {
            Some((key, rest)) => {
                let wanted = rest.strip_suffix(']')?;
                value
                    .get(key)?
                    .as_array()?
                    .iter()
                    .find(|item| item.get("id").and_then(Value::as_str) == Some(wanted))?
            }
            None => value.get(part)?,
        };
    }
    Some(value)
}

fn entry_of(doc: &Value, row: &NameRow) -> Option<DuskEntry> {
    let raw = get(doc, &row.path).filter(|v| !v.is_null())?;
    let (id, evm_address) = match raw {
        Value::Object(map) => (
            map.get("contractId")?.as_str()?.to_string(),
            map.get("evmAddress")
                .and_then(Value::as_str)
                .map(str::to_string),
        ),
        Value::Number(n) if row.kind == "chain-id" => (n.as_u64()?.to_string(), None),
        Value::String(s) => (s.clone(), None),
        _ => return None,
    };
    let chain_id = row.chain.as_deref().and_then(|chain| {
        doc.get("routes")?
            .get("chains")?
            .get(chain)?
            .get("chainId")?
            .as_u64()
    });
    let label = doc
        .get("display")
        .and_then(|d| d.get("labels"))
        .and_then(|l| l.get(&id))
        .and_then(Value::as_str)
        .map(str::to_string);
    Some(DuskEntry {
        name: row.name.clone(),
        network: doc
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        layer: row.layer.clone(),
        kind: row.kind.clone(),
        id,
        evm_address,
        chain: row.chain.clone(),
        chain_id,
        label,
        fallback: row.fallback,
        path: row.path.clone(),
    })
}

/// The embedded table. It is fixed at build time and covered by this crate's tests, so a
/// parse failure is a broken build and panics.
pub fn table() -> &'static NameTable {
    static TABLE: OnceLock<NameTable> = OnceLock::new();
    TABLE
        .get_or_init(|| NameTable::from_json(NAMES_JSON).unwrap_or_else(|e| panic!("embedded {e}")))
}

fn raw_network(network: &str) -> Option<&'static Value> {
    static DOCS: OnceLock<Vec<(String, Value)>> = OnceLock::new();
    let docs = DOCS.get_or_init(|| {
        crate::EMBEDDED
            .iter()
            .map(|(file, text)| {
                let doc: Value = serde_json::from_str(text)
                    .unwrap_or_else(|e| panic!("embedded snapshot {file}: {e}"));
                let name = doc
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();
                (name, doc)
            })
            .collect()
    });
    docs.iter()
        .find(|(name, _)| name == network)
        .map(|(_, doc)| doc)
}

/// `dusk/<name>/<network>/public` from the embedded snapshot; `None` for an unknown name or
/// network, or where the network has no such value.
pub fn entry(network: &str, name: &str) -> Option<DuskEntry> {
    table().entry(raw_network(network)?, name)
}

/// Every Dusk name the network has a value for, in table order.
pub fn entries(network: &str) -> Vec<DuskEntry> {
    raw_network(network)
        .map(|doc| table().entries(doc))
        .unwrap_or_default()
}
