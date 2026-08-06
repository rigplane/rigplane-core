/**
 * MOR-1088 — mobile PTT gesture / orientation-swap safety harness.
 *
 * VERIFICATION-ONLY TOOLING (mirrors the MOR-1070/1087 fixture doctrine:
 * outside `src/`, unread by `tsconfig.app.json`/`eslint src/`/`vitest`).
 *
 * WHY THIS EXISTS. The MOR-1070/1085/1087 cockpit harness stubs
 * `$lib/runtime/tx-controller/app-host` entirely (`fixtures/stubs/app-host.ts`
 * just records calls) — it never runs a real lease through `model.ts`'s
 * state machine. MOR-1088's acceptance requires proving PTT safety "through
 * the REAL command/authority path", so this harness instead builds the real
 * `TxController` (`src/lib/runtime/tx-controller/controller.ts` +
 * `model.ts`, byte-identical, unstubbed) directly, with a fixed fake
 * authority/eligibility instead of the WS-backed session projector
 * `tx-controller/app-host.ts` normally uses — the same class of substitution
 * `ReferenceLayout.svelte` already makes for `RadioLayout` (a lighter,
 * real-component stand-in, not a re-simulation).
 *
 * The orientation-swap wiring below (recreate-one-recognizer-per-surface,
 * shared `fabDown`/`fabUp` reading LIVE state) is a hand-mirror of
 * `MobileRadioLayout.svelte`'s own `$effect`, not an import of it. Scenarios
 * below prove the PATTERN is load-bearing where mirrored faithfully, not that
 * MobileRadioLayout.svelte's own copy is byte-identical (verified by direct
 * reading instead). Extraction into a shared mountable function is MOR-1378.
 * `PttFab.svelte` itself is mounted unmodified.
 */
import { mount, unmount } from 'svelte';
import { TxController, type TxControllerDependencies } from '../src/lib/runtime/tx-controller/controller';
import { createPttGesture, type PttGesture } from '../src/components-v2/wiring/tx-ptt-gesture';
import type {
  Eligibility, PttMarker, PttObservation, TxGuard,
} from '../src/lib/runtime/tx-controller/model';
import PttFab from '../src/components-v2/controls/PttFab.svelte';
import { getPttMode, setPttMode } from './ptt-state.svelte';

const AUTH_EPOCH = 1;
let epoch = AUTH_EPOCH; // bumped by window.__ptt.epochBump() (MOR-1088 connection-loss scenario)
const eligibility: Eligibility = {
  catPtt: true, browserTxAudio: true, controlLive: true, permit: 'allowed',
  target: { receiver: 'MAIN', slot: 'A', frequencyHz: 14_200_000 },
};
let obsSeq = 0;
const marker = (): PttMarker =>
  ({ authorityEpoch: epoch, pttObservationSeq: ++obsSeq, pttLastObservedMonotonic: null });
const observation = (): PttObservation =>
  ({ value: false, observed: true, fresh: true, source: 'radio-readback', marker: marker() });

const calls: string[] = [];
let cmdSeq = 0;
const deps: TxControllerDependencies = {
  startAudio: () => Promise.resolve(null),
  stopLocalAudio: () => {},
  restoreMod: () => {},
  commandId: () => `cmd-${++cmdSeq}`,
  schedule: (cb, ms) => setTimeout(cb, ms),
  cancel: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  timeoutMs: { 'audio-start': 5000, 'on-confirmation': 5000, 'off-confirmation': 5000 },
  // Synchronous "sent" report — real command flow, no transport, no wall-clock
  // dependence in the reported barrier (its epoch, not a timestamp, is what
  // downstream transitions read). `eventEpoch` is the LIVE epoch at delivery
  // time (matching `browser-dependencies.ts`'s real `event.eventEpoch` off the
  // WS delivery event, not the lease's originally-stamped epoch — they only
  // coincide absent a reconnect mid-flight, which the epoch-bump scenario
  // deliberately exercises). An 'off' command also fires the matching
  // authoritative "radio confirms PTT is off" observation immediately after —
  // standing in for the real radio-readback poll `browser-dependencies.ts`
  // waits on — so a release actually reaches guard=null in this harness
  // instead of parking forever in `releasing` with no radio to ack it.
  sendPtt: (command, _commandId, _correlation, report) => {
    report({ outcome: 'sent', eventEpoch: epoch, barrier: marker() });
    if (command === 'off') {
      controller.dispatch({
        type: 'authority', epoch, ptt: observation(), eligibility, offCommandId: `cmd-${++cmdSeq}`,
      });
    }
  },
};
const controller = new TxController(
  AUTH_EPOCH, { authorityEpoch: AUTH_EPOCH, pttObservationSeq: 0, pttLastObservedMonotonic: null }, deps,
);
controller.subscribe((s) => setPttMode(s.intent === 'latched' ? 'latched' : s.guard !== null ? 'held' : 'idle'));

// The same 4-verb facade `tx-controller/app-host.ts` exposes over
// `controller.dispatch` — rebuilt here against a fixed eligibility/PTT
// observation instead of `browser-dependencies.ts`'s WS-session projector.
const facade = {
  start: (sourceId: string, leaseId: string) => controller.dispatch(
    { type: 'start', sourceId, leaseId, intent: 'momentary', eligibility, ptt: observation() }),
  setIntent: (sourceId: string, guard: TxGuard) => controller.dispatch(
    { type: 'intent', sourceId, guard, intent: 'latched' }),
  release: (sourceId: string, guard: TxGuard) => controller.dispatch(
    { type: 'release', sourceId, guard, commandId: deps.commandId('off') }),
};

// ── orientation-swap wiring (mirrors MobileRadioLayout.svelte) ──────────
let surface: 'portrait' | 'landscape' = 'portrait';
let seq = 0;
let generation = 0;
let ptt: PttGesture | null = null;
let fabInstance: object | null = null;
const portraitEl = document.getElementById('portrait-slot')!;
const landscapeEl = document.getElementById('landscape-slot')!;

// Stable, shared across every surface generation — reads LIVE `surface`/`ptt`
// at call time, exactly like the real component's `fabDown`/`fabUp`. This is
// the function whose staleness guard MOR-1088 sabotage-tests (see build
// report item 10/11): a press begun on an old surface can still fire this
// after a rotation swapped in a new recognizer.
function fabDown(): void { if (surface === 'portrait') ptt?.down(); }
function fabUp(): void { if (surface === 'portrait') ptt?.up(); }

function applySurface(): void {
  ptt?.destroy();
  const sourceId = `mobile-ptt-${surface}-${++seq}`;
  generation = seq;
  let leaseSeq = 0;
  ptt = createPttGesture(
    { guard: () => controller.snapshot().guard, latched: () => controller.snapshot().intent === 'latched' },
    {
      start: () => { calls.push('tx.start'); facade.start(sourceId, `${sourceId}-${++leaseSeq}`); },
      latch: (guard) => { calls.push('tx.latch'); facade.setIntent(sourceId, guard); },
      release: (guard) => { calls.push('tx.release'); facade.release(sourceId, guard); },
    },
    { schedule: (cb, ms) => setTimeout(cb, ms), cancel: (h) => clearTimeout(h as ReturnType<typeof setTimeout>) },
  );
  if (fabInstance) { unmount(fabInstance); fabInstance = null; }
  portraitEl.replaceChildren();
  landscapeEl.replaceChildren();
  if (surface === 'portrait') {
    fabInstance = mount(PttFab, {
      target: portraitEl,
      props: { get mode() { return getPttMode(); }, txPermit: 'allowed', onDown: fabDown, onUp: fabUp },
    });
  } else {
    // Real component's landscape strip uses direct (undelayed, unguarded)
    // handlers — no 50ms hold-timer race exists on this path, so it needs no
    // liveness re-check the way fabDown/fabUp do.
    const btn = document.createElement('button');
    btn.dataset.testid = 'ls-ptt';
    btn.style.cssText = 'min-width:52px;min-height:44px;';
    btn.textContent = 'PTT';
    btn.addEventListener('pointerdown', () => ptt?.down());
    btn.addEventListener('pointerup', () => ptt?.up());
    btn.addEventListener('pointercancel', () => ptt?.up());
    landscapeEl.appendChild(btn);
  }
}
applySurface();

declare global {
  interface Window {
    __ptt: {
      setSurface: (next: 'portrait' | 'landscape') => void;
      generation: () => number;
      guardId: () => string | null;
      intent: () => 'momentary' | 'latched' | null;
      callCount: () => number;
      callsSince: (n: number) => string[];
      epochBump: () => void;
    };
  }
}
window.__ptt = {
  setSurface: (next) => { surface = next; applySurface(); },
  generation: () => generation,
  guardId: () => controller.snapshot().guard?.leaseId ?? null,
  intent: () => controller.snapshot().intent,
  callCount: () => calls.length,
  callsSince: (n) => calls.slice(n),
  epochBump: () => {
    epoch += 1;
    controller.dispatch({
      type: 'epoch', epoch, baseline: { authorityEpoch: epoch, pttObservationSeq: 0, pttLastObservedMonotonic: null },
      offCommandId: `cmd-${++cmdSeq}`,
    });
  },
};
document.body.dataset.harnessReady = 'true';
