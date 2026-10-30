//! Lookups, labels, blocklist and parsing of the embedded snapshot.

use dusk_networks::{BlockReason, Network, by_chain_id, by_name};

#[test]
fn chain_ids_and_names() {
    for (id, name, label) in [
        (1, "mainnet", "Mainnet"),
        (2, "testnet", "Testnet"),
        (3, "devnet", "Devnet"),
    ] {
        let n = by_chain_id(id).unwrap();
        assert_eq!(n.name, name);
        assert_eq!(n.label, label);
        assert_eq!(by_name(name).unwrap(), n);
    }
    assert_eq!(dusk_networks::all().len(), 3);
    assert!(by_chain_id(0).is_none());
    assert!(by_chain_id(745).is_none());
    assert!(by_name("local").is_none());
    assert!(by_name("Testnet").is_none());
}

#[test]
fn testnet_values() {
    let t = by_chain_id(2).unwrap();
    assert_eq!(t.urls.node, "https://testnet.nodes.dusk.network");
    assert_eq!(t.urls.prover, "https://testnet.provers.dusk.network");
    assert_eq!(
        t.apps.explorer.as_deref(),
        Some("https://apps.testnet.dusk.network/explorer")
    );
    assert_eq!(t.contracts.transfer, format!("01{}", "0".repeat(62)));
    assert_eq!(t.contracts.stake, format!("02{}", "0".repeat(62)));
    assert!(t.contracts.multicall.is_some());

    let evm = t.dusk_evm.as_ref().unwrap();
    assert_eq!(evm.chain_id, 745);
    assert_eq!(evm.rpc_url, "https://rpc.testnet.evm.dusk.network");
    assert_eq!(
        evm.explorer_url,
        "https://explorer.testnet.evm.dusk.network"
    );
    assert_eq!(evm.currency.decimals, 18);
    assert_eq!(
        evm.predeploys.l2_cross_domain_messenger,
        "0x4200000000000000000000000000000000000007"
    );
    let messenger = evm.l1_contract("l1_cross_domain_messenger").unwrap();
    assert!(messenger.contract_id.starts_with("63cd2c93"));
    assert!(messenger.evm_address.starts_with("0x"));
    assert!(evm.drivers.get("optimismPortal").is_some());

    assert_eq!(t.routes.chains["ethereum"].chain_id, 11_155_111);
    assert_eq!(t.routes.dusk_evm.len(), 2);
}

#[test]
fn devnet_nulls() {
    let d = by_name("devnet").unwrap();
    assert!(d.sozu.is_none());
    assert!(d.dusk_domains.is_none());
    assert!(d.dusk_evm.is_none());
    assert!(d.apps.bridge.is_none());
    assert!(d.contracts.multicall.is_none());
    assert!(d.routes.chains.is_empty());
    assert!(by_name("mainnet").unwrap().dusk_evm.is_none());
}

#[test]
fn labels() {
    let t = by_chain_id(2).unwrap();
    assert_eq!(t.label_for(&t.contracts.stake), Some("Stake contract"));
    let sozu = t.sozu.as_ref().unwrap();
    assert_eq!(t.label_for(&sozu.hub), Some("Sozu hub"));
    // Accounts are labelled by base58 key too.
    assert_eq!(
        t.label_for(&t.routes.services[0].account),
        Some("Sepolia bridge")
    );
    assert_eq!(t.label_for(&"f".repeat(64)), None);
}

#[test]
fn blocklist() {
    for n in dusk_networks::all() {
        assert!(!n.display.blocklist.is_empty(), "{}", n.name);
        for b in &n.display.blocklist {
            assert!(n.is_blocked(&b.account));
            assert_ne!(b.reason, BlockReason::Other);
        }
        assert!(!n.is_blocked(&n.contracts.transfer));
    }
    let t = by_chain_id(2).unwrap();
    let reasons: Vec<_> = t.display.blocklist.iter().map(|b| b.reason).collect();
    assert!(reasons.contains(&BlockReason::Compromised));
    assert!(reasons.contains(&BlockReason::Scam));
    let first = &t.display.blocklist[0];
    assert_eq!(t.blocked(&first.account), Some(first));
}

#[test]
fn sozu_ids_are_fallbacks_behind_the_hub() {
    let m = by_chain_id(1).unwrap().sozu.as_ref().unwrap();
    let t = by_chain_id(2).unwrap().sozu.as_ref().unwrap();
    assert_ne!(m.hub, t.hub);
    // Every fallback is a contract ID distinct from the hub.
    for s in [m, t] {
        let f = &s.fallbacks;
        for id in [&f.pool, &f.relayer, &f.st_dusk, &f.substrate, &f.vault] {
            assert_eq!(id.len(), 64);
            assert_ne!(id, &s.hub);
        }
    }
    // The schema describes them as the hub's names at writing time, read live from the hub.
    let schema: serde_json::Value = serde_json::from_str(dusk_networks::SCHEMA_JSON).unwrap();
    let about = schema["properties"]["sozu"]["description"]
        .as_str()
        .unwrap();
    assert!(about.contains("read them from the hub"), "{about}");
}

/// Every key the schema requires at the top level is typed, and every embedded file
/// carries exactly the schema's top-level keys.
#[test]
fn schema_shaped_parsing() {
    let schema: serde_json::Value = serde_json::from_str(dusk_networks::SCHEMA_JSON).unwrap();
    let mut required: Vec<&str> = schema["required"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap())
        .collect();
    required.sort_unstable();
    let ids: Vec<u64> = schema["properties"]["chainId"]["enum"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap())
        .collect();
    for (file, text) in [
        ("mainnet.json", dusk_networks::MAINNET_JSON),
        ("testnet.json", dusk_networks::TESTNET_JSON),
        ("devnet.json", dusk_networks::DEVNET_JSON),
    ] {
        let raw: serde_json::Value = serde_json::from_str(text).unwrap();
        let mut keys: Vec<&str> = raw
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        keys.sort_unstable();
        assert_eq!(keys, required, "{file}");
        let n = Network::from_json(file, text).unwrap();
        assert!(ids.contains(&n.chain_id), "{file}");
        assert_eq!(format!("{}.json", n.name), file);
    }
}

#[test]
fn tolerant_of_new_keys_strict_on_typed_ones() {
    let mut raw: serde_json::Value = serde_json::from_str(dusk_networks::TESTNET_JSON).unwrap();
    raw["somethingNew"] = serde_json::json!({"x": 1});
    raw["urls"]["indexer"] = serde_json::json!("https://example.invalid");
    raw["display"]["blocklist"][0]["reason"] = serde_json::json!("sanctioned");
    let n = Network::from_json("grown.json", &raw.to_string()).unwrap();
    assert_eq!(n.display.blocklist[0].reason, BlockReason::Other);

    let mut missing: serde_json::Value = serde_json::from_str(dusk_networks::TESTNET_JSON).unwrap();
    missing["urls"].as_object_mut().unwrap().remove("node");
    assert!(Network::from_json("missing.json", &missing.to_string()).is_err());

    let mut wrong: serde_json::Value = serde_json::from_str(dusk_networks::TESTNET_JSON).unwrap();
    wrong["chainId"] = serde_json::json!("2");
    let err = Network::from_json("wrong.json", &wrong.to_string()).unwrap_err();
    assert!(err.to_string().contains("wrong.json"));
}

#[test]
fn sozu_vectors_embedded() {
    let v: serde_json::Value = serde_json::from_str(dusk_networks::SOZU_VECTORS_JSON).unwrap();
    assert!(v["pools"].is_object());
    assert!(v["fees"].is_object());
}
