/**
 * RFG annunciator quiet state on the LCD faces (MOR-2546, owner ruling
 * 2026-09-23 — extends #3591 to the LCD skins).
 *
 * The RFG chip lights only while RF gain is REDUCED, judged on the displayed
 * percentage the VFO deck applies (`levelFormatsBelowMax`, 0..1 fraction
 * domain). At a displayed 100% (including a raw 254/255), and while the
 * value is null, the chip prints no text, keeps its slot (chip count and
 * position are unchanged), and is aria-hidden. A radio without the rf_gain
 * capability draws no RFG chip at all.
 *
 * Harness mirrors `AmberLabels.profile-data.isolated.test.ts`: real
 * `$lib/state/field-status` resolver, shared AGC read-back projection, and
 * `AmberIndStrip` renderer; only the runtime/adapter seams and the
 * state-dependent `panel-props` slice are mocked.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';

const panelProps = vi.hoisted(() => ({
  value: {
    radioState: null as ServerState | null,
    caps: null as Capabilities | null,
    hasCapability: ((_name: string) => true) as (name: string) => boolean,
    hasAudioFft: false,
    hasDualReceiver: false,
  },
}));

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveAmberScopeProps: () => panelProps.value,
  deriveAmberCockpitProps: () => panelProps.value,
  deriveAmberTelemetryProps: () => ({ vdRaw: null, idRaw: null }),
  getAmberCockpitHandlers: () => ({ onTuningChange: vi.fn() }),
  getVfoHandlers: () => ({ onFreqChange: vi.fn(), onModeChange: vi.fn() }),
  bindVfoTunerContext: () => ({ read: () => ({ view: null }) }),
}));

vi.mock('$lib/runtime/adapters/qsy-history-adapter', () => ({
  deriveQsyRecent: () => [],
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    scope: { subscribe: vi.fn(() => vi.fn()), hardwareScopeConnected: false },
    defaultScopeStatus: {
      source: null, available: false, resourceSelected: false, demand: 0,
      lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
    },
  },
}));

vi.mock('$lib/runtime/props/panel-props', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/props/panel-props')>();
  return {
    ...actual,
    toTxProps: () => ({
      txActive: false, voxActive: false, compActive: false, compLevel: 0,
      atuActive: false, atuTuning: false, hasTx: false, hasTuner: false, hasMonitor: false,
    }),
    toRitXitProps: () => ({
      ritActive: false, xitActive: false,
      ritOffset: Number.NaN, xitOffset: Number.NaN,
      hasRit: true, hasXit: true,
    }),
    toVfoOpsProps: () => ({ splitActive: false }),
    toDspProps: () => ({ notchMode: 'off', notchFreq: 0 }),
    toFilterProps: () => ({ filterWidth: Number.NaN, filterWidthMax: 9999, ifShift: 0 }),
  };
});

import AmberScope from '../AmberScope.svelte';
import AmberCockpit from '../AmberCockpit.svelte';

// ── Helpers ─────────────────────────────────────────────────────────────────

let components: ReturnType<typeof mount>[] = [];

function receiverWithRfGain(rfGain: number | null) {
  return {
    freqHz: 14_074_000, mode: 'USB', filter: 1, dataMode: 0, sMeter: 0,
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 128, rfGain,
    squelch: 0, agc: 2,
  };
}

function stateWithRfGain(rfGain: number | null): ServerState {
  return {
    active: 'MAIN',
    main: receiverWithRfGain(rfGain),
    sub: receiverWithRfGain(rfGain),
    fieldStatus: {
      'main.att': {
        storePath: 'receiver.main.operator_controls.att',
        observed: true, freshness: 'fresh', availability: 'available',
      },
      'main.preamp': {
        storePath: 'receiver.main.operator_controls.preamp',
        observed: true, freshness: 'fresh', availability: 'available',
      },
      'main.nb': {
        storePath: 'receiver.main.operator_controls.nb',
        observed: true, freshness: 'fresh', availability: 'available',
      },
      'main.rfGain': {
        storePath: 'receiver.main.operator_controls.rf_gain',
        observed: true, freshness: 'fresh', availability: 'available',
      },
      'main.agc': {
        storePath: 'receiver.main.operator_controls.agc',
        observed: true, freshness: 'fresh', availability: 'available',
      },
      'main.squelch': {
        storePath: 'receiver.main.operator_controls.squelch',
        observed: true, freshness: 'fresh', availability: 'available',
      },
    },
  } as unknown as ServerState;
}

function mountFace(
  face: typeof AmberScope | typeof AmberCockpit,
  state: ServerState | null,
  hasCapability: (name: string) => boolean = () => true,
) {
  panelProps.value = {
    radioState: state,
    caps: null,
    hasCapability,
    hasAudioFft: false,
    hasDualReceiver: false,
  };
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(face, { target });
  flushSync();
  components.push(component);
  return target;
}

const mountScope = (state: ServerState | null, hasCapability?: (name: string) => boolean) =>
  mountFace(AmberScope, state, hasCapability);
const mountCockpit = (state: ServerState | null, hasCapability?: (name: string) => boolean) =>
  mountFace(AmberCockpit, state, hasCapability);

function chips(target: HTMLElement): HTMLElement[] {
  return Array.from(target.querySelectorAll<HTMLElement>('.lcd-ind'));
}

function litChip(target: HTMLElement): HTMLElement | undefined {
  return chips(target).find((el) => el.textContent?.trim() === 'RFG');
}

function quietChip(target: HTMLElement): HTMLElement | undefined {
  return chips(target).find((el) => el.getAttribute('aria-hidden') === 'true');
}

beforeEach(() => {
  components = [];
  vi.clearAllMocks();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

// ── Tests ───────────────────────────────────────────────────────────────────

describe('RFG chip lights only while RF gain is reduced (MOR-2546)', () => {
  it.each([
    ['AmberScope', mountScope],
    ['AmberCockpit', mountCockpit],
  ] as const)('lights RFG at 0.6 on %s', (_name, mountFace) => {
    const chip = litChip(mountFace(stateWithRfGain(0.6)));
    expect(chip?.textContent?.trim()).toBe('RFG');
    expect(chip?.classList.contains('active')).toBe(true);
  });

  it.each([
    ['AmberScope', mountScope],
    ['AmberCockpit', mountCockpit],
  ] as const)('lights RFG at 253/255 (displays 99%) on %s', (_name, mountFace) => {
    const chip = litChip(mountFace(stateWithRfGain(253 / 255)));
    expect(chip?.textContent?.trim()).toBe('RFG');
    expect(chip?.classList.contains('active')).toBe(true);
  });

  it.each([
    ['AmberScope', mountScope],
    ['AmberCockpit', mountCockpit],
  ] as const)('shows no text at the maximum (1) on %s', (_name, mountFace) => {
    const chip = quietChip(mountFace(stateWithRfGain(1)));
    expect(chip).toBeDefined();
    expect(chip?.textContent?.trim()).toBe('');
    expect(chip?.classList.contains('active')).toBe(false);
    expect(chip?.getAttribute('aria-hidden')).toBe('true');
  });

  it.each([
    ['AmberScope', mountScope],
    ['AmberCockpit', mountCockpit],
  ] as const)('shows no text at 254/255 (displays 100%) on %s', (_name, mountFace) => {
    const chip = quietChip(mountFace(stateWithRfGain(254 / 255)));
    expect(chip).toBeDefined();
    expect(chip?.textContent?.trim()).toBe('');
    expect(chip?.classList.contains('active')).toBe(false);
    expect(chip?.getAttribute('aria-hidden')).toBe('true');
  });

  it.each([
    ['AmberScope', mountScope],
    ['AmberCockpit', mountCockpit],
  ] as const)('shows no text while the reading is unknown (null) on %s', (_name, mountFace) => {
    const chip = quietChip(mountFace(stateWithRfGain(null)));
    expect(chip).toBeDefined();
    expect(chip?.textContent?.trim()).toBe('');
    expect(chip?.classList.contains('active')).toBe(false);
    expect(chip?.getAttribute('aria-hidden')).toBe('true');
  });

  it.each([
    ['AmberScope', mountScope],
    ['AmberCockpit', mountCockpit],
  ] as const)('keeps the RFG slot in flow at the maximum on %s', (_name, mountFace) => {
    // The chip is never dropped: the reduced state and the maximum state
    // render the same number of chips, and the chip at the lit RFG index
    // is the quiet one (aria-hidden, no text).
    const lit = chips(mountFace(stateWithRfGain(0.6)));
    const rfgIndex = lit.findIndex((el) => el.textContent?.trim() === 'RFG');
    expect(rfgIndex).toBeGreaterThanOrEqual(0);

    const quiet = chips(mountFace(stateWithRfGain(1)));
    expect(quiet.length).toBe(lit.length);
    expect(quiet[rfgIndex]?.getAttribute('aria-hidden')).toBe('true');
    expect(quiet[rfgIndex]?.textContent?.trim()).toBe('');
  });

  it.each([
    ['AmberScope', mountScope],
    ['AmberCockpit', mountCockpit],
  ] as const)('draws no RFG chip when the radio lacks rf_gain on %s', (_name, mountFace) => {
    const target = mountFace(stateWithRfGain(0.6), (name) => name !== 'rf_gain');
    expect(litChip(target)).toBeUndefined();
    expect(quietChip(target)).toBeUndefined();
  });
});

// ── Reserved-slot pins (round-2 review, MOR-2546) ───────────────────────────
//
// The behaviour tests above observe only the rendered DOM, so they all stayed
// green when `reserveSlot: true` was deleted from both faces together with
// both layout rules. Following the #3591 precedent (`VfoIndicatorRow.test.ts`,
// `readFileSync` of the component + a regex on its `<style>`), pin the
// mechanism itself: the `slot-reserved` class that only the faces'
// `reserveSlot: true` flag produces on each rendered chip, and the strip's
// CSS rules read from source.

describe('RFG reserved-slot pins (round-2 review, MOR-2546)', () => {
  const stripSource = readFileSync(
    'src/components-v2/panels/lcd/AmberIndStrip.svelte',
    'utf8',
  );

  it('pins .lcd-ind.slot-reserved to a fixed 4ch content box', () => {
    expect(stripSource).toMatch(
      /\.lcd-ind\.slot-reserved\s*\{[^}]*min-inline-size:\s*4ch/,
    );
    expect(stripSource).toMatch(
      /\.lcd-ind\.slot-reserved\s*\{[^}]*box-sizing:\s*content-box/,
    );
  });

  it('pins .lcd-ind.ind-empty to a transparent border', () => {
    expect(stripSource).toMatch(
      /\.lcd-ind\.ind-empty\s*\{[^}]*border-color:\s*transparent/,
    );
  });

  it.each([
    ['AmberScope', mountScope],
    ['AmberCockpit', mountCockpit],
  ] as const)('classes lit and quiet RFG chips slot-reserved on %s', (_name, mountFace) => {
    const lit = litChip(mountFace(stateWithRfGain(0.6)));
    expect(lit?.classList.contains('slot-reserved')).toBe(true);

    const quiet = quietChip(mountFace(stateWithRfGain(1)));
    expect(quiet?.classList.contains('slot-reserved')).toBe(true);
  });

  it.each([
    ['AmberScope', mountScope],
    ['AmberCockpit', mountCockpit],
  ] as const)('classes the quiet RFG chip ind-empty on %s', (_name, mountFace) => {
    const quiet = quietChip(mountFace(stateWithRfGain(1)));
    expect(quiet?.classList.contains('ind-empty')).toBe(true);
  });
});
