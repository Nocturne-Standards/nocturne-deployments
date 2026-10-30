//! Reads this repo's own pins-v2 files (current files and generated legacy files).

use std::path::PathBuf;

use nocturne_deployments::v2::{Catalog, FullName, Reader};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

#[test]
fn resolves_from_this_repo() {
    let cat = Catalog::open(&[repo_root()]).unwrap();
    let id = cat
        .resolve(&FullName::parse("knot/knot-registry/testnet/own").unwrap(), Reader::Own)
        .unwrap();
    assert_eq!(id.len(), 64);
    assert!(cat.resolve(&FullName::parse("tnst/tnst-token").unwrap(), Reader::Public).is_ok());
    assert!(cat.resolve(&FullName::parse("knot/knot-registry").unwrap(), Reader::Public).is_err());
}

#[test]
fn no_legacy_files_in_the_public_repo() {
    // The old monolith files are generated only in the private checkout now.
    assert!(nocturne_deployments::load_layer(repo_root(), "duskds", "testnet").is_err());
}

#[test]
fn dusk_networks_from_the_base() {
    let cat = Catalog::open(&[repo_root()]).unwrap();
    let testnet = cat.network("testnet").unwrap();
    assert_eq!(testnet.chain_id, 2);
    assert_eq!(cat.network_by_chain_id(2).unwrap(), testnet);
    assert_eq!(testnet.dusk_evm.as_ref().unwrap().chain_id, 745);
    assert!(cat.network_by_chain_id(9).is_none());
}

#[test]
fn index_base_version_matches_the_embedded_snapshot() {
    let raw = std::fs::read_to_string(repo_root().join("index.json")).unwrap();
    let index: serde_json::Value = serde_json::from_str(&raw).unwrap();
    assert_eq!(
        index["base"]["dusk"]["version"].as_str().unwrap(),
        nocturne_deployments::dusk_networks::DUSK_NETWORKS_VERSION
    );
}

#[test]
fn index_reads_with_or_without_a_networks_section() {
    let raw = std::fs::read_to_string(repo_root().join("index.json")).unwrap();
    let mut index: serde_json::Value = serde_json::from_str(&raw).unwrap();
    for with_networks in [true, false] {
        if with_networks {
            index["networks"] = serde_json::json!({"testnet": {"duskds": {"chain_id": 2}}});
        } else {
            index.as_object_mut().unwrap().remove("networks");
        }
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("index.json"), index.to_string()).unwrap();
        let repo = nocturne_deployments::v2::Repo::open(dir.path()).unwrap();
        assert!(!repo.index.entries.is_empty());
    }
}
