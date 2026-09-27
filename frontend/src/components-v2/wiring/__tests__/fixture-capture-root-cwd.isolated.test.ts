import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// MOR-2080 measured this full-build subprocess at 18.22 s under load; a
// saturated runner ran 33 s here (MOR-2743). The budget is a machine-speed
// ceiling sized to CI, not a behaviour bound.
const PREFLIGHT_TIMEOUT_MS = 120_000;

describe('fixture capture repository-root invocation (MOR-1409 A04a)', () => {
  it('anchors its Vite preflight at the frontend app root', () => {
    const repoRoot = resolve(import.meta.dirname, '../../../../..');
    const output = execFileSync(
      process.execPath,
      ['frontend/fixtures/capture.mjs', '--preflight-only'],
      { cwd: repoRoot, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
    );

    expect(output).toContain('PASS fixture build preflight');
  }, PREFLIGHT_TIMEOUT_MS);
});
