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
 *       with no manifest-declared zone, so per the canon's option (i) it
 *       mounts in the SINGLE composition only, bare, and renders NOTHING in
 *       the DUAL composition — pinned here with a view model that actually
 *       CARRIES the `ritXit`/`scan` groups (a fixture that cannot see the
 *       surface would repeat the exact vacuous-green bug the canon exists to
 *       catch, per the rxAudio precedent).
 *   (c) The default path (no rit/xit capability, no scan evidence) stays
 *       byte-identical.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

type Snapshot = {
  phase: string; intent: string | null; guard: { leaseId: string } | null;
  radioTx: string; txRisk: string; mayOwnKey: boolean; fault: string | null;
};

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  snapshot: null as unknown,
  audio: { muted: true, rxEnabled: false, volume: 0 },
  audioConnected: false,
  guardVisible: false,
  listeners: new Set<(next: unknown) => void>(),
}));

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: vi.fn() }));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});
vi.mock('$lib/runtime', () => ({
  runtime: {
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
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => h.snapshot,
    subscribe: (listener: (next: unknown) => void) => {
      h.listeners.add(listener);
      return () => { h.listeners.delete(listener); };
    },
    start: vi.fn(), setIntent: vi.fn(), release: vi.fn(), resetFault: vi.fn(),
  }),
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

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};
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

const liveCaps = (tags: readonly string[]): Capabilities => ({
  model: 'fixture', scope: false, audio: false, tx: true,
  capabilities: tags, receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
  stateContractVersion: 1, providerGeneration: 0,
} as unknown as Capabilities);

/** `rit`/`xit` capability tags are what makes the ritXit group present;
 *  scan needs no tag at all — its raw fields above are the whole gate. */
const RIT_XIT_TAGS = ['tx', 'rit', 'xit'] as const;
const SILENT_TAGS = ['tx'] as const;

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

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
  resetRadioState();
  setCapabilities(liveCaps(RIT_XIT_TAGS));
  useState(liveState());
  h.caps = liveCaps(RIT_XIT_TAGS);
  h.snapshot = { ...IDLE };
  h.audio = { muted: true, rxEnabled: false, volume: 0 };
  h.audioConnected = false;
  h.guardVisible = false;
  h.listeners.clear();
  vi.mocked(sendCommand).mockClear();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
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

  it('starting a scan sends scan_start with the last-observed type', () => {
    useState(liveState({ scanning: false, scanType: 0x22 } as Partial<ServerState>));
    render();
    el('scan-toggle')!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('scan_start', { type: 0x22 });
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
