//! The Dusk name table: the embedded copy, lookups against the snapshot, and the file format.

use dusk_networks::by_name;
use dusk_networks::names::{self, NAMES_JSON, NameTable};
use serde_json::json;

const MESSENGER: &str = "63cd2c93016296b2ec357e31fd44b716cf2cda3b31217d45087f17ccc6440530";

#[test]
fn embedded_table_parses_with_unique_kebab_names() {
    let table = names::table();
    assert!(table.rows.len() >= 40);
    for row in &table.rows {
        assert!(
            row.name.split('-').all(|p| !p.is_empty()
                && p.chars()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())),
            "{}",
            row.name
        );
        assert!(
            ["duskds", "duskevm", "external"].contains(&row.layer.as_str()),
            "{}",
            row.name
        );
        assert!(
            ["contract", "account", "address", "chain-id"].contains(&row.kind.as_str()),
            "{}",
            row.name
        );
        assert_eq!(row.chain.is_some(), row.layer == "external", "{}", row.name);
    }
    assert_eq!(NameTable::from_json(NAMES_JSON).unwrap(), *table);
}

#[test]
fn testnet_names_agree_with_the_typed_network() {
    let t = by_name("testnet").unwrap();
    let evm = t.dusk_evm.as_ref().unwrap();
    let id = |name: &str| names::entry("testnet", name).unwrap().id;
    assert_eq!(id("transfer"), t.contracts.transfer);
    assert_eq!(id("stake"), t.contracts.stake);
    assert_eq!(id("sozu-hub"), t.sozu.as_ref().unwrap().hub);
    assert_eq!(id("chain-id"), t.chain_id.to_string());
    assert_eq!(id("evm-chain-id"), evm.chain_id.to_string());
    assert_eq!(
        id("evm-predeploy-l2-cross-domain-messenger"),
        evm.predeploys.l2_cross_domain_messenger
    );

    let messenger = names::entry("testnet", "evm-l1-cross-domain-messenger").unwrap();
    let typed = evm.l1_contract("l1_cross_domain_messenger").unwrap();
    assert_eq!(messenger.id, MESSENGER);
    assert_eq!(messenger.id, typed.contract_id);
    assert_eq!(
        messenger.evm_address.as_deref(),
        Some(typed.evm_address.as_str())
    );
    assert_eq!(messenger.layer, "duskds");
    assert!(!messenger.fallback);
    assert_eq!(
        messenger.full_name(),
        "dusk/evm-l1-cross-domain-messenger/testnet/public"
    );
    assert_eq!(messenger.label.as_deref(), t.label_for(MESSENGER));

    let predeploy = names::entry("testnet", "evm-predeploy-l2-cross-domain-messenger").unwrap();
    assert_eq!(predeploy.layer, "duskevm");
    assert_eq!(predeploy.kind, "address");
}

#[test]
fn fallbacks_are_marked() {
    for name in ["sozu-pool", "sozu-stdusk", "evm-fault-dispute-game-hub"] {
        assert!(names::entry("testnet", name).unwrap().fallback, "{name}");
    }
    for name in ["sozu-hub", "evm-dispute-game-factory", "evm-tx-router"] {
        assert!(!names::entry("testnet", name).unwrap().fallback, "{name}");
    }
}

#[test]
fn routes_and_gaps() {
    let main = names::entries("mainnet");
    let bsc = main
        .iter()
        .find(|e| e.name == "dusk-to-bsc-payout-address")
        .unwrap();
    assert_eq!(
        (bsc.layer.as_str(), bsc.chain.as_deref(), bsc.chain_id),
        ("external", Some("bsc"), Some(56))
    );
    assert!(!main.iter().any(|e| e.name.starts_with("evm-")));
    let devnet: Vec<String> = names::entries("devnet")
        .into_iter()
        .map(|e| e.name)
        .collect();
    assert_eq!(devnet, ["transfer", "stake", "chain-id"]);
    assert!(names::entry("testnet", "no-such-name").is_none());
    assert!(names::entry("local", "transfer").is_none());
    assert!(names::entries("local").is_empty());
}

#[test]
fn table_file_format_with_a_fixture() {
    let table = NameTable::from_json(include_str!("fixtures/dusk-names.v1.json")).unwrap();
    let doc = json!({
        "name": "fixturenet",
        "chainId": 9,
        "contracts": {"transfer": "aa"},
        "sozu": {"hub": "bb", "fallbacks": {"pool": "cc"}},
        "duskEvm": {"contracts": {"optimism_portal": {"contractId": "dd", "evmAddress": "0xDD"}}},
        "routes": {
            "chains": {"bsc": {"chainId": 97, "token": "0x01"}},
            "migrations": [{"id": "other", "contract": "0x02"}, {"id": "bsc-to-dusk", "contract": "0x03"}]
        },
        "display": {"labels": {"aa": "Transfer"}}
    });
    let got: Vec<(String, String)> = table
        .entries(&doc)
        .into_iter()
        .map(|e| (e.name, e.id))
        .collect();
    assert_eq!(
        got,
        [
            ("transfer".to_string(), "aa".to_string()),
            ("hub-pool".to_string(), "cc".to_string()),
            ("evm-portal".to_string(), "dd".to_string()),
            ("bsc-to-dusk-contract".to_string(), "0x03".to_string()),
            ("chain-id".to_string(), "9".to_string()),
        ]
    );
    let transfer = table.entry(&doc, "transfer").unwrap();
    assert_eq!(
        (transfer.network.as_str(), transfer.label.as_deref()),
        ("fixturenet", Some("Transfer"))
    );
    assert!(table.entry(&doc, "hub-pool").unwrap().fallback);
    assert_eq!(
        table
            .entry(&doc, "evm-portal")
            .unwrap()
            .evm_address
            .as_deref(),
        Some("0xDD")
    );
    let route = table.entry(&doc, "bsc-to-dusk-contract").unwrap();
    assert_eq!(
        (route.chain.as_deref(), route.chain_id),
        (Some("bsc"), Some(97))
    );
    // Missing or null values have no entry.
    let bare = json!({"name": "bare", "chainId": null, "contracts": {"transfer": null}});
    assert!(table.entries(&bare).is_empty());
}

#[test]
fn bad_table_files_are_refused() {
    let good: serde_json::Value =
        serde_json::from_str(include_str!("fixtures/dusk-names.v1.json")).unwrap();
    let mut wrong_schema = good.clone();
    wrong_schema["schema"] = json!("nocturne.dusk-names.v2");
    let mut wrong_tier = good.clone();
    wrong_tier["tier"] = json!("own");
    let mut twice = good.clone();
    let first = twice["names"][0].clone();
    twice["names"].as_array_mut().unwrap().push(first);
    let mut unknown_field = good.clone();
    unknown_field["names"][0]["extra"] = json!(1);
    for bad in [wrong_schema, wrong_tier, twice, unknown_field] {
        assert!(NameTable::from_json(&bad.to_string()).is_err(), "{bad}");
    }
}
