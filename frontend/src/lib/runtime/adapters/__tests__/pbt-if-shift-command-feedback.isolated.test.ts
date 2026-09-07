/**
 * MOR-2425 — PBT inner/outer and IF-shift state-backed feedback.
 *
 * Mirrors `dsp-command-feedback.isolated.test.ts`'s harness shape. PBT_INNER
 * and PBT_OUTER read/confirm RAW state (0-255 BCD on IC-7300); IF-shift is
 * real (identity-mapped Hz) on a radio that declares `if_shift`, and derived
 * from the two PBT feedbacks (converted raw->Hz) everywhere else.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { CommandLifecycle } from '$lib/stores/commands.svelte';

const h = vi.hoisted(() => ({
  state: null as ServerState | null,
  caps: null as Capabilities | null,
  commands: [] as CommandLifecycle[],
  session: { state: 'connected' as 'connected' | 'disconnected', epoch: 7 },
  active: 'MAIN' as 'MAIN' | 'SUB' | null,
  operational: true,
  indicatorCount: 1,
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get controlSession() { return h.session; },
  },
}));
vi.mock('$lib/stores/commands.svelte', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/stores/commands.svelte')>(),
  getCommandLifecycles: () => h.commands,
  isCommandLifecycleSuperseded: (command: CommandLifecycle) => command.id.endsWith('-old'),
}));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', () => ({
  toRadioViewModel: () => h.active === null
    ? { activeReceiver: { status: 'unknown' }, receiverIndicators: [] }
    : {
      activeReceiver: { status: 'known', receiver: h.active },
      receiverIndicators: Array.from({ length: h.indicatorCount }, () => ({
        receiver: h.active,
        availability: { structural: true, operational: h.operational },
      })),
    },
}));

import {
  IF_SHIFT_COMMAND_DESCRIPTOR, PBT_INNER_COMMAND_DESCRIPTOR, PBT_OUTER_COMMAND_DESCRIPTOR,
} from '$lib/stores/commands.svelte';
import {
  getIfShiftControlFeedback, getPbtInnerControlFeedback, getPbtOuterControlFeedback,
} from '../panel-adapters';
import { pbtRawToHz, deriveIfShift } from '$lib/radio/filter-controls';

const PBT_SCALE = { rawCenter: 128, displayMin: -1200, displayMax: 1200 };
const fresh = (marker = 5) => ({
  storePath: 'fixture', observed: true, freshness: 'fresh' as const,
  availability: 'available' as const, lastObservedMonotonic: marker,
});
const state = (over: Record<string, unknown> = {}): ServerState => ({
  stateContractVersion: 1, providerGeneration: 3, active: 'MAIN',
  main: { pbtInner: 131, pbtOuter: 140, ifShift: 60 },
  sub: { pbtInner: 132, pbtOuter: 141, ifShift: 61 },
  fieldStatus: {
    'main.pbtInner': fresh(), 'main.pbtOuter': fresh(), 'main.ifShift': fresh(),
    'sub.pbtInner': fresh(), 'sub.pbtOuter': fresh(), 'sub.ifShift': fresh(),
  },
  ...over,
} as unknown as ServerState);
const pbtCaps = (overCapabilities: readonly string[] = ['pbt']): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 3, receivers: 1, vfoScheme: 'single',
  capabilities: overCapabilities,
  controls: { pbt_inner: { raw_center: 128, display_min: -1200, display_max: 1200 } },
} as unknown as Capabilities);
const ftx1Caps = (): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 3, receivers: 1, vfoScheme: 'single',
  capabilities: ['if_shift'],
  controls: {},
} as unknown as Capabilities);
const connected = { state: 'connected' as const, epoch: 7 };
const command = (
  descriptor: typeof PBT_INNER_COMMAND_DESCRIPTOR, param: 'value' | 'offset', target: number,
  over: Partial<CommandLifecycle> = {},
): CommandLifecycle => ({
  id: descriptor.intentName, name: descriptor.intentName,
  params: { [param]: target, receiver: h.active === 'SUB' ? 1 : 0 },
  originalEpoch: 7, createdAt: 1, updatedAt: 1, timeoutMs: 5_000,
  status: 'pending', providerGeneration: 3, ...over,
});

function reset(): void {
  h.state = null; h.caps = null; h.commands = [];
  h.session = { state: 'connected', epoch: 7 }; h.active = 'MAIN'; h.operational = true;
  h.indicatorCount = 1;
}

describe('qualified raw PBT inner/outer command feedback', () => {
  afterEach(reset);

  it('is unavailable before authority is present and available once state/caps/session line up', () => {
    h.session = { state: 'disconnected', epoch: -1 };
    expect(getPbtInnerControlFeedback()).toMatchObject({
      confirmed: null, phase: 'unavailable', availability: 'unavailable',
      scope: { control: 'pbt-inner', receiver: 0 },
    });
    h.state = state(); h.caps = pbtCaps(); h.session = connected;
    expect(getPbtInnerControlFeedback(connected)).toMatchObject({
      confirmed: 131, phase: 'idle', availability: 'available',
    });
    expect(getPbtOuterControlFeedback(connected)).toMatchObject({
      confirmed: 140, phase: 'idle', availability: 'available',
    });
  });

  it('projects an exact raw submitted target distinct from the confirmed reading', () => {
    h.state = state(); h.caps = pbtCaps();
    h.commands = [command(PBT_INNER_COMMAND_DESCRIPTOR, 'value', 150)];
    expect(getPbtInnerControlFeedback(connected)).toMatchObject({
      confirmed: 131, target: 150, requestedTarget: 150, phase: 'submitted', busy: true,
    });
  });

  it('is structurally absent on a radio with no pbt capability (FTX-1-shaped caps)', () => {
    h.state = state(); h.caps = ftx1Caps();
    expect(getPbtInnerControlFeedback(connected).availability).toBe('unavailable');
    expect(getPbtOuterControlFeedback(connected).availability).toBe('unavailable');
  });

  it('is structurally absent when the pbt capability is declared without a usable range', () => {
    h.state = state();
    h.caps = pbtCaps();
    h.caps = { ...h.caps, controls: {} } as unknown as Capabilities;
    expect(getPbtInnerControlFeedback(connected).availability).toBe('unavailable');
  });

  it('uses the active SUB receiver scope and field', () => {
    h.active = 'SUB';
    h.state = state({ active: 'SUB' });
    h.caps = { ...pbtCaps(), receivers: 2, vfoScheme: 'main_sub' } as unknown as Capabilities;
    expect(getPbtInnerControlFeedback(connected)).toMatchObject({
      confirmed: 132, scope: { control: 'pbt-inner', receiver: 1 },
    });
    expect(getPbtOuterControlFeedback(connected)).toMatchObject({
      confirmed: 141, scope: { control: 'pbt-outer', receiver: 1 },
    });
  });
});

describe('IF-shift command feedback (real on Yaesu, derived on Icom PBT-only)', () => {
  afterEach(reset);

  it('reads the real descriptor directly on a radio with its own if_shift command', () => {
    h.state = state(); h.caps = ftx1Caps();
    expect(getIfShiftControlFeedback(connected)).toMatchObject({
      confirmed: 60, domain: 'hz', phase: 'idle', availability: 'available',
      scope: { control: 'if-shift', receiver: 0 },
    });
    h.commands = [command(IF_SHIFT_COMMAND_DESCRIPTOR, 'offset', 80)];
    expect(getIfShiftControlFeedback(connected)).toMatchObject({
      confirmed: 60, target: 80, requestedTarget: 80, domain: 'hz', busy: true,
    });
  });

  it('derives confirmed Hz from the two PBT feedbacks on a PBT-only radio', () => {
    h.state = state(); h.caps = pbtCaps();
    const expected = deriveIfShift(pbtRawToHz(131, PBT_SCALE), pbtRawToHz(140, PBT_SCALE));
    expect(getIfShiftControlFeedback(connected)).toMatchObject({
      confirmed: expected, domain: 'hz', phase: 'idle', availability: 'available', busy: false,
    });
  });

  it('derives a pending target from whichever PBT side is busy, confirmed for the other', () => {
    h.state = state(); h.caps = pbtCaps();
    h.commands = [command(PBT_INNER_COMMAND_DESCRIPTOR, 'value', 150)];
    const expectedTarget = deriveIfShift(pbtRawToHz(150, PBT_SCALE), pbtRawToHz(140, PBT_SCALE));
    const feedback = getIfShiftControlFeedback(connected);
    expect(feedback).toMatchObject({ target: expectedTarget, domain: 'hz', busy: true });
    expect(feedback.phase).not.toBe('idle');
  });

  it('is unavailable when the underlying PBT feedback is unavailable (no real if_shift, no usable pbt range)', () => {
    h.state = state();
    h.caps = { ...pbtCaps(), controls: {} } as unknown as Capabilities;
    expect(getIfShiftControlFeedback(connected)).toMatchObject({
      confirmed: null, domain: 'hz', phase: 'unavailable', availability: 'unavailable',
    });
  });

  it('is unavailable when the radio has neither a real if_shift command nor pbt at all', () => {
    h.state = state();
    h.caps = { ...ftx1Caps(), capabilities: [] } as unknown as Capabilities;
    expect(getIfShiftControlFeedback(connected).availability).toBe('unavailable');
  });
});
