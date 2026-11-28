import { describe, expect, it } from 'vitest';
import { ESLint } from 'eslint';
import { join, relative } from 'node:path';

// import.meta.url is not a file: URL under the vitest DOM environment; use the
// same root resolution as the other source tests in this folder.
const frontend = join(__dirname, '..', '..');
const ruleId = 'journey-waits/condition-waits';
// Existing files without failure evidence retain their current lint debt.
const legacy: Record<string, number> = {
  "tests/e2e/journey/dual-rx-reconnect.spec.ts": 2,
  "tests/e2e/journey/link-loss-veil.spec.ts": 6,
  "tests/e2e/journey/memory-store-clear.spec.ts": 24,
  "tests/e2e/journey/operator-journey.spec.ts": 4,
  "tests/e2e/journey/panorama-follow-request.spec.ts": 7,
  "tests/e2e/journey/panorama-passband-hold.spec.ts": 6,
  "tests/e2e/journey/panorama-scroll-c-geometry.spec.ts": 7,
  "tests/e2e/journey/panorama-zoom-overlay.spec.ts": 7,
  "tests/e2e/journey/ptt-refusal-audio-abort.spec.ts": 3,
  "tests/e2e/journey/rx-makeup-gain.spec.ts": 2
};

describe('journey wait guard (MOR-4887)', { timeout: 120_000 }, () => {
  const eslint = new ESLint({ cwd: frontend });
  it('rejects sleeps and click/reload calls without an explicit timeout', async () => {
    const [result] = await eslint.lintText(`
      await page.waitForTimeout(100);
      await page['waitForTimeout'](100);
      await button.click();
      await button.click({ force: true });
      await page.reload();
      await page.reload({ waitUntil: 'commit' });
    `, { filePath: 'tests/e2e/journey/guard-fixture.spec.ts' });
    expect(result.messages.map(message => message.ruleId)).toEqual(Array(6).fill(ruleId));
  });

  it('accepts explicit action budgets, mouse coordinates, and shared helpers', async () => {
    const [result] = await eslint.lintText(`
      await button.click({ timeout: 10_000 });
      await page.reload({ waitUntil: 'commit', timeout: 10_000 });
      await page.mouse.click(100, 200);
      await clickPanorama(page, button);
      await reloadPanorama(page);
      await awaitRenderPipeline(page);
    `, { filePath: 'tests/e2e/journey/guard-fixture.spec.ts' });
    expect(result.messages).toEqual([]);
  });

  it('allows no new violations across journey files, retaining only the unmodified evidence backlog', async () => {
    const results = await eslint.lintFiles(['tests/e2e/journey/**/*.ts']);
    const actual: Record<string, number> = {};
    for (const result of results) {
      const hits = result.messages.filter(message => message.ruleId === ruleId);
      expect(result.messages.filter(message => message.ruleId !== ruleId)).toEqual([]);
      if (hits.length) actual[relative(frontend, result.filePath)] = hits.length;
    }
    expect(actual).toEqual(legacy);
  });
});
