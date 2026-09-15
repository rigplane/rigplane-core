import { describe, expect, it, vi } from 'vitest';
import {
  createContinuousPair,
  createLegacyContinuousPairPolicy,
  createRenderedNativeRangeContinuousPairPolicy,
  nativeRangeContinuousPairPolicy,
  type ContinuousPairInput,
} from '../continuous-pair.svelte';
import type { CommandScalarFeedback, ScalarDomain } from '../continuous-scalar.svelte';

const DOMAIN: ScalarDomain = {
  min: 0, max: 1, step: 0.01, defaultValue: null, fineStepDivisor: 10,
};

const feedback = (
  control: string,
  confirmed: number | null,
  over: Partial<CommandScalarFeedback> = {},
): Readonly<CommandScalarFeedback> => ({
  confirmed, target: null, requestedTarget: null, phase: 'idle', busy: false,
  availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
  providerGeneration: 3,
  sessionEpoch: 7, scope: { control, receiver: 0 }, repeatPolicy: 'latest-target-wins',
  ...over,
});

function readingSetup(
  policy = nativeRangeContinuousPairPolicy,
  over: Partial<Extract<ContinuousPairInput, { evidence: 'reading' }>> = {},
) {
  const requestRf = vi.fn<(value: number) => void>();
  const requestSql = vi.fn<(value: number) => void>();
  let current: Extract<ContinuousPairInput, { evidence: 'reading' }> = {
    evidence: 'reading', domain: DOMAIN, enabled: true, ownerKey: 'rf-sql:main',
    rf: { reading: { status: 'known', value: 1 }, availability: 'available' },
    sql: { reading: { status: 'known', value: 0 }, availability: 'available' },
    requestRf, requestSql, ...over,
  };
  const pair = createContinuousPair(() => current, policy);
  return {
    pair, requestRf, requestSql,
    update: (next: Partial<typeof current>) => { current = { ...current, ...next }; },
  };
}

function commandSetup() {
  const requestRf = vi.fn<(value: number) => void>();
  const requestSql = vi.fn<(value: number) => void>();
  let current: Extract<ContinuousPairInput, { evidence: 'command-feedback' }> = {
    evidence: 'command-feedback', domain: DOMAIN, enabled: true,
    rf: { command: 'set_rf_gain', feedback: feedback('rf-gain', 1) },
    sql: { command: 'set_squelch', feedback: feedback('squelch', 0) },
    requestRf, requestSql,
  };
  const pair = createContinuousPair(
    () => current,
    createLegacyContinuousPairPolicy({ keyboardDebounceMs: 50 }),
  );
  return {
    pair, requestRf, requestSql, read: () => current,
    update: (next: Partial<typeof current>) => { current = { ...current, ...next }; },
  };
}

describe('continuous pair evidence and geometry', () => {
  it('preserves both supplied lanes when invalid geometry disables editing', () => {
    const { pair } = readingSetup(undefined, {
      domain: { ...DOMAIN, max: Number.NaN },
      rf: { reading: { status: 'known', value: 0.4 }, availability: 'available' },
      sql: { reading: { status: 'known', value: 0.2 }, availability: 'available' },
    });
    expect(pair.view).toMatchObject({
      canonical: { rf: 0.4, sql: 0.2 }, position: null, axisDraft: null,
      domainValid: false, editable: false,
      lanes: { rf: { canonical: 0.4 }, sql: { canonical: 0.2 } },
    });
  });

  it('keeps off-axis facts intact while projecting their geometry', () => {
    const { pair } = readingSetup(undefined, {
      rf: { reading: { status: 'known', value: 0.4 }, availability: 'available' },
      sql: { reading: { status: 'known', value: 0.2 }, availability: 'available' },
    });
    expect(pair.view.canonical).toEqual({ rf: 0.4, sql: 0.2 });
    expect(pair.view.position).toBeCloseTo(0.632, 3);
    expect(pair.view.lanes.rf.canonical).toBe(0.4);
  });

  it.each([
    [{ status: 'unknown' } as const, { status: 'known', value: 0 } as const],
    [{ status: 'known', value: Number.NaN } as const, { status: 'known', value: 0 } as const],
  ])('makes pair geometry unknown when either lane is not finite and known', (rf, sql) => {
    const { pair } = readingSetup(undefined, {
      rf: { reading: rf, availability: 'available' },
      sql: { reading: sql, availability: 'available' },
    });
    expect(pair.view).toMatchObject({ position: null, editable: false });
    const lane = pair.view.lanes.rf;
    expect(lane.evidence).toBe('reading');
    if (lane.evidence === 'reading') expect(lane.reading).toBe(rf);
  });

  it('does not re-quantize a fine lane step through the inverse axis', () => {
    vi.useFakeTimers();
    const { pair, requestSql } = readingSetup(
      createLegacyContinuousPairPolicy({ keyboardDebounceMs: 50 }),
    );
    const lease = pair.attachRenderer();
    expect(lease.key({ key: 'ArrowRight', fine: true })).toBe(true);
    vi.advanceTimersByTime(50);
    expect(requestSql).toHaveBeenCalledExactlyOnceWith(0.001);
    expect(pair.view.lanes.sql.localRequested).toBe(0.001);
    vi.useRealTimers();
  });
});

describe('continuous pair request reconciliation', () => {
  it('calls RF then SQL only for lanes changed from effective requested state', () => {
    const order: string[] = [];
    const requestRf = vi.fn(() => order.push('rf'));
    const requestSql = vi.fn(() => order.push('sql'));
    const { pair } = readingSetup(undefined, { requestRf, requestSql });
    const lease = pair.attachRenderer();
    lease.nativeInput(0);
    expect(order).toEqual(['rf']);
    lease.nativeInput(1);
    expect(order).toEqual(['rf', 'rf', 'sql']);
  });

  it('dispatches a canonical reversal after a different local request and deduplicates repeats', () => {
    const { pair, requestSql } = readingSetup();
    const lease = pair.attachRenderer();
    lease.nativeInput(1);
    lease.nativeInput(0.5);
    lease.nativeInput(0.5);
    expect(requestSql.mock.calls).toEqual([[1], [0]]);
    expect(pair.view.lanes.sql.localRequested).toBe(0);
  });

  it('prefers supplied command targets over local records', () => {
    vi.useFakeTimers();
    const { pair, requestSql, read, update } = commandSetup();
    const lease = pair.attachRenderer();
    lease.key({ key: 'ArrowRight', fine: false });
    vi.advanceTimersByTime(50);
    expect(requestSql).toHaveBeenCalledExactlyOnceWith(0.01);
    update({ sql: { ...read().sql, feedback: feedback('squelch', 0, {
      target: 0.02, requestedTarget: 0.02, phase: 'submitted', busy: true,
      lifecycleId: 'sql-1', transitionId: 'sql-submitted',
    }) } });
    lease.key({ key: 'ArrowLeft', fine: false });
    vi.advanceTimersByTime(50);
    expect(requestSql).toHaveBeenLastCalledWith(0.01);
    vi.useRealTimers();
  });

  it('keeps staggered lane outcomes and announcements independent', () => {
    const { pair, read, update } = commandSetup();
    update({
      rf: { ...read().rf, feedback: feedback('rf-gain', 0.8, {
        requestedTarget: 0.8, phase: 'confirmed', outcome: { phase: 'confirmed' },
        lifecycleId: 'rf-1', transitionId: 'rf-confirmed',
      }) },
      sql: { ...read().sql, feedback: feedback('squelch', 0, {
        requestedTarget: 0.3, phase: 'failed', outcome: { phase: 'failed', error: 'rejected' },
        lifecycleId: 'sql-1', transitionId: 'sql-failed',
      }) },
    });
    const first = pair.view;
    expect(first.lanes.rf).toMatchObject({ phase: 'confirmed', error: null });
    expect(first.lanes.sql).toMatchObject({ phase: 'failed', error: 'rejected' });
    expect(first.lanes.rf.announcement).toBe('Confirmed: 0.8');
    expect(first.lanes.sql.announcement).toBe('Failed: 0.3');
    const second = pair.view;
    expect(second.lanes.rf.announcement).toBeNull();
    expect(second.lanes.sql.announcement).toBeNull();
  });

  it('preserves known command readings but disables editing across availability replacement', () => {
    const { pair, read, update } = commandSetup();
    update({ sql: { ...read().sql, feedback: feedback('squelch', 0.2, {
      availability: 'unavailable', phase: 'unavailable',
    }) } });
    expect(pair.view).toMatchObject({
      canonical: { rf: 1, sql: 0.2 }, editable: false,
      lanes: { sql: { availability: 'unavailable', canonical: 0.2 } },
    });
  });
});

describe('continuous pair delegates one scalar lifetime', () => {
  it('retains rendered-native axis steps while crossing the center dead zone', () => {
    const { pair, requestRf, requestSql } = readingSetup(
      createRenderedNativeRangeContinuousPairPolicy(),
    );
    const lease = pair.attachRenderer();
    const axisDrafts: Array<number | null> = [];

    for (let press = 0; press < 6; press += 1) {
      expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(true);
      axisDrafts.push(pair.view.axisDraft);
    }

    expect(axisDrafts).toEqual([0.51, 0.52, 0.53, 0.54, 0.55, 0.56]);
    expect(requestRf).not.toHaveBeenCalled();
    expect(requestSql.mock.calls).toEqual([[0.02], [0.04]]);
  });

  it('continues rendered-native keyboard input from the normalized pointer axis', () => {
    const { pair, requestRf, requestSql } = readingSetup(
      createRenderedNativeRangeContinuousPairPolicy(),
    );
    const lease = pair.attachRenderer();
    const token = lease.beginPointer()!;

    lease.pointer(token, 0.513);
    lease.endPointer(token);
    expect(pair.view).toMatchObject({ axisDraft: 0.51, displayedPosition: 0.51 });
    expect(requestRf).not.toHaveBeenCalled();
    expect(requestSql).not.toHaveBeenCalled();

    expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(true);
    expect(pair.view).toMatchObject({ axisDraft: 0.52, displayedPosition: 0.52 });
    expect(requestRf).not.toHaveBeenCalled();
    expect(requestSql).not.toHaveBeenCalled();
  });

  it.each([
    ['ArrowLeft', false, 0.49, [], []],
    ['ArrowDown', true, 0.49, [], []],
    ['ArrowRight', true, 0.51, [], []],
    ['ArrowUp', false, 0.51, [], []],
    ['Home', false, 0, [[0]], []],
    ['End', false, 1, [], [[1]]],
  ] as const)('handles rendered-native %s on the axis lattice', (key, fine, axis, rf, sql) => {
    const setup = readingSetup(createRenderedNativeRangeContinuousPairPolicy());
    const lease = setup.pair.attachRenderer();

    expect(lease.key({ key, fine })).toBe(true);
    expect(setup.pair.view.axisDraft).toBe(axis);
    expect(setup.requestRf.mock.calls).toEqual(rf);
    expect(setup.requestSql.mock.calls).toEqual(sql);
  });

  it('keeps rendered-native wheel immediate and reset inert', () => {
    const { pair, requestRf, requestSql } = readingSetup(
      createRenderedNativeRangeContinuousPairPolicy(),
      { sql: { reading: { status: 'known', value: 0.5 }, availability: 'available' } },
    );
    const lease = pair.attachRenderer();

    lease.wheel({ direction: 1, fine: true });
    lease.reset();
    expect(requestRf).not.toHaveBeenCalled();
    expect(requestSql).toHaveBeenCalledExactlyOnceWith(0.52);
    expect(pair.view).toMatchObject({ axisDraft: null, interaction: 'idle' });

    const axis = createRenderedNativeRangeContinuousPairPolicy().axis!;
    expect(axis.key(Number.NaN, { key: 'Home', fine: false }, DOMAIN)).toBeNull();
    expect(axis.wheel(Number.POSITIVE_INFINITY, { direction: 1, fine: false }, DOMAIN)).toBeNull();
    expect(axis.normalize(0.5, { ...DOMAIN, max: DOMAIN.min })).toBeNull();
  });

  it('makes rendered-native unknown authority inert and cancels state on replacement', () => {
    const policy = createRenderedNativeRangeContinuousPairPolicy();
    const unknown = readingSetup(policy, {
      rf: { reading: { status: 'unknown' }, availability: 'available' },
    });
    const unknownLease = unknown.pair.attachRenderer();
    expect(unknownLease.key({ key: 'ArrowRight', fine: false })).toBe(false);
    expect(unknownLease.beginPointer()).toBeNull();
    expect(unknown.requestRf).not.toHaveBeenCalled();
    expect(unknown.requestSql).not.toHaveBeenCalled();

    const healthy = readingSetup(policy);
    const stale = healthy.pair.attachRenderer();
    expect(stale.key({ key: 'ArrowRight', fine: false })).toBe(true);
    expect(healthy.pair.view.axisDraft).toBe(0.51);
    const replacement = healthy.pair.attachRenderer();
    expect(healthy.pair.view.axisDraft).toBeNull();
    expect(stale.key({ key: 'ArrowRight', fine: false })).toBe(false);
    expect(replacement.key({ key: 'ArrowRight', fine: false })).toBe(true);
    healthy.update({ ownerKey: 'rf-sql:sub' });
    expect(healthy.pair.view.axisDraft).toBeNull();
  });

  it('keeps native input immediate and omits legacy wheel, key and reset behavior', () => {
    const { pair, requestRf, requestSql } = readingSetup();
    const lease = pair.attachRenderer();
    lease.nativeInput(0);
    expect(requestRf).toHaveBeenCalledExactlyOnceWith(0);
    expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(false);
    lease.wheel({ direction: 1, fine: false });
    lease.reset();
    expect(requestRf).toHaveBeenCalledTimes(1);
    expect(requestSql).not.toHaveBeenCalled();
  });

  it('uses immediate pointer, wheel and reset but debounces legacy keyboard', () => {
    vi.useFakeTimers();
    const { pair, requestRf, requestSql } = readingSetup(
      createLegacyContinuousPairPolicy({ keyboardDebounceMs: 50 }),
    );
    const lease = pair.attachRenderer();
    const token = lease.beginPointer()!;
    lease.pointer(token, 0);
    lease.wheel({ direction: 1, fine: false });
    lease.reset();
    expect(requestRf).toHaveBeenCalledTimes(3);
    expect(requestSql).not.toHaveBeenCalled();
    lease.key({ key: 'ArrowRight', fine: false });
    expect(requestSql).not.toHaveBeenCalled();
    vi.advanceTimersByTime(50);
    expect(requestSql).toHaveBeenCalledExactlyOnceWith(0.01);
    vi.useRealTimers();
  });

  it('expires wheel draft and cancels debounced work on a terminal lane outcome', () => {
    vi.useFakeTimers();
    const { pair, requestSql, read, update } = commandSetup();
    const lease = pair.attachRenderer();
    lease.wheel({ direction: 1, fine: false });
    expect(pair.view.interaction).toBe('wheel');
    vi.advanceTimersByTime(300);
    expect(pair.view).toMatchObject({ interaction: 'idle', axisDraft: null });
    lease.key({ key: 'ArrowRight', fine: false });
    update({ rf: { ...read().rf, feedback: feedback('rf-gain', 1, {
      requestedTarget: 0.5, phase: 'failed', outcome: { phase: 'failed', error: 'no' },
      lifecycleId: 'rf-failed', transitionId: 'rf-failed',
    }) } });
    void pair.view;
    vi.advanceTimersByTime(50);
    expect(requestSql).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it('invalidates old renderer leases and current work on authority replacement', () => {
    vi.useFakeTimers();
    const { pair, requestSql, update } = readingSetup(
      createLegacyContinuousPairPolicy({ keyboardDebounceMs: 50 }),
    );
    const stale = pair.attachRenderer();
    stale.key({ key: 'ArrowRight', fine: false });
    update({ ownerKey: 'rf-sql:sub' });
    void pair.view;
    vi.advanceTimersByTime(50);
    expect(requestSql).not.toHaveBeenCalled();
    const current = pair.attachRenderer();
    stale.nativeInput(1);
    expect(requestSql).not.toHaveBeenCalled();
    current.nativeInput(1);
    expect(requestSql).toHaveBeenCalledExactlyOnceWith(1);
    vi.useRealTimers();
  });

  it('cancels pending work when command receiver authority changes', () => {
    vi.useFakeTimers();
    const { pair, requestSql, read, update } = commandSetup();
    const lease = pair.attachRenderer();
    lease.key({ key: 'ArrowRight', fine: false });
    update({ sql: { ...read().sql, feedback: feedback('squelch', 0, {
      sessionEpoch: 8, scope: { control: 'squelch', receiver: 1 },
    }) } });
    void pair.view;
    vi.advanceTimersByTime(50);
    expect(requestSql).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('invalidates pair draft, lease, and lane announcements on provider replacement without a null read', () => {
    const { pair, read, update } = commandSetup();
    update({ rf: { ...read().rf, feedback: feedback('rf-gain', 0.8, {
      requestedTarget: 0.8, phase: 'failed', outcome: { phase: 'failed' },
      lifecycleId: 'old-rf', transitionId: 'old-rf-failed',
    }) } });
    const stale = pair.attachRenderer();
    expect(stale.view.lanes.rf.announcement).toBe('Failed: 0.8');
    const staleToken = stale.beginPointer()!;
    stale.pointer(staleToken, 0.5);
    expect(pair.view.draft).not.toBeNull();

    update({
      rf: { ...read().rf, feedback: feedback('rf-gain', 0.8, { providerGeneration: 4 }) },
      sql: { ...read().sql, feedback: feedback('squelch', 0.2, { providerGeneration: 4 }) },
    });
    expect(pair.view).toMatchObject({
      draft: null, lanes: { rf: { announcement: null }, sql: { announcement: null } },
    });
    stale.pointer(staleToken, 0);
    expect(pair.view.draft).toBeNull();
    stale.nativeInput(0);
    expect(pair.view.draft).not.toBeNull();
  });

  it('keeps lane announcement identity when appearance leases are replaced', () => {
    const { pair, read, update } = commandSetup();
    update({ rf: { ...read().rf, feedback: feedback('rf-gain', 0.8, {
      requestedTarget: 0.8, phase: 'confirmed', outcome: { phase: 'confirmed' },
      lifecycleId: 'rf-appearance', transitionId: 'rf-appearance-confirmed',
    }) } });
    const first = pair.attachRenderer();
    expect(first.view.lanes.rf.announcement).toBe('Confirmed: 0.8');
    const replacement = pair.attachRenderer();
    expect(replacement.view.lanes.rf.announcement).toBeNull();
    first.nativeInput(0);
    expect(pair.view.axisDraft).toBeNull();
  });

  it('drops cancelled pointer draft without retracting emitted requests and destroys ownership', () => {
    const { pair, requestRf } = readingSetup(
      createLegacyContinuousPairPolicy({ keyboardDebounceMs: 50 }),
    );
    const lease = pair.attachRenderer();
    const token = lease.beginPointer()!;
    lease.pointer(token, 0);
    expect(requestRf).toHaveBeenCalledExactlyOnceWith(0);
    lease.cancelPointer(token);
    expect(pair.view.axisDraft).toBeNull();
    pair.destroy();
    lease.nativeInput(0.2);
    expect(requestRf).toHaveBeenCalledTimes(1);
  });
});
