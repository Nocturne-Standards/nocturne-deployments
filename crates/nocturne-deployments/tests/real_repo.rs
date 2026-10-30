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
        .resolve(
            &FullName::parse("knot/knot-registry/testnet/own").unwrap(),
            Reader::Own,
        )
        .unwrap();
    assert_eq!(id.len(), 64);
    assert!(
        cat.resolve(&FullName::parse("tnst/tnst-token").unwrap(), Reader::Public)
            .is_ok()
    );
    assert!(
        cat.resolve(
            &FullName::parse("knot/knot-registry").unwrap(),
            Reader::Public
        )
        .is_err()
    );
}

#[test]
fn no_legacy_files_in_the_public_repo() {
    // The old monolith files are generated only in the private checkout now.
    assert!(nocturne_deployments::load_layer(repo_root(), "duskds", "testnet").is_err());
}
