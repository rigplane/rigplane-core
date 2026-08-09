import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const panelAdaptersSource = readFileSync(
  'src/lib/runtime/adapters/panel-adapters.ts',
  'utf8',
);
const mobileLayoutSource = readFileSync(
  'src/components-v2/layout/MobileRadioLayout.svelte',
  'utf8',
);
const layoutUtilsSource = readFileSync(
  'src/components-v2/layout/layout-utils.ts',
  'utf8',
);

describe('MOR-1409 dead meter adapter graph', () => {
  it('has no production consumer, allocation, or projection residue', () => {
    expect(mobileLayoutSource).not.toContain('makeMeterHandlers');
    for (const retired of [
      'makeMeterHandlers',
      'getMeterHandlers',
      'deriveMeterProps',
      'toMeterProps',
      'MeterProps',
    ]) {
      expect(panelAdaptersSource).not.toContain(retired);
    }
    expect(layoutUtilsSource).not.toContain('meterSource');
  });
});
