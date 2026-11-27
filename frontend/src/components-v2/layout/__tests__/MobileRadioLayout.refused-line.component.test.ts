// MOR-4828 companion to the desktop StatusBar refusal test: the same FTX-1
// mic-gain refusal golden — produced by the real Python producer
// (`tests/web/test_state_wire_goldens.py`) — replayed through the real
// ws-client/store into the phone layout. The refusal row carries the
// locale-table key plus params on the wire; the `refused-fields-line` must
// name the refused field and the plain reason where a tooltip cannot reach
// (a phone has no hover), and clear on recovery.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const s = await import('./SpectrumPanelStub.svelte');
  return { default: s.default };
});
vi.mock('../../display/FrequencyDisplay.svelte', async () => {
  const s = await import('./fixtures/FrequencyDisplayEcho.svelte');
  return { default: s.default };
});
vi.mock('../../meters/LinearSMeter.svelte', async () => {
  const s = await import('./fixtures/LinearSMeterEcho.svelte');
  return { default: s.default };
});
vi.mock('../../controls/BandSelector.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/FilterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/RxAudioPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/TxPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/DspPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/AgcPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/RfFrontEnd.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/RitXitPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/AntennaPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/ScanPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/CwPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/DockMeterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../../panels/EssentialsPanel.svelte', () => ({ default: function S() { return {}; } }));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    onChange: () => () => {}, getAppliedAudioConfig: () => null,
    setOperatorNotifier: vi.fn(),
  } satisfies Partial<(typeof import('$lib/audio/audio-manager'))['audioManager']>,
}));
vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({
    volume: 50, muted: false, rxEnabled: false, txEnabled: false,
    micEnabled: false, bridgeRunning: false,
    rxError: false, txError: false, txCodecFallback: false,
  })),
  getRxAudioTargetSnapshot: vi.fn(() => Object.freeze({ muted: false, rxEnabled: false })),
  subscribeRxAudioTarget: vi.fn((handler: (target: { muted: boolean; rxEnabled: boolean }) => void) => {
    handler(Object.freeze({ muted: false, rxEnabled: false }));
    return () => {};
  }),
} satisfies { [K in keyof typeof import('$lib/stores/audio.svelte')]?: (typeof import('$lib/stores/audio.svelte'))[K] }));
vi.mock('$lib/utils/tx-permit', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/utils/tx-permit')>(),
  getTxPermit: vi.fn<(typeof import('$lib/utils/tx-permit'))['getTxPermit']>(() => 'allowed'),
}));

const txHost = vi.hoisted(() => ({ current: undefined as unknown as ManagedAppTxController }));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHost.current,
}));

import MobileRadioLayout from '../MobileRadioLayout.svelte';
import type { Capabilities } from '$lib/types/capabilities';
import { FTX1_CAPABILITIES } from '$lib/runtime/adapters/__tests__/fixtures/ftx1-profile';
import { setCapabilities, clearCapabilities } from '$lib/stores/capabilities.svelte';
import { setRadioPowerOn, setRadioHealth } from '$lib/stores/connection.svelte';
import { resetRadioState } from '$lib/stores/radio.svelte';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { mobileLayout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY,
} from '../../../presentation/workspace/resolution';
import stateGoldens from '$lib/transport/__tests__/fixtures/state-wire-goldens.json';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import { connect, disconnect } from '$lib/transport/ws-client';

const EN_REFUSED =
  'The radio refused to report mic gain. The control is unavailable ' +
  'until the radio answers. The app is still connected.';

let tx: ManagedAppTxHarness;
let components: ReturnType<typeof mount>[] = [];

function setViewport(landscape: boolean) {
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: landscape ? 844 : 390 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: landscape ? 390 : 844 });
}

function mountMobile(): HTMLElement {
  const plan = resolveSurfacePlan(mobileLayout, readWorkspace({ version: 1 }).workspace);
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(MobileRadioLayout, {
    target,
    context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]),
  }));
  flushSync();
  return target;
}

beforeEach(() => {
  components = []; setViewport(false);
  tx = new ManagedAppTxHarness(); txHost.current = tx.controller;
  resetRadioState(); resetCommandLifecycle();
  const caps: Capabilities = { ...FTX1_CAPABILITIES };
  setCapabilities(caps);
  setRadioPowerOn(true); setRadioHealth(null); localStorage.clear();
});

afterEach(async () => {
  for (const component of components) await unmount(component);
  expect(tx.listenerCount()).toBe(0);
  disconnect();
  vi.restoreAllMocks(); vi.unstubAllGlobals(); clearCapabilities();
  document.body.innerHTML = ''; localStorage.clear();
});

describe('MobileRadioLayout refused-fields line (MOR-4828)', () => {
  it('names the refused field and the plain reason, and clears on recovery', () => {
    vi.stubGlobal('WebSocket', MockWebSocket); instances.length = 0;
    connect('ws://test/api/v1/ws');
    const socket = instances[0]; socket.simulateOpen();
    const phone = mountMobile();
    const replay = (data: unknown) => {
      socket.simulateMessage(JSON.stringify({ type: 'state_update', data }));
      flushSync();
    };

    const { micGainRefused, micGainRecovered } = stateGoldens['FTX-1'];
    replay(micGainRefused);
    const line = () => phone.querySelector('[data-testid="refused-fields-line"]');
    expect(line(), 'the refusal must be visible without a hover target').not.toBeNull();
    expect(line()!.textContent).toBe(EN_REFUSED);

    replay(micGainRecovered);
    expect(phone.querySelector('[data-testid="refused-fields-line"]')).toBeNull();
  });

  it('falls back to the English sentence for a legacy row without keys', () => {
    vi.stubGlobal('WebSocket', MockWebSocket); instances.length = 0;
    connect('ws://test/api/v1/ws');
    const socket = instances[0]; socket.simulateOpen();
    const phone = mountMobile();

    const { micGainRefused } = stateGoldens['FTX-1'];
    const legacy = JSON.parse(JSON.stringify(micGainRefused)) as {
      data: { fieldStatus: Record<string, Record<string, unknown>> };
    };
    const row = legacy.data.fieldStatus.micGain;
    delete row.messageKey;
    delete row.messageParams;
    socket.simulateMessage(JSON.stringify({ type: 'state_update', data: legacy.data }));
    flushSync();
    expect(phone.querySelector('[data-testid="refused-fields-line"]')!.textContent).toBe(EN_REFUSED);
  });
});
