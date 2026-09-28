import { describe, expect, it } from 'vitest';
import { createHash } from 'node:crypto';
// @ts-ignore -- the baseline is an intentionally plain Node ESM data module.
import * as baseline from '../../scripts/control-feedback-debt-baseline.mjs';

const { CONTROL_FEEDBACK_DEBT_BASELINE } = baseline;

describe('control feedback debt baseline (MOR-1714)', () => {
  // MOR-2909: the legacy RitXitPanel Offset row left the radio-backed
  // debt — it rides the shared scalar binding now (feedback-integrated).
  it('exports exactly 38 unique identities in deterministic order', () => {
    expect(CONTROL_FEEDBACK_DEBT_BASELINE).toHaveLength(38);
    expect(new Set(CONTROL_FEEDBACK_DEBT_BASELINE).size).toBe(38);
    expect(CONTROL_FEEDBACK_DEBT_BASELINE).toEqual([...CONTROL_FEEDBACK_DEBT_BASELINE].sort());
    const digest = createHash('sha256').update(CONTROL_FEEDBACK_DEBT_BASELINE.join('\n')).digest('hex');
    expect(digest).toBe('8660601f8fc25fc4a0a58814cd47dbf73600c8709b39e479c8f18ad4ba48066c');
  });

  it('exposes no mutable membership collection', () => {
    expect(Object.keys(baseline)).toEqual(['CONTROL_FEEDBACK_DEBT_BASELINE']);
    expect(Object.isFrozen(CONTROL_FEEDBACK_DEBT_BASELINE)).toBe(true);
    expect(() => (CONTROL_FEEDBACK_DEBT_BASELINE as unknown as string[]).push('src/semantic/NewDebt.svelte::input::unlabelled::value')).toThrow(TypeError);
    expect(CONTROL_FEEDBACK_DEBT_BASELINE).toHaveLength(38);
  });

  it('uses only stable public identity fields', () => {
    for (const identity of CONTROL_FEEDBACK_DEBT_BASELINE) {
      expect(identity).toMatch(/^src\/[A-Za-z0-9._/-]+\.svelte::(?:ValueControl|input)::.+::.+$/);
    }
  });
});
