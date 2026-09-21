/**
 * MOR-2513 — FTX-1 live-payload adapter conformance.
 *
 * Companion to `stores/__tests__/mor2513-null-live-payload.test.ts` (the
 * ingestion seam) and `RadioLayout.isolated.test.ts`'s live-payload mount
 * (the composition): this file proves the VIEW MODEL built from the real
 * captured payload — 108 unobserved-null leaves, `ab_shared` dual-receiver
 * topology, SUB controls partly undeclared — validates against the real
 * `validateRadioViewModel`, and that every nulled leaf reaches the contract
 * as its explicit unknown branch: `main.filter === null` must yield an
 * unknown filter reading with the mode-keyed filter table still resolved
 * from the OBSERVED mode, never indexed with null.
 */
import { describe, expect, it } from 'vitest';
import { validateRadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel, type MetersTxAuthority } from '../radio-view-model-adapter';
import {
  FTX1_CAPABILITIES,
  FTX1_STATE,
  FTX1_STATE_FULLY_UNOBSERVED,
} from './fixtures/ftx1-profile';

const TX_OFF: MetersTxAuthority = { radioTx: 'off', txRisk: 'none' };

// The two captures come from different bench sessions (capabilities at
// backend 60d05a42, state at 86187ed3); `validIdentity` requires the pair
// to name one provider generation, so the caps' is aligned to the state's.
const CAPS = { ...FTX1_CAPABILITIES, providerGeneration: FTX1_STATE.providerGeneration };

describe('FTX-1 live payload — view model (MOR-2513)', () => {
  it('builds a validator-clean model from the live capture', () => {
    expect(() =>
      validateRadioViewModel(toRadioViewModel(FTX1_STATE, CAPS, TX_OFF)),
    ).not.toThrow();
  });

  it('carries one unslotted VFO per structural receiver with live MAIN readings', () => {
    const view = validateRadioViewModel(
      toRadioViewModel(FTX1_STATE, CAPS, TX_OFF));
    expect(view.vfos.map((vfo) => vfo.receiver)).toEqual(['MAIN', 'SUB']);
    expect(view.vfos.every((vfo) => vfo.slot.kind === 'unslotted')).toBe(true);
    const main = view.vfos[0]!;
    expect(main.frequencyHz).toBe(FTX1_STATE.main!.freqHz);
    expect(main.mode).toBe('USB');
    expect(main.filter).toBeNull();
  });

  it('maps the unobserved main.filter onto an unknown reading while the mode-keyed config stays resolved', () => {
    const view = validateRadioViewModel(
      toRadioViewModel(FTX1_STATE, CAPS, TX_OFF));
    expect(view.modeFilter).toBeDefined();
    expect(view.modeFilter!.currentMode.reading).toEqual({ status: 'known', value: 'USB' });
    expect(view.modeFilter!.currentFilter.reading.status).toBe('unknown');
    expect(view.modeFilter!.filterWidth.reading).toEqual({ status: 'known', value: 3000 });
    expect(view.modeFilter!.activeFilterConfiguration).not.toBeNull();
  });

  it('exposes meters with known Vd and unknown TX leaves over the null readings', () => {
    const view = validateRadioViewModel(
      toRadioViewModel(FTX1_STATE, CAPS, TX_OFF));
    expect(FTX1_STATE.powerMeter).toBeNull();
    expect(view.meters).toBeDefined();
    expect(view.meters!.drainVoltage.reading).toEqual({ status: 'known', value: 13.8 });
    expect(view.meters!.power.reading.status).toBe('unknown');
  });

  it('marks SUB att structurally absent — the live capture declares it undeclared', () => {
    const view = validateRadioViewModel(
      toRadioViewModel(FTX1_STATE, CAPS, TX_OFF));
    const sub = view.receiverIndicators!.find((item) => item.receiver === 'SUB')!;
    expect(sub.attenuator.availability.structural).toBe(false);
  });
});

describe('FTX-1 fully-unobserved payload — view model (MOR-2513)', () => {
  it('still builds a validator-clean model with every nullable leaf null', () => {
    expect(() =>
      validateRadioViewModel(
        toRadioViewModel(FTX1_STATE_FULLY_UNOBSERVED, CAPS, TX_OFF)),
    ).not.toThrow();
  });

  it('degrades every VFO reading and the mode/filter facts to unknown', () => {
    const view = validateRadioViewModel(
      toRadioViewModel(FTX1_STATE_FULLY_UNOBSERVED, CAPS, TX_OFF));
    for (const vfo of view.vfos) {
      expect(vfo.frequencyHz).toBeNull();
      expect(vfo.mode).toBeNull();
      expect(vfo.filter).toBeNull();
      expect(vfo.display!.frequencyHz.state).toBe('unknown');
    }
    expect(view.modeFilter!.currentMode.reading.status).toBe('unknown');
    expect(view.modeFilter!.currentFilter.reading.status).toBe('unknown');
    expect(view.modeFilter!.filterWidth.reading.status).toBe('unknown');
    expect(view.modeFilter!.activeFilterConfiguration).toBeNull();
  });
});
