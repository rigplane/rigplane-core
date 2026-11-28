/**
 * MOR-4828 — the refusal reason is visible, not tooltip-only.
 *
 * The radio-indicator tooltip carried the refusal sentence while nothing on
 * screen named it. This mounts the real `StatusBar.svelte` with a stubbed
 * runtime state: a keyed refusal row must render both in the tooltip AND in
 * the visible `refusal-line` row; a legacy row without keys (or with an
 * unknown key) falls back to the English wire sentence; a recovered row
 * clears the line.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const EN_REFUSED =
  'The radio refused to report mic gain. The control is unavailable ' +
  'until the radio answers. The app is still connected.';

const stateStore = vi.hoisted(() => ({
  current: null as Record<string, unknown> | null,
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getRadioLinkState: vi.fn(() => 'connected'),
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
    get state() {
      return stateStore.current;
    },
    defaultScopeStatus: {
      source: null,
      available: false,
      resourceSelected: false,
      demand: 0,
      lifecycle: 'inactive',
      transport: 'disconnected',
      frameSeen: false,
    },
    connectionStale: false,
    remoteController: false,
    system: {
      disconnect: vi.fn(),
      connect: vi.fn(),
      identifyFrequency: vi.fn(async () => null),
    },
  },
}));

import StatusBar from '../StatusBar.svelte';

function refusedFieldStatus(overrides: Record<string, unknown> = {}) {
  return {
    micGain: {
      storePath: 'global.operator_controls.mic_gain',
      observed: false,
      freshness: 'unknown',
      availability: 'unavailable',
      message: EN_REFUSED,
      messageKey: 'readRefused',
      messageParams: { field: 'mic gain' },
      ...overrides,
    },
  };
}

describe('StatusBar refusal line (MOR-4828)', () => {
  let target: HTMLElement | null = null;
  let instance: object | null = null;

  const mountBar = () => {
    target = document.createElement('div');
    document.body.appendChild(target);
    instance = mount(StatusBar, { target }) as object;
    flushSync();
  };

  afterEach(() => {
    if (instance) unmount(instance);
    instance = null;
    target?.remove();
    target = null;
    stateStore.current = null;
  });

  it('shows the keyed refusal in the tooltip and in a visible line', () => {
    stateStore.current = { fieldStatus: refusedFieldStatus() };
    mountBar();

    const indicator = target!.querySelector('.status-indicators .indicator');
    expect(indicator!.getAttribute('title')?.split('\n')).toEqual([
      'Radio ↔ Server: connected',
      EN_REFUSED,
    ]);
    const line = target!.querySelector('[data-testid="refusal-line"]');
    expect(line, 'the refusal reason must be visible, not tooltip-only').not.toBeNull();
    expect(line!.textContent).toBe(EN_REFUSED);
  });

  it('falls back to the English sentence for a legacy row without keys', () => {
    const { messageKey: _key, messageParams: _params, ...legacy } =
      refusedFieldStatus().micGain as Record<string, unknown>;
    stateStore.current = { fieldStatus: { micGain: legacy } };
    mountBar();

    expect(target!.querySelector('[data-testid="refusal-line"]')!.textContent).toBe(EN_REFUSED);
  });

  it('falls back to the English sentence for an unknown key', () => {
    stateStore.current = { fieldStatus: refusedFieldStatus({ messageKey: 'futureCode' }) };
    mountBar();

    expect(target!.querySelector('[data-testid="refusal-line"]')!.textContent).toBe(EN_REFUSED);
  });

  it('clears the visible line when the read recovers', () => {
    stateStore.current = {
      fieldStatus: {
        micGain: {
          storePath: 'global.operator_controls.mic_gain',
          observed: true,
          freshness: 'fresh',
          availability: 'available',
        },
      },
    };
    mountBar();

    const indicator = target!.querySelector('.status-indicators .indicator');
    expect(indicator!.getAttribute('title')).toBe('Radio ↔ Server: connected');
    expect(target!.querySelector('[data-testid="refusal-line"]')).toBeNull();
  });
});
