/**
 * MOR-2111 — repeater panel family conformance (the claim for the five
 * intents added in `claimed.ts`), the pending tone-mode target those intents
 * leave in flight, and the pure `$lib/radio/repeater-transitions` helpers.
 *
 * `makeRepeaterHandlers()` takes the mode/shift/frequency as explicit
 * arguments, so the dispatch is pure and needs no fixture field-status gate: every
 * case below asserts the exact frames for a selector transition, with the
 * receiver on every command.
 *
 *   OFF  → TONE   set_repeater_tone on:true
 *   OFF  → TSQL   set_repeater_tone on:true, set_repeater_tsql on:true
 *   TONE → TSQL   set_repeater_tsql on:true
 *   TSQL → TONE   set_repeater_tsql on:false
 *   TONE → OFF    set_repeater_tone on:false
 *   TSQL → OFF    set_repeater_tsql on:false, set_repeater_tone on:false
 *
 * `set_tsql_freq` is dispatched only in TSQL; `set_tone_freq` otherwise.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { expectFrames, expectRefusal, h } from './conformance/harness';
import { makeRepeaterHandlers } from '../../commands/panel-commands';
import { getPendingRepeaterTone } from '../panel-adapters';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';
import {
  formatToneHz,
  shiftDirection,
  shiftFromDirection,
  stepToneFreq,
} from '$lib/radio/repeater-transitions';

const handlers = makeRepeaterHandlers();

describe('makeRepeaterHandlers tone-mode transition frames (MOR-2111)', () => {
  beforeEach(() => h.sendCommand.mockClear());
  afterEach(() => resetCommandLifecycle());

  const transitions: ReadonlyArray<{
    readonly label: string;
    readonly from: 'off' | 'tone' | 'tsql';
    readonly to: 'off' | 'tone' | 'tsql';
    readonly frames: Array<[string, Record<string, unknown>]>;
  }> = [
    { label: 'OFF → TONE', from: 'off', to: 'tone',
      frames: [['set_repeater_tone', { on: true, receiver: 0 }]] },
    { label: 'OFF → TSQL', from: 'off', to: 'tsql',
      frames: [['set_repeater_tone', { on: true, receiver: 0 }],
        ['set_repeater_tsql', { on: true, receiver: 0 }]] },
    { label: 'TONE → TSQL', from: 'tone', to: 'tsql',
      frames: [['set_repeater_tsql', { on: true, receiver: 0 }]] },
    { label: 'TSQL → TONE', from: 'tsql', to: 'tone',
      frames: [['set_repeater_tsql', { on: false, receiver: 0 }]] },
    { label: 'TONE → OFF', from: 'tone', to: 'off',
      frames: [['set_repeater_tone', { on: false, receiver: 0 }]] },
    { label: 'TSQL → OFF', from: 'tsql', to: 'off',
      frames: [['set_repeater_tsql', { on: false, receiver: 0 }],
        ['set_repeater_tone', { on: false, receiver: 0 }]] },
  ];

  for (const t of transitions) {
    it(`${t.label} dispatches the minimal representable frames`, () => {
      expectFrames(() => handlers.onToneModeChange(t.from, t.to, 0), t.frames);
    });
  }

  it('a self-transition dispatches nothing', () => {
    expectRefusal(() => handlers.onToneModeChange('off', 'off', 0));
    expectRefusal(() => handlers.onToneModeChange('tone', 'tone', 0));
    expectRefusal(() => handlers.onToneModeChange('tsql', 'tsql', 0));
  });

  it('the receiver rides every command', () => {
    expectFrames(() => handlers.onToneModeChange('off', 'tsql', 1), [
      ['set_repeater_tone', { on: true, receiver: 1 }],
      ['set_repeater_tsql', { on: true, receiver: 1 }],
    ]);
  });
});

describe('makeRepeaterHandlers shift and tone-frequency frames (MOR-2111)', () => {
  beforeEach(() => h.sendCommand.mockClear());
  afterEach(() => resetCommandLifecycle());

  it.each([
    ['simplex', 0, 0], ['plus', 1, 1], ['minus', 2, 0],
  ] as const)('shift %s maps to OS direction %i (receiver %i)', (shift, direction, receiver) => {
    expectFrames(() => handlers.onShiftChange(shift, receiver), [['set_repeater_shift', { direction, receiver }]]);
  });

  it('tone frequency writes set_tone_freq in TONE/OFF', () => {
    expectFrames(() => handlers.onToneFreqChange(8850, false, 0), [['set_tone_freq', { freq: 8850, receiver: 0 }]]);
  });

  it('tone frequency writes set_tsql_freq in TSQL', () => {
    expectFrames(() => handlers.onToneFreqChange(8850, true, 1), [['set_tsql_freq', { freq: 8850, receiver: 1 }]]);
  });
});

describe('getPendingRepeaterTone collapses an in-flight transition to its target (MOR-2111)', () => {
  beforeEach(() => h.sendCommand.mockClear());
  afterEach(() => resetCommandLifecycle());

  // Both two-command transitions leave both commands in flight at once.
  it('TSQL → OFF (tsql off and tone off both pending) reads off', () => {
    handlers.onToneModeChange('tsql', 'off', 0);
    expect(getPendingRepeaterTone(0)).toBe('off');
  });

  it('OFF → TSQL (tone on and tsql on both pending) reads tsql', () => {
    handlers.onToneModeChange('off', 'tsql', 1);
    expect(getPendingRepeaterTone(1)).toBe('tsql');
    expect(getPendingRepeaterTone(0)).toBeNull();
  });

  it.each([
    ['off', 'tone', 'tone'], ['tone', 'tsql', 'tsql'], ['tsql', 'tone', 'tone'], ['tone', 'off', 'off'],
  ] as const)('%s → %s (one command pending) reads %s', (from, to, expected) => {
    handlers.onToneModeChange(from, to, 0);
    expect(getPendingRepeaterTone(0)).toBe(expected);
  });
});

describe('repeater-transitions pure helpers (MOR-2111)', () => {
  it('shiftDirection and shiftFromDirection map the four OS directions both ways', () => {
    expect(shiftDirection('simplex')).toBe(0);
    expect(shiftDirection('plus')).toBe(1);
    expect(shiftDirection('minus')).toBe(2);
    expect(shiftDirection('ars')).toBe(3);
    expect([0, 1, 2, 3].map(shiftFromDirection)).toEqual(['simplex', 'plus', 'minus', 'ars']);
    expect([-1, 4, 1.5].map(shiftFromDirection)).toEqual([null, null, null]);
  });

  it('stepToneFreq clamps at both ends and never wraps', () => {
    const tones = [6700, 8850, 10000];
    expect(stepToneFreq(8850, tones, 1)).toBe(10000);
    expect(stepToneFreq(8850, tones, -1)).toBe(6700);
    expect(stepToneFreq(10000, tones, 1)).toBe(10000);
    expect(stepToneFreq(6700, tones, -1)).toBe(6700);
  });

  it('stepToneFreq moves an off-chart value to the nearest chart value in the pressed direction', () => {
    const tones = [6700, 8850, 10000];
    expect(stepToneFreq(7000, tones, 1)).toBe(8850);
    expect(stepToneFreq(7000, tones, -1)).toBe(6700);
    expect(stepToneFreq(9999, tones, 1)).toBe(10000);
    expect(stepToneFreq(9999, tones, -1)).toBe(8850);
  });

  it('stepToneFreq clamps an off-chart value beyond the chart to the end in the pressed direction', () => {
    const tones = [6700, 8850, 10000];
    expect(stepToneFreq(5000, tones, -1)).toBe(6700);
    expect(stepToneFreq(5000, tones, 1)).toBe(6700);
    expect(stepToneFreq(20000, tones, 1)).toBe(10000);
    expect(stepToneFreq(20000, tones, -1)).toBe(10000);
    expect(stepToneFreq(8850, [], 1)).toBe(8850);
  });

  it('formatToneHz renders centiHz with one decimal', () => {
    expect(formatToneHz(8850)).toBe('88.5');
    expect(formatToneHz(10000)).toBe('100.0');
    expect(formatToneHz(6700)).toBe('67.0');
  });
});
