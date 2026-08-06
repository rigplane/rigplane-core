/**
 * MOR-1090 — writes build identity (commit sha, platform, tool versions)
 * next to the approved baselines after every run, win or lose, so a
 * verifier reading `manifest.json` knows what produced the last comparison
 * without re-running it. Mirrors the `buildIdentity` block
 * `fixtures/capture.mjs` already writes for the MOR-1070 harness.
 *
 * HONEST SEMANTICS (verify-mor-1090.md §2 F-1): `commit`/`commitShort` are
 * read from `git rev-parse HEAD` at run time, which is necessarily the tree
 * this manifest is generated FROM — when this file's own regeneration is
 * part of a commit (the normal "update baselines" flow), that commit does
 * not exist yet at write time, so the field records its PARENT. This is not
 * a bug to chase (writing it post-commit would require a second commit or
 * an amend, both worse than the thing they'd fix) — it is the correct,
 * honest reading of "what tree state produced these pixels", and it is
 * documented here so nobody "fixes" it into something less honest.
 */
import { execFileSync } from 'node:child_process';
import { arch, platform } from 'node:os';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { COMPARATOR } from '../../../playwright.visual.config';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const FRONTEND = path.resolve(HERE, '../../..');
const BASELINES = path.join(FRONTEND, 'fixtures/approved-baselines');
const git = (...args: string[]) =>
  execFileSync('git', ['-C', FRONTEND, ...args], { encoding: 'utf8' }).trim();

export default async function globalTeardown(): Promise<void> {
  const manifest = {
    ticket: 'MOR-1090',
    generatedAt: new Date().toISOString(),
    commit: git('rev-parse', 'HEAD'),
    commitShort: git('rev-parse', '--short=8', 'HEAD'),
    branch: git('rev-parse', '--abbrev-ref', 'HEAD'),
    platform: `${platform()}-${arch()}`,
    node: process.version,
    playwright: JSON.parse(
      readFileSync(path.join(FRONTEND, 'node_modules/@playwright/test/package.json'), 'utf8'),
    ).version,
    comparator: { engine: 'playwright-bundled-pixelmatch', ...COMPARATOR },
  };
  writeFileSync(path.join(BASELINES, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
}
