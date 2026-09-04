/**
 * MOR-2163 — selector-reachability guardrail for every registered design
 * language.
 *
 * THE DEFECT THIS CATCHES. A design language supplies tokens, renderers and a
 * stylesheet; it may never change the markup (the markup comes from the
 * shared semantic surfaces — `contract.ts`'s own doc comment). Nothing
 * checked that a stylesheet's selectors address markup those surfaces
 * actually emit. `segmentline.css` was authored against a standalone
 * mockup's own class names (`.dl-freq`, `.dl-cell`, `.dl-meter`, …) that the
 * real semantic surfaces never emit — every existing test stayed green
 * because `stylesheet.test.ts` parses CSS as TEXT and a rule aimed at a
 * class nothing renders parses identically to a working one.
 *
 * METHOD. For every language `listDesignLanguageIds()` returns (the
 * declarations barrel, `presentation/languages/declarations`, registers
 * them — imported here transitively through `VfoSurface.svelte` and
 * explicitly below so the registration is not left to that transitive
 * chain), this file mounts the REAL semantic surfaces — `VfoSurface`,
 * `RxTxSurface`, `MetersSurface` directly, plus `SemanticRadioSurfaces`
 * (the only component that emits `.semantic-surfaces`/`.channel-strip`) —
 * across a battery of real states, and asks the REAL DOM whether each
 * selector in that language's own `<id>.css` matches at least one element,
 * via `document.querySelectorAll`. No selector list is hand-maintained on
 * either side: the selectors come from parsing the CSS file named by the
 * registered id, and the markup comes from rendering, never from a fixture
 * of expected class names.
 *
 * WHY NOT `frontend/fixtures/catalog.ts`. Its own harness
 * (`fixtures/harness-state.ts`) documents itself as verification-only,
 * `src/`-blind: "Nothing under `src/` imports this file" — and indeed
 * nothing does (`grep -rn "^import.*catalog'" .` finds only
 * `fixtures/main.ts` and `fixtures/assertions.ts` importing it, both inside
 * `fixtures/`). `vitest.include` is scoped to `src/**` besides. This file
 * instead reuses `src/semantic/fixtures/topologies.ts` — the in-tree
 * fixture module the sibling `design-language-wiring.component.test.ts`
 * already mounts these same three components against — for VFO/meter
 * states, and a small `TxAuthoritySnapshot` vocabulary (below) for RX/TX
 * states, the same shape `design-language-wiring.component.test.ts`'s own
 * `IDLE_RX`/`KEYED` and `stylesheet.test.ts`'s own `SurfaceState` fixtures
 * already use — extended here to the two phases (`releasing`, `failed`)
 * those files did not individually need. The one thing genuinely reused
 * from `frontend/fixtures/catalog.ts`'s IDEA rather than its code is the
 * TX-phase vocabulary itself (rx/pending/keyed/fault) — its exact values
 * are unreachable from `src/` and are reconstructed here as literal members
 * of `TxAuthoritySnapshot['phase']`, not invented.
 *
 * `SemanticRadioSurfaces.svelte` reads `$lib/runtime`, the App TX
 * controller and the MOD-input guard directly; those three seams are
 * mocked (matching `src/components-v2/wiring/__tests__/
 * semantic-rx-tx-wiring.component.test.ts`'s own pattern) — nothing else:
 * `panel-commands.ts`'s real handler factories build fine under jsdom as
 * long as no click ever reaches them, and this file never clicks anything.
 *
 * COMPARING ON THE SELECTOR'S OWN TERMS. Every rule's mandatory
 * `[data-design-language='<id>'][data-design-language]` prefix trivially
 * matches `document.documentElement` once `activate()` sets the attribute
 * there (production's own, only activation mechanism, MOR-1278), so running
 * the selector via `document.querySelectorAll` tests the TAIL, never the
 * prefix — with one exception `reachable()` handles explicitly, not
 * silently: a selector ending in a pseudo-element can never match through
 * `querySelectorAll`, in jsdom or any real browser, because a pseudo-element
 * is not a DOM node — every such selector would report as an orphan
 * regardless of whether its SUBJECT exists. `reachable()` strips a trailing
 * pseudo-element and judges the subject instead; see `TRAILING_PSEUDO_ELEMENT`
 * below for exactly what "trailing pseudo-element" covers (stated precisely
 * there, not repeated here to avoid a second copy that can go stale). The
 * permanent control below (`describe('reachable() judges a trailing
 * pseudo-element…`) is what a mutation cannot pass by treating every
 * pseudo-element tail as reachable rather than genuinely checking the
 * subject.
 *
 * KNOWN LIMITATION — `:focus-visible` IS ONLY TRUSTWORTHY FOR THE TWO
 * ELEMENTS A SCENE ACTUALLY FOCUSES. `SCENES` calls `.focus()` exactly
 * twice: once on `.rx-tx-key`, once on `.rx-tx-unkey`. A `:focus-visible`
 * rule scoped to either is genuinely exercised. A `:focus-visible` rule
 * scoped to anything else this file renders — `.vfo-select`, `.fact-toggle`,
 * `.vfo-tile`, or any future focusable class — is NOT exercised: no scene
 * ever focuses them, so `reachable()` would report such a rule as an orphan
 * whether or not it truly is one. None of the three stylesheets currently
 * writes a `:focus-visible` rule scoped that narrowly (studioline's and
 * fieldline's own rule is the bare `[dl][dl] :focus-visible`, which any
 * focused descendant satisfies — the two scenes above happen to be enough
 * for it, not because it was designed around them), so this is a live gap
 * in what a future rule could safely rely on, not a current false orphan.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import '../../../presentation/languages/declarations';
import { getDesignLanguage, listDesignLanguageIds } from '../../../presentation/languages/contract';
import type { MeterField, RadioViewModel } from '../../../semantic/radio-view-model';
import { topologyFixtures, withMeters } from '../../../semantic/fixtures/topologies';
import VfoSurface from '../../../semantic/VfoSurface.svelte';
import RxTxSurface from '../../../semantic/RxTxSurface.svelte';
import MetersSurface from '../../../semantic/MetersSurface.svelte';

const h = vi.hoisted(() => ({
  state: null as ServerState | null,
  caps: null as Capabilities | null,
  txController: null as ManagedAppTxController | null,
}));
vi.mock('$lib/runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
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
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => h.txController,
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import {
  ManagedAppTxHarness, type ManagedAppTxServerSnapshot,
} from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

// ── CSS parsing: same block/selector-list splitting `stylesheet.test.ts`
// uses in each language's own directory, extended with a line number per
// selector — the orphan report needs "file:line", `stylesheet.test.ts`'s
// cascade ranking never did. ─────────────────────────────────────────────

interface StyleRule { readonly selector: string; readonly line: number }

const cssPathFor = (id: string): string => join('src/presentation/languages', id, `${id}.css`);

function extractSelectors(cssPath: string): readonly StyleRule[] {
  const source = readFileSync(cssPath, 'utf8');
  // Blank comment BODIES (keep every newline) so line numbers computed below
  // stay correct and a selector-shaped string inside a comment is never
  // mistaken for a live rule.
  const css = source.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '));
  const rules: StyleRule[] = [];
  for (const match of css.matchAll(/([^{}]+)\{[^{}]*\}/g)) {
    const selectorList = match[1];
    const listStart = match.index ?? 0;
    let charCursor = 0;
    for (const piece of selectorList.split(',')) {
      const pieceStart = listStart + charCursor;
      charCursor += piece.length + 1; // +1 for the comma this split consumed
      const selector = piece.trim();
      if (!selector) continue;
      const offset = pieceStart + piece.indexOf(selector);
      const line = css.slice(0, offset).split('\n').length;
      rules.push({ selector, line });
    }
  }
  return rules;
}

// `document.querySelectorAll` — under jsdom exactly as in a real browser —
// returns an empty NodeList for ANY selector ending in a pseudo-element,
// regardless of whether the rule's real subject exists: a pseudo-element is
// generated content, never a DOM node `querySelectorAll` can return. Found
// in review: `.rx-tx-surface::before` (subject genuinely live) reported as
// studioline's SOLE orphan under the first version of this check.
//
// COVERAGE, STATED PRECISELY rather than claimed as total (review finding:
// an earlier version of this comment claimed "every pseudo-element form the
// CSS files use or could," which a direct probe of `::marker`, `::selection`,
// `::backdrop`, `::-webkit-scrollbar`, `::file-selector-button`, `::cue`,
// `::part()`, `::slotted()` and `::target-text` showed false — none of those
// nine were in the original keyword list). `TRAILING_PSEUDO_ELEMENT` strips
// by SYNTACTIC FORM, not by an enumerated keyword list, so it is closed
// against the CSS Selectors grammar rather than against today's vocabulary:
//   - any trailing `::<identifier>`, bare or with ONE level of parenthesized
//     arguments (`::part(button)`, `::slotted(.foo)`, `::cue(video)`) — the
//     double colon is EXCLUSIVELY pseudo-element syntax in CSS, so this half
//     covers every pseudo-element that syntax can ever name, including
//     vendor-prefixed and future ones, not just the nine named above;
//   - the four CSS2.1 single-colon legacy aliases — `:before`, `:after`,
//     `:first-line`, `:first-letter` — the ONLY single-colon forms the
//     Selectors spec recognises as pseudo-elements; every other single-colon
//     form (`:hover`, `:focus-visible`, `:not()`, `:has()`, …) is a
//     pseudo-CLASS and must NOT be stripped, and is not (verified below and
//     by the permanent control).
// KNOWN GAP: a functional pseudo-element whose OWN argument contains nested
// parens (e.g. a hypothetical `::slotted(:not(.foo))`) is not handled — the
// one-level `[^()]*` does not balance nested parens. None of the three
// stylesheets uses a functional pseudo-element at all today, so this is a
// documented limit, not a silent one.
const TRAILING_PSEUDO_ELEMENT = /::[-\w]+(?:\([^()]*\))?$|:(?:before|after|first-line|first-letter)$/i;

const subjectOf = (selector: string): string => selector.replace(TRAILING_PSEUDO_ELEMENT, '');

function reachable(selector: string): boolean {
  const subject = subjectOf(selector);
  try {
    return document.querySelectorAll(subject).length > 0;
  } catch (e) {
    throw new Error(`selector "${selector}" (subject "${subject}") failed to parse: ${(e as Error).message}`);
  }
}

// ── Activation: the one mechanism MOR-1278 sanctions. ───────────────────

function activate(id: string): void {
  document.documentElement.dataset.designLanguage = id;
}
function deactivate(): void {
  delete document.documentElement.dataset.designLanguage;
}

// ── A small server-owned RX/TX vocabulary. The canonical harness projects
// these documents through the same managed-state mapper used by the App, so
// every `[data-session=...]` value the CSS can select on is still rendered
// at least once. `PENDING`'s `txRisk: 'uncertain'` gives `[data-rf='uncertain']`
// coverage. `[data-rf='unknown']` coverage comes from `RF_UNKNOWN` below, NOT
// from `FAILED`: `FAILED` also carries `txRisk: 'uncertain'`, and `rfState()`
// checks that branch before it ever falls through to `'unknown'`, so `FAILED`
// yields `rf='uncertain'` too, same as `PENDING`. `FAILED`'s non-null `fault`
// is what makes `.rx-tx-fault` render at all. ─────────────────────────────

const RX_IDLE: ManagedAppTxServerSnapshot = { intent: 'rx', observedPtt: 'off' };
const PENDING: ManagedAppTxServerSnapshot = { intent: 'transmit', observedPtt: 'off' };
const KEYED: ManagedAppTxServerSnapshot = { intent: 'transmit', observedPtt: 'on' };
// Not covered by any `frontend/fixtures/catalog.ts` TX-phase fixture (its
// four `tx-phase-*` fixtures span idle/pending/active/failed only) — the one
// state this file adds beyond that vocabulary, needed because both
// studioline.css and fieldline.css key a rule off `[data-session='releasing']`.
const RELEASING: ManagedAppTxServerSnapshot = {
  intent: 'rx', observedPtt: 'on', releaseRequired: true,
};
const FAILED: ManagedAppTxServerSnapshot = {
  intent: 'rx', observedPtt: 'unknown', releaseRequired: true, lastError: 'audio-failed',
};
const STALE: ManagedAppTxServerSnapshot = { intent: 'rx', observedPtt: 'off', stale: true };
// `rfState()` (rx-tx-surface.ts) returns 'unknown' only when NEITHER the
// transmitting NOR the uncertain branch fires AND the radio is not
// positively observed off — `radioTx: 'unknown', txRisk: 'none'` is the one
// combination that reaches it. Both studioline.css and fieldline.css key a
// rule off `.rx-tx-surface:has([data-rf='unknown'])` as an alternative to
// `[data-rf='uncertain']` (already covered by `PENDING`), so this state is
// needed for that alternative to be exercised at all.
const RF_UNKNOWN: ManagedAppTxServerSnapshot = { intent: 'rx', observedPtt: 'unknown' };

// ── Meter field builders, for the two segmentline-only attribute states
// (`[data-dl-unknown='true']` / `[data-dl-hot='true']`) neither studioline
// nor fieldline reference. `999999` clamps against `meter-utils`'s
// `RAW_SCALE_MAX` (255, uncalibrated fallback) to the scale's own maximum,
// so the fraction is 1.0 — above `segmentline`'s meters-renderer
// `HOT_THRESHOLD` (0.8) regardless of calibration, which this harness never
// loads. ──────────────────────────────────────────────────────────────────

function meterField(value?: number): MeterField {
  return {
    reading: value === undefined ? { status: 'unknown' } : { status: 'known', value },
    availability: { structural: true, operational: true },
    relevant: true,
  };
}

// ── Scenes: one real mount + real state each. Reachability is "matched in
// ANY scene", so each scene only needs to add states the earlier ones
// didn't cover. ───────────────────────────────────────────────────────────

interface Scene { readonly name: string; render(): { cleanup: () => void } }

function mountInto(component: unknown, props: Record<string, unknown>): { cleanup: () => void } {
  const root = document.createElement('div');
  document.body.appendChild(root);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const instance = mount(component as any, { target: root, props });
  flushSync();
  return { cleanup: () => { unmount(instance); root.remove(); } };
}

const MINIMAL_STATE = {
  active: 'MAIN', split: false, dualWatch: false, ptt: false,
  txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
  main: { freqHz: 14195000, mode: 'USB', filter: 1 },
  fieldStatus: {},
} as unknown as ServerState;
const MINIMAL_CAPS = {
  model: 'fixture', scope: false, audio: true, tx: true,
  stateContractVersion: 1, providerGeneration: 1,
  capabilities: ['audio', 'tx'],
  receivers: 1, vfoScheme: 'single',
  freqRanges: [], modes: ['USB'], filters: ['WIDE'], antennas: 1,
  attValues: [], preValues: [], agcModes: [], agcLabels: {},
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [], scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities;

const rxTxScene = (name: string, input: ManagedAppTxServerSnapshot, focusSelector?: string): Scene => ({
  name: `rx-tx-surface (${name})`,
  render() {
    const txHarness = new ManagedAppTxHarness(input);
    const { cleanup } = mountInto(RxTxSurface, {
      view: topologyFixtures['2/main_sub'], tx: txHarness.controller.snapshot(),
      onRequestKey: vi.fn(), onRequestUnkey: vi.fn(),
    });
    if (focusSelector) document.querySelector<HTMLElement>(focusSelector)?.focus();
    return { cleanup: () => { cleanup(); expect(txHarness.trace()).toEqual([]); } };
  },
});

const metersBase = withMeters(topologyFixtures['2/main_sub']);
const metersScene = (name: string, signal: MeterField): Scene => ({
  name: `meters-surface (${name})`,
  render() {
    const view: RadioViewModel = { ...metersBase, meters: { ...metersBase.meters!, signal } };
    return mountInto(MetersSurface, { view });
  },
});

const SCENES: readonly Scene[] = [
  {
    // The only scene that reaches `.semantic-surfaces`/`.channel-strip` —
    // the two selectors both studioline.css and fieldline.css declare that
    // no directly-mounted VfoSurface/RxTxSurface/MetersSurface ever emits.
    name: 'semantic-radio-surfaces (dual strips)',
    render() {
      const txHarness = new ManagedAppTxHarness();
      h.txController = txHarness.controller;
      h.state = MINIMAL_STATE;
      h.caps = MINIMAL_CAPS;
      const { cleanup } = mountInto(SemanticRadioSurfaces, { strips: 'dual' });
      return { cleanup: () => {
        cleanup();
        expect(txHarness.listenerCount()).toBe(0);
        expect(txHarness.trace()).toEqual([]);
        h.state = null; h.caps = null; h.txController = null;
      } };
    },
  },
  {
    // `disabled` forces `.vfo-select:disabled`; `1/ab`'s unknown dualWatch
    // (with `hasDualReceiver` forced true so the toggle renders at all)
    // gives `.fact-toggle:disabled`.
    name: 'vfo-surface (1/ab, disabled, dual-watch unknown)',
    render: () => mountInto(VfoSurface, {
      viewModel: topologyFixtures['1/ab'], hasDualReceiver: true, disabled: true,
    }),
  },
  {
    // `hasTunableFrequency` (VfoSurface.svelte) needs `vfo.isActiveSlot &&
    // vfo.frequencyHz !== null && onTuneFrequency !== undefined`; `2/main_sub`'s
    // M-A tile already satisfies the first two, so supplying `onTuneFrequency`
    // is the one thing missing to mount `FrequencyDisplayInteractive` — and
    // with it, the `.digit`/`.sep` spans that render the actual per-glyph
    // frequency readout. No scene above ever supplies it, so without this one
    // a selector targeting `.digit`/`.sep` would misreport as an orphan
    // regardless of whether it is one.
    name: 'vfo-surface (2/main_sub, tunable — FrequencyDisplayInteractive mounts)',
    render: () => mountInto(VfoSurface, {
      viewModel: topologyFixtures['2/main_sub'], hasDualReceiver: true,
      onTuneFrequency: vi.fn(),
    }),
  },
  {
    // Active tile, enabled select/fact-toggle, active-receiver readout.
    name: 'vfo-surface (2/main_sub, enabled)',
    render: () => mountInto(VfoSurface, {
      viewModel: topologyFixtures['2/main_sub'], hasDualReceiver: true, disabled: false,
    }),
  },
  rxTxScene('idle — receiving, key enabled, key focused', RX_IDLE, '.rx-tx-key'),
  // `.rx-tx-unkey` is never disabled (RxTxSurface.svelte: "Never gated: no
  // `disabled`, no `{#if}`, no guard in the handler"), so it can always take
  // focus. This is the only scene that ever focuses it — needed because
  // `:focus-visible` on the unkey button was previously unexercised (the
  // key-focus scene above never touches it).
  rxTxScene('idle — unkey focused', RX_IDLE, '.rx-tx-unkey'),
  rxTxScene('pending — RF uncertain, key blocked', PENDING),
  rxTxScene('keyed — RF transmitting', KEYED),
  rxTxScene('releasing', RELEASING),
  rxTxScene('failed — fault text rendered', FAILED),
  rxTxScene('stale — key disabled', STALE),
  rxTxScene('RF unknown', RF_UNKNOWN),
  metersScene('signal unknown', meterField()),
  metersScene('signal hot', meterField(999999)),
  {
    name: "root [data-theme='high-contrast']",
    render() {
      document.documentElement.dataset.theme = 'high-contrast';
      const { cleanup } = mountInto(VfoSurface, { viewModel: topologyFixtures['2/main_sub'] });
      return { cleanup: () => { cleanup(); delete document.documentElement.dataset.theme; } };
    },
  },
  {
    name: "root [data-language-mode='light']",
    render() {
      document.documentElement.dataset.languageMode = 'light';
      const { cleanup } = mountInto(VfoSurface, { viewModel: topologyFixtures['2/main_sub'] });
      return { cleanup: () => { cleanup(); delete document.documentElement.dataset.languageMode; } };
    },
  },
];

describe('MOR-2163 — every design-language stylesheet selector reaches real markup', () => {
  it.each(listDesignLanguageIds())('%s: no selector targets markup nothing emits', (id) => {
    expect(getDesignLanguage(id)).toBeDefined();
    const cssPath = cssPathFor(id);
    const rules = extractSelectors(cssPath);
    expect(rules.length).toBeGreaterThan(0);

    activate(id);
    const matched = new Set<string>();
    for (const scene of SCENES) {
      const { cleanup } = scene.render();
      for (const rule of rules) {
        if (!matched.has(rule.selector) && reachable(rule.selector)) matched.add(rule.selector);
      }
      cleanup();
    }
    deactivate();

    const orphans = rules.filter((r) => !matched.has(r.selector));
    const orphanReport = orphans.map((o) => `  ${o.selector}  (${cssPath}:${o.line})`).join('\n');
    expect(orphans, `${orphans.length} orphaned selector(s) in ${cssPath}:\n${orphanReport}`).toEqual([]);
  });
});

// ── PERMANENT CONTROL (review finding, not optional). `reachable()` must
// judge a pseudo-element-tailed selector by its SUBJECT, never by handing the
// whole string to `document.querySelectorAll` — that call always returns an
// empty NodeList for a pseudo-element tail, live subject or not, so a naive
// implementation misreports every such rule as an orphan (this is exactly
// what happened before this fix: `.rx-tx-surface::before`, subject genuinely
// live, was studioline's SOLE false orphan). Both assertions matter equally:
// the live-subject case proves the fix works, and the dead-subject case
// proves it is not a blanket "pseudo-elements are always reachable" escape
// hatch that would hide a real orphan wearing a pseudo-element tail.
//
// NAMED FORMS BELOW ARE A SAMPLE, NOT THE CLOSED SET (review finding: an
// earlier version of this test's own name claimed "every pseudo-element form
// the CSS files use or could," which was false — see the honest coverage
// comment on `TRAILING_PSEUDO_ELEMENT` above for what is actually closed:
// the double-colon form generally, by CSS grammar, plus the four CSS2.1
// single-colon aliases). The list below exercises the two colon styles, a
// hyphenated/vendor-prefixed identifier, and a functional form with an
// argument — one representative of each SHAPE the regex distinguishes, not
// an attempt to enumerate every pseudo-element CSS defines.
describe('reachable() judges a trailing pseudo-element by its subject', () => {
  const LIVE = "[data-design-language='studioline'][data-design-language] .rx-tx-surface";
  const DEAD = "[data-design-language='studioline'][data-design-language] .rx-tx-surface-DOES-NOT-EXIST";
  const SAMPLE_TAILS = [
    ':before', '::before', '::after', '::first-line', '::first-letter', // legacy + double-colon core
    '::placeholder', '::marker', '::selection', '::backdrop', // review-named, no arguments
    '::-webkit-scrollbar', '::file-selector-button', // hyphenated / vendor-prefixed identifiers
    '::cue', '::cue(video)', '::part(button)', '::slotted(.foo)', '::target-text', // functional forms
  ];

  it('reports a live subject reachable, and a dead one not, for every sampled tail', () => {
    activate('studioline');
    const txHarness = new ManagedAppTxHarness(RX_IDLE);
    const { cleanup } = mountInto(RxTxSurface, {
      view: topologyFixtures['2/main_sub'], tx: txHarness.controller.snapshot(),
      onRequestKey: vi.fn(), onRequestUnkey: vi.fn(),
    });
    try {
      for (const tail of SAMPLE_TAILS) {
        expect(reachable(`${LIVE}${tail}`), `${LIVE}${tail} should be reachable`).toBe(true);
        expect(reachable(`${DEAD}${tail}`), `${DEAD}${tail} should NOT be reachable`).toBe(false);
      }

      // ── OVER-REACH GUARD (review finding, blocking). A pseudo-CLASS tail
      // must NEVER be stripped — only a pseudo-ELEMENT may be. Every
      // assertion above tests UNDER-reach (a pseudo-element wrongly left
      // un-stripped); none of them can catch OVER-reach, because
      // `SAMPLE_TAILS` is entirely pseudo-elements. Found in review: widening
      // the single-colon branch by one token —
      // `:(?:before|after|first-line|first-letter)$` to `:[-\w]+$` —
      // silently strips `:hover`, `:focus-visible`, `:disabled`, … too, and
      // NOTHING above reddens. That specific widening would disable the
      // `:focus-visible` verification the two focus scenes above exist to
      // prove: a `:focus-visible` rule on a class no scene ever focuses
      // would report reachable regardless, because the pseudo-class gets
      // stripped before `querySelectorAll` ever sees it.
      //
      // `.rx-tx-surface` exists, but nothing is hovering it in this test
      // environment, so a correctly-scoped `:hover` tail must stay
      // unreachable — this is the exact case the widening above breaks.
      expect(reachable(`${LIVE}:hover`), `${LIVE}:hover should NOT be reachable`).toBe(false);
      // A parenthesised single-colon pseudo-class — the shape most likely to
      // slip past a DIFFERENT careless widening, since the double-colon
      // branch already accepts a parenthesised argument and "make the two
      // branches consistent" is a plausible next bad edit. `.rx-tx-key` is a
      // real descendant of `.rx-tx-surface` (RxTxSurface.svelte's template).
      expect(reachable(`${LIVE}:has(.rx-tx-key)`), `${LIVE}:has(.rx-tx-key) should be reachable`).toBe(true);
      expect(reachable(`${LIVE}:has(.no-such-descendant)`), `${LIVE}:has(.no-such-descendant) should NOT be reachable`).toBe(false);
    } finally {
      cleanup();
      expect(txHarness.trace()).toEqual([]);
      deactivate();
    }
  });
});
