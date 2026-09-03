/**
 * MOR-2153 — the `peer-split` glass CHASSIS, mounted end to end.
 *
 * Scope: this is a CHASSIS test, not a full behavior-pinning suite like
 * `DualReceiverCockpit.component.test.ts`'s. `SemanticRadioSurfaces` and its
 * zone-mount mechanics are already covered exhaustively elsewhere (the
 * `semantic-*-wiring.component.test.ts` family); what this file is the ONLY
 * place that proves is PeerSplitLayout's own additions on top of that wiring:
 * the `ScaledStage` native size, the `.peer-split-glass` chrome class, the
 * wall-clock (not radio state), and that the real DOM elements the dual
 * composition emits today (`.channel-strips`, `.cockpit-global-row`, `.rx-tx-zone`,
 * `.tx-aux-surface`, `.meters-surface`, `.scope-display-surface`) land as
 * DESCENDANTS of the glass.
 *
 * NOT asserted here, at all: that the grid CSS actually applies (`display:
 * grid` on `.semantic-surfaces`, row/column placement). Measured, not
 * assumed — this Vitest/jsdom config injects no component `<style>` during a
 * mount (`document.querySelectorAll('style').length` is 0 after `render()`),
 * so every element's `getComputedStyle` reads the UA default regardless of
 * what any `<style>` block declares; a computed-style assertion here would
 * be permanently unfalsifiable. The grid actually applying is verified in a
 * real browser via the `fixtures/` harness; see the MOR-2153 build report
 * for the exact command and what it showed.
 *
 * The mock boilerplate below is copied from `semantic-rx-tx-wiring
 * .component.test.ts` (the pattern `SemanticRadioSurfaces.svelte`'s own file
 * header names as the minimal precedent). MEASURED, not estimated: 47 files
 * under `src/` match `grep -rl "tx-controller/app-host" --include="*.test.ts"
 * src` (46 before this file existed) — spanning both `*.component.test.ts`
 * and `*.isolated.test.ts`, not only the former; of the 41 total
 * `*.component.test.ts` files (`find src -name '*.component.test.ts'`), 27
 * carry this mock shape. No shared helper wraps it: `find src -iname
 * "*test-helper*" -o -iname "*test-util*"` returns nothing.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

type Snapshot = {
  phase: string; intent: string | null; guard: { leaseId: string } | null;
  radioTx: string; txRisk: string; mayOwnKey: boolean; fault: string | null;
  pendingOff: { commandId: string } | null; modRestorePending: boolean;
  cleanupGuard: { leaseId: string } | null;
};

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  snapshot: null as unknown,
  listeners: new Set<(next: unknown) => void>(),
  start: vi.fn(),
  release: vi.fn(),
  setIntent: vi.fn(),
  resetFault: vi.fn(),
  noop: vi.fn(),
  modInputGuard: { visible: false, sourceLabel: null } as { visible: boolean; sourceLabel: string | null },
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
    get connectionAudio() { return false; },
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get radioPowerOn() { return null; },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => h.snapshot,
    subscribe: (listener: (next: unknown) => void) => {
      h.listeners.add(listener);
      return () => h.listeners.delete(listener);
    },
    start: h.start,
    setIntent: h.setIntent,
    release: h.release,
    resetFault: h.resetFault,
  }),
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => h.modInputGuard,
  getModInputTxGuardHandlers: () => ({ onSetLan: h.noop, onDismiss: h.noop }),
}));
vi.mock('$lib/runtime/commands/panel-commands', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/commands/panel-commands')>();
  return {
    ...actual,
    makeVfoHandlers: () => ({ onVfoSelect: h.noop, onSplitToggle: h.noop, onDualWatchToggle: h.noop }),
    makeVoxHandlers: () => ({
      onVoxToggle: h.noop, onVoxGainChange: h.noop, onAntiVoxGainChange: h.noop, onVoxDelayChange: h.noop,
    }),
    makeTxHandlers: () => ({
      onRfPowerChange: h.noop, onMicGainChange: h.noop, onAtuToggle: h.noop, onAtuTune: h.noop,
      onVoxToggle: h.noop, onCompToggle: h.noop, onCompLevelChange: h.noop, onMonToggle: h.noop,
      onMonLevelChange: h.noop, onDriveGainChange: h.noop,
    }),
    makeRxAudioHandlers: () => ({ onMonitorModeChange: h.noop, onAfLevelChange: h.noop }),
    makeCwPanelHandlers: () => ({
      onKeySpeedChange: h.noop, onCwPitchChange: h.noop, onBreakInDelayChange: h.noop,
      onBreakInModeChange: h.noop, onApfChange: h.noop, onTwinPeakToggle: h.noop, onReversePaddleToggle: h.noop,
    }),
    makeAudioRoutingHandlers: () => ({ onFocusChange: h.noop, onSplitStereoChange: h.noop }),
    makeModeHandlers: () => ({ onModInputChange: h.noop, onModeChange: h.noop, onDataModeChange: h.noop }),
    makeFilterHandlers: () => ({
      onFilterChange: h.noop, onFilterWidthChange: h.noop, onFilterShapeChange: h.noop,
      onIfShiftChange: h.noop, onPbtInnerChange: h.noop, onPbtOuterChange: h.noop,
    }),
    makeDspHandlers: () => ({
      onNrModeChange: h.noop, onNrLevelChange: h.noop, onNbToggle: h.noop, onNbLevelChange: h.noop,
      onNbDepthChange: h.noop, onNbWidthChange: h.noop, onNotchModeChange: h.noop, onNotchFreqChange: h.noop,
      onManualNotchWidthChange: h.noop, onAgcTimeChange: h.noop,
    }),
    makeAgcHandlers: () => ({ onAgcModeChange: h.noop }),
    makeRfFrontEndHandlers: () => ({
      onAttChange: h.noop, onPreChange: h.noop, onRfGainChange: h.noop,
      onSquelchChange: h.noop, onDigiSelToggle: h.noop, onIpPlusToggle: h.noop,
    }),
    makeBandHandlers: () => ({ onBandSelect: h.noop }),
    makeAntennaHandlers: () => ({ onSelectAnt1: h.noop, onSelectAnt2: h.noop, onToggleRxAnt: h.noop }),
    makeRitXitHandlers: () => ({
      onRitToggle: h.noop, onXitToggle: h.noop, onRitOffsetChange: h.noop, onXitOffsetChange: h.noop, onClear: h.noop,
    }),
    makeScanHandlers: () => ({
      onScanStart: h.noop, onScanStop: h.noop, onDfSpanChange: h.noop, onResumeChange: h.noop,
    }),
    makeScopeControlsHandlers: () => ({
      onModeChange: h.noop, onEdgeChange: h.noop, onSpanChange: h.noop, onSpeedChange: h.noop,
      onHoldChange: h.noop, onRefChange: h.noop, onDualChange: h.noop, onReceiverChange: h.noop,
      onDuringTxChange: h.noop, onCenterTypeChange: h.noop, onVbwChange: h.noop, onRbwChange: h.noop,
    }),
  };
});

const { default: PeerSplitLayout } = await import('../PeerSplitLayout.svelte');

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null, pendingOff: null, modRestorePending: false, cleanupGuard: null,
};

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };

/** 2/ab_shared — one of `peer-split`'s two declared compatible topologies
 *  (`presentation/layouts/segmentline-declarations.ts`), also the FTX-1's
 *  real topology per the accepted spec §2. */
function abSharedState(): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget',
    'main.freqHz', 'main.mode', 'main.filter', 'sub.freqHz', 'sub.mode', 'sub.filter',
    'powerMeter', 'swrMeter', 'main.sMeter', 'sub.sMeter'];
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14250000 },
    main: { freqHz: 14250000, mode: 'USB', filter: 1, sMeter: -12 },
    sub: { freqHz: 146520000, mode: 'FM', filter: 1, sMeter: -30 },
    powerMeter: 0, swrMeter: 10,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

/** Caps carrying enough evidence to populate every zone this chassis can
 *  currently mount: `meters`/`scope`/the five `txAux` evidence tags. */
const liveCaps = (): Capabilities => ({
  model: 'fixture', scope: true, audio: true, tx: true,
  stateContractVersion: 1, providerGeneration: 1,
  capabilities: [
    'audio', 'tx', 'dual_rx', 'meters', 'scope', 'split', 'dual_watch',
    'tuner', 'vox', 'compressor', 'monitor', 'drive_gain',
  ],
  receivers: 2, vfoScheme: 'ab_shared',
  freqRanges: [], modes: ['USB', 'FM'], filters: ['FIL1'],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;

// MOR-2253 slice 1: canvas size is now a required prop from the shell
// (`components-v2/layout/LcdLayout.svelte`), not a component-local constant —
// 1280x540 hardcoded here matches this file's own pinned assertion below
// (`.scaled-stage`'s inline `1280px`/`540px`), same as `../../../presentation/
// layouts/__tests__/manifest-shape.test.ts` hardcodes the same numbers for
// its own, unrelated fixture reasons (a `__tests__` file, excluded from the
// production "declared once" scan in `presentation/groups/__tests__/
// contract.test.ts`).
function render(): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(PeerSplitLayout, { target, props: { canvasW: 1280, canvasH: 540, minScale: 0.5 } });
  flushSync();
}

/** MOR-2153 review: the local half of the clock reads the process's own
 *  timezone (`Date.prototype.getHours`/`getMinutes`), a review round found
 *  "machine-dependent and unasserted". `process.env.TZ = 'UTC'` in
 *  `beforeEach` was tried first and MEASURED not to work under
 *  `vi.useFakeTimers()` in this environment: the fixed instant below
 *  (14:32 UTC) still read back as this machine's real local offset (10:32
 *  in the environment this was verified in), not UTC — so it was reverted
 *  rather than kept as a comment describing behavior that does not hold.
 *  `expectedLocal()` below computes the SAME `getHours`/`getMinutes` pair
 *  the component's `clockLabel` does, independently, against whatever this
 *  process's real local offset actually is — deterministic and correct on
 *  every machine without needing to pin or predict that offset. */
const FROZEN_INSTANT = new Date('2026-09-01T14:32:00Z');
function expectedLocal(): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${pad(FROZEN_INSTANT.getHours())}:${pad(FROZEN_INSTANT.getMinutes())}`;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(FROZEN_INSTANT);
  h.state = abSharedState();
  h.caps = liveCaps();
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  h.start.mockReset();
  h.release.mockReset();
  h.setIntent.mockReset();
  h.resetFault.mockReset();
  h.noop.mockReset();
  h.modInputGuard = { visible: false, sourceLabel: null };
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
  vi.useRealTimers();
});

describe('the peer-split chassis mounts', () => {
  it('renders the glass chrome and the dual composition inside it, without throwing', () => {
    expect(() => render()).not.toThrow();
    const glass = q('[data-testid="peer-split-glass"]');
    expect(glass).not.toBeNull();
    expect(glass!.classList.contains('peer-split-glass')).toBe(true);
  });

  // MUTATION TARGET: swapping ScaledStage's nativeW/nativeH kills this — the
  // stage renders its content box at the declared size regardless of the
  // holder's actual measured box (jsdom reports a 0x0 holder, so `scale`
  // stays 1 and the native size is what shows up verbatim).
  it('declares the 1280x540 native stage the segmentline manifest also declares', () => {
    render();
    const stage = q<HTMLDivElement>('.scaled-stage');
    expect(stage).not.toBeNull();
    expect(stage!.style.width).toBe('1280px');
    expect(stage!.style.height).toBe('540px');
  });

  it('places the real dual-composition zones inside the glass, not beside it', () => {
    render();
    const glass = q('[data-testid="peer-split-glass"]');
    expect(glass!.querySelector('.channel-strips')).not.toBeNull();
    expect(glass!.querySelector('.cockpit-global-row')).not.toBeNull();
    expect(glass!.querySelector('.rx-tx-zone')).not.toBeNull();
    // Bare optional surfaces (no declared zone owns them yet — MOR-2151(cont.)):
    expect(glass!.querySelector('.tx-aux-surface')).not.toBeNull();
    expect(glass!.querySelector('.meters-surface')).not.toBeNull();
    expect(glass!.querySelector('.scope-display-surface')).not.toBeNull();
  });

  // NOT ASSERTED HERE: that `.semantic-surfaces` actually receives
  // `display: grid`. Measured, not assumed — this Vitest/jsdom config
  // injects NO component `<style>` at all during a component-mount test
  // (`document.querySelectorAll('style').length` is 0 and
  // `document.styleSheets.length` is 0 after `render()`; every element's
  // `getComputedStyle().display` falls back to the UA default ('block')
  // regardless of what any `<style>` block declares, scoped or `:global`).
  // `expect(getComputedStyle(surfaces).display).toBe('grid')` would
  // therefore be permanently RED here — 'block' never equals 'grid' — not
  // unfalsifiably green; still worth removing, since a pin that can only
  // ever fail is exactly as useless as one that can only ever pass, and
  // either shape reads as coverage that is not there. The grid actually
  // applying is verified in a real browser: `npx vite --config
  // vite.fixtures.config.ts`, fixture `peer-split-chassis`,
  // `getComputedStyle(document.querySelector('.semantic-surfaces')).display`.

  // MOR-2153: wall-clock time is not radio state — nothing to mock beyond
  // the system clock itself (`vi.setSystemTime(FROZEN_INSTANT)` above).
  //
  // MUTATION TARGETS (both found by a MOR-2153 review round, neither
  // caught by the single combined-text assertion this replaces):
  //  - swapping the `utc`/`local` fields `clockLabel` returns: with
  //    `toContain('14:32')` on the combined `.peer-split-clock` text, a
  //    swap was invisible because both halves happen to read '14:32'
  //    digits either way at the frozen instant — only the trailing 'Z'
  //    distinguishes them, which the two separate assertions below check
  //    per span (`utc` exact 'HH:MMZ'; `local` against `expectedLocal()`,
  //    computed independently the same way `clockLabel` does, so it stays
  //    correct — and the swap stays caught — on any machine's timezone).
  //  - moving the clock markup to a sibling of `<ScaledStage>` (breaking
  //    Lesson 3 — "the clock lives inside the scaled stage"): querying
  //    from `target` (the whole mount point) finds it regardless of where
  //    in the tree it landed. Queried from `glass` here instead, so the
  //    clock must be a DESCENDANT of `.peer-split-glass` — inside the
  //    stage, per Lesson 3 — to be found at all.
  it('renders a wall-clock readout, inside the glass, not derived from any radio field', () => {
    render();
    const glass = q('[data-testid="peer-split-glass"]');
    expect(glass).not.toBeNull();
    const clock = glass!.querySelector('[data-testid="peer-split-clock"]');
    expect(clock).not.toBeNull();
    const utc = clock!.querySelector('[data-testid="peer-split-clock-utc"]');
    const local = clock!.querySelector('[data-testid="peer-split-clock-local"]');
    expect(utc!.textContent).toBe('14:32Z');
    expect(local!.textContent).toBe(expectedLocal());
  });
});

describe('the glass bezel keeps its own CSS after the chassis class it used to wear died (#2968)', () => {
  const source = readFileSync('src/skins/segmentline/PeerSplitLayout.svelte', 'utf8');
  const styleBlock = source.match(/<style>([\s\S]*?)<\/style>/)?.[1] ?? '';
  // Comments name the very rule this pin is about, so they are stripped
  // first — same reason `stylesheet.test.ts` strips them from `segmentline
  // .css` before parsing.
  const css = styleBlock.replace(/\/\*[\s\S]*?\*\//g, '');

  /** Exact-selector match against a `{ declarations }` block, mirroring
   *  `stylesheet.test.ts`'s `parseRules`/`findExact` at the scale this file
   *  needs (one selector). Throws — rather than returning `undefined` for an
   *  `it()` block to silently pass past — when the `<style>` block is
   *  missing or the selector renamed, so this test cannot go quietly green
   *  as coverage that stopped existing. */
  function declarationsFor(selector: string): Record<string, string> {
    for (const [, selectorList, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      for (const candidate of selectorList.split(',')) {
        if (candidate.trim() !== selector) continue;
        const declarations: Record<string, string> = {};
        for (const declaration of body.split(';')) {
          const colon = declaration.indexOf(':');
          if (colon > 0) declarations[declaration.slice(0, colon).trim()] = declaration.slice(colon + 1).trim();
        }
        return declarations;
      }
    }
    throw new Error(`no rule for selector "${selector}" in PeerSplitLayout.svelte's <style> block`);
  }

  it('declares the bezel geometry #2968 orphaned, as its own rule rather than a design-language one', () => {
    const glass = declarationsFor('.peer-split-glass');
    expect(glass.position).toBe('relative');
    expect(glass.overflow).toBe('hidden');
    expect(glass['box-sizing']).toBe('border-box');
    expect(glass.padding).toBe('14px');
    expect(glass.border).toBe('2px solid var(--dl-segmentline-bezel-edge, #8a7020)');
    expect(glass['border-radius']).toBe('10px');
    expect(glass.background).toBe('var(--dl-segmentline-glass, #c8a030)');
  });

  it('keeps the meters/scope row from collapsing below its 72px floor', () => {
    const grid = declarationsFor('.peer-split-glass :global(.semantic-surfaces.semantic-surfaces)');
    expect(grid['grid-template-rows']).toContain('minmax(72px, 1fr)');
  });

  // The receiver strips must occupy THIS chassis's own two column
  // tracks (subgrid), not a separately-computed nested grid that only looks
  // aligned at an even split — MEASURED live (`vite.fixtures.config.ts`,
  // `peer-split-chassis` fixture): forcing the parent's `grid-template-
  // columns` to `2fr 1fr` moved both `.channel-strip` elements to the exact
  // same widths as `.rx-tx-zone`/`.tx-aux-surface` (826.66px / 413.34px);
  // reverting `.channel-strips`'s own `grid-template-columns` to the
  // wiring's un-overridden `repeat(auto-fit, minmax(0, 1fr))` kept both
  // strips at an even 50/50 split regardless, which is exactly the
  // collapse-back this pin catches.
  it('subgrids the channel strips onto the chassis columns instead of a separately-computed split', () => {
    const strips = declarationsFor('.peer-split-glass :global(.channel-strips.channel-strips)');
    expect(strips['grid-template-columns']).toBe('subgrid');
  });

  // This file's `:global(.scaled-stage-holder)` rule gives the holder
  // `flex: 1` so it claims the flex line instead of content-sizing to zero
  // (see that rule's own comment). `flex` applies to a flex ITEM only, so
  // the rule is load-bearing only while the holder is a DIRECT child of
  // `.peer-split-holder`. The selector is a descendant combinator: it would
  // keep matching, while silently doing nothing, if `ScaledStage` ever grew
  // an element ABOVE its holder. MOR-2270 added a wrapper INSIDE the
  // holder, which leaves this intact — this pin is what makes the next
  // restructuring say so instead of collapsing the glass quietly.
  it('keeps ScaledStage\'s holder a direct child of .peer-split-holder', () => {
    render();
    const outer = q<HTMLElement>('.peer-split-holder');
    const holder = q<HTMLElement>('.scaled-stage-holder');
    expect(outer).not.toBeNull();
    expect(holder).not.toBeNull();
    expect(holder!.parentElement).toBe(outer);
    expect(declarationsFor('.peer-split-holder :global(.scaled-stage-holder)').flex).toBe('1');
  });
});

describe('the glass forwards the shell-resolved scale floor to its stage (MOR-2259)', () => {
  // A SOURCE pin, not a behavioural one: jsdom computes no layout, so
  // `ScaledStage`'s `measure()` sees a 0x0 holder and returns before the
  // floor can influence anything observable here (the same limit this
  // file's header and `../../../primitives/stage/__tests__/
  // ScaledStage.isolated.test.ts`'s `flex-shrink` pin both record). The
  // floor's arithmetic is pinned in `stage-scale.test.ts`; what is pinned
  // here is the one link those tests cannot see — that this component
  // actually hands its `minScale` prop on rather than accepting it and
  // dropping it.
  const source = readFileSync('src/skins/segmentline/PeerSplitLayout.svelte', 'utf8');
  const markup = source.replace(/<script[\s\S]*?<\/script>/, '').replace(/<style>[\s\S]*?<\/style>/, '');

  it('passes minScale to ScaledStage in the markup', () => {
    const tag = markup.match(/<ScaledStage[^>]*>/)?.[0];
    expect(tag, 'no <ScaledStage ...> tag found in PeerSplitLayout.svelte markup').toBeDefined();
    expect(tag).toMatch(/\{minScale\}|minScale=/);
  });
});
