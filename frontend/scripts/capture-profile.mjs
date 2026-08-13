#!/usr/bin/env node
/**
 * Live radio-profile capture (MOR-1428, sidecar provenance MOR-1557).
 *
 * Fetches `/api/v1/state` and `/api/v1/capabilities` from a running
 * rigplane-core web server and writes them into the fixture pair a
 * conformance suite consumes — e.g.
 * `frontend/src/lib/runtime/adapters/__tests__/fixtures/ic7300-state.json`
 * and `…/ic7300-capabilities.json`. Plain Node ESM, no dependencies
 * (Node 18+ native `fetch`), so it runs standalone against any reachable
 * stand — no vitest/vite pipeline required.
 *
 * This is a READ-ONLY GET against the two public state endpoints. It
 * issues no writes, no CI-V commands, no WS traffic — safe to run against
 * a live bench radio at any time.
 *
 * Usage
 * -----
 *   node scripts/capture-profile.mjs <baseUrl> <name> [outDir] [--backend-sha <sha>]
 *
 * `<baseUrl>` is required — there is no default — and must point at a
 * reachable rigplane-core web server, e.g.:
 *
 *   node scripts/capture-profile.mjs http://localhost:8099 ic7300
 *   node scripts/capture-profile.mjs http://localhost:8099 ic7300 \
 *     src/lib/runtime/adapters/__tests__/fixtures --backend-sha e0b19814
 *
 * The backend HEAD SHA can also be passed via `RIGPLANE_BACKEND_SHA` env
 * (the `--backend-sha` flag wins if both are given); recorded verbatim,
 * never derived by shelling out to git here.
 *
 * Writes `<outDir>/<name>-state.json`, `<outDir>/<name>-capabilities.json`
 * (default `outDir`: `src/lib/runtime/adapters/__tests__/fixtures`, keys
 * recursively sorted, byte-faithful and metadata-free), plus a sidecar
 * `<outDir>/<name>-provenance.json` carrying the capture metadata instead:
 * capture timestamp, radio model, receivers, stateContractVersion,
 * providerGeneration, backend HEAD SHA. PUBLIC BOUNDARY: the sidecar NEVER
 * records the bench host/IP/URL — `source` is always the fixed string
 * `"local bench instance"`, regardless of what `<baseUrl>` was.
 */

import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = resolve(SCRIPT_DIR, '..');
const DEFAULT_OUT_DIR = 'src/lib/runtime/adapters/__tests__/fixtures';

function sortKeysDeep(value) {
  if (Array.isArray(value)) return value.map(sortKeysDeep);
  if (value !== null && typeof value === 'object') {
    const sorted = {};
    for (const key of Object.keys(value).sort()) sorted[key] = sortKeysDeep(value[key]);
    return sorted;
  }
  return value;
}

async function fetchJson(baseUrl, path) {
  const url = new URL(path, baseUrl).toString();
  const response = await fetch(url, { method: 'GET' });
  if (!response.ok) {
    throw new Error(`GET ${url} -> HTTP ${response.status}`);
  }
  return { url, body: await response.json() };
}

/** Splits `--backend-sha <sha>` / `--backend-sha=<sha>` out of the positional args. */
function parseArgs(argv) {
  const positional = [];
  let backendSha;
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--backend-sha') {
      backendSha = argv[++i];
    } else if (arg.startsWith('--backend-sha=')) {
      backendSha = arg.slice('--backend-sha='.length);
    } else {
      positional.push(arg);
    }
  }
  return { positional, backendSha };
}

async function main() {
  const { positional, backendSha: backendShaFlag } = parseArgs(process.argv.slice(2));
  const [baseUrlArg, name, outDirArg] = positional;
  if (!baseUrlArg || !name) {
    console.error(
      'Usage: node scripts/capture-profile.mjs <baseUrl> <name> [outDir] [--backend-sha <sha>]',
    );
    process.exitCode = 1;
    return;
  }
  const baseUrl = baseUrlArg.replace(/\/+$/, '');
  const outDir = resolve(FRONTEND_ROOT, outDirArg ?? DEFAULT_OUT_DIR);
  const backendHeadSha = backendShaFlag ?? process.env.RIGPLANE_BACKEND_SHA ?? null;

  const [state, capabilities] = await Promise.all([
    fetchJson(baseUrl, '/api/v1/state'),
    fetchJson(baseUrl, '/api/v1/capabilities'),
  ]);

  await mkdir(outDir, { recursive: true });
  const statePath = join(outDir, `${name}-state.json`);
  const capsPath = join(outDir, `${name}-capabilities.json`);
  const provenancePath = join(outDir, `${name}-provenance.json`);
  await writeFile(statePath, `${JSON.stringify(sortKeysDeep(state.body), null, 2)}\n`, 'utf8');
  await writeFile(capsPath, `${JSON.stringify(sortKeysDeep(capabilities.body), null, 2)}\n`, 'utf8');

  const capturedAt = new Date().toISOString();
  // PUBLIC BOUNDARY (absolute rule): never record the bench host/IP/URL here
  // — `source` is always this fixed string, regardless of `baseUrl`.
  const provenance = {
    capturedAt,
    radioModel: capabilities.body.model ?? null,
    receivers: capabilities.body.receivers ?? null,
    stateContractVersion: state.body.stateContractVersion ?? null,
    providerGeneration: state.body.providerGeneration ?? null,
    backendHeadSha,
    source: 'local bench instance',
  };
  await writeFile(provenancePath, `${JSON.stringify(provenance, null, 2)}\n`, 'utf8');

  console.log('Captured profile:');
  console.log(`  model:        ${provenance.radioModel ?? '(unknown)'}`);
  console.log(`  receivers:    ${provenance.receivers ?? '(unknown)'}`);
  console.log(`  captured at:  ${capturedAt}`);
  console.log(`  backend SHA:  ${backendHeadSha ?? '(not provided — pass --backend-sha)'}`);
  console.log(`  state ->      ${statePath}`);
  console.log(`  caps  ->      ${capsPath}`);
  console.log(`  provenance -> ${provenancePath}`);
  console.log('');
  console.log('The state/capabilities JSON stay byte-faithful and metadata-free —');
  console.log('capture provenance now lives only in the sidecar above (never the');
  console.log('bench host/IP/URL — update the fixture loader\'s header comment and');
  console.log('the PR body by hand to describe the bench in prose if useful).');
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
