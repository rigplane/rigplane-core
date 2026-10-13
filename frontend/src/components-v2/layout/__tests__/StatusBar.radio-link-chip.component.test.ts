/**
 * MOR-1526 F3 (verifier review round 1) — the store-level tests in
 * `lib/stores/__tests__/connection.test.ts` pin `getRadioLinkState()`'s
 * derivation logic, but nothing pinned that `StatusBar.svelte` actually
 * *calls* it. The verifier's Witness B reverted only the StatusBar rewire
 * (back to `getRadioStatus()`) and the full suite stayed green — the store
 * fix was correct but unwired-to-the-DOM regressions were invisible.
 *
 * This test mounts the real `StatusBar.svelte` with a DISCRIMINATING mock:
 * `getRadioLinkState` says 'connected', `getRadioStatus` (the old,
 * event-only source) says 'disconnected'. Only a build that reads
 * `getRadioLinkState()` can render the radio indicator as connected/green;
 * a build that still reads `getRadioStatus()` renders it disconnected/red.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

vi.mock('$lib/stores/connection.svelte', () => ({
  getRadioLinkState: vi.fn(() => 'connected'),
  getRadioStatus: vi.fn(() => 'disconnected'),
  getConnectionStatus: vi.fn(() => 'connected'),
  isAudioConnected: vi.fn(() => false),
  getWsConnected: vi.fn(() => true),
  getRadioPowerOn: vi.fn(() => true),
  getRigConnected: vi.fn(() => true),
  getRadioReady: vi.fn(() => true),
  getRadioHealth: vi.fn(() => null),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasAnyScope: vi.fn(() => false),
  hasAudio: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false),
  hasCapability: vi.fn(() => false),
}));

vi.mock('$lib/stores/layout.svelte', () => ({
  getLayoutMode: vi.fn(() => 'standard'),
  setLayoutMode: vi.fn(),
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  getActiveFrequencyHz: vi.fn(() => null),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    defaultScopeStatus: {
      source: null,
      available: false,
      resourceSelected: false,
      demand: 0,
      lifecycle: 'inactive',
      transport: 'disconnected',
      frameSeen: false,
    },
    system: {
      disconnect: vi.fn(),
      connect: vi.fn(),
      powerOn: vi.fn(async () => {}),
      powerOff: vi.fn(async () => {}),
      identifyFrequency: vi.fn(async () => null),
    },
  },
}));

import StatusBar from '../StatusBar.svelte';
import {
  getRadioHealth, getRadioLinkState, getRadioPowerOn, getRadioReady, getRigConnected,
} from '$lib/stores/connection.svelte';

describe('StatusBar radio-link chip wiring (MOR-1526 F3)', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;

  afterEach(() => {
    if (instance) unmount(instance);
    instance = null;
    target?.remove();
    target = null;
  });

  it('DISCRIMINATING: renders the radio indicator connected/green from getRadioLinkState, not the stale getRadioStatus() event value', () => {
    target = document.createElement('div');
    document.body.appendChild(target);
    instance = mount(StatusBar, { target }) as object;
    flushSync();

    const radioIndicator = target.querySelector('.status-indicators .indicator');
    expect(radioIndicator, 'expected the radio indicator to render').not.toBeNull();

    // Exact match, not `.toContain('connected')` — 'disconnected' also
    // contains the substring 'connected', which would silently pass under
    // the reverted (Witness B) wiring and defeat the discrimination.
    expect(radioIndicator!.getAttribute('title')).toBe('Radio ↔ Server: connected');
    // Independent, non-overlapping confirmation via the rendered tone:
    // green (#4ade80) only when the mocked getRadioLinkState() value wins;
    // a build still reading getRadioStatus() renders red (#ef4444) instead.
    expect(radioIndicator!.getAttribute('style')).toContain('#4ade80');
    expect(radioIndicator!.getAttribute('style')).not.toContain('#ef4444');
  });
});

// The reason text in the radio indicator's title, one case per branch of
// the label derivation (`radio-health-label.ts: deriveRadioHealthLabel`).
describe('StatusBar radio indicator title carries the health reason', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;

  const health = (
    likelyCause: NonNullable<ReturnType<typeof getRadioHealth>>['likelyCause'],
    readiness: NonNullable<ReturnType<typeof getRadioHealth>>['readiness'] = 'ready',
  ) => ({
    serverReachable: true, radioLink: 'connected' as const, readiness, likelyCause,
    sinceMs: 0, lastError: null,
  });

  afterEach(() => {
    if (instance) unmount(instance);
    instance = null;
    target?.remove();
    target = null;
    vi.mocked(getRadioHealth).mockReturnValue(null);
    vi.mocked(getRadioLinkState).mockReturnValue('connected');
    vi.mocked(getRigConnected).mockReturnValue(true);
    vi.mocked(getRadioReady).mockReturnValue(true);
    vi.mocked(getRadioPowerOn).mockReturnValue(true);
  });

  function radioTitle(): string | null {
    target = document.createElement('div');
    document.body.appendChild(target);
    instance = mount(StatusBar, { target }) as object;
    flushSync();
    return target.querySelector('.status-indicators .indicator')!.getAttribute('title');
  }

  it.each([
    ['radio_network_lost', 'stalled', 'Radio ↔ Server: connected (radio link lost)'],
    ['radio_not_responding', 'delayed', 'Radio ↔ Server: connected (radio response delayed)'],
    ['radio_not_responding', 'stalled', 'Radio ↔ Server: connected (radio not responding)'],
    ['radio_powered_off_likely', 'stalled', 'Radio ↔ Server: connected (radio appears powered off or unreachable)'],
    ['server_unreachable', 'ready', 'Radio ↔ Server: connected (server unreachable)'],
    ['radio_session_busy', 'recovering', 'Radio ↔ Server: connected (radio in use by another connection, retrying)'],
    ['operator_disconnected', 'idle', 'Radio ↔ Server: connected (disconnected by the operator)'],
    ['unknown', 'ready', 'Radio ↔ Server: connected'],
  ] as const)('%s (%s)', (cause, readiness, expected) => {
    vi.mocked(getRadioHealth).mockReturnValue(health(cause, readiness));
    expect(radioTitle()).toBe(expected);
  });

  it('keeps an idle operator disconnect disconnected without a delayed tone', () => {
    vi.mocked(getRadioHealth).mockReturnValue({
      ...health('operator_disconnected', 'idle'), radioLink: 'disconnected',
    });
    vi.mocked(getRadioLinkState).mockReturnValue('disconnected');
    vi.mocked(getRigConnected).mockReturnValue(false);
    vi.mocked(getRadioReady).mockReturnValue(false);
    expect(radioTitle()).toBe('Radio ↔ Server: disconnected (disconnected by the operator)');
    const style = target!.querySelector('.status-indicators .indicator')!.getAttribute('style');
    expect(style).toContain('#ef4444');
    expect(style).not.toContain('#facc15');
  });

  it('reads "rig offline" when the rig is down and the link is degraded', () => {
    vi.mocked(getRigConnected).mockReturnValue(false);
    vi.mocked(getRadioLinkState).mockReturnValue('degraded');
    expect(radioTitle()).toBe('Radio ↔ Server: degraded (rig offline)');
  });

  it('reads "radio not ready" when only readiness is missing and the link is degraded', () => {
    vi.mocked(getRadioReady).mockReturnValue(false);
    vi.mocked(getRadioLinkState).mockReturnValue('degraded');
    expect(radioTitle()).toBe('Radio ↔ Server: degraded (radio link degraded — radio not ready)');
  });

  it('shows the link as disconnected, without a reason, while the radio is powered off', () => {
    vi.mocked(getRadioPowerOn).mockReturnValue(false);
    vi.mocked(getRigConnected).mockReturnValue(false);
    vi.mocked(getRadioLinkState).mockReturnValue('degraded');
    expect(radioTitle()).toBe('Radio ↔ Server: disconnected');
  });
});
