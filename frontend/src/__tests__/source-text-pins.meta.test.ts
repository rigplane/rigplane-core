import { describe, expect, it } from 'vitest';
// @ts-ignore -- the ledger is an intentionally plain Node ESM data module.
import { SOURCE_TEXT_PIN_BASELINE } from '../../scripts/source-text-pin-baseline.mjs';
// @ts-ignore -- the inventory is an intentionally plain Node ESM script.
import { auditDigests, auditTestSource, scanSourceTextPins } from '../../scripts/source-text-pin-inventory.mjs';

const baseline: readonly string[] = SOURCE_TEXT_PIN_BASELINE;
const scan = scanSourceTextPins() as { files: string[]; pins: string[]; digests: string[] };

describe('source-text pin ledger', () => {
  it('is sorted, unique and frozen', () => {
    expect(baseline).toEqual([...new Set(baseline)].sort());
    expect(Object.isFrozen(baseline)).toBe(true);
  });

  it('has no test that reads source text outside the ledger', () => {
    const added = scan.pins.filter((pin) => !baseline.includes(pin));
    expect(added, 'new source-text test: assert behaviour instead').toEqual([]);
  });

  it('has no stale ledger row', () => {
    const stale = baseline.filter((pin) => !scan.pins.includes(pin));
    expect(stale, 'test no longer reads source text: delete its ledger row').toEqual([]);
  });

  it('has no digest computed over a file read', () => {
    expect(scan.digests, 'source digests are banned, not ledgered').toEqual([]);
  });
});

describe('source-text pin detector', () => {
  const file = 'src/x.test.ts';

  it('ignores a behavioural test and pins direct, derived and ?raw reads', () => {
    expect(auditTestSource(file, `
      it('sends one command', () => { expect(ws.sent).toHaveLength(1); });
    `)).toEqual([]);
    expect(auditTestSource(file, `
      import raw from './Panel.svelte?raw';
      const source = readFileSync('src/Panel.svelte', 'utf8');
      const css = source.slice(source.indexOf('<style>'));
      describe('panel', () => {
        it('direct', () => { expect(readFileSync('a.svelte', 'utf8')).toContain('x'); });
        it.each([1])('derived %s', () => { expect(css).toContain('x'); });
        test('raw', () => { expect(raw).toContain('x'); });
        it('mounts', () => { expect(render()).toBeTruthy(); });
      });
    `)).toEqual(['src/x.test.ts::panel > direct', 'src/x.test.ts::panel > derived %s', 'src/x.test.ts::panel > raw']);
  });

  it('finds digests over reads and exempts vendored fixtures only', () => {
    expect(auditDigests(file, `
      const source = readFileSync('src/a.ts', 'utf8');
      createHash('sha256').update(source.slice(0, 9)).digest('hex');
      createHash('sha256').update(readFileSync('component-kit-api/fixtures/k/src/A.svelte')).digest('hex');
      createHash('sha256').update(JSON.stringify(view)).digest('hex');
    `)).toEqual(['src/x.test.ts:3']);
  });
});
