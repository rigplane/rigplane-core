import { describe, expect, it, vi } from 'vitest';
import type { DualActionBlockViewModel } from '../radio-view-model';
import {
  invokeVfoOperation,
  projectVfoOperations,
  type VfoOperationCallbacks,
  type VfoOperationIntent,
  type VfoOperationProjectionInput,
} from '../vfo-operation-projection';

const REASONS = {
  receiverUnavailable: 'receiver unavailable',
  identityUnknown: 'identity unknown',
  splitUnknown: 'split unknown',
  dualWatchUnknown: 'dual watch unknown',
} as const;

const noCallbacks = (): VfoOperationCallbacks => ({
  onToggleSplit: undefined,
  onToggleDualWatch: undefined,
  onSelectMainReceiver: undefined,
  onSelectSubReceiver: undefined,
  onEqualizeVfos: undefined,
  onSwapVfos: undefined,
  onQuickSplit: undefined,
  onQuickDualWatch: undefined,
  onSpeak: undefined,
});

const spyCallbacks = () => ({
  onToggleSplit: vi.fn(), onToggleDualWatch: vi.fn(),
  onSelectMainReceiver: vi.fn(), onSelectSubReceiver: vi.fn(),
  onEqualizeVfos: vi.fn(), onSwapVfos: vi.fn(),
  onQuickSplit: vi.fn(), onQuickDualWatch: vi.fn(), onSpeak: vi.fn(),
}) satisfies VfoOperationCallbacks;

const availableActions = (): DualActionBlockViewModel => ({
  main: { structural: true, operational: true },
  sub: { structural: true, operational: true },
  equalize: { structural: true, operational: true },
  swap: { structural: true, operational: true },
  quickSplit: { structural: true, operational: true },
  quickDualWatch: { structural: true, operational: true },
  speak: { structural: true, operational: true },
});

function input(
  changes: Partial<Omit<VfoOperationProjectionInput, 'callbacks'>> & {
    callbacks?: Partial<VfoOperationCallbacks>;
  } = {},
): VfoOperationProjectionInput {
  return {
    hasVfoPair: true,
    hasDualReceiver: true,
    relativeIdentityUnknown: false,
    activeReceiver: { status: 'known', receiver: 'MAIN' },
    split: { status: 'known', value: false },
    dualWatch: { status: 'known', value: false },
    actions: availableActions(),
    reasons: REASONS,
    ...changes,
    callbacks: { ...noCallbacks(), ...changes.callbacks },
  };
}

describe('projectVfoOperations', () => {
  it('keeps the exact eight named operations and excludes unrelated mechanisms', () => {
    const projected = projectVfoOperations(input());
    expect(Object.keys(projected)).toEqual([
      'split', 'dualWatch', 'activeReceiver', 'equalize', 'swap',
      'quickSplit', 'quickDualWatch', 'speak', 'groupReason',
    ]);
    for (const key of ['digest', 'frequency', 'slot', 'ptt', 'tune', 'context', 'lease', 'feedback']) {
      expect(projected).not.toHaveProperty(key);
    }
    expect(projected.activeReceiver.options.map(({ value }) => value)).toEqual(['MAIN', 'SUB']);
  });

  it('preserves the no-action-block fallback without making quick or receiver actions live', () => {
    const projected = projectVfoOperations(input({
      actions: undefined,
      callbacks: { onEqualizeVfos: vi.fn(), onSwapVfos: vi.fn(), onQuickSplit: vi.fn() },
    }));
    expect(projected.equalize.availability).toMatchObject({ structural: true, operational: true });
    expect(projected.swap.availability).toMatchObject({ structural: true, operational: true });
    expect(projected.quickSplit.availability).toMatchObject({ structural: false, operational: false });
    expect(projected.activeReceiver.availability).toMatchObject({ structural: false, operational: false });
  });

  it('keeps unknown facts honest and gives identity priority only to quick actions', () => {
    const callbacks = spyCallbacks();
    const projected = projectVfoOperations(input({
      relativeIdentityUnknown: true,
      activeReceiver: { status: 'unknown' },
      split: { status: 'unknown' },
      dualWatch: { status: 'unknown' },
      callbacks,
    }));
    expect(projected.split).toMatchObject({
      reading: { status: 'unknown' },
      availability: { structural: true, operational: false, reason: REASONS.splitUnknown },
    });
    expect(projected.dualWatch.availability).toMatchObject({ operational: false, reason: REASONS.dualWatchUnknown });
    expect(projected.quickSplit.availability).toMatchObject({ operational: false, reason: REASONS.identityUnknown });
    expect(projected.quickDualWatch.availability).toMatchObject({ operational: false, reason: REASONS.identityUnknown });
    expect(projected.equalize.availability.operational).toBe(true);
    expect(projected.swap.availability.operational).toBe(true);
    expect(projected.groupReason).toBe(REASONS.identityUnknown);
    expect(projected.activeReceiver.reading).toEqual({ status: 'unknown' });
    expect(projected.activeReceiver.availability.operational).toBe(true);
  });

  it('keeps option reasons source-owned and callback absence reasonless', () => {
    const actions = availableActions();
    actions.main = { structural: true, operational: false };
    const projected = projectVfoOperations(input({
      actions,
      callbacks: { onSelectMainReceiver: vi.fn() },
    }));
    expect(projected.activeReceiver.options[0].availability).toEqual({
      structural: true, operational: false, reason: REASONS.receiverUnavailable,
    });
    expect(projected.activeReceiver.options[1].availability).toEqual({
      structural: true, operational: false, reason: undefined,
    });
  });

  it('preserves accepted toggle DOM admission when callbacks are absent', () => {
    const projected = projectVfoOperations(input({ callbacks: {} }));
    expect(projected.split.availability.operational).toBe(true);
    expect(projected.dualWatch.availability.operational).toBe(true);
  });
});

describe('invokeVfoOperation', () => {
  it('maps all nine intents one-to-one', () => {
    const callbacks = spyCallbacks();
    const cases: readonly [VfoOperationIntent, keyof VfoOperationCallbacks][] = [
      [{ kind: 'toggle-split' }, 'onToggleSplit'],
      [{ kind: 'toggle-dual-watch' }, 'onToggleDualWatch'],
      [{ kind: 'select-receiver', receiver: 'MAIN' }, 'onSelectMainReceiver'],
      [{ kind: 'select-receiver', receiver: 'SUB' }, 'onSelectSubReceiver'],
      [{ kind: 'equalize' }, 'onEqualizeVfos'],
      [{ kind: 'swap' }, 'onSwapVfos'],
      [{ kind: 'quick-split' }, 'onQuickSplit'],
      [{ kind: 'quick-dual-watch' }, 'onQuickDualWatch'],
      [{ kind: 'speak' }, 'onSpeak'],
    ];
    const current = input({ callbacks });

    for (const [intent, expected] of cases) {
      Object.values(callbacks).forEach((callback) => callback.mockClear());
      invokeVfoOperation(() => current, intent);
      for (const [name, callback] of Object.entries(callbacks)) {
        expect(callback, `${intent.kind} -> ${name}`).toHaveBeenCalledTimes(name === expected ? 1 : 0);
      }
    }
  });

  type GateChange = Partial<Pick<VfoOperationProjectionInput,
    'split' | 'dualWatch' | 'hasDualReceiver' | 'relativeIdentityUnknown'>> & {
      action?: keyof DualActionBlockViewModel; structural?: boolean; operational?: boolean;
    };
  const rejected: readonly [string, VfoOperationIntent, keyof VfoOperationCallbacks, GateChange][] = [
    ['split unknown', { kind: 'toggle-split' }, 'onToggleSplit', { split: { status: 'unknown' } }],
    ['dual absent', { kind: 'toggle-dual-watch' }, 'onToggleDualWatch', { hasDualReceiver: false }],
    ['MAIN absent', { kind: 'select-receiver', receiver: 'MAIN' }, 'onSelectMainReceiver', { action: 'main', structural: false }],
    ['SUB disabled', { kind: 'select-receiver', receiver: 'SUB' }, 'onSelectSubReceiver', { action: 'sub', operational: false }],
    ['equalize absent', { kind: 'equalize' }, 'onEqualizeVfos', { action: 'equalize', structural: false }],
    ['swap disabled', { kind: 'swap' }, 'onSwapVfos', { action: 'swap', operational: false }],
    ['quick split identity unknown', { kind: 'quick-split' }, 'onQuickSplit', { relativeIdentityUnknown: true }],
    ['quick DW fact unknown', { kind: 'quick-dual-watch' }, 'onQuickDualWatch', { dualWatch: { status: 'unknown' } }],
    ['speak disabled', { kind: 'speak' }, 'onSpeak', { action: 'speak', operational: false }],
  ];
  it.each(rejected)('rejects %s even when invocation is forced', (_name, intent, callbackName, change) => {
    const callback = vi.fn();
    const actions = availableActions();
    if (change.action !== undefined) actions[change.action] = {
      structural: change.structural ?? true,
      operational: change.operational ?? true,
    };
    const current = input({
      actions,
      ...('split' in change ? { split: change.split } : {}),
      ...('dualWatch' in change ? { dualWatch: change.dualWatch } : {}),
      ...('hasDualReceiver' in change ? { hasDualReceiver: change.hasDualReceiver } : {}),
      ...('relativeIdentityUnknown' in change
        ? { relativeIdentityUnknown: change.relativeIdentityUnknown } : {}),
      callbacks: { [callbackName]: callback },
    });
    invokeVfoOperation(() => current, intent);
    expect(callback).not.toHaveBeenCalled();
  });

  it('reads current input exactly once and uses only the replacement callback', () => {
    const stale = vi.fn();
    const replacement = vi.fn();
    let current = input({ callbacks: { onEqualizeVfos: stale } });
    expect(projectVfoOperations(current).equalize.availability.operational).toBe(true);
    current = input({ callbacks: { onEqualizeVfos: replacement } });
    let reads = 0;
    invokeVfoOperation(() => { reads += 1; return current; }, { kind: 'equalize' });
    expect(reads).toBe(1);
    expect(stale).not.toHaveBeenCalled();
    expect(replacement).toHaveBeenCalledOnce();

    const actions = availableActions();
    actions.equalize = { structural: true, operational: false };
    current = input({ actions, callbacks: { onEqualizeVfos: replacement } });
    invokeVfoOperation(() => current, { kind: 'equalize' });
    expect(replacement).toHaveBeenCalledOnce();
    current = input({ callbacks: { onEqualizeVfos: undefined } });
    invokeVfoOperation(() => current, { kind: 'equalize' });
    expect(replacement).toHaveBeenCalledOnce();
  });
});
