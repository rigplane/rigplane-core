/**
 * MOR-1890 — the refusal-code resolver shared by adapters and semantic.
 * Fails closed on anything outside the catalog so raw server text falls
 * back verbatim; pins the kebab-case ↔ catalog-key pairing moved out of
 * `semantic/rx-tx-surface.ts` (layer-rule fix).
 */
import { describe, expect, it } from 'vitest';
import { t } from '../index';
import { BLOCKED_REASON_KEY, blockedReasonLabel } from '../blocked-reasons';

describe('blockedReasonLabel (MOR-1890)', () => {
  it.each([
    ['tx-target-unknown', 'core.band.tx.reason.targetUnknown'],
    ['tx-permit-denied', 'core.band.tx.reason.outOfBand'],
    ['tx-permit-unknown', 'core.rxTx.blocked.permitUnknown'],
    ['tx-fault', 'core.rxTx.blocked.fault'],
    ['tx-busy', 'core.rxTx.blocked.busy'],
    ['radio-transmitting', 'core.rxTx.blocked.radioTransmitting'],
    ['rf-state-unknown', 'core.rxTx.blocked.rfStateUnknown'],
  ])('resolves %s through catalog key %s', (code, key) => {
    expect(blockedReasonLabel(code)).toBe(t(key));
  });

  it('returns undefined for an unrecognised code, keeping the raw server fallback', () => {
    expect(blockedReasonLabel('not a semantic code')).toBeUndefined();
  });

  it('never looks up prototype junk', () => {
    expect(blockedReasonLabel('constructor')).toBeUndefined();
    expect(blockedReasonLabel('toString')).toBeUndefined();
    expect(blockedReasonLabel('hasOwnProperty')).toBeUndefined();
  });

  it('keeps the exported table aligned with the resolver', () => {
    for (const code of Object.keys(BLOCKED_REASON_KEY)) {
      expect(blockedReasonLabel(code)).toBeDefined();
    }
  });
});
