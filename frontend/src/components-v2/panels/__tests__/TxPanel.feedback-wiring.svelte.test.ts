import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { CommandLifecycle } from '$lib/stores/commands.svelte';

const VALUES = {
  micGain: 40,
  driveGain: 80,
  compressorLevel: 120,
  monitorGain: 160,
} as const;
const COMMANDS = {
  micGain: 'set_mic_gain',
  driveGain: 'set_drive_gain',
  compressorLevel: 'set_compressor_level',
  monitorGain: 'set_monitor_gain',
} as const;
const LABELS = {
  micGain: 'Mic Gain',
  driveGain: 'Drive Gain',
  compressorLevel: 'Comp Level',
  monitorGain: 'Mon Level',
} as const;
const FIELDS = Object.keys(VALUES) as (keyof typeof VALUES)[];

const fresh = (marker = 1) => ({
  storePath: 'fixture', observed: true, freshness: 'fresh' as const,
  availability: 'available' as const, lastObservedMonotonic: marker,
});
function connectedState(providerGeneration = 3): ServerState {
  return {
    stateContractVersion: 1, providerGeneration, active: 'MAIN', main: {}, sub: {},
    ...VALUES,
    fieldStatus: Object.fromEntries(FIELDS.map((field) => [field, fresh()])),
  } as unknown as ServerState;
}
function connectedCaps(providerGeneration = 3): Capabilities {
  return {
    stateContractVersion: 1, providerGeneration,
    capabilities: ['tx', 'drive_gain', 'compressor', 'monitor'],
  } as unknown as Capabilities;
}

const canonical = $state({
  state: connectedState() as ServerState | null,
  caps: connectedCaps() as Capabilities | null,
  session: { state: 'connected' as 'connected' | 'disconnected', epoch: 1 },
});
const radioListeners = new Set<(state: ServerState | null) => void>();
const props = $state({
  txActive: false, txActiveAvailable: true, rfPower: 0.5, micGain: VALUES.micGain,
  atuActive: false, atuTuning: false, voxActive: false, compActive: true,
  compLevel: VALUES.compressorLevel, monActive: true, monLevel: VALUES.monitorGain,
  driveGain: VALUES.driveGain, hasTx: true, hasTuner: true, hasMonitor: true,
});
const handlers = {
  onRfPowerChange: vi.fn(), onMicGainChange: vi.fn(), onAtuToggle: vi.fn(),
  onAtuTune: vi.fn(), onVoxToggle: vi.fn(), onCompToggle: vi.fn(),
  onCompLevelChange: vi.fn(), onMonToggle: vi.fn(), onMonLevelChange: vi.fn(),
  onDriveGainChange: vi.fn(),
};
const LEVEL_HANDLERS = {
  micGain: handlers.onMicGainChange,
  driveGain: handlers.onDriveGainChange,
  compressorLevel: handlers.onCompLevelChange,
  monitorGain: handlers.onMonLevelChange,
} as const;
const managedSnapshot = {
  intent: 'rx', phase: 'idle', fault: null, radioTx: 'off', txRisk: 'none', fresh: true,
};
const managedController = {
  snapshot: () => managedSnapshot,
  subscribe: (listener: (snapshot: typeof managedSnapshot) => void) => {
    listener(managedSnapshot);
    return () => {};
  },
  pttOn: vi.fn(), pttOff: vi.fn(), transmitOn: vi.fn(), forceOff: vi.fn(),
};

let TxPanel: typeof import('../TxPanel.svelte').default;
let commands: typeof import('$lib/stores/commands.svelte');
let component: ReturnType<typeof mount> | null = null;
let target: HTMLDivElement;

beforeAll(async () => {
  vi.doMock('$lib/runtime/frontend-runtime', () => ({ runtime: {
    get state() { return canonical.state; },
    get caps() { return canonical.caps; },
    get controlSession() { return canonical.session; },
  } }));
  vi.doMock('$lib/stores/radio.svelte', () => ({
    getRadioState: () => canonical.state,
    subscribeRadioState: (listener: (state: ServerState | null) => void) => {
      radioListeners.add(listener);
      listener(canonical.state);
      return () => radioListeners.delete(listener);
    },
  }));
  vi.doMock('$lib/runtime/adapters/panel-adapters', async (importOriginal) => ({
    ...await importOriginal<typeof import('$lib/runtime/adapters/panel-adapters')>(),
    deriveTxProps: () => props,
    getTxHandlers: () => handlers,
  }));
  vi.doMock('$lib/runtime/tx-controller/managed-app-host', () => ({
    getManagedAppTxController: () => managedController,
  }));
  vi.doMock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
    deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
    getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
  }));
  vi.doMock('$lib/runtime/adapters/mod-input-auto.svelte', () => ({
    deriveAutoLanModInputProps: () => ({ available: false, enabled: false }),
    setAutoLanModInputEnabled: vi.fn(),
  }));
  commands = await import('$lib/stores/commands.svelte');
  TxPanel = (await import('../TxPanel.svelte')).default;
});

afterAll(() => {
  vi.doUnmock('$lib/runtime/frontend-runtime');
  vi.doUnmock('$lib/stores/radio.svelte');
  vi.doUnmock('$lib/runtime/adapters/panel-adapters');
  vi.doUnmock('$lib/runtime/tx-controller/managed-app-host');
  vi.doUnmock('$lib/runtime/adapters/mod-input-tx-guard.svelte');
  vi.doUnmock('$lib/runtime/adapters/mod-input-auto.svelte');
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  target?.remove();
  commands.resetCommandLifecycle();
  canonical.state = connectedState();
  canonical.caps = connectedCaps();
  canonical.session = { state: 'connected', epoch: 1 };
  Object.assign(props, { compActive: true, monActive: true, hasMonitor: true });
  for (const handler of Object.values(handlers)) handler.mockClear();
});

function render(): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(TxPanel, { target });
  flushSync();
}
function openSettings(): void {
  const button = [...target.querySelectorAll<HTMLButtonElement>('.v2-control-button')]
    .find((entry) => entry.textContent?.includes('LEVELS'))!;
  button.click();
  flushSync();
}
function closeSettings(): void {
  target.querySelector<HTMLButtonElement>('[aria-label="Close TX settings"]')!.click();
  flushSync();
}
function slider(field: keyof typeof VALUES): HTMLElement {
  return target.querySelector<HTMLElement>(`[aria-label="${LABELS[field]}"]`)!;
}
function value(field: keyof typeof VALUES): string {
  const header = [...target.querySelectorAll('.vc-header')]
    .find((entry) => entry.querySelector('.vc-label')?.textContent === LABELS[field]);
  return header?.querySelector('.vc-value')?.textContent ?? '';
}
function begin(field: keyof typeof VALUES, requested: number, id: string = field): CommandLifecycle {
  return commands.beginCommand({
    id, name: COMMANDS[field], params: { level: requested }, originalEpoch: 1,
    timeoutMs: 5_000,
  });
}

describe('TxPanel v3 command-feedback wiring', () => {
  it('projects four independent optimistic targets over canonical raw truth', () => {
    render();
    openSettings();
    for (const [index, field] of FIELDS.entries()) begin(field, VALUES[field] + index + 1);
    flushSync();

    for (const [index, field] of FIELDS.entries()) {
      expect(slider(field).dataset.commandPhase).toBe('submitted');
      expect(slider(field).getAttribute('aria-busy')).toBe('true');
      expect(slider(field).getAttribute('aria-valuenow')).toBe(String(VALUES[field]));
      expect(value(field)).toBe(`${Math.round(VALUES[field] / 2.55)}%`);
      const descriptionId = slider(field).getAttribute('aria-describedby')!;
      expect(target.querySelector(`#${descriptionId}`)?.textContent)
        .toContain(`${Math.round(((VALUES[field] + index + 1) / 255) * 100)}%`);
    }
    expect(target.querySelectorAll('[data-control-feedback-status]')).toHaveLength(4);
  });

  it('keeps ACK awaiting, exposes terminal error, and confirms only exact fresh readback', () => {
    render();
    openSettings();
    const mic = begin('micGain', 99, 'mic');
    const drive = begin('driveGain', 109, 'drive');
    commands.acknowledgeCommand(mic.id, 1, 1);
    commands.failCommand(drive.id, 1, 1, 'radio refused');
    flushSync();
    expect(slider('micGain').dataset.commandPhase).toBe('awaiting-confirmation');
    expect(slider('driveGain').dataset.commandPhase).toBe('failed');
    expect(target.textContent).toContain('radio refused');

    const next = {
      ...connectedState(), micGain: 99,
      fieldStatus: { ...connectedState().fieldStatus, micGain: fresh(2) },
    } as ServerState;
    canonical.state = next;
    for (const listener of radioListeners) listener(next);
    flushSync();
    expect(slider('micGain').dataset.commandPhase).toBe('confirmed');
    expect(slider('micGain').getAttribute('aria-valuenow')).toBe('99');
  });

  it('consumes a hidden terminal transition and retains evidence without replay on reopen', () => {
    render();
    const command = begin('micGain', 99, 'hidden-mic');
    commands.failCommand(command.id, 1, 1, 'closed failure');
    flushSync();
    expect(target.querySelector('[aria-label="Mic Gain"]')).toBeNull();

    openSettings();
    const first = target.querySelector('[data-control-feedback-status]')?.textContent;
    expect(slider('micGain').dataset.commandPhase).toBe('failed');
    expect(first).toContain('closed failure');
    closeSettings();
    openSettings();
    expect(target.querySelectorAll('[data-control-feedback-status]')).toHaveLength(1);
    expect(target.querySelector('[data-control-feedback-status]')?.textContent).toBe(first);
  });

  it('fails closed across authority loss and recovers without remount', () => {
    vi.useFakeTimers();
    render();
    openSettings();
    slider('micGain').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    canonical.state = null;
    canonical.caps = null;
    canonical.session = { state: 'disconnected', epoch: 1 };
    flushSync();
    vi.advanceTimersByTime(50);
    expect(slider('micGain').getAttribute('aria-disabled')).toBe('true');
    expect(handlers.onMicGainChange).not.toHaveBeenCalled();

    canonical.state = connectedState(4);
    canonical.caps = connectedCaps(4);
    canonical.session = { state: 'connected', epoch: 2 };
    flushSync();
    slider('micGain').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    vi.advanceTimersByTime(50);
    expect(slider('micGain').getAttribute('aria-disabled')).toBe('false');
    expect(handlers.onMicGainChange).toHaveBeenCalledExactlyOnceWith(41);
    vi.useRealTimers();
  });

  it('keeps scalar gestures away from PTT, TUNE, and toggle routes', () => {
    vi.useFakeTimers();
    render();
    openSettings();
    slider('monitorGain').dispatchEvent(new KeyboardEvent('keydown', {
      key: 'ArrowRight', bubbles: true,
    }));
    vi.advanceTimersByTime(50);
    expect(handlers.onMonLevelChange).toHaveBeenCalledExactlyOnceWith(161);
    expect(managedController.pttOn).not.toHaveBeenCalled();
    expect(managedController.transmitOn).not.toHaveBeenCalled();
    expect(handlers.onAtuTune).not.toHaveBeenCalled();
    expect(handlers.onMonToggle).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('routes all four lanes independently through their existing raw handlers', () => {
    vi.useFakeTimers();
    render();
    openSettings();
    for (const field of FIELDS) {
      slider(field).dispatchEvent(new KeyboardEvent('keydown', {
        key: 'ArrowRight', bubbles: true,
      }));
    }
    vi.advanceTimersByTime(50);
    for (const field of FIELDS) {
      expect(LEVEL_HANDLERS[field]).toHaveBeenCalledExactlyOnceWith(VALUES[field] + 1);
    }
    vi.useRealTimers();
  });

  it.each([
    ['failed', 'micGain', (command: CommandLifecycle) =>
      commands.failCommand(command.id, 1, 1, 'radio rejected')],
    ['timed-out', 'driveGain', () => vi.advanceTimersByTime(26)],
    ['cancelled', 'compressorLevel', () => commands.cancelPendingCommands(1, 'session lost')],
  ] as const)('projects the actual %s lifecycle outcome', (phase, field, complete) => {
    vi.useFakeTimers();
    render();
    openSettings();
    const command = commands.beginCommand({
      id: `terminal-${phase}`, name: COMMANDS[field], params: { level: VALUES[field] + 1 },
      originalEpoch: 1, timeoutMs: 25,
    });
    complete(command);
    flushSync();
    expect(slider(field).dataset.commandPhase).toBe(phase);
    expect(slider(field).getAttribute('aria-busy')).toBe('false');
    expect(target.querySelector('[data-control-feedback-status]')?.textContent).toBeTruthy();
    vi.useRealTimers();
  });

  it('keeps inactive COMP/MON renderer-free and reveals consumed terminal evidence', () => {
    Object.assign(props, { compActive: false, monActive: false });
    render();
    openSettings();
    const comp = begin('compressorLevel', 121, 'hidden-comp');
    const mon = begin('monitorGain', 161, 'hidden-mon');
    commands.failCommand(comp.id, 1, 1, 'comp rejected');
    commands.failCommand(mon.id, 1, 1, 'mon rejected');
    flushSync();
    expect(target.querySelector('[aria-label="Comp Level"]')).toBeNull();
    expect(target.querySelector('[aria-label="Mon Level"]')).toBeNull();

    Object.assign(props, { compActive: true, monActive: true });
    flushSync();
    expect(slider('compressorLevel').dataset.commandPhase).toBe('failed');
    expect(slider('monitorGain').dataset.commandPhase).toBe('failed');
    expect([...target.querySelectorAll('[data-control-feedback-status]')].map((node) => node.textContent))
      .toEqual(expect.arrayContaining([
        expect.stringContaining('comp rejected'), expect.stringContaining('mon rejected'),
      ]));
  });

  it('fails each malformed or stale authority lane closed, then follows fresh truth', () => {
    render();
    openSettings();
    canonical.state = {
      ...connectedState(),
      micGain: Number.NaN,
      fieldStatus: {
        ...connectedState().fieldStatus,
        driveGain: { ...fresh(), freshness: 'stale' },
        compressorLevel: { ...fresh(), observed: false },
        monitorGain: { ...fresh(), lastObservedMonotonic: undefined },
      },
    } as unknown as ServerState;
    flushSync();
    for (const field of FIELDS) {
      expect(slider(field).getAttribute('aria-disabled')).toBe('true');
      expect(value(field)).toBe('—');
    }

    canonical.state = {
      ...connectedState(), micGain: 41, driveGain: 81, compressorLevel: 121, monitorGain: 161,
    } as ServerState;
    flushSync();
    for (const field of FIELDS) expect(slider(field).getAttribute('aria-disabled')).toBe('false');
    expect(FIELDS.map((field) => slider(field).getAttribute('aria-valuenow')))
      .toEqual(['41', '81', '121', '161']);
  });
});
