import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import type { Capabilities, ControlDomain } from '$lib/types/capabilities';

const handlers = {
  onNrModeChange: vi.fn(),
  onNrLevelChange: vi.fn(),
  onNbToggle: vi.fn(),
  onNbLevelChange: vi.fn(),
  onNotchModeChange: vi.fn(),
  onNotchFreqChange: vi.fn(),
};

const runtimeState = vi.hoisted(() => ({
  state: null as Record<string, unknown> | null,
  caps: null as Capabilities | null,
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return runtimeState.state; },
    get caps() { return runtimeState.caps; },
  },
}));

vi.mock('$lib/runtime/adapters/panel-adapters', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/adapters/panel-adapters')>();
  return {
    ...actual,
    getDspHandlers: () => handlers,
  };
});

import DspPanel from '../DspPanel.svelte';

const FTX_NR_DOMAIN: ControlDomain = {
  mapping: 'identity',
  raw_min: 0,
  raw_max: 10,
  raw_step: 1,
  raw_origin: 0,
  display_min: '0' as never,
  display_max: '10' as never,
  display_step: '1' as never,
  display_origin: '0' as never,
  display_unit: 'level',
  quantization: 'reject',
  restoration: 'exact',
};

let components: ReturnType<typeof mount>[] = [];

function caps(nrLevel?: unknown): Capabilities {
  return {
    model: 'FTX-1', scope: false, audio: false, tx: false,
    capabilities: ['nr', 'nb', 'notch'], receivers: 2, vfoScheme: 'main_sub',
    freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm'] },
    webrtc: { available: false, enabled: false }, txBands: null,
    stateContractVersion: 1, providerGeneration: 0,
    controls: nrLevel === undefined ? {} : { nr_level: nrLevel } as unknown as Capabilities['controls'],
  };
}

function state(raw: number | undefined, nrLevelStatus: 'available' | 'missing' | 'stale' = 'available') {
  return {
    active: 'MAIN',
    main: {
      nr: true,
      ...(raw === undefined ? {} : { nrLevel: raw }),
      nb: false,
      nbLevel: 0,
      autoNotch: false,
      manualNotch: false,
      notchFilter: 0,
      manualNotchWidth: 0,
      agcTimeConstant: 0,
    },
    sub: {},
    fieldStatus: {
      'main.nr': { availability: 'available', freshness: 'fresh' },
      'main.nrLevel': { availability: nrLevelStatus, freshness: nrLevelStatus === 'stale' ? 'stale' : 'fresh' },
      'main.nb': { availability: 'available', freshness: 'fresh' },
      'main.autoNotch': { availability: 'available', freshness: 'fresh' },
      'main.manualNotch': { availability: 'available', freshness: 'fresh' },
      'main.agcTimeConstant': { availability: 'available', freshness: 'fresh' },
    },
  };
}

function mountPanel(): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(DspPanel, { target }));
  flushSync();
  return target;
}

function nrButton(target: HTMLElement): HTMLButtonElement {
  const button = Array.from(target.querySelectorAll<HTMLButtonElement>('.dsp-btn-wrap button'))
    .find((candidate) => candidate.textContent?.trim().startsWith('NR'));
  expect(button).toBeDefined();
  return button!;
}

function openNrModal(target: HTMLElement): void {
  vi.useFakeTimers();
  try {
    nrButton(target).dispatchEvent(new Event('pointerdown', { bubbles: true }));
    vi.advanceTimersByTime(600);
    flushSync();
  } finally {
    vi.useRealTimers();
  }
}

function nrLevel(target: HTMLElement): HTMLElement | null {
  return target.querySelector('[aria-label="NR Level"]');
}

beforeEach(() => {
  components = [];
  runtimeState.caps = caps(FTX_NR_DOMAIN);
  runtimeState.state = state(4);
  Object.values(handlers).forEach((handler) => handler.mockReset());
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  document.body.innerHTML = '';
  vi.useRealTimers();
});

describe('DspPanel exact NR-level projection (MOR-1735)', () => {
  it.each([0, 1, 4, 10])('mounts exact FTX NR value %i with its 0..10 domain', (raw) => {
    runtimeState.state = state(raw);
    const target = mountPanel();

    expect(nrButton(target).textContent?.trim()).toBe(`NR ${raw}`);
    openNrModal(target);
    const slider = nrLevel(target);
    expect(slider?.getAttribute('aria-valuemin')).toBe('0');
    expect(slider?.getAttribute('aria-valuemax')).toBe('10');
    expect(slider?.getAttribute('aria-valuenow')).toBe(String(raw));
  });

  it('passes a mounted exact-domain change through without legacy rescaling', () => {
    runtimeState.state = state(3);
    const target = mountPanel();
    openNrModal(target);

    vi.useFakeTimers();
    try {
      nrLevel(target)?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      vi.advanceTimersByTime(50);
      expect(handlers.onNrLevelChange).toHaveBeenCalledWith(4);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([
    ['stale readback', state(4, 'stale'), caps(FTX_NR_DOMAIN)],
    ['unread readback', state(undefined), caps(FTX_NR_DOMAIN)],
    ['malformed metadata', state(4), caps({ ...FTX_NR_DOMAIN, display_max: '9' })],
    ['trapping metadata', state(4), caps(new Proxy({ ...FTX_NR_DOMAIN }, { get: () => { throw new Error('metadata trap'); } }))],
    ['out-of-domain raw value', state(11), caps(FTX_NR_DOMAIN)],
  ])('does not render an adjustable NR slider for %s', (_name, nextState, nextCaps) => {
    runtimeState.state = nextState;
    runtimeState.caps = nextCaps;
    const target = mountPanel();

    openNrModal(target);
    expect(nrLevel(target)).toBeNull();
    expect(handlers.onNrLevelChange).not.toHaveBeenCalled();
  });

  it('uses the exact domain origin as the unread fallback position without allowing a write', () => {
    runtimeState.state = state(undefined);
    const target = mountPanel();

    expect(nrButton(target).textContent?.trim()).toBe('NR 0');
    openNrModal(target);
    expect(target.textContent).toContain('NR Level 0');
    expect(nrLevel(target)).toBeNull();
    expect(handlers.onNrLevelChange).not.toHaveBeenCalled();
  });

  it('keeps the 0..15 legacy range when exact metadata is safely absent', () => {
    runtimeState.caps = caps();
    runtimeState.state = state(128);
    const target = mountPanel();

    expect(nrButton(target).textContent?.trim()).toBe('NR 8');
    openNrModal(target);
    const slider = nrLevel(target);
    expect(slider?.getAttribute('aria-valuemin')).toBe('0');
    expect(slider?.getAttribute('aria-valuemax')).toBe('15');
    expect(slider?.getAttribute('aria-valuenow')).toBe('8');
  });
});
