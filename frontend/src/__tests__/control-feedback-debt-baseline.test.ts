import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
// @ts-ignore -- the baseline is an intentionally plain Node ESM data module.
import * as baseline from '../../scripts/control-feedback-debt-baseline.mjs';

const { CONTROL_FEEDBACK_DEBT_BASELINE } = baseline;

describe('control feedback debt baseline (MOR-1714)', () => {
  it('exports exactly 39 unique identities in deterministic order', () => {
    expect(CONTROL_FEEDBACK_DEBT_BASELINE).toHaveLength(39);
    expect(new Set(CONTROL_FEEDBACK_DEBT_BASELINE).size).toBe(39);
    expect(CONTROL_FEEDBACK_DEBT_BASELINE).toEqual([...CONTROL_FEEDBACK_DEBT_BASELINE].sort());
    const digest = createHash('sha256').update(CONTROL_FEEDBACK_DEBT_BASELINE.join('\n')).digest('hex');
    expect(digest).toBe('fde5f1fc7f3e26c929cedc83cf95d61301d0904f1e9840d0958eb2136ee4f2a9');
  });

  it('exposes no mutable membership collection', () => {
    expect(Object.keys(baseline)).toEqual(['CONTROL_FEEDBACK_DEBT_BASELINE']);
    expect(Object.isFrozen(CONTROL_FEEDBACK_DEBT_BASELINE)).toBe(true);
    expect(() => (CONTROL_FEEDBACK_DEBT_BASELINE as unknown as string[]).push('src/semantic/NewDebt.svelte::input::unlabelled::value')).toThrow(TypeError);
    expect(CONTROL_FEEDBACK_DEBT_BASELINE).toHaveLength(39);
  });

  it('uses only stable public identity fields', () => {
    for (const identity of CONTROL_FEEDBACK_DEBT_BASELINE) {
      expect(identity).toMatch(/^src\/[A-Za-z0-9._/-]+\.svelte::(?:ValueControl|input)::.+::.+$/);
    }
  });
});
