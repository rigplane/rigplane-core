/**
 * AmberCockpit AGC / preamp indicator label sourcing (MOR-1529).
 *
 * Smoke-test companion to `AmberLabels.profile-data.isolated.test.ts`
 * (AmberScope): proves the same profile-data label fix was applied to
 * `AmberCockpit`'s per-VFO indicator strip, not just its AmberScope twin.
 * `panel-props` and `$lib/state/field-status` are left real (mirrors
 * `AmberCockpit.qsy-authority.isolated.test.ts`'s mocking style); only the
 * runtime/adapter seams are mocked to feed a controlled `radioState`/`caps`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';

const h = vi.hoisted(() => ({
  props: {
    radioState: null as unknown,
    caps: null as Capabilities | null,
    hasAudioFft: false,
    hasDualReceiver: false,
    hasCapability: (_name: string) => true,
  } as Record<string, unknown>,
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveAmberCockpitProps: () => h.props,
  deriveAmberTelemetryProps: () => ({ vdRaw: null, idRaw: null }),
  getAmberCockpitHandlers: () => ({ onTuningChange: vi.fn() }),
  getVfoHandlers: () => ({ onFreqChange: vi.fn(), onModeChange: vi.fn() }),
  bindVfoTunerContext: () => Object.freeze({ read: vi.fn(() => ({ view: null })) }),
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  presentationResources: { acquire: vi.fn(), release: vi.fn() },
  runtime: {
    scope: { registerPresentationDriver: vi.fn(), subscribe: vi.fn(() => vi.fn()) },
  },
}));

import AmberCockpit from '../AmberCockpit.svelte';

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;

function baseReceiver() {
  return {
    freqHz: 14_074_000, mode: 'USB', filter: 1, dataMode: 0, sMeter: 0,
    att: 0, preamp: 1, nb: false, nr: false, afLevel: 128, rfGain: 255,
    squelch: 0, agc: 3,
  };
}

function mountCockpit(caps: Capabilities | null) {
  h.props = {
    radioState: {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver(),
      fieldStatus: {
        'main.agc': {
          storePath: 'receiver.main.operator_controls.agc',
          observed: true, freshness: 'fresh', availability: 'available',
        },
        'main.preamp': {
          storePath: 'receiver.main.operator_controls.preamp',
          observed: true, freshness: 'fresh', availability: 'available',
        },
      },
    },
    caps,
    hasAudioFft: false,
    hasDualReceiver: false,
    hasCapability: () => true,
  };
  component = mount(AmberCockpit, { target });
  flushSync();
}

function indChip(text: string): HTMLElement | undefined {
  return Array.from(target.querySelectorAll<HTMLElement>('.lcd-ind'))
    .find((el) => el.textContent?.trim().startsWith(text));
}

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
});

afterEach(() => {
  if (component) unmount(component);
  component = undefined;
  target.remove();
});

describe('AmberCockpit AGC/preamp label sourcing (MOR-1529)', () => {
  it('labels X6200 AGC=3 as AUTO from profile data, not the hardcoded SLOW', () => {
    const caps = {
      agcLabels: { '0': 'OFF', '1': 'FAST', '2': 'SLOW', '3': 'AUTO' },
    } as unknown as Capabilities;
    mountCockpit(caps);
    const chip = indChip('AGC');
    expect(chip?.textContent?.trim()).toBe('AGC AUTO');
  });

  it('renders a profile-declared preamp label instead of the hardcoded AMP1', () => {
    const caps = {
      preLabels: { '0': 'OFF', '1': 'BOOST' },
    } as unknown as Capabilities;
    mountCockpit(caps);
    const chip = Array.from(target.querySelectorAll<HTMLElement>('.lcd-ind'))
      .find((el) => el.textContent?.trim() === 'BOOST');
    expect(chip).toBeDefined();
  });
});
