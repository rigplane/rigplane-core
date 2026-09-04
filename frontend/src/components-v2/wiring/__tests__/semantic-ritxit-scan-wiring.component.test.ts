/**
 * MOR-1308 — the semantic RIT/XIT + scan surface wired into
 * `SemanticRadioSurfaces`.
 *
 * `semantic/__tests__/RitXitScanSurface.test.ts` proves what the pure surface
 * does with a view model. This file proves what only the composed tree can
 * prove, using the REAL adapter and the REAL command bus (only the
 * runtime/transport/TX-authority SEAMS are spied), mirroring
 * `semantic-rx-audio-wiring.component.test.ts`:
 *
 *   (a) O1 end to end: editing the offset through the RIT-leading state and
 *       through the XIT-leading state both reach the wire as the SAME
 *       `set_rit_frequency` command — proving `makeRitXitHandlers()`'s two
 *       offset handlers converge, not merely asserting it about a stub.
 *   (b) MOUNTING CANON (MOR-1304 ruling). This is a control-bearing surface
 *       the DUAL composition's only layout (`dual-receiver-cockpit.ts`)
 *       declares no zone for, so per the canon's option (i) it mounts in the
 *       SINGLE composition only and renders NOTHING in the DUAL composition.
 *       No longer bare under `desktop-v2`, which declared `rit-xit-scan` in
 *       MOR-1367 (S8) — pinned here with a view model that actually
 *       CARRIES the `ritXit`/`scan` groups (a fixture that cannot see the
 *       surface would repeat the exact vacuous-green bug the canon exists to
 *       catch, per the rxAudio precedent).
 *   (c) The default path (no rit/xit capability, no scan evidence) stays
 *       byte-identical.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities, ControlDomain } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';


const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  txController: null as ManagedAppTxController | null,
  audio: { muted: true, rxEnabled: false, volume: 0 },
  audioConnected: false,
  guardVisible: false,
}));

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: vi.fn() }));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});
vi.mock('$lib/runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return h.state; },
    get caps() { return h.caps; },
    get audio() { return h.audio; },
    get connectionAudio() { return h.audioConnected; },
    // MOR-1312 slice 12B (rebase fix): the wiring now also hands the adapter
    // a scope-display snapshot (the FIFTH argument). This file tests
    // ritXit/scan, so this stays on its pre-1312 path regardless of these
    // values.
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => h.txController,
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: h.guardVisible, sourceLabel: 'MIC' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import { sendCommand } from '$lib/transport/ws-client';
// UNMOCKED real store: `makeRitXitHandlers().onRitToggle`/`onXitToggle` read
// `getRadioState()?.ritOn`/`.ritTx` to decide which way to flip. Seeding it to
// agree with `h.state` (below) keeps the toggle direction deterministic and
// independent of whatever a prior test in this file left behind.
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import { setCapabilities } from '$lib/stores/capabilities.svelte';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

/** A radio that has observed RIT ON at +250 Hz, is idle-scanning PROG with a
 *  masked resume of 1, and knows its active receiver — every field this
 *  surface can render is present so its structural gates all pass. */
function liveState(over: Partial<ServerState> = {}): ServerState {
  const paths = [
    'active', 'split', 'dualWatch', 'txTarget', 'ritOn', 'ritTx', 'ritFreq',
    'scanning', 'scanType', 'scanResumeMode',
  ];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
  }
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
    sMeter: 0, att: 0, preamp: 0, nb: false, nr: false,
    afLevel: 0, rfGain: 0, squelch: 0,
  });
  return {
    revision: 1, stateRevision: 1, freshnessRevision: 1, observationSeq: 1,
    updatedAt: '2026-08-08T00:00:00Z', tunerStatus: 0,
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    stateContractVersion: 1, providerGeneration: 0,
    ritOn: true, ritTx: false, ritFreq: 250, scanning: false, scanType: 0x01, scanResumeMode: 1,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    ...over,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (
  tags: readonly string[], controls?: Capabilities['controls'],
): Capabilities => ({
  model: 'fixture', scope: false, audio: false, tx: true,
  capabilities: tags, receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
  stateContractVersion: 1, providerGeneration: 0,
  ...(controls === undefined ? {} : { controls }),
} as unknown as Capabilities);

const FTX_RIT_DOMAIN: ControlDomain = {
  mapping: 'identity', raw_min: -9999, raw_max: 9999, raw_step: 1, raw_origin: 0,
  display_min: '-9999' as never, display_max: '9999' as never,
  display_step: '1' as never, display_origin: '0' as never, display_unit: 'Hz',
  quantization: 'reject', restoration: 'exact',
};

/** `rit`/`xit` capability tags make the ritXit group present; the explicit
 *  scan commands additionally require the declared `scan` capability. */
const RIT_XIT_TAGS = ['tx', 'rit', 'xit', 'scan'] as const;
const SILENT_TAGS = ['tx'] as const;

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let txHarness: ManagedAppTxHarness;

function render(props: { strips?: 'single' | 'dual' } = {}): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const el = (id: string) => q<HTMLElement>(`[data-testid="${id}"]`);
/** Keeps the mocked view-model state AND the real command-bus store
 *  (`getRadioState()`) in agreement — `onRitToggle`/`onXitToggle` read the
 *  latter directly. */
function useState(state: ServerState): void {
  h.state = state;
  setRadioState(state);
}

beforeEach(() => {
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  resetRadioState();
  setCapabilities(liveCaps(RIT_XIT_TAGS));
  useState(liveState());
  h.caps = liveCaps(RIT_XIT_TAGS);
  h.audio = { muted: true, rxEnabled: false, volume: 0 };
  h.audioConnected = false;
  h.guardVisible = false;
  vi.mocked(sendCommand).mockClear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  expect(txHarness.listenerCount()).toBe(0);
  expect(txHarness.trace()).toEqual([]);
  document.body.innerHTML = '';
});

/* ── (a) O1 end to end: both offset paths converge on the wire ────── */

describe('O1: editing via either gate reaches the wire as the identical command', () => {
  it('RIT-leading: sends set_rit_frequency with the edited value', () => {
    render();
    const input = q<HTMLInputElement>('[data-testid="ritxit-offset"] input')!;
    input.value = '300';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_rit_frequency', { freq: 300 });
  });

  it('XIT-leading: sends the SAME set_rit_frequency command, not a different one', () => {
    useState(liveState({ ritOn: false, ritTx: true } as Partial<ServerState>));
    render();
    const input = q<HTMLInputElement>('[data-testid="ritxit-offset"] input')!;
    input.value = '-300';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_rit_frequency', { freq: -300 });
  });

  it('never shows two divergent offset values', () => {
    render();
    expect(target.querySelectorAll('[data-testid="ritxit-offset"]').length).toBe(1);
  });

  it('composes the shipped RIT/XIT and scan command factories', async () => {
    const real = await import('$lib/runtime/commands/panel-commands');
    for (const name of ['onRitToggle', 'onXitToggle', 'onRitOffsetChange', 'onXitOffsetChange', 'onClear']) {
      expect(typeof (real.makeRitXitHandlers() as Record<string, unknown>)[name]).toBe('function');
    }
    for (const name of ['onScanStart', 'onScanStop', 'onResumeChange']) {
      expect(typeof (real.makeScanHandlers() as Record<string, unknown>)[name]).toBe('function');
    }
  });
});

describe('MOR-1731 exact RIT domain', () => {
  function renderExact(state: ServerState = liveState()): HTMLInputElement {
    const caps = liveCaps(RIT_XIT_TAGS, { rit: FTX_RIT_DOMAIN });
    h.caps = caps;
    setCapabilities(caps);
    useState(state);
    render();
    return q<HTMLInputElement>('[data-testid="ritxit-offset"] input')!;
  }

  function pressAt(raw: number, key: string): void {
    if (component) unmount(component);
    component = null;
    document.body.innerHTML = '';
    const input = renderExact(liveState({ ritFreq: raw }));
    const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
    input.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
    flushSync();
  }

  it('renders exact zero on the declared one-Hz native lattice without an intent', () => {
    const input = renderExact(liveState({ ritFreq: 0 }));
    expect([input.min, input.max, input.step, input.value]).toEqual(['-9999', '9999', '1', '0']);
    expect(el('ritxit-offset-value')!.textContent).toBe('0');
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it.each([
    ['horizontal', 'ArrowRight', 'ArrowLeft'],
    ['vertical', 'ArrowUp', 'ArrowDown'],
  ])('separates %s 50 Hz gestures from the native lattice and preserves endpoints',
    (_axis, increase, decrease) => {
      pressAt(0, increase);
      pressAt(50, decrease);
      pressAt(0, decrease);
      pressAt(-50, increase);
      pressAt(0, 'Home');
      pressAt(0, 'End');
      expect(vi.mocked(sendCommand).mock.calls).toEqual([
        ['set_rit_frequency', { freq: 50 }],
        ['set_rit_frequency', { freq: 0 }],
        ['set_rit_frequency', { freq: -50 }],
        ['set_rit_frequency', { freq: 0 }],
        ['set_rit_frequency', { freq: -9999 }],
        ['set_rit_frequency', { freq: 9999 }],
      ]);
    });

  it.each([
    ['RIT-leading', { ritOn: true, ritTx: false }, 50],
    ['XIT-leading', { ritOn: false, ritTx: true }, -50],
  ] as const)('%s exact input reaches exactly one existing offset handler', (_name, flags, freq) => {
    const input = renderExact(liveState(flags));
    input.value = String(freq);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_rit_frequency', { freq });
  });

  it('keeps the legacy constants when capability domains are absent', () => {
    render();
    const input = q<HTMLInputElement>('[data-testid="ritxit-offset"] input')!;
    expect([input.min, input.max, input.step]).toEqual(['-9999', '9999', '50']);
  });

  it.each([
    ['malformed domain', liveState(), { rit: { mapping: 'identity' } }],
    ['invalid current raw value', liveState({ ritFreq: 10000 }), { rit: FTX_RIT_DOMAIN }],
    ['failed exact encode', liveState({ ritFreq: 0 }), { rit: {
      ...FTX_RIT_DOMAIN, raw_min: -10000, raw_max: 10000, raw_step: 2,
      display_min: '-10000' as never, display_max: '10000' as never, display_step: '2' as never,
    } }],
  ] as const)('%s refuses adjustment and emits nothing', (_name, state, controls) => {
    const caps = liveCaps(RIT_XIT_TAGS, controls as never);
    h.caps = caps;
    setCapabilities(caps);
    useState(state);
    render();
    const input = q<HTMLInputElement>('[data-testid="ritxit-offset"] input')!;
    if (_name !== 'failed exact encode') expect(input.disabled).toBe(true);
    input.value = '1';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it.each([
    ['unknown receiver', liveState({ active: undefined } as Partial<ServerState>)],
    ['wrong VFO', liveState({ active: 'OTHER' } as never)],
    ['unread offset', (() => { const state = liveState({ ritFreq: undefined }); return state; })()],
    ['stale offset', (() => {
      const state = liveState();
      return { ...state, fieldStatus: { ...state.fieldStatus,
        ritFreq: { ...fresh, freshness: 'stale', availability: 'stale' } } } as ServerState;
    })()],
  ] as const)('%s emits no exact-domain offset intent', (_name, state) => {
    const input = renderExact(state);
    expect(input.disabled).toBe(true);
    input.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'ArrowRight', bubbles: true, cancelable: true,
    }));
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(sendCommand).not.toHaveBeenCalled();
  });
});

/* ── intents reach the real bus ────────────────────────────────────── */

describe('the surface intents reach the shipped command vocabulary', () => {
  it('toggling RIT sends set_rit_status', () => {
    render();
    el('ritxit-rit-toggle')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_rit_status', { on: false });
  });

  it('toggling XIT sends set_rit_tx_status', () => {
    render();
    el('ritxit-xit-toggle')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_rit_tx_status', { on: true });
  });

  it('Clear sends set_rit_frequency with freq 0', () => {
    render();
    el('ritxit-clear')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_rit_frequency', { freq: 0 });
  });

  // MOR-1495 review R2: type ownership moved to the surface's own local UI
  // state (`selectedType`, default PROG/0x01) — START no longer depends on
  // an OBSERVED scanType at all, which is what let a cold-start radio (no
  // scan command ever issued) enable the control in the first place. See
  // `RitXitScanSurface.svelte`'s file header and
  // `semantic/__tests__/RitXitScanSurface.test.ts` for the full story.
  // Cold-start (scanType never reported at all) is covered at the pure
  // surface layer — `semantic/__tests__/RitXitScanSurface.test.ts` — where
  // the fixture builder can express an unobserved field directly; this
  // file's shared `liveState()` fixture always seeds every path `fresh`
  // (see its own doc comment), so it cannot express that case. This test
  // proves the piece THIS layer owns instead: even when the store DOES
  // carry an observed scanType, the real command bus receives the
  // surface's own default, never the observed value.
  it('starting a scan sends scan_start with the surface\'s own default type, not the observed one', () => {
    useState(liveState({ scanning: false, scanType: 0x22 } as Partial<ServerState>));
    render();
    el('scan-toggle')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('scan_start', { type: 0x01 });
  });

  it('stopping a scan sends scan_stop', () => {
    useState(liveState({ scanning: true } as Partial<ServerState>));
    render();
    el('scan-toggle')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('scan_stop', {});
  });

  it('cycling resume mode sends scan_set_resume with the advanced mask', () => {
    render();
    el('scan-resume-cycle')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('scan_set_resume', { mode: 0xD2 });
  });
});

/* ── (b) MOUNTING CANON: single bare, dual absent ──────────────────── */

describe('the surface mounts only in the single composition, never in dual', () => {
  it('renders no ritXit/scan surface for a radio with no evidence at all', () => {
    h.caps = liveCaps(SILENT_TAGS);
    useState(liveState({ ritOn: undefined, ritTx: undefined, ritFreq: undefined,
      scanning: undefined, scanType: undefined, scanResumeMode: undefined } as Partial<ServerState>));
    render();
    expect(el('ritxit-scan-surface')).toBeNull();
  });

  it('renders it bare in the single composition, outside every zone', () => {
    render();
    const surface = el('ritxit-scan-surface')!;
    expect(surface).not.toBeNull();
    expect(surface.closest('[data-zone-id]')).toBeNull();
  });

  /**
   * MOUNTING CANON (MOR-1304 ruling). The view model here DOES carry both
   * groups — proven by the single-composition assertion above using the
   * identical fixture — so this is not the vacuous "the fixture can't see it
   * anyway" green the canon calls out; the absence below is the real thing.
   */
  it('renders NO ritXit/scan surface in the dual composition, zoned or unzoned', () => {
    render({ strips: 'dual' });
    expect(el('ritxit-scan-surface')).toBeNull();
    expect(target.innerHTML).not.toContain('ritxit-scan-surface');
  });

  it('leaves the cockpit composition with no focusable control outside a declared zone', () => {
    render({ strips: 'dual' });
    const outside = [...target.querySelectorAll<HTMLElement>('button, input, select, [tabindex]')]
      .filter((node) => !node.matches(':disabled') && node.closest('[data-zone-id]') === null);
    expect(outside).toEqual([]);
  });
});
