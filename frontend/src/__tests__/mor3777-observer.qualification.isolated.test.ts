/** Real-setup subprocess qualification: counts, real/fake timers, cap and silence.
 * Generated fixture failures are intentional; this is not historical acceptance.
 */
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from 'vitest';

const FRONTEND_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
);
const VITEST_CLI = path.join(FRONTEND_ROOT, 'node_modules', 'vitest', 'vitest.mjs');
const REAL_SETUP = path.join(FRONTEND_ROOT, 'vitest.setup.ts');

const ALLOWLISTED_REL = 'src/components-v2/layout/__tests__/RadioLayout.isolated.test.ts';
const UNRELATED_REL = 'src/other/mor3777-unrelated.other.test.ts';
const INTENTIONAL_FAILS = 9; // strictly above MOR3777_EMIT_MAX (8)
const EMIT_CAP = 8;

const OBS_LINE_RE = /^\[mor-3777-obs (?:case|file) /;
const CASE_LINE_RE =
  /^\[mor-3777-obs case F1\/\d+ tid=\d+ pool=\S+ pid=\d+ fake_start=(?:true|false) fake_end=(?:true|false) timers_end=(?:UNAVAILABLE|\d+) case_lifecycle_ms=(?:\d+(?:\.\d+)?) evd_cum_mean_ms=\S+ evd_cum_max_ms=\S+ (?:proc_rss_mb=\S+ .*|proc_resource=UNAVAILABLE) verdict=fail\]$/;
const FILE_LINE_RE =
  /^\[mor-3777-obs file F1 cases=10 emitted=8 pass=1 fail=9 other=0 evd_cum_mean_ms=\S+ evd_cum_max_ms=\S+ (?:proc_rss_mb=\S+ .*|proc_resource=UNAVAILABLE)\]$/;

function fixtureFailingCase(index: number): string {
  return [
    `test('mor3777 case ${index} fails on purpose', () => {`,
    '  vi.useRealTimers();',
    ...(index === 2 ? ["  vi.useFakeTimers({ toFake: ['hrtime', 'setTimeout', 'clearTimeout'] });", '  expect(vi.isFakeTimers()).toBe(true);'] : []),
    `  throw new Error('MOR3777_OBS_SECRET_PAYLOAD_${index}');`,
    '});',
    '',
  ].join('\n');
}

const FIXTURE_OBSERVED = [
  ...Array.from({ length: INTENTIONAL_FAILS }, (_, i) => fixtureFailingCase(i + 1)),
  "test('mor3777 case 10 passes on purpose', () => {",
  '  expect(7).toBe(7);',
  '});',
  '',
].join('\n');

const FIXTURE_UNRELATED = [
  "test('unrelated mor3777 file must stay unobserved', () => {",
  "  throw new Error('UNRELATED_MOR3777_SILENT_PAYLOAD');",
  '});',
  '',
].join('\n');

function clip(text: string): string {
  return text.length > 4000 ? `${text.slice(0, 4000)}…` : text;
}

test(
  'mor-3777 observer qualification through the real vitest setup (subprocess, bounded)',
  { timeout: 120_000 },
  () => {
    expect(
      VITEST_CLI.endsWith(path.join('vitest', 'vitest.mjs')),
      'vitest CLI must resolve from the existing frontend install',
    ).toBe(true);

    const scratch = mkdtempSync(path.join(tmpdir(), 'mor3777-obs-'));
    let output = '';
    try {
      const observedPath = path.join(scratch, ALLOWLISTED_REL);
      const unrelatedPath = path.join(scratch, UNRELATED_REL);
      mkdirSync(path.dirname(observedPath), { recursive: true });
      mkdirSync(path.dirname(unrelatedPath), { recursive: true });
      writeFileSync(observedPath, FIXTURE_OBSERVED);
      writeFileSync(unrelatedPath, FIXTURE_UNRELATED);
      writeFileSync(
        path.join(scratch, 'package.json'),
        '{"name":"mor3777-observer-fixture","private":true,"type":"module"}\n',
      );
      writeFileSync(
        path.join(scratch, 'vitest.config.mjs'),
        `export default {\n  test: {\n    globals: true,\n    environment: 'node',\n    setupFiles: ${JSON.stringify(REAL_SETUP)},\n    include: ['src/**/*.test.ts'],\n    pool: 'threads',\n    maxWorkers: 1,\n    disableConsoleIntercept: true,\n  },\n};\n`,
      );

      const child = spawnSync(
        process.execPath,
        [VITEST_CLI, 'run', '--config', path.join(scratch, 'vitest.config.mjs')],
        {
          cwd: scratch,
          encoding: 'utf8',
          maxBuffer: 1024 * 1024,
          timeout: 30_000,
          killSignal: 'SIGKILL',
          env: { ...process.env, FORCE_COLOR: '0', CI: '1' },
        },
      );
      expect(child.error, 'child vitest must spawn').toBeUndefined();
      output = `${child.stdout ?? ''}\n${child.stderr ?? ''}`.replace(
        /\x1b\[[0-9;]*m/g,
        '',
      );

      expect(child.status, `child exit code (output follows)\n${clip(output)}`).not.toBe(0);
      expect(child.status).not.toBeNull();

      const lines = output.split('\n');
      const observerLines = lines.filter((line) => OBS_LINE_RE.test(line));
      const caseLines = observerLines.filter((line) => line.includes('case F1/'));
      const fileLines = observerLines.filter((line) =>
        line.startsWith('[mor-3777-obs file '),
      );

      expect(
        caseLines.length,
        `observer case lines (cap ${EMIT_CAP}) in output:\n${clip(output)}`,
      ).toBe(EMIT_CAP);
      for (const line of caseLines) {
        expect(line, `case line shape:\n${line}`).toMatch(CASE_LINE_RE);
      }
      expect(caseLines[0]).toContain('fake_start=false fake_end=false timers_end=UNAVAILABLE');
      expect(caseLines[1]).toContain('fake_start=false fake_end=true timers_end=0');
      expect(caseLines.filter((line) => line.includes('fake_end=true'))).toHaveLength(1);

      expect(fileLines.length, `observer file lines:\n${fileLines.join('\n')}`).toBe(1);
      expect(fileLines[0]).toMatch(FILE_LINE_RE);

      expect(output, 'unrelated fixture must actually run and fail').toContain(
        UNRELATED_REL,
      );
      const leaked = observerLines.filter(
        (line) =>
          line.includes('unrelated') ||
          line.includes('UNRELATED') ||
          line.includes('MOR3777_OBS_SECRET_PAYLOAD') ||
          line.includes('mor3777 case'),
      );
      expect(
        leaked,
        `observer lines leaking raw names/payloads:\n${leaked.join('\n')}`,
      ).toEqual([]);

      expect(output).toMatch(/Test Files\s+2 failed/);
      expect(output).toMatch(/Tests\s+10 failed \| 1 passed/);
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  },
);
