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
import { measuredPbtRawToHz, deriveIfShift } from '$lib/radio/filter-controls';

// MOR-2497: the derived IF-shift feedback converts each PBT raw on the
// measured lattice at the OBSERVED width and the mode's declared step. The
// fixture radio is USB at filter 3600 (50 Hz step): raw 131 reads +50 Hz,
// raw 140 reads +150 Hz.
const PBT_WIDTH_HZ = 3600;
const PBT_STEP_HZ = 50;
const pbtHz = (raw: number, stepHz: number = PBT_STEP_HZ) => measuredPbtRawToHz(raw, PBT_WIDTH_HZ, stepHz);
const fresh = (marker = 5) => ({
  storePath: 'fixture', observed: true, freshness: 'fresh' as const,
  availability: 'available' as const, lastObservedMonotonic: marker,
});
const state = (over: Record<string, unknown> = {}): ServerState => ({
  stateContractVersion: 1, providerGeneration: 3, active: 'MAIN',
  main: { mode: 'USB', dataMode: 0, filterWidth: PBT_WIDTH_HZ, pbtInner: 131, pbtOuter: 140, ifShift: 60 },
  sub: { mode: 'USB', dataMode: 0, filterWidth: PBT_WIDTH_HZ, pbtInner: 132, pbtOuter: 141, ifShift: 61 },
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
  filterConfig: { USB: { pbtStepHz: PBT_STEP_HZ } },
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
    const expected = deriveIfShift(pbtHz(131)!, pbtHz(140)!);
    expect(getIfShiftControlFeedback(connected)).toMatchObject({
      confirmed: expected, domain: 'hz', phase: 'idle', availability: 'available', busy: false,
    });
  });

  it('derives a pending target from whichever PBT side is busy, confirmed for the other', () => {
    h.state = state(); h.caps = pbtCaps();
    h.commands = [command(PBT_INNER_COMMAND_DESCRIPTOR, 'value', 150)];
    const expectedTarget = deriveIfShift(pbtHz(150)!, pbtHz(140)!);
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

  it('is unavailable when no mode declares a step (legacy payload): no honest raw->Hz conversion (MOR-2497)', () => {
    // The retired +/-1200 proportional scale used to fabricate a reading
    // here. On the lattice path a payload without `pbtStepHz` converts
    // nothing — the derived feedback fails closed, the same refusal the
    // reading path already applies.
    h.state = state();
    h.caps = { ...pbtCaps(), filterConfig: {} } as unknown as Capabilities;
    expect(getIfShiftControlFeedback(connected)).toMatchObject({
      confirmed: null, target: null, requestedTarget: null,
      domain: 'hz', phase: 'unavailable', availability: 'unavailable',
    });
  });

  it('is unavailable when the observed filter width forms no lattice (width unobserved)', () => {
    const stateNoWidth = state({
      main: { mode: 'USB', dataMode: 0, pbtInner: 131, pbtOuter: 140, ifShift: 60 },
    });
    h.state = stateNoWidth; h.caps = pbtCaps();
    expect(getIfShiftControlFeedback(connected).availability).toBe('unavailable');
  });

  it('resolves the "-D" variant\'s own step when dataMode > 0 (MOR-2497 review pin)', () => {
    // USB-D declares a 100 Hz step, USB a 50 Hz one. With dataMode 1 the
    // resolver must pick USB-D: at width 3600, raws 160/128 read +500/0 on
    // the 100 Hz lattice (confirmed 250) but +450/0 on the 50 Hz one
    // (confirmed 225) — an adapter ignoring dataMode lands on 225 and this
    // pin fails.
    h.state = state({
      main: { mode: 'USB', dataMode: 1, filterWidth: PBT_WIDTH_HZ, pbtInner: 160, pbtOuter: 128, ifShift: 60 },
    });
    h.caps = {
      ...pbtCaps(),
      filterConfig: { 'USB-D': { pbtStepHz: 100 }, USB: { pbtStepHz: PBT_STEP_HZ } },
    } as unknown as Capabilities;
    const expected = deriveIfShift(pbtHz(160, 100)!, pbtHz(128, 100)!);
    expect(expected).toBe(250);
    expect(getIfShiftControlFeedback(connected)).toMatchObject({
      confirmed: expected, domain: 'hz', phase: 'idle', availability: 'available',
    });
  });

  it('reports the non-confirmed outcome, not confirmed, when one PBT side confirms and the other fails', () => {
    h.state = state(); h.caps = pbtCaps();
    h.commands = [
      command(PBT_INNER_COMMAND_DESCRIPTOR, 'value', 150, { status: 'confirmed' }),
      command(PBT_OUTER_COMMAND_DESCRIPTOR, 'value', 160, { status: 'failed', error: 'echo mismatch' }),
    ];
    const feedback = getIfShiftControlFeedback(connected);
    expect(feedback.busy).toBe(false);
    expect(feedback.phase).toBe('failed');
    expect(feedback.outcome).toMatchObject({ phase: 'failed' });
  });

  it('reports timed-out (not confirmed) when the confirmed side is the other one', () => {
    h.state = state(); h.caps = pbtCaps();
    h.commands = [
      command(PBT_INNER_COMMAND_DESCRIPTOR, 'value', 150, { status: 'timed-out' }),
      command(PBT_OUTER_COMMAND_DESCRIPTOR, 'value', 160, { status: 'confirmed' }),
    ];
    const feedback = getIfShiftControlFeedback(connected);
    expect(feedback.busy).toBe(false);
    expect(feedback.phase).toBe('timed-out');
    expect(feedback.outcome).toMatchObject({ phase: 'timed-out' });
  });

  it('reports confirmed with the derived Hz value when both PBT sides confirm', () => {
    h.state = state(); h.caps = pbtCaps();
    h.commands = [
      command(PBT_INNER_COMMAND_DESCRIPTOR, 'value', 150, { status: 'confirmed' }),
      command(PBT_OUTER_COMMAND_DESCRIPTOR, 'value', 160, { status: 'confirmed' }),
    ];
    const expected = deriveIfShift(pbtHz(131)!, pbtHz(140)!);
    const feedback = getIfShiftControlFeedback(connected);
    expect(feedback.busy).toBe(false);
    expect(feedback.phase).toBe('confirmed');
    expect(feedback.outcome).toMatchObject({ phase: 'confirmed' });
    expect(feedback.confirmed).toBe(expected);
  });

  it('while one side merely acknowledged and the other is only submitted, is never more settled than the submitted side', () => {
    h.state = state(); h.caps = pbtCaps();
    h.commands = [
      command(PBT_INNER_COMMAND_DESCRIPTOR, 'value', 150, { status: 'acknowledged' }),
      command(PBT_OUTER_COMMAND_DESCRIPTOR, 'value', 160, { status: 'pending' }),
    ];
    const feedback = getIfShiftControlFeedback(connected);
    expect(feedback.busy).toBe(true);
    expect(feedback.phase).toBe('submitted');
    expect(feedback.phase).not.toBe('awaiting-confirmation');
  });

  it('ranks a held (queued) side as less advanced than a merely awaiting-confirmation side', () => {
    h.state = state(); h.caps = pbtCaps();
    h.commands = [
      command(PBT_INNER_COMMAND_DESCRIPTOR, 'value', 150, {
        status: 'acknowledged',
        hold: {
          commandId: PBT_INNER_COMMAND_DESCRIPTOR.intentName, originalEpoch: 7, eventEpoch: 1,
          kind: 'held', reason: 'tx_active', expiresAt: 999,
        },
      }),
      command(PBT_OUTER_COMMAND_DESCRIPTOR, 'value', 160, { status: 'acknowledged' }),
    ];
    const feedback = getIfShiftControlFeedback(connected);
    expect(feedback.busy).toBe(true);
    expect(feedback.phase).toBe('queued');
    expect(feedback.phase).not.toBe('awaiting-confirmation');
  });

  it('composes a transitionId that changes with either half, distinct across two gestures', () => {
    h.state = state(); h.caps = pbtCaps();
    h.commands = [
      command(PBT_INNER_COMMAND_DESCRIPTOR, 'value', 150, { id: 'gesture-a' }),
      command(PBT_OUTER_COMMAND_DESCRIPTOR, 'value', 160),
    ];
    const first = getIfShiftControlFeedback(connected);
    h.commands = [
      command(PBT_INNER_COMMAND_DESCRIPTOR, 'value', 150, { id: 'gesture-b' }),
      command(PBT_OUTER_COMMAND_DESCRIPTOR, 'value', 160),
    ];
    const second = getIfShiftControlFeedback(connected);
    expect(first.transitionId).not.toBe(second.transitionId);
    expect(first.lifecycleId ?? first.transitionId).not.toBe(second.lifecycleId ?? second.transitionId);

    h.commands = [
      command(PBT_INNER_COMMAND_DESCRIPTOR, 'value', 150),
      command(PBT_OUTER_COMMAND_DESCRIPTOR, 'value', 160, { id: 'gesture-c' }),
    ];
    const third = getIfShiftControlFeedback(connected);
    h.commands = [
      command(PBT_INNER_COMMAND_DESCRIPTOR, 'value', 150),
      command(PBT_OUTER_COMMAND_DESCRIPTOR, 'value', 160, { id: 'gesture-d' }),
    ];
    const fourth = getIfShiftControlFeedback(connected);
    expect(third.transitionId).not.toBe(fourth.transitionId);
    expect(third.lifecycleId ?? third.transitionId).not.toBe(fourth.lifecycleId ?? fourth.transitionId);
  });
});
