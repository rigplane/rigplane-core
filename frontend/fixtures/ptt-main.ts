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
 * The orientation-swap wiring below is NO LONGER a hand-mirror: MOR-1378
 * extracted `MobileRadioLayout.svelte`'s own `$effect` into
 * `components-v2/wiring/mobile-ptt-surface.ts`, and this harness now imports
 * and drives that REAL, unmodified module — the same code the shipped layout
 * composes. `PttFab.svelte` is likewise mounted unmodified. What remains
 * harness-local is only the host facade (a fixed fake authority/eligibility in
 * place of the WS-backed session projector) and the surface swap trigger the
 * component gets from its `isLandscape` `$derived`.
 */
import { mount, unmount } from 'svelte';
import { TxController, type TxControllerDependencies } from '../src/lib/runtime/tx-controller/controller';
import {
  createMobilePttSurface, type MobilePttBinding,
} from '../src/components-v2/wiring/mobile-ptt-surface';
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

// The same facade `tx-controller/app-host.ts` exposes over
// `controller.dispatch` — rebuilt here against a fixed eligibility/PTT
// observation instead of `browser-dependencies.ts`'s WS-session projector, and
// shaped to `MobilePttHost` so the REAL `createMobilePttSurface` runs on it.
const host = {
  snapshot: () => controller.snapshot(),
  resetFault: () => controller.dispatch({ type: 'reset-fault' }),
  start: (sourceId: string, leaseId: string) => {
    calls.push('tx.start');
    controller.dispatch({ type: 'start', sourceId, leaseId, intent: 'momentary', eligibility, ptt: observation() });
  },
  setIntent: (sourceId: string, guard: TxGuard) => {
    calls.push('tx.latch');
    controller.dispatch({ type: 'intent', sourceId, guard, intent: 'latched' });
  },
  release: (sourceId: string, guard: TxGuard) => {
    calls.push('tx.release');
    controller.dispatch({ type: 'release', sourceId, guard, commandId: deps.commandId('off') });
  },
};

// ── orientation swap — drives the REAL wiring module (MOR-1378) ─────────
let surface: 'portrait' | 'landscape' = 'portrait';
let generation = 0;
let ptt: MobilePttBinding | null = null;
let fabInstance: object | null = null;
const portraitEl = document.getElementById('portrait-slot')!;
const landscapeEl = document.getElementById('landscape-slot')!;

function applySurface(): void {
  ptt?.destroy();
  generation += 1;
  ptt = createMobilePttSurface(
    surface, host,
    { schedule: (cb, ms) => setTimeout(cb, ms), cancel: (h) => clearTimeout(h as ReturnType<typeof setTimeout>) },
    () => surface,
  );
  // Reads the LIVE binding slot at call time, faithfully modelling how Svelte
  // compiles `onDown={fabDown}` in the shipped component: the layout's stable
  // handler forwards to whatever `ptt` currently holds. This is the staleness
  // the MOR-1088 double-flip scenario exercises.
  const fabDown = (): void => ptt?.fabDown();
  const fabUp = (): void => ptt?.fabUp();
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
