//! Product `dusk`: `dusk/<name>/<network>/public` resolves from the Dusk snapshot embedded
//! in the `dusk-networks` crate, through its name table, never from catalog entries.
//! Both readers read it. A name marked `fallback` resolves (`record.fallback` is `true`);
//! Dusk says to read those values live, so do not pin them.

use std::collections::HashMap;

use dusk_networks::DUSK_NETWORKS_VERSION;
use dusk_networks::names::{self, DuskEntry, TIER};

use crate::v2::{Catalog, Contract, Found, FullName};
use crate::{Error, Result};

impl Catalog {
    pub(crate) fn find_dusk(&self, name: &FullName) -> Result<Found> {
        let missing = || Error::MissingContractId {
            key: name.to_string(),
            path: self.repos[0].root.clone(),
        };
        if name.tier.as_deref().is_some_and(|t| t != TIER) {
            return Err(missing());
        }
        let entry = names::entry(&name.network, &name.name).ok_or_else(missing)?;
        Ok(Found {
            full_name: FullName {
                tier: Some(TIER.to_string()),
                ..name.clone()
            },
            repo: format!("@dusk-network/networks@{DUSK_NETWORKS_VERSION}"),
            contract: contract(&entry),
        })
    }
}

fn contract(entry: &DuskEntry) -> Contract {
    let mut record = serde_json::json!({
        "kind": entry.kind,
        "path": entry.path,
        "label": entry.label,
        "fallback": entry.fallback,
    });
    if let Some(evm) = &entry.evm_address {
        record["evm_address"] = evm.clone().into();
    }
    if let Some(chain) = &entry.chain {
        record["chain"] = chain.clone().into();
        record["chain_id"] = entry.chain_id.into();
    }
    Contract {
        layer: entry.layer.clone(),
        id: entry.id.clone(),
        version: Some(DUSK_NETWORKS_VERSION.to_string()),
        tx: None,
        at: String::new(),
        refs: HashMap::new(),
        record,
    }
}
