/**
 * MOR-1409 A14 — AmberCockpit / AmberScope migration from the stale
 * `wiring/state-adapter` projections to the honesty-hardened
 * `$lib/runtime/props/panel-props` ones (Core #2317, ruling `5247582313`,
 * correction `5247684776`).
 *
 * `panel-props.ts` defaults an unobserved reading to `Number.NaN` instead of
 * a plausible-looking stand-in (`0` / `2400` / …). Three of the six migrated
 * functions (`toRitXitProps`, `toMeterProps`, `toFilterProps`) introduce
 * real `NaN` sentinels into values both owner files already render. This
 * file proves the resulting silent-fallback risk is real (traced in the A14
 * plan §4.2) and that both owner files gate their own render on the value
 * actually being finite — never re-fabricating the old defaults, and never
 * touching the frozen consumer files (`AmberSmeter.svelte`,
 * `AmberFilterGhost.svelte`, `meter-utils.ts`, `smeter-scale.ts`, `rit-utils.ts`)
 * per the two-owner constraint (`5247582313` clause 1).
 *
 * Mounts the real `AmberCockpit`/`AmberScope` trees; only the runtime/adapter
 * seams are mocked to feed a controlled `ServerState` (mirrors
 * `AmberCockpit.qsy-authority.isolated.test.ts` and
 * `lcd-availability.isolated.test.ts`'s mounting shape).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { readFileSync } from 'node:fs';
import type { ServerState } from '$lib/types/state';

const cockpitProps = vi.hoisted(() => ({
  value: {
    radioState: null as ServerState | null,
    caps: null,
    hasCapability: (_name: string) => true,
    hasAudioFft: false,
    hasDualReceiver: false,
  },
}));

const scopeProps = vi.hoisted(() => ({
  value: {
    radioState: null as ServerState | null,
    caps: null,
    hasCapability: (_name: string) => true,
    hasAudioFft: false,
    hasDualReceiver: false,
  },
}));

const amberCaps = {
  capabilities: [
    'rit', 'xit', 'vox', 'compressor', 'tuner', 'split', 'dial_lock', 'ip_plus',
  ],
} as any;

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveAmberCockpitProps: () => cockpitProps.value,
  deriveAmberScopeProps: () => scopeProps.value,
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

import AmberCockpit from '../AmberCockpit.svelte';
import AmberScope from '../AmberScope.svelte';

// ── Helpers ─────────────────────────────────────────────────────────────────

let components: ReturnType<typeof mount>[] = [];

function baseReceiver(overrides: Record<string, unknown> = {}) {
  return {
    freqHz: 14_074_000, mode: 'USB', filter: 1, dataMode: 0, sMeter: 0,
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 128, rfGain: 255,
    squelch: 0, agc: 2,
    ...overrides,
  };
}

function mountCockpit(
  state: ServerState | null,
  hasAudioFft = false,
  caps = amberCaps,
) {
  cockpitProps.value = {
    radioState: state,
    caps,
    hasCapability: (name: string) => caps?.capabilities?.includes(name) ?? false,
    hasAudioFft,
    hasDualReceiver: false,
  };
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(AmberCockpit, { target });
  flushSync();
  components.push(component);
  return target;
}

function mountScope(
  state: ServerState | null,
  hasAudioFft = false,
  caps = amberCaps,
) {
  scopeProps.value = {
    radioState: state,
    caps,
    hasCapability: (name: string) => caps?.capabilities?.includes(name) ?? false,
    hasAudioFft,
    hasDualReceiver: false,
  };
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(AmberScope, { target });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  components = [];
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

// ── Static: the projection swap itself ──────────────────────────────────────

describe('AmberCockpit / AmberScope import migration (MOR-1409 A14)', () => {
  it('AmberCockpit.svelte no longer imports wiring/state-adapter', () => {
    const source = readFileSync('src/components-v2/panels/lcd/AmberCockpit.svelte', 'utf8');
    expect(source).not.toMatch(/wiring\/state-adapter/);
    expect(source).toContain("from '$lib/runtime/props/panel-props'");
  });

  it('AmberScope.svelte no longer imports wiring/state-adapter', () => {
    const source = readFileSync('src/components-v2/panels/lcd/AmberScope.svelte', 'utf8');
    expect(source).not.toMatch(/wiring\/state-adapter/);
    expect(source).toContain("from '$lib/runtime/props/panel-props'");
  });
});

describe('Amber RIT indicator availability (MOR-1586)', () => {
  const fresh = {
    storePath: 'fixture', observed: true, freshness: 'fresh', availability: 'available',
  } as const;
  const missing = {
    storePath: 'fixture', observed: false, freshness: 'unknown', availability: 'missing',
  } as const;

  function indicatorLabels(target: HTMLElement): string[] {
    return [...target.querySelectorAll('.lcd-ind')].map((indicator) => indicator.textContent ?? '');
  }

  it('uses the real capability props to render confirmed RIT in both entry points', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver(),
      ritOn: true,
      ritFreq: 0,
      ritTx: false,
      fieldStatus: { ritOn: fresh, ritFreq: fresh, ritTx: fresh },
    } as unknown as ServerState;

    expect(indicatorLabels(mountCockpit(state))).toContain('RIT');
    expect(indicatorLabels(mountScope(state))).toContain('RIT');
  });

  it('suppresses RIT rather than presenting an unobserved false value as confirmed off', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver(),
      ritOn: false,
      ritFreq: 0,
      ritTx: false,
      fieldStatus: { ritOn: missing, ritFreq: missing, ritTx: missing },
    } as unknown as ServerState;

    expect(indicatorLabels(mountCockpit(state))).not.toContain('RIT');
    expect(indicatorLabels(mountScope(state))).not.toContain('RIT');
  });
});

describe('Amber TX and inline RIT availability (MOR-1586 review)', () => {
  const fresh = {
    storePath: 'fixture', observed: true, freshness: 'fresh', availability: 'available',
  } as const;
  const missing = {
    storePath: 'fixture', observed: false, freshness: 'unknown', availability: 'missing',
  } as const;
  const stale = {
    storePath: 'fixture', observed: true, freshness: 'stale', availability: 'available',
  } as const;

  function indicatorLabels(target: HTMLElement): string[] {
    return [...target.querySelectorAll('.lcd-ind')].map((indicator) => indicator.textContent ?? '');
  }

  function indicator(target: HTMLElement, label: string): Element | undefined {
    return [...target.querySelectorAll('.lcd-ind')]
      .find((element) => element.textContent === label);
  }

  function fullyObservedState(fieldStatus: Record<string, typeof fresh | typeof missing | typeof stale>) {
    return {
      active: 'MAIN',
      main: baseReceiver({ ipplus: true }), sub: baseReceiver(),
      ptt: true, voxOn: true, compressorOn: true, compressorLevel: 7,
      tunerStatus: 1, split: true, dialLock: true,
      ritOn: true, ritFreq: 250, ritTx: true,
      fieldStatus,
    } as unknown as ServerState;
  }

  it('suppresses stale TX rather than rendering a confirmed state in both entry points', () => {
    const state = {
      active: 'MAIN', main: baseReceiver(), sub: baseReceiver(), ptt: true,
      fieldStatus: { ptt: stale },
    } as unknown as ServerState;

    expect(indicatorLabels(mountCockpit(state))).not.toContain('TX');
    expect(indicatorLabels(mountScope(state))).not.toContain('TX');
  });

  it('suppresses stale active RIT/XIT inline rows in the cockpit', () => {
    const state = {
      active: 'MAIN', main: baseReceiver(), sub: baseReceiver(),
      ritOn: true, ritFreq: 250, ritTx: true,
      fieldStatus: { ritOn: stale, ritFreq: stale, ritTx: stale },
    } as unknown as ServerState;

    expect(mountCockpit(state).querySelector('.lcd-rit-row')).toBeNull();
  });

  it('renders every changed token only from fresh facts in each applicable entry point', () => {
    const fieldStatus = Object.fromEntries([
      'ptt', 'voxOn', 'compressorOn', 'compressorLevel', 'tunerStatus',
      'split', 'dialLock', 'main.ipplus', 'ritOn', 'ritFreq', 'ritTx',
    ].map((field) => [field, fresh]));
    const cockpit = mountCockpit(fullyObservedState(fieldStatus));
    const scope = mountScope(fullyObservedState(fieldStatus));

    for (const label of ['TX', 'VOX', 'PROC 7', 'ATU', 'SPLIT', 'LOCK', 'IP+', 'RIT']) {
      expect(indicator(cockpit, label)).toBeDefined();
    }
    for (const label of ['TX', 'VOX', 'PROC 7', 'SPLIT', 'LOCK', 'RIT']) {
      expect(indicator(scope, label)).toBeDefined();
    }
    expect(cockpit.querySelector('.rit-label')?.textContent).toBe('RIT');
  });

  it('suppresses every changed token when the corresponding facts are missing or stale', () => {
    const fieldStatus = Object.fromEntries([
      'ptt', 'voxOn', 'compressorOn', 'compressorLevel', 'tunerStatus', 'split',
      'dialLock', 'main.ipplus', 'ritOn', 'ritFreq', 'ritTx',
    ].map((field, index) => [field, index % 2 === 0 ? missing : stale]));
    const cockpit = mountCockpit(fullyObservedState(fieldStatus));
    const scope = mountScope(fullyObservedState(fieldStatus));

    for (const label of ['TX', 'VOX', 'PROC 7', 'ATU', 'SPLIT', 'LOCK', 'IP+', 'RIT']) {
      expect(indicator(cockpit, label)).toBeUndefined();
    }
    for (const label of ['TX', 'VOX', 'PROC 7', 'SPLIT', 'LOCK', 'RIT']) {
      expect(indicator(scope, label)).toBeUndefined();
    }
    expect(cockpit.querySelector('.lcd-rit-row')).toBeNull();
  });

  it('preserves confirmed-off and confirmed-on distinctions without fabricating levels', () => {
    const offState = fullyObservedState({ ritOn: fresh, ritFreq: fresh, ritTx: fresh, ptt: fresh });
    offState.ritOn = false;
    offState.ritTx = false;
    offState.ptt = false;
    const cockpit = mountCockpit(offState);
    const scope = mountScope(offState);
    expect(indicator(cockpit, 'RIT')?.classList.contains('active')).toBe(false);
    expect(indicator(scope, 'RIT')?.classList.contains('active')).toBe(false);
    expect(indicator(cockpit, 'TX')?.classList.contains('active')).toBe(false);
    expect(indicator(scope, 'TX')?.classList.contains('active')).toBe(false);

    const levelStale = fullyObservedState({ compressorOn: fresh, compressorLevel: stale });
    expect(indicator(mountCockpit(levelStale), 'PROC')).toBeDefined();
    expect(indicator(mountScope(levelStale), 'PROC')).toBeDefined();
    expect(indicator(mountCockpit(levelStale), 'PROC 7')).toBeUndefined();
    expect(indicator(mountScope(levelStale), 'PROC 7')).toBeUndefined();
  });

  it('renders the cockpit inline XIT row only when XIT is confirmed fresh', () => {
    const state = fullyObservedState({ ritOn: fresh, ritFreq: fresh, ritTx: fresh });
    state.ritOn = false;
    state.ritTx = true;
    expect(mountCockpit(state).querySelector('.rit-label')?.textContent).toBe('XIT');
  });
});

// ── RIT-offset consumer-boundary guard (AmberCockpit only — AmberScope never
//    reads ritOffset, per plan §3.2) ─────────────────────────────────────────

describe('AmberCockpit RIT-offset NaN guard (MOR-1409 A14 plan §4.2 finding #1)', () => {
  it('state === null: no RIT row at all (ritActive itself is false)', () => {
    const target = mountCockpit(null);
    expect(target.querySelector('.rit-value')).toBeNull();
  });

  it('connected but ritFreq unobserved: RIT is on, but the offset never renders "NaN"', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver(),
      ritOn: true,
      // ritFreq intentionally absent — panel-props defaults it to NaN.
    } as unknown as ServerState;

    const target = mountCockpit(state);
    const value = target.querySelector('.rit-value');
    expect(value).not.toBeNull();
    expect(value!.textContent).not.toContain('NaN');
  });

  it('connected with an observed ritFreq: the real offset still renders (guard is not overbroad)', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver(),
      ritOn: true,
      ritFreq: 250,
    } as unknown as ServerState;

    const target = mountCockpit(state);
    const value = target.querySelector('.rit-value');
    expect(value!.textContent).toBe('+0.25 kHz');
  });
});

// ── Meter-format silent-fallback guard (AmberCockpit only — AmberScope never
//    imports toMeterProps, per plan §3.2) — correction 5247684776 ──────────

describe('AmberCockpit meter-source silent-fallback guard (MOR-1409 A14 plan §4.2 finding #2)', () => {
  function meterSourceButton(target: HTMLElement): HTMLButtonElement {
    const btn = target.querySelector<HTMLButtonElement>('.lcd-meter-src-btn');
    if (!btn) throw new Error('meter source button did not mount');
    return btn;
  }

  it('powerMeter unobserved: selecting PO does not present a fabricated full-scale reading — falls back to S', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver(),
      // powerMeter intentionally absent — panel-props defaults meter.rfPower to NaN.
    } as unknown as ServerState;

    const target = mountCockpit(state);
    const btn = meterSourceButton(target);
    expect(btn.textContent).toBe('S');
    btn.click();
    flushSync();
    // User selected PO, but the field is unobserved: the displayed source
    // must not silently present the piecewise() clamp-and-fallthrough
    // top-of-scale value as though it were a confirmed reading.
    expect(btn.textContent).toBe('S');
  });

  it('powerMeter observed: selecting PO does present PO (guard is not overbroad)', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver(),
      powerMeter: 100,
    } as unknown as ServerState;

    const target = mountCockpit(state);
    const btn = meterSourceButton(target);
    btn.click();
    flushSync();
    expect(btn.textContent).toBe('PO');
  });
});

// ── Filter-ratio consumer-boundary guard (both owner files, ghost fallback
//    path — plan §4.2 finding #3) ───────────────────────────────────────────

function ghostPassbandPoints(target: HTMLElement): string {
  const el = target.querySelector('polyline.passband');
  if (!el) throw new Error('AmberFilterGhost passband polyline did not mount');
  return el.getAttribute('points') ?? '';
}

describe('AmberCockpit filter-ratio NaN guard (MOR-1409 A14 plan §4.2 finding #3)', () => {
  it('state === null: the ghost passband geometry stays finite', () => {
    const target = mountCockpit(null, false);
    expect(ghostPassbandPoints(target)).not.toContain('NaN');
  });

  it('connected but filterWidth unobserved: the ghost passband geometry stays finite', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver(),
      // main.filterWidth intentionally absent — panel-props defaults
      // filterProps.filterWidth to NaN.
    } as unknown as ServerState;

    const target = mountCockpit(state, false);
    expect(ghostPassbandPoints(target)).not.toContain('NaN');
  });

  it('connected with an observed filterWidth: the ghost still renders real geometry (guard is not overbroad)', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver({ filterWidth: 1800 }),
      sub: baseReceiver(),
    } as unknown as ServerState;

    const target = mountCockpit(state, false);
    const points = ghostPassbandPoints(target);
    expect(points).not.toContain('NaN');
    expect(points.length).toBeGreaterThan(0);
  });
});

describe('AmberScope filter-ratio NaN guard (MOR-1409 A14 plan §4.2 finding #3)', () => {
  it('state === null: the ghost passband geometry stays finite', () => {
    const target = mountScope(null, false);
    expect(ghostPassbandPoints(target)).not.toContain('NaN');
  });

  it('connected but filterWidth unobserved: the ghost passband geometry stays finite', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver(),
    } as unknown as ServerState;

    const target = mountScope(state, false);
    expect(ghostPassbandPoints(target)).not.toContain('NaN');
  });

  it('connected with an observed filterWidth: the ghost still renders real geometry (guard is not overbroad)', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver({ filterWidth: 1800 }),
      sub: baseReceiver(),
    } as unknown as ServerState;

    const target = mountScope(state, false);
    const points = ghostPassbandPoints(target);
    expect(points).not.toContain('NaN');
    expect(points.length).toBeGreaterThan(0);
  });
});
