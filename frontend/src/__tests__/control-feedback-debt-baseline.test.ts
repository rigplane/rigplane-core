import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
// @ts-ignore -- the baseline is an intentionally plain Node ESM data module.
import { CONTROL_FEEDBACK_DEBT_BASELINE, FROZEN_RADIO_DEBT } from '../../scripts/control-feedback-debt-baseline.mjs';

describe('control feedback debt baseline (MOR-1714)', () => {
  it('exports exactly 39 unique identities in deterministic order', () => {
    expect(CONTROL_FEEDBACK_DEBT_BASELINE).toHaveLength(39);
    expect(FROZEN_RADIO_DEBT.size).toBe(39);
    expect([...FROZEN_RADIO_DEBT]).toEqual(CONTROL_FEEDBACK_DEBT_BASELINE);
    expect(CONTROL_FEEDBACK_DEBT_BASELINE).toEqual([...CONTROL_FEEDBACK_DEBT_BASELINE].sort());
    const digest = createHash('sha256').update(CONTROL_FEEDBACK_DEBT_BASELINE.join('\n')).digest('hex');
    expect(digest).toBe('43bb955cd6dee7b1bff17dd66936244e6c82a9a6cd3ba944735a1b6aed499d1f');
  });

  it('uses only stable public identity fields', () => {
    for (const identity of CONTROL_FEEDBACK_DEBT_BASELINE) {
      expect(identity).toMatch(/^src\/[A-Za-z0-9._/-]+\.svelte::(?:ValueControl|input)::.+::.+$/);
    }
  });
});
