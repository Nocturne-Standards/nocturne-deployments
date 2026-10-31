//! `dusk/<name>/<network>/public` through the pins-v2 `Catalog`.

use std::path::PathBuf;

use nocturne_deployments::v2::{Catalog, FullName, Reader};

fn catalog() -> Catalog {
    Catalog::open(&[PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")]).unwrap()
}

fn name(text: &str) -> FullName {
    FullName::parse(text).unwrap()
}

#[test]
fn resolves_dusk_names_for_both_readers() {
    let cat = catalog();
    for reader in [Reader::Own, Reader::Public] {
        assert_eq!(
            cat.resolve(
                &name("dusk/evm-l1-cross-domain-messenger/testnet/public"),
                reader
            )
            .unwrap(),
            "63cd2c93016296b2ec357e31fd44b716cf2cda3b31217d45087f17ccc6440530"
        );
        // No tier: Dusk names are public.
        assert_eq!(
            cat.resolve(
                &name("dusk/evm-predeploy-l2-cross-domain-messenger/testnet"),
                reader
            )
            .unwrap(),
            "0x4200000000000000000000000000000000000007"
        );
    }
    assert_eq!(
        cat.resolve(&name("dusk/chain-id/mainnet/public"), Reader::Own)
            .unwrap(),
        "1"
    );
}

#[test]
fn found_carries_the_entry() {
    let found = catalog()
        .find(&name("dusk/sozu-pool/testnet"), Reader::Own)
        .unwrap();
    assert_eq!(found.full_name.to_string(), "dusk/sozu-pool/testnet/public");
    assert_eq!(
        found.repo,
        format!(
            "@dusk-network/networks@{}",
            nocturne_deployments::dusk_networks::DUSK_NETWORKS_VERSION
        )
    );
    assert_eq!(found.contract.layer, "duskds");
    assert_eq!(found.contract.record["fallback"], true);
    assert_eq!(found.contract.record["path"], "sozu.fallbacks.pool");
    let messenger = catalog()
        .find(
            &name("dusk/evm-l1-cross-domain-messenger/testnet"),
            Reader::Public,
        )
        .unwrap();
    assert_eq!(
        messenger.contract.record["evm_address"],
        "0x8B6Aa15321d9656c91ad2d5fBc2AE8b750aD6F9a"
    );
    assert_eq!(messenger.contract.record["fallback"], false);
}

#[test]
fn unknown_dusk_names_fail() {
    let cat = catalog();
    for text in [
        "dusk/no-such-name/testnet/public",
        "dusk/transfer/local/public",
        "dusk/transfer/testnet/own",
        "dusk/evm-l1-cross-domain-messenger/mainnet",
    ] {
        assert!(cat.resolve(&name(text), Reader::Own).is_err(), "{text}");
    }
}
