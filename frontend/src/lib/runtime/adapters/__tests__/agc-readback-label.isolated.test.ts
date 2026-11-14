/**
 * MOR-4436 — readable-but-not-settable AGC modes.
 *
 * A radio can REPORT a mode its panel selected that the protocol cannot
 * command (G7250 Medium/Auto). `projectAgcReadback` must then return a
 * `readbackLabel` with `mode` left `undefined` (no button selected) while
 * `toAgcProps` surfaces the same label as `agcReadbackLabel` — both only
 * when the contract names the code in `labels` (and readback modes) but
 * NOT in `agcModes`. A code outside both lists keeps today's behaviour.
 */
import { describe, expect, it } from 'vitest';

import { projectAgcReadback } from '../radio-view-model-adapter';
import { agcReadbackLabelFor, toAgcProps } from '../../props/panel-props';

// G7250-like contract: modes = settable buttons, readback adds code 2.
function g7250Caps() {
  return {
    capabilities: ['agc'],
    agcModes: [0, 1, 3],
    agcLabels: { '0': 'OFF', '1': 'FAST', '3': 'SLOW' },
    agcReadback: { modes: [0, 1, 2, 3], labels: { '2': 'MID' } },
  } as any;
}

function stateWithAgc(value: number, availability = 'available' as const) {
  return {
    active: 'MAIN',
    main: { agc: value },
    sub: {},
    fieldStatus: {
      'main.agc': {
        storePath: 'main.agc',
        observed: availability === 'available',
        freshness: availability === 'available' ? 'fresh' : 'unknown',
        availability,
      },
    },
  } as any;
}

describe('projectAgcReadback readback labels (MOR-4436)', () => {
  it('selects the button for a settable code and sets no readback label', () => {
    const result = projectAgcReadback(g7250Caps(), 1);
    expect(result.mode).toBe(1);
    expect(result.indicatorValue).toBe('FAST');
    expect(result.readbackLabel).toBeUndefined();
  });

  it('returns the label with no selected mode for a readback-only code', () => {
    const result = projectAgcReadback(g7250Caps(), 2);
    expect(result.mode).toBeUndefined();
    expect(result.readbackLabel).toBe('MID');
    expect(result.indicatorValue).toBe('MID');
  });

  it('keeps today\'s behaviour for a code outside both lists', () => {
    const result = projectAgcReadback(g7250Caps(), 9);
    expect(result.mode).toBeUndefined();
    expect(result.readbackLabel).toBeUndefined();
  });

  it('leaves a readback-only code unlabeled when the contract has no labels (FTX-1 shape)', () => {
    const caps = {
      capabilities: ['agc'],
      agcModes: [0, 1, 2, 3, 4],
      agcLabels: { '0': 'OFF', '1': 'FAST', '2': 'MID', '3': 'SLOW', '4': 'AUTO' },
      agcReadback: {
        modes: [0, 1, 2, 3, 4, 5, 6],
        autoMode: 4,
        autoSpeedLabels: { '4': 'FAST', '5': 'MID', '6': 'SLOW' },
      },
    } as any;
    const result = projectAgcReadback(caps, 5);
    expect(result).toEqual({ mode: 4, indicatorValue: 'AUTO', autoMode: 4, autoSpeed: 'MID' });
    expect(result.readbackLabel).toBeUndefined();
  });

  it('is unchanged without a contract (IC-7300 shape)', () => {
    const caps = {
      capabilities: ['agc'],
      agcModes: [1, 2, 3],
      agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' },
      agcReadback: null,
    } as any;
    expect(projectAgcReadback(caps, 2)).toEqual({ mode: 2, indicatorValue: 'MID' });
    expect(projectAgcReadback(caps, 9)).toEqual({});
  });
});

describe('agcReadbackLabelFor / toAgcProps (MOR-4436)', () => {
  it('names only the readback-only code with a contract label', () => {
    const caps = g7250Caps();
    expect(agcReadbackLabelFor(caps, 2)).toBe('MID');
    expect(agcReadbackLabelFor(caps, 1)).toBeUndefined();
    expect(agcReadbackLabelFor(caps, 9)).toBeUndefined();
    expect(agcReadbackLabelFor(null, 2)).toBeUndefined();
  });

  it('toAgcProps carries the label while buttons stay on the settable modes', () => {
    const props = toAgcProps(stateWithAgc(2), g7250Caps());
    expect(props.agcMode).toBe(2);
    expect(props.agcModes).toEqual([0, 1, 3]);
    expect(props.agcReadbackLabel).toBe('MID');
    expect(props.hasAgc).toBe(true);
  });

  it('toAgcProps omits the label for settable and unknown codes', () => {
    expect(toAgcProps(stateWithAgc(1), g7250Caps()).agcReadbackLabel).toBeUndefined();
    expect(toAgcProps(stateWithAgc(9), g7250Caps()).agcReadbackLabel).toBeUndefined();
    expect('agcReadbackLabel' in toAgcProps(stateWithAgc(1), g7250Caps())).toBe(false);
  });

  it('toAgcProps omits the label when the field is unavailable', () => {
    const props = toAgcProps(stateWithAgc(2, 'missing' as const), g7250Caps());
    expect(props.agcReadbackLabel).toBeUndefined();
    expect(props.agcMode).toBeNaN();
  });
});
