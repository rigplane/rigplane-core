#!/usr/bin/env node
/**
 * Live radio-profile capture (MOR-1428).
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
 *   node scripts/capture-profile.mjs <baseUrl> <name> [outDir]
 *
 * `<baseUrl>` is required — there is no default — and must point at a
 * reachable rigplane-core web server, e.g.:
 *
 *   node scripts/capture-profile.mjs http://localhost:8099 ic7300
 *   node scripts/capture-profile.mjs http://localhost:8099 ic7300 \
 *     src/lib/runtime/adapters/__tests__/fixtures
 *
 * Writes `<outDir>/<name>-state.json` and `<outDir>/<name>-capabilities.json`
 * (default `outDir`: `src/lib/runtime/adapters/__tests__/fixtures`, relative
 * to `frontend/`). Keys are recursively sorted so successive captures of an
 * unchanged radio diff cleanly. Each file's top-level `_provenance` sibling
 * fields are NOT embedded in the JSON itself (the payload must stay a
 * byte-faithful capture of the real API response) — provenance (base URL,
 * capture date, radio model, backend HEAD SHA) is printed to stdout for the
 * caller to record in the fixture loader's header comment and the PR body.
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

async function main() {
  const [baseUrlArg, name, outDirArg] = process.argv.slice(2);
  if (!baseUrlArg || !name) {
    console.error('Usage: node scripts/capture-profile.mjs <baseUrl> <name> [outDir]');
    process.exitCode = 1;
    return;
  }
  const baseUrl = baseUrlArg.replace(/\/+$/, '');
  const outDir = resolve(FRONTEND_ROOT, outDirArg ?? DEFAULT_OUT_DIR);

  const [state, capabilities] = await Promise.all([
    fetchJson(baseUrl, '/api/v1/state'),
    fetchJson(baseUrl, '/api/v1/capabilities'),
  ]);

  await mkdir(outDir, { recursive: true });
  const statePath = join(outDir, `${name}-state.json`);
  const capsPath = join(outDir, `${name}-capabilities.json`);
  await writeFile(statePath, `${JSON.stringify(sortKeysDeep(state.body), null, 2)}\n`, 'utf8');
  await writeFile(capsPath, `${JSON.stringify(sortKeysDeep(capabilities.body), null, 2)}\n`, 'utf8');

  const capturedAt = new Date().toISOString();
  console.log('Captured profile:');
  console.log(`  model:        ${capabilities.body.model ?? '(unknown)'}`);
  console.log(`  receivers:    ${capabilities.body.receivers ?? '(unknown)'}`);
  console.log(`  base URL:     ${baseUrl}`);
  console.log(`  captured at:  ${capturedAt}`);
  console.log(`  state ->      ${statePath}`);
  console.log(`  caps  ->      ${capsPath}`);
  console.log('');
  console.log('Record base URL / capture date / radio model / backend HEAD SHA');
  console.log('in the fixture loader\'s header comment and the PR body — the raw');
  console.log('JSON payloads intentionally carry no provenance metadata of their');
  console.log('own (they must stay a byte-faithful capture of the real API).');
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
