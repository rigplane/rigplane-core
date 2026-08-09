import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('fixture capture repository-root invocation (MOR-1409 A04a)', () => {
  it('anchors its Vite preflight at the frontend app root', () => {
    const repoRoot = resolve(process.cwd(), '..');
    const output = execFileSync(
      process.execPath,
      ['frontend/fixtures/capture.mjs', '--only', 'keyboard-activation-vfo-split', '--port', '5211'],
      { cwd: repoRoot, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
    );

    expect(output).toContain('PASS  keyboard-activation-vfo-split--desktop  (34/34 assertions)');
  }, 30_000);
});
