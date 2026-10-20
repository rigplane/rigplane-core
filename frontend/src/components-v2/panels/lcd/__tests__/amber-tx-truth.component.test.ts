/**
 * MOR-2855 — the amber LCD faces take their TX look from the managed TX
 * snapshot (safety invariant R9), never from the `state.ptt` echo.
 *
 * `state.ptt` is a readback echo: while the PTT reading is stale the server
 * keeps publishing the last value (`ptt: true` with a stale fieldStatus),
 * and after a reconnect it publishes `null` even while Core still owes an
 * unconfirmed release. The three scenarios pin both failure directions on
 * the mounted faces:
 *
 *  - stale echo, nothing keyed: the echo says TX, the managed snapshot is
 *    idle → no TX look;
 *  - release owed, echo gone: the echo is unobserved, the managed snapshot
 *    owes a release → TX look;
 *  - confirmed TX, echo RX: the managed snapshot confirms TX while the
 *    echo reads RX → TX look.
 *
 * Mounts the real `AmberCockpit`/`AmberScope` trees against the real
 * `panel-props` projections; the adapter/runtime seams and the managed TX
 * controller are mocked to feed a controlled `ServerState` and a controlled
 * server TX projection (the `ManagedAppTxHarness` shape the LcdLayout tests
 * use).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { ServerState } from '$lib/types/state';
import {
  ManagedAppTxHarness,
  type ManagedAppTxServerSnapshot,
} from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

const txHarness = new ManagedAppTxHarness();

const faceProps = vi.hoisted(() => ({
  value: {
    radioState: null as ServerState | null,
    caps: null as Record<string, unknown> | null,
    hasCapability: (_name: string) => false,
    hasAudioFft: false,
    hasDualReceiver: false,
  },
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveAmberCockpitProps: () => faceProps.value,
  deriveAmberScopeProps: () => faceProps.value,
  deriveAmberTelemetryProps: () => ({ vdRaw: null, idRaw: null }),
  getAmberCockpitHandlers: () => ({ onTuningChange: vi.fn() }),
  getVfoHandlers: () => ({ onFreqChange: vi.fn(), onModeChange: vi.fn() }),
  bindVfoTunerContext: () => ({ read: vi.fn(() => ({ view: null })) }),
}));

vi.mock('$lib/runtime/adapters/qsy-history-adapter', () => ({
  deriveQsyRecent: () => [],
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  presentationResources: { acquire: vi.fn(() => ({})), release: vi.fn() },
  runtime: {
    send: vi.fn(),
    scope: {
      registerPresentationDriver: vi.fn(),
      subscribe: vi.fn(() => vi.fn()),
    },
  },
}));

vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHarness.controller,
}));

import AmberCockpit from '../AmberCockpit.svelte';
import AmberScope from '../AmberScope.svelte';

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;

// A TX-capable radio: the TX chip is gated on `hasTx` alone, so it stays
// rendered in every scenario below.
const TX_CAPS = { tx: true, capabilities: [] };

const FACES = ['cockpit', 'scope'] as const;
type Face = (typeof FACES)[number];

function baseReceiver() {
  return {
    freqHz: 14_074_000, mode: 'USB', filter: 1, dataMode: 0, sMeter: 0,
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 128, rfGain: 255,
    squelch: 0, agc: 2,
  };
}

function radioState(overrides: Record<string, unknown> = {}): ServerState {
  return {
    active: 'MAIN',
    main: baseReceiver(),
    sub: baseReceiver(),
    ...overrides,
  } as unknown as ServerState;
}

// The server's stale-echo shape: the last PTT value stays published while
// the reading itself is stale (owner ruling R29 — stale-but-observed stays
// available, so the old chip gate did NOT hide it either).
const STALE_PTT_STATUS = {
  storePath: 'global.tx_state.ptt',
  observed: true,
  freshness: 'stale',
  availability: 'stale',
};

function mountFace(face: Face, state: ServerState | null): HTMLDivElement {
  faceProps.value = {
    radioState: state,
    caps: TX_CAPS,
    hasCapability: () => false,
    hasAudioFft: false,
    hasDualReceiver: false,
  };
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(face === 'cockpit' ? AmberCockpit : AmberScope, { target });
  flushSync();
  return target;
}

function lcdRoot(target: HTMLDivElement): Element {
  const root = target.querySelector('.amber-lcd');
  if (!root) throw new Error('LCD root did not mount');
  return root;
}

function txChip(target: HTMLDivElement): HTMLElement | undefined {
  return Array.from(target.querySelectorAll<HTMLElement>('.lcd-ind'))
    .find((el) => el.textContent?.trim() === 'TX');
}

function resetManagedTx(snapshot: ManagedAppTxServerSnapshot): void {
  txHarness.reset(snapshot);
}

beforeEach(() => {
  component = undefined;
});

afterEach(() => {
  if (component) unmount(component);
  component = undefined;
  target.remove();
});

describe('Amber LCD TX truth follows the managed TX snapshot, not state.ptt (MOR-2855, R9)', () => {
  it.each(FACES)('stale ptt echo with an idle managed snapshot shows no TX look (%s)', (face) => {
    // Managed snapshot idle: radioTx unknown, txRisk none.
    resetManagedTx({ intent: 'rx', observedPtt: 'unknown', releaseRequired: false });
    const mounted = mountFace(face, radioState({
      ptt: true,
      fieldStatus: { ptt: { ...STALE_PTT_STATUS } },
    }));
    expect(lcdRoot(mounted).classList.contains('tx-active')).toBe(false);
    const chip = txChip(mounted);
    expect(chip).toBeDefined();
    expect(chip?.classList.contains('active')).toBe(false);
  });

  it.each(FACES)('an owed release with the echo gone keeps the TX look (%s)', (face) => {
    // Managed snapshot: phase releasing, radioTx unknown, txRisk uncertain,
    // release owed. The server state's ptt is unobserved (null) — the
    // post-reconnect echo shape.
    resetManagedTx({ intent: 'rx', observedPtt: 'unknown', releaseRequired: true });
    const mounted = mountFace(face, radioState({ ptt: null }));
    expect(lcdRoot(mounted).classList.contains('tx-active')).toBe(true);
    const chip = txChip(mounted);
    expect(chip).toBeDefined();
    expect(chip?.classList.contains('active')).toBe(true);
  });

  it.each(FACES)('a confirmed managed TX wins over a ptt echo that reads RX (%s)', (face) => {
    // Managed snapshot: radioTx on, txRisk confirmed-on — while the echo
    // publishes ptt false, proving the echo is ignored in both directions.
    resetManagedTx({ intent: 'ptt', observedPtt: 'on', releaseRequired: false });
    const mounted = mountFace(face, radioState({ ptt: false }));
    expect(lcdRoot(mounted).classList.contains('tx-active')).toBe(true);
    const chip = txChip(mounted);
    expect(chip).toBeDefined();
    expect(chip?.classList.contains('active')).toBe(true);
  });
});
