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

// MOR-2673 review F1: the shipped profile catalogs the reserved mode-box
// width is derived from — rigs/ftx1.toml [modes].list (longest label
// "DATA-FM-N" = 9ch, filters list empty) and rigs/ic7300.toml (longest
// "RTTY-R" = 6ch, three FIL filters).
const ftx1Caps = {
  capabilities: [...amberCaps.capabilities],
  modes: [
    'LSB', 'USB', 'CW-U', 'FM', 'AM', 'RTTY-L', 'CW-L', 'DATA-L', 'RTTY-U',
    'DATA-FM', 'FM-N', 'DATA-U', 'AM-N', 'PSK', 'DATA-FM-N', 'C4FM-DN', 'C4FM-VW',
  ],
  filters: [],
} as any;

const ic7300Caps = {
  capabilities: [...amberCaps.capabilities],
  modes: ['USB', 'LSB', 'CW', 'CW-R', 'AM', 'FM', 'RTTY', 'RTTY-R'],
  filters: ['FIL1', 'FIL2', 'FIL3'],
} as any;

function reservedCh(element: Element | null): number {
  const minWidth = (element as HTMLElement | null)?.style?.minWidth ?? '';
  // MOR-2673 quick follow-up: the reservation is `calc(Nch + Npx)` — one
  // ch per glyph plus the rule's 1px letter-spacing per glyph.
  const parsed = /(\d+(?:\.\d+)?)ch/.exec(minWidth);
  return parsed ? Number.parseFloat(parsed[1]) : 0;
}

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
  dual = false,
) {
  scopeProps.value = {
    radioState: state,
    caps,
    hasCapability: (name: string) => caps?.capabilities?.includes(name) ?? false,
    hasAudioFft,
    hasDualReceiver: dual,
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

  function indicator(target: HTMLElement, label: string): Element | undefined {
    return [...target.querySelectorAll('.lcd-ind')]
      .find((element) => element.textContent === label);
  }

  function stateFor(
    field: string,
    value: boolean | number,
    status: typeof fresh | typeof missing | typeof stale,
  ): ServerState {
    const state: any = {
      active: 'MAIN',
      main: baseReceiver({ ipplus: true }), sub: baseReceiver(),
      ptt: true, voxOn: true, compressorOn: true, compressorLevel: 7,
      tunerStatus: 1, split: true, dialLock: true,
      ritOn: true, ritFreq: 250, ritTx: true,
      fieldStatus: { [field]: status },
    };
    if (field === 'main.ipplus') state.main.ipplus = value;
    else state[field] = value;
    return state as ServerState;
  }

  const tokenCases = [
    ['TX', 'ptt', false, true, 'TX', [mountCockpit, mountScope]],
    ['VOX', 'voxOn', false, true, 'VOX', [mountCockpit, mountScope]],
    ['PROC', 'compressorOn', false, true, 'PROC 7', [mountCockpit, mountScope]],
    ['ATU', 'tunerStatus', 0, 1, 'ATU', [mountCockpit]],
    ['SPLIT', 'split', false, true, 'SPLIT', [mountCockpit, mountScope]],
    ['LOCK', 'dialLock', false, true, 'LOCK', [mountCockpit, mountScope]],
    ['IP+', 'main.ipplus', false, true, 'IP+', [mountCockpit]],
    ['RIT', 'ritOn', false, true, 'RIT', [mountCockpit, mountScope]],
  ] as const;

  it.each(tokenCases)('%s distinguishes fresh off/on from missing, and presents its last state when stale (R29)', (
    _name, field, offValue, onValue, label, mounts,
  ) => {
    for (const mountPanel of mounts) {
      const offLabel = _name === 'PROC' ? 'PROC' : label;
      const off = indicator(mountPanel(stateFor(field, offValue, fresh)), offLabel);
      expect(off).toBeDefined();
      expect(off?.classList.contains('active')).toBe(false);

      const on = indicator(mountPanel(stateFor(field, onValue, fresh)), label);
      expect(on).toBeDefined();
      expect(on?.classList.contains('active')).toBe(true);
      expect(indicator(mountPanel(stateFor(field, onValue, missing)), label)).toBeUndefined();

      const staleOn = indicator(mountPanel(stateFor(field, onValue, stale)), label);
      expect(staleOn).toBeDefined();
      expect(staleOn?.classList.contains('active')).toBe(true);
    }
  });

  it('distinguishes ATU from TUNE and never fabricates a PROC level for a missing reading; a stale one keeps its last level (R29)', () => {
    const tuning = indicator(mountCockpit(stateFor('tunerStatus', 2, fresh)), 'TUNE');
    expect(tuning?.classList.contains('active')).toBe(true);

    expect(indicator(mountCockpit(stateFor('tunerStatus', 2, missing)), 'TUNE')).toBeUndefined();
    expect(indicator(mountCockpit(stateFor('compressorLevel', 7, missing)), 'PROC')).toBeDefined();
    expect(indicator(mountScope(stateFor('compressorLevel', 7, missing)), 'PROC')).toBeDefined();
    expect(indicator(mountCockpit(stateFor('compressorLevel', 7, missing)), 'PROC 7')).toBeUndefined();
    expect(indicator(mountScope(stateFor('compressorLevel', 7, missing)), 'PROC 7')).toBeUndefined();

    const staleTuning = indicator(mountCockpit(stateFor('tunerStatus', 2, stale)), 'TUNE');
    expect(staleTuning?.classList.contains('active')).toBe(true);
    expect(indicator(mountCockpit(stateFor('compressorLevel', 7, stale)), 'PROC')).toBeUndefined();
    expect(indicator(mountScope(stateFor('compressorLevel', 7, stale)), 'PROC')).toBeUndefined();
    expect(indicator(mountCockpit(stateFor('compressorLevel', 7, stale)), 'PROC 7')).toBeDefined();
    expect(indicator(mountScope(stateFor('compressorLevel', 7, stale)), 'PROC 7')).toBeDefined();
  });

  it('shows inline XIT for fresh or stale XIT-on (R29) and hides only a missing XIT', () => {
    for (const status of [fresh, missing, stale]) {
      const state = stateFor('ritTx', true, status) as any;
      state.ritOn = false;
      const label = mountCockpit(state).querySelector('.rit-label')?.textContent;
      expect(label).toBe(status === missing ? undefined : 'XIT');
    }
  });

  it('hides the inline row for fresh confirmed-off RIT and XIT', () => {
    const state = stateFor('ritTx', false, fresh) as any;
    state.ritOn = false;
    state.fieldStatus.ritOn = fresh;

    expect(mountCockpit(state).querySelector('.rit-label')).toBeNull();
  });

  it('gives a stale-but-observed RIT-on the same label priority as a fresh one (R29)', () => {
    const state = stateFor('ritTx', true, fresh) as any;
    state.ritOn = true;
    state.fieldStatus.ritOn = stale;

    expect(mountCockpit(state).querySelector('.rit-label')?.textContent).toBe('RIT');
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

// ── MOR-2673: the unread mode/RIT sentinel never reaches the screen ────────

describe('Amber unread mode / RIT sentinel (MOR-2673)', () => {
  const fresh = {
    storePath: 'fixture', observed: true, freshness: 'fresh', availability: 'available',
  } as const;

  function cockpitSource(): string {
    return readFileSync('src/components-v2/panels/lcd/AmberCockpit.svelte', 'utf8');
  }
  function scopeSource(): string {
    return readFileSync('src/components-v2/panels/lcd/AmberScope.svelte', 'utf8');
  }

  it('AmberCockpit draws no mode box at all until the profile catalog is known', () => {
    // The App mounts the skin before the capabilities fetch lands
    // (App.svelte resolves the presentation without waiting for
    // runtime.bootstrap), so a caps-less mount is a real first frame.
    // The box appears once, with its reservation — never hugs its padding
    // and then jumps when the catalog arrives (MOR-2673).
    for (const caps of [amberCaps, null]) {
      const state = {
        active: 'MAIN',
        main: baseReceiver({ mode: undefined }),
        fieldStatus: {},
      } as unknown as ServerState;
      const target = mountCockpit(state, false, caps);
      expect(target.querySelector('.vfo-mode-box')).toBeNull();
    }
  });

  it('AmberCockpit renders an unread main mode as an empty reserved slot, never a dash run', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver({ mode: undefined, filter: undefined }),
      fieldStatus: {},
    } as unknown as ServerState;
    const target = mountCockpit(state, false, ic7300Caps);
    const box = target.querySelector('.vfo-mode-box');
    expect(box).not.toBeNull();
    expect(box?.textContent).toBe('');
    expect(reservedCh(box)).toBeGreaterThanOrEqual(8);
    expect(target.textContent ?? '').not.toContain('---');
  });

  it('AmberCockpit renders a known mode exactly as before the change', () => {
    const target = mountCockpit({
      active: 'MAIN',
      main: baseReceiver(),
      fieldStatus: {},
    } as unknown as ServerState, false, ic7300Caps);
    expect(target.querySelector('.vfo-mode-box')?.textContent).toBe('USB 1');
  });

  it('AmberCockpit reserves the mode box and RIT value in every state (structural pin)', () => {
    const source = cockpitSource();
    // MOR-2673 review F1: no radio-specific width constant in the CSS — the
    // mode box reserves through the profile-derived inline `min-width`
    // (pinned behaviorally below); the `.rit-value` rule still reserves
    // "−9.99 kHz" = 9ch.
    expect(source).not.toMatch(/\.vfo-mode-box \{[^}]*min-width:/s);
    expect(source).toContain('modeBoxMinWidth');
    // MOR-2673 quick follow-up: content-box keeps the padding/border out of
    // the ch budget; without it the reservation under-covers the widest
    // text and the box jumps when a reading arrives.
    expect(source).toMatch(/\.vfo-mode-box \{[^}]*box-sizing: content-box;/s);
    expect(source).toMatch(/\.rit-value \{[^}]*min-width: 9ch;/s);
    // The dash-run sentinel is gone from both amber faces entirely.
    expect(cockpitSource()).not.toContain("'---'");
    expect(scopeSource()).not.toContain("'---'");
  });

  it('AmberCockpit renders an active RIT with an unread offset as an empty reserved value', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      ritOn: true,
      fieldStatus: { ritOn: fresh, ritFreq: fresh },
    } as unknown as ServerState;
    const target = mountCockpit(state);
    const value = target.querySelector('.rit-value');
    expect(value).not.toBeNull();
    expect(value?.textContent).toBe('');
  });

  it('AmberCockpit renders a known RIT offset exactly as before the change', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      ritOn: true,
      ritFreq: 250,
      fieldStatus: { ritOn: fresh, ritFreq: fresh },
    } as unknown as ServerState;
    expect(mountCockpit(state).querySelector('.rit-value')?.textContent).toBe('+0.25 kHz');
  });

  it('AmberScope draws no mode box at all until the profile catalog is known', () => {
    // Same first-frame rule as the cockpit: caps-less caps (or a catalog
    // without modes) draws no box — it appears once, with its reservation.
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      fieldStatus: {},
    } as unknown as ServerState;
    for (const caps of [amberCaps, null]) {
      const target = mountScope(state, false, caps, true);
      expect(target.querySelector('.vfo-mode-box')).toBeNull();
    }
  });

  it('AmberScope renders unread main and sub modes as empty reserved slots, never a dash run', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver({ mode: undefined }),
      sub: baseReceiver({ mode: undefined }),
      fieldStatus: {},
    } as unknown as ServerState;
    const target = mountScope(state, false, ic7300Caps, true);
    const boxes = [...target.querySelectorAll('.vfo-mode-box')];
    expect(boxes).toHaveLength(2);
    for (const box of boxes) {
      expect(box.textContent).toBe('');
      expect(reservedCh(box)).toBeGreaterThanOrEqual(6);
    }
    expect(target.textContent ?? '').not.toContain('---');
  });

  it('AmberScope renders known modes exactly as before the change and reserves the box structurally', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver(),
      sub: baseReceiver({ mode: 'CW-R' }),
      fieldStatus: {},
    } as unknown as ServerState;
    const target = mountScope(state, false, ic7300Caps, true);
    const boxes = [...target.querySelectorAll('.vfo-mode-box')];
    expect(boxes.map((box) => box.textContent)).toEqual(['USB', 'CW-R']);
    // MOR-2673 review F1: no radio-specific width constant in the CSS —
    // the box reserves through the profile-derived inline `min-width`
    // (pinned behaviorally below).
    expect(scopeSource()).not.toMatch(/\.vfo-mode-box \{[^}]*min-width:/s);
    expect(scopeSource()).toContain('modeBoxMinWidth');
    expect(scopeSource()).toMatch(/\.vfo-mode-box \{[^}]*box-sizing: content-box;/s);
  });
});

// ── MOR-2673 review F1: the reserved mode-box width is profile-derived ──────
// The old CSS constants (6ch / 8ch) under-covered the shipped Yaesu FTX-1
// catalog ("DATA-FM-N" = 9ch, "DATA-FM-N 1" = 11ch); the reservation must
// come from the mounted profile's own catalogs, never a hardcoded constant.

describe('Amber mode-box reserved width is derived from the profile catalog (MOR-2673 review F1)', () => {
  it('AmberCockpit reserves at least 11ch for the FTX-1 catalog ("DATA-FM-N 1")', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver({ mode: 'DATA-FM-N', filter: 1 }),
      fieldStatus: {},
    } as unknown as ServerState;
    const target = mountCockpit(state, false, ftx1Caps);
    const box = target.querySelector('.vfo-mode-box');
    expect(box?.textContent).toBe('DATA-FM-N 1');
    expect(reservedCh(box)).toBeGreaterThanOrEqual(11);
  });

  it('AmberCockpit keeps the IC-7300 reservation at 8ch ("RTTY-R 1")', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver({ mode: 'RTTY-R', filter: 1 }),
      fieldStatus: {},
    } as unknown as ServerState;
    const target = mountCockpit(state, false, ic7300Caps);
    const box = target.querySelector('.vfo-mode-box');
    expect(box?.textContent).toBe('RTTY-R 1');
    expect(reservedCh(box)).toBe(8);
  });

  it('AmberScope reserves at least 9ch for the FTX-1 catalog ("DATA-FM-N")', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver({ mode: 'DATA-FM-N' }),
      fieldStatus: {},
    } as unknown as ServerState;
    const target = mountScope(state, false, ftx1Caps);
    const box = target.querySelector('.vfo-mode-box');
    expect(box?.textContent).toBe('DATA-FM-N');
    expect(reservedCh(box)).toBeGreaterThanOrEqual(9);
  });

  it('AmberScope keeps the IC-7300 reservation at 6ch ("RTTY-R")', () => {
    const state = {
      active: 'MAIN',
      main: baseReceiver({ mode: 'RTTY-R' }),
      fieldStatus: {},
    } as unknown as ServerState;
    const target = mountScope(state, false, ic7300Caps);
    const box = target.querySelector('.vfo-mode-box');
    expect(box?.textContent).toBe('RTTY-R');
    expect(reservedCh(box)).toBe(6);
  });
});
