#!/usr/bin/env node
// Encodes JSON contract-call args into hex-encoded RKYV bytes via a contract's
// compiled data-driver wasm, for feeding into `rusk-wallet contract-call --fn-args`.
//
// Reuses @dusk/connect's own data-driver loader (references/repos/connect,
// already a dependency of investor-portal/wallet-connect-prototype) instead of
// pulling in a second SDK (w3sper) just for this one-off encoding step. Loads
// the wasm straight off disk (no fetch), unlike @dusk/connect's own
// fetchWasmDataDriver.
//
// Usage:
//   node scripts/encode-contract-args.mjs <dd-wasm-path> <fnName> <json-args>
//
// Prints the hex-encoded bytes (no 0x prefix) to stdout, no trailing newline.
//
// Also supports a schema-inspection mode, useful for figuring out the exact
// JSON shape a function expects before writing a real call:
//   node scripts/encode-contract-args.mjs <dd-wasm-path> --schema

import { readFile, access } from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

async function resolveDriverModulePath() {
  const candidates = [];

  if (process.env.DUSK_CONNECT_DRIVER) {
    candidates.push(process.env.DUSK_CONNECT_DRIVER);
  }

  if (process.env.CALLER_REPO_ROOT) {
    candidates.push(
      path.join(process.env.CALLER_REPO_ROOT, "references/repos/connect/dist/driver.js"),
    );
  }

  candidates.push(
    path.join(__dirname, "../../sme_platform/references/repos/connect/dist/driver.js"),
  );
  candidates.push(path.join(__dirname, "../references/repos/connect/dist/driver.js"));

  for (const candidate of candidates) {
    try {
      await access(candidate, fsConstants.R_OK);
      return candidate;
    } catch {
      // try next candidate
    }
  }

  console.error("error: could not find @dusk/connect driver module");
  console.error("Tried:");
  for (const candidate of candidates) {
    console.error(`  - ${candidate}`);
  }
  console.error(
    "(expected a built dist/ — run 'npm run build' in references/repos/connect if missing)",
  );
  process.exit(1);
}

const driverModulePath = await resolveDriverModulePath();

function usage() {
  console.error("Usage: encode-contract-args.mjs <dd-wasm-path> <fnName> <json-args>");
  console.error("       encode-contract-args.mjs <dd-wasm-path> --schema");
  process.exit(1);
}

const [, , wasmPath, fnNameOrFlag, jsonArgs] = process.argv;
if (!wasmPath || !fnNameOrFlag) usage();

let loadWasmDataDriver;
try {
  ({ loadWasmDataDriver } = await import(driverModulePath));
} catch (err) {
  console.error(`error: could not load @dusk/connect's driver module from ${driverModulePath}`);
  console.error(`       (expected a built dist/ — run 'npm run build' in references/repos/connect if missing)`);
  console.error(String(err?.message ?? err));
  process.exit(1);
}

const bytes = await readFile(wasmPath);
const driver = await loadWasmDataDriver(new Uint8Array(bytes));
driver.init?.();

if (fnNameOrFlag === "--schema") {
  process.stdout.write(JSON.stringify(driver.getSchema(), null, 2));
  process.stdout.write("\n");
  process.exit(0);
}

if (jsonArgs === undefined) usage();

const fnName = fnNameOrFlag;
let encoded;
try {
  encoded = driver.encodeInputFn(fnName, jsonArgs);
} catch (err) {
  console.error(`error: failed to encode args for '${fnName}': ${err?.message ?? err}`);
  process.exit(1);
}

process.stdout.write(Buffer.from(encoded).toString("hex"));
