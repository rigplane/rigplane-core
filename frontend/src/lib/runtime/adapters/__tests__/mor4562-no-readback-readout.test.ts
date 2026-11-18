/**
 * MOR-4562 — a single-RX A/B radio with no VFO readback (`vfoScheme: 'ab'`,
 * `vfoReadback: 'none'`) must render the selected VFO's frequency / mode /
 * filter, without inventing a slot letter (MOR-988).
 *
 * Fixture: the owner's real G7250 snapshot (see `fixtures/g7250-profile.ts`).
 */
import { describe, expect, it } from 'vitest';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { G7250_CAPABILITIES, G7250_STATE } from './fixtures/g7250-profile';

describe('G7250 snapshot (ab scheme, vfoReadback none)', () => {
  it('is the shape the bug needs: values fresh, no slot ever observed', () => {
    expect(G7250_CAPABILITIES.vfoScheme).toBe('ab');
    expect(G7250_CAPABILITIES.vfoReadback).toBe('none');
    expect(G7250_STATE.fieldStatus?.['main.freqHz']?.observed).toBe(true);
    expect(G7250_STATE.fieldStatus?.['main.activeSlot']?.observed).not.toBe(true);
    expect(G7250_STATE.fieldStatus?.['main.vfoA.freqHz']?.observed).not.toBe(true);
  });

  it('shows the selected VFO readout with no slot letter', () => {
    const view = toRadioViewModel(G7250_STATE, G7250_CAPABILITIES);
    const main = view!.vfos.find((vfo) => vfo.receiver === 'MAIN' && vfo.slot.kind === 'relative'
      && vfo.slot.role === 'selected');
    expect(main).toBeDefined();
    expect(main!.label).toBe('Selected VFO');
    expect(main!.display.frequencyHz).toMatchObject({ state: 'current', value: G7250_STATE.main!.freqHz });
    expect(main!.display.mode).toMatchObject({ state: 'current', value: G7250_STATE.main!.mode });
    expect(main!.display.filter).toMatchObject({ state: 'current' });
    expect(view!.vfos.some((vfo) => vfo.slot.kind === 'slotted')).toBe(false);
  });

  it('never claims an A or B slot while the radio names none', () => {
    const view = toRadioViewModel(G7250_STATE, G7250_CAPABILITIES);
    expect(view!.vfos.every((vfo) => !/\b[AB]$/.test(vfo.label))).toBe(true);
  });

  it('another radio with the same shape (X6200-style capabilities) takes the same path', () => {
    // Derived from the real snapshot: only the model name differs; the X6200
    // profile declares the same `ab` scheme and no readback.
    const caps = { ...G7250_CAPABILITIES, model: 'X6200' };
    const view = toRadioViewModel(structuredClone(G7250_STATE), caps);
    expect(view!.vfos.find((vfo) => vfo.slot.kind === 'relative')!.display.frequencyHz)
      .toMatchObject({ state: 'current' });
  });
});
