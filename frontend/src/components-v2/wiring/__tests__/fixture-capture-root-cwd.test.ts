import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('fixture capture repository-root invocation (MOR-1409 A04a)', () => {
  it('anchors its Vite preflight at the frontend app root', () => {
    const repoRoot = resolve(import.meta.dirname, '../../../../..');
    const output = execFileSync(
      process.execPath,
      ['frontend/fixtures/capture.mjs', '--preflight-only'],
      { cwd: repoRoot, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
    );

    expect(output).toContain('PASS fixture build preflight');
  }, 15_000);
});
