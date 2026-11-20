/**
 * Visual-witness entry for `components/spectrum/SpectrumPanel.svelte`.
 *
 * WHY A SECOND ENTRY. `fixtures/index.html` mounts the cockpit / reference /
 * peer-split / LCD skins; none of them mounts `SpectrumPanel`, so nothing in
 * `fixtures/approved-baselines/` covers the panorama the spectrum renderer
 * and the DOM overlay paint. This entry is the same shape as
 * `ptt-harness.html`/`ptt-main.ts`: a standalone page in the same fixtures
 * server, not a new fixture id in `catalog.ts`.
 *
 * WHAT IS FROZEN. MOR-4270: the panel consumes the shared `ScopeStream`, so
 * the witness feeds ONE constant frame through the real ingress parse (the
 * production v2 fixture header) into a synthetic
 * stream patched onto the fixture runtime, and hands the panel a
 * frozen managed region for the passband. No clock and no scope transport
 * reaches the trace, the waterfall or the tune-line/passband overlay.
 * `binByte` is integer-only arithmetic over the bin index — no
 * `Math.random`, no `Date`, no transcendental function.
 *
 * WHAT IS NOT. Rendering and state projection stay production-real. The
 * witness replaces only the panel's frequency and filter output callbacks
 * with an in-page command log, so interaction tests can prove that separator
 * gestures do not escape through either command seam. The toolbar's STEP
 * readout still comes from the live tuning store and
 * `BandPlanOverlay`/`SpectrumToolbar` still issue their band-plan fetches.
 *
 */
import { flushSync, mount } from 'svelte';
import '../src/app.css';
import '../src/components-v2/theme/index';
import SpectrumPanel from '../src/components/spectrum/SpectrumPanel.svelte';
import { ScopeStream } from '../src/lib/runtime/scope-stream';
import type { ScopeMessageSource } from '../src/lib/runtime/scope-stream';
import type { ScopeSubscription } from '../src/lib/runtime/scope-view';
import { runtime } from '../src/lib/runtime/frontend-runtime';
import { radio } from '../src/lib/stores/radio.svelte';
import { setCapabilities } from '../src/lib/stores/capabilities.svelte';
import { WsChannel } from '../src/lib/transport/ws-client';
import radioWireUrl from '../../tests/fixtures/scope-wire/radio.bin?url';
import ic7300StateJson from '../src/lib/runtime/adapters/__tests__/fixtures/ic7300-state.json';
import ic7300CapsJson from '../src/lib/runtime/adapters/__tests__/fixtures/ic7300-capabilities.json';
import { parseCurrentScopeFrame } from '../src/lib/runtime/adapters/scope-adapter';
import {
  getFilterHandlers,
  getVfoHandlers,
} from '../src/lib/runtime/adapters/panel-adapters';

type WitnessCommand = Readonly<{
  kind: 'frequency' | 'filter';
  args: readonly unknown[];
}>;

declare global {
  interface Window {
    __spectrumWitness: {
      commands: WitnessCommand[];
      /** MOR-4605: every radio command the page tried to send
       * (`name(params)`), recorded at the WsChannel send seam — the
       * offline witness ships nothing. */
      scopeIntents(): string[];
      autoFloor?: {
        deliverFrames(count: number, noise: number, carrier: number): Promise<void>;
      };
    };
  }
}

const commands: WitnessCommand[] = [];
const sentCommands: string[] = [];
window.__spectrumWitness = {
  commands,
  scopeIntents: () => [...sentCommands],
};
const vfoHandlers = getVfoHandlers();
const filterHandlers = getFilterHandlers();
vfoHandlers.onFreqChange = (...args) => commands.push({ kind: 'frequency', args });
filterHandlers.onFilterWidthCommit = (...args) => commands.push({ kind: 'filter', args });

if (new URLSearchParams(location.search).has('legacyBaseline')) {
  const style = document.createElement('style');
  style.textContent = `
    .spectrum-split-region { display: contents !important; }
    .spectrum-split-separator { display: none !important; }
    .spectrum-with-scales { flex: 0 0 30% !important; border-bottom: 1px solid var(--panel-border) !important; }
    .freq-axis { flex: 0 0 20px !important; border-bottom: 1px solid var(--panel-border) !important; }
    .waterfall-area { flex: 1 1 70% !important; }
  `;
  document.head.append(style);
}

if (new URLSearchParams(location.search).has('portrait')) {
  document.getElementById('app')!.style.height = '220px';
}

const BIN_COUNT = 256;
/** The renderer saturates at 80 (`SPECTRUM_AMPLITUDE_MAX`); stay under it. */
const BIN_BYTE_MAX = 80;
const NOISE_FLOOR_BYTE = 10;

/** Deterministic 0-15 dither, integer ops only. */
function noiseByte(index: number): number {
  let h = Math.imul(index + 1, 2654435761) >>> 0;
  h ^= h >>> 15;
  h = Math.imul(h, 2246822519) >>> 0;
  h ^= h >>> 13;
  return (h >>> 0) % 16;
}

const PEAKS = [
  { center: 60, halfWidth: 7, height: 40 },
  { center: 128, halfWidth: 4, height: 60 },
  { center: 196, halfWidth: 12, height: 24 },
];

function binByte(index: number): number {
  let value = NOISE_FLOOR_BYTE + noiseByte(index);
  for (const peak of PEAKS) {
    const distance = Math.abs(index - peak.center);
    if (distance > peak.halfWidth) continue;
    const shoulder = NOISE_FLOOR_BYTE
      + Math.round((peak.height * (peak.halfWidth - distance)) / peak.halfWidth);
    if (shoulder > value) value = shoulder;
  }
  return Math.min(BIN_BYTE_MAX, value);
}

const START_HZ = 14_090_000;
const END_HZ = 14_110_000;
const TUNE_HZ = 14_100_000;
/** 0 = CTR. In CTR the panel draws the tune line at 50% and never hides it. */
const FRAME_MODE = 0;

const template = await (await fetch(radioWireUrl)).arrayBuffer();
const context = { source: 'radio' as const, sourceId: 'radio:main', epoch: 1 };
const parsed = parseCurrentScopeFrame(template, context);
if (!parsed.ok) throw new Error(`Invalid producer fixture: ${parsed.reason}`);
const pixelOffset = template.byteLength - parsed.frame.width;

function encodeWitnessFrame(): ArrayBuffer {
  const bytes = new Uint8Array(pixelOffset + BIN_COUNT);
  bytes.set(new Uint8Array(template, 0, pixelOffset));
  const view = new DataView(bytes.buffer);
  view.setUint16(6, BIN_COUNT, true);
  view.setFloat64(24, START_HZ, true);
  view.setFloat64(32, END_HZ, true);
  view.setFloat64(40, START_HZ, true);
  view.setFloat64(48, END_HZ, true);
  for (let index = 0; index < BIN_COUNT; index += 1) bytes[pixelOffset + index] = binByte(index);
  return bytes.buffer;
}

/* ── MOR-4605 product-seam mode (`?autoFloor`) ─────────────────────────────
 * The browser test clicks the panel's real AUTO and REF keys, so this mode
 * mounts the toolbar (hideScopeControls=false) against the REAL captured
 * IC-7300 state/capabilities pair — the one pair in the tree whose
 * `scopeControls.refDb` is observed with a declared −20..20 range, so the
 * REF keys render live exactly as on the bench. The capture's VFO sits at
 * 14.188 MHz; the clone moves it to TUNE_HZ so the panel's follow view (the
 * confirmed frequency ± the requested span) is covered exactly by the
 * delivered frames — no "view wider than coverage" state. Frames stay flat
 * noise plus one 5-bin carrier (integer levels, converging to exact row
 * bytes after a level change) and are delivered on demand through the SAME
 * ingress: the production v2 header, the real parser, the real ScopeStream.
 * Radio commands the page tries to send (the AUTO-off REF write) are
 * recorded at the WsChannel send seam and ship nothing: the witness owns no
 * socket. */
const autoFloorMode = new URLSearchParams(location.search).has('autoFloor');
/** Carrier bins 120..124 — the exact-carrier middle is bin 122. */
const AUTO_FLOOR_CARRIER = [120, 125] as const;
/** The first delivered frame's levels; the spec's phases continue from it. */
const AUTO_FLOOR_FIRST = { noise: 10, carrier: 50 } as const;

let autoFloorWindow: { start: number; end: number } | null = null;

/** The frame window for a surface request: an explicit view as-is, a follow
 *  request centred on the confirmed frequency the clone pins to TUNE_HZ. */
function autoFloorViewWindow(subscription: ScopeSubscription): { start: number; end: number } {
  const view = subscription.view;
  if ('startHz' in view && 'endHz' in view) return { start: view.startHz, end: view.endHz };
  const span = 'spanHz' in view ? view.spanHz : 100_000;
  return { start: TUNE_HZ - span / 2, end: TUNE_HZ + span / 2 };
}

function encodeAutoFloorFrame(noise: number, carrier: number, seq: number): ArrayBuffer {
  if (!autoFloorWindow) throw new Error('autoFloor frame before any subscription');
  const bytes = new Uint8Array(pixelOffset + BIN_COUNT);
  bytes.set(new Uint8Array(template, 0, pixelOffset));
  const view = new DataView(bytes.buffer);
  view.setUint16(6, BIN_COUNT, true);
  view.setUint32(12, seq, true);
  view.setFloat64(24, autoFloorWindow.start, true);
  view.setFloat64(32, autoFloorWindow.end, true);
  view.setFloat64(40, autoFloorWindow.start, true);
  view.setFloat64(48, autoFloorWindow.end, true);
  bytes.fill(noise, pixelOffset);
  bytes.fill(carrier, pixelOffset + AUTO_FLOOR_CARRIER[0], pixelOffset + AUTO_FLOOR_CARRIER[1]);
  return bytes.buffer;
}

if (autoFloorMode) {
  const state = structuredClone(ic7300StateJson) as { main: { freqHz: number } };
  state.main.freqHz = TUNE_HZ;
  radio.current = state as unknown as typeof radio.current;
  setCapabilities(ic7300CapsJson as unknown as Parameters<typeof setCapabilities>[0]);
  const channelSend = WsChannel.prototype.send;
  WsChannel.prototype.send = function (this: WsChannel, cmd: { type: string; name?: string; params?: unknown }) {
    if (cmd.type === 'cmd' && cmd.name) {
      sentCommands.push(`${cmd.name}(${JSON.stringify(cmd.params)})`);
      return false;
    }
    return channelSend.call(this, cmd as Parameters<typeof channelSend>[0]);
  };
}

let deliverWitness: ((buffer: ArrayBuffer) => void) | null = null;
let delivered = false;
let autoFloorSeq = 0;
let markReady: () => void;
const ready = new Promise<void>((resolve) => { markReady = resolve; });
const witnessSource: ScopeMessageSource = {
  subscribe(sink) { deliverWitness = sink; return () => { deliverWitness = null; }; },
  close: () => {},
};
const witnessStream = new ScopeStream(witnessSource,
  context, 'borrowed', undefined, (subscription) => {
    if (!subscription || subscription.maxFps === 0 || subscription.width < 1) return;
    if (autoFloorMode) autoFloorWindow = autoFloorViewWindow(subscription);
    if (delivered) return;
    delivered = true;
    queueMicrotask(() => {
      if (autoFloorMode) {
        deliverWitness?.(encodeAutoFloorFrame(
          AUTO_FLOOR_FIRST.noise, AUTO_FLOOR_FIRST.carrier, ++autoFloorSeq));
      } else deliverWitness?.(encodeWitnessFrame());
      flushSync();
      document.body.dataset.harnessReady = 'true';
      markReady();
    });
  });
if (autoFloorMode) {
  // One animation frame per delivered frame: the panel's render scheduler
  // coalesces wakes inside one frame, so each delivery is awaited out before
  // the next replaces the pending one.
  const nextFrame = () => new Promise<void>((resolve) => { requestAnimationFrame(() => resolve()); });
  window.__spectrumWitness.autoFloor = {
    async deliverFrames(count: number, noise: number, carrier: number): Promise<void> {
      for (let index = 0; index < count; index += 1) {
        await nextFrame();
        deliverWitness?.(encodeAutoFloorFrame(noise, carrier, ++autoFloorSeq));
        flushSync();
      }
      await nextFrame();
    },
  };
}
// The witness owns no socket: the synthetic stream replaces the runtime's
// shared stream and the health getters read as an honest connected viewer.
(runtime.scope as unknown as { stream(): typeof witnessStream }).stream = () => witnessStream;
Object.defineProperty(runtime.scope, 'hardwareScopeConnected', { get: () => true });
Object.defineProperty(runtime.scope, 'hardwareScopeFrameLive', { get: () => true });

const REGION = Object.freeze({
  passband: Object.freeze({
    state: 'current' as const,
    tuple: Object.freeze({
      frequencyHz: TUNE_HZ,
      mode: 'USB',
      widthHz: 2_400,
      shiftHz: 0,
      frameMode: FRAME_MODE,
      startHz: START_HZ,
      endHz: END_HZ,
    }),
  }),
  demanded: true,
  setDemand: () => {},
});

mount(SpectrumPanel, {
  target: document.getElementById('app')!,
  props: {
    scopeRegion: REGION,
    scopeDemanded: true,
    // MOR-4605 mode shows the toolbar's REF cluster (the AUTO key beside
    // REF); the default frozen witness keeps its original props untouched.
    hideScopeControls: !autoFloorMode,
    hideSourceControls: true,
    hideAutoStepToggle: true,
  },
});

flushSync();
await ready;
