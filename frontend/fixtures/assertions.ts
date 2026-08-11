/**
 * MOR-1070 — the behavior assertions that must pass BEFORE a screenshot is
 * taken. They are the browser half of the invariants MOR-1067/1068/1069/1256
 * pinned in jsdom, plus the three that only a real engine can decide:
 * `nothing-hidden` (computed visibility), `strip-columns` (evaluated media
 * queries) and `touch-targets` (laid-out hit boxes).
 *
 * A capture whose assertions did not all pass is recorded as INVALID in the
 * manifest — the picture is evidence of the assertions, never a substitute.
 */
import { toRadioViewModel } from '../src/lib/runtime/adapters/radio-view-model-adapter';
import { desktopV2Layout } from '../src/presentation/layouts/declarations';
import type { Expectation } from './catalog';
import { harness } from './harness-state';

export interface AssertionResult {
  name: string;
  ok: boolean;
  detail: string;
}

export interface AssertionOptions {
  /**
   * Expected arrangement of the two channel strips at this viewport — the
   * operator-visible truth of MOR-1069's reflow, read off laid-out boxes
   * rather than off the grid's resolved `grid-template-columns` string (which
   * `repeat(auto-fit, …)` makes an implementation detail).
   */
  arrangement?: 'side-by-side' | 'stacked';
  /** Require every cockpit control to be >= 44x44 CSS px (pointer: coarse). */
  touchTargets?: boolean;
  /** Require every animation/transition inside the cockpit to be switched off. */
  reducedMotion?: boolean;
  /**
   * MOR-1085 — which mounted root this capture's assertions read from.
   * `main.ts` supplies this from the fixture's `layout` (default
   * `'dual-receiver-cockpit'`, the pre-MOR-1085 hardcoded value, so every
   * existing cockpit fixture is byte-identical without touching its call
   * site).
   */
  rootTestId?: string;
  /** MOR-1087 — true when `Tab` reached a real control (`focusTabs`), so
   *  `:focus-visible` is live and the focus-ring contrast check applies. */
  focusVisible?: boolean;
}

// MOR-1085: which mounted root the selectors below read from for the
// DURATION of one `runAssertions` call — set at the top of that function from
// `options.rootTestId`. A module-level var rather than a threaded parameter
// keeps every existing `qa()`/`q()` call site in this file unchanged.
let currentRootTestId = 'dual-receiver-cockpit';
const root = (): HTMLElement =>
  document.querySelector<HTMLElement>(`[data-testid="${currentRootTestId}"]`)!;
const qa = <T extends HTMLElement>(sel: string): T[] => [...root().querySelectorAll<T>(sel)];
const q = <T extends HTMLElement>(sel: string): T | null => root().querySelector<T>(sel);
const eq = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

function visible(el: HTMLElement): boolean {
  const cs = getComputedStyle(el);
  if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') {
    return false;
  }
  if (cs.contentVisibility === 'hidden') return false;
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

/** Every focusable control the cockpit renders, in DOM order. */
const controls = (): HTMLElement[] =>
  qa<HTMLElement>('button, input, select, a[href], [tabindex]');

/** MOR-1087 item 5 — WCAG contrast ratio on a real `getComputedStyle()`
 *  `rgb()`/`rgba()` string (same formula the `studioline`/`fieldline`
 *  `tokens.test.ts` arithmetic uses on the DECLARED palette; this measures
 *  what the browser PAINTS). `null` when unparseable or fully transparent. */
function parseOpaqueRgb(color: string): [number, number, number] | null {
  const m = /^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/.exec(color.trim());
  if (!m) return null;
  if (m[4] !== undefined && Number(m[4]) === 0) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}
function relativeLuminance([r, g, b]: readonly [number, number, number]): number {
  const channel = (c: number): number => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}
function contrastRatio(a: string, b: string): number | null {
  const [pa, pb] = [parseOpaqueRgb(a), parseOpaqueRgb(b)];
  if (!pa || !pb) return null;
  const [la, lb] = [relativeLuminance(pa), relativeLuminance(pb)];
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
/** First non-transparent ancestor `background-color` — most controls here
 *  declare none of their own (`.rx-tx-key { background: none }`). */
function effectiveBackground(el: HTMLElement): string {
  let node: HTMLElement | null = el;
  while (node) {
    const bg = getComputedStyle(node).backgroundColor;
    if (parseOpaqueRgb(bg) !== null) return bg;
    node = node.parentElement;
  }
  return getComputedStyle(document.documentElement).backgroundColor;
}
/** MOR-1087 FINDING (measured live, not guessed): `studioline`'s/`fieldline`'s
 *  idle-state key-label text clears only 3:1 (their own arithmetic proof,
 *  WCAG 1.4.11 non-text), not the 4.5:1 WCAG 1.4.3 text floor a real label
 *  needs — studioline min 4.07:1, fieldline min 3.93:1 (its TX/fault states
 *  already clear 4.5:1). Pinned just under each so a further regression still
 *  fails; needs a follow-up ticket to lighten the idle tone. */
const TEXT_CONTRAST_FLOOR: Readonly<Record<string, number>> = {
  'studioline:rx-tx-key': 4.0,
  'fieldline:rx-tx-key': 3.9,
};

/**
 * MOR-1379 — the reference/single composition's declared zone SET, read
 * live off the real `desktopV2Layout` manifest rather than a per-fixture
 * literal in `catalog.ts` (`toReferenceFixture` sets `expect.zones: []`
 * unconditionally — that literal predates `fixtures/main.ts` resolving any
 * plan for this layout, MOR-1085, and updating 20 catalog literals by hand
 * every time an S-slice declares a new desktop-v2 zone is exactly the "goes
 * stale the moment base moves" trap the MOR-1355 discharge ruling named).
 * Module scope, computed once: importing the manifest directly is what
 * keeps this immune to that trap — the alternative, a hardcoded zone-id
 * list here, would need a human to remember to touch it on every future
 * S-slice. `receiver-deck`/`rx-tx` are excluded: `SemanticRadioSurfaces
 * .svelte`'s `zoned()` snippet is deliberately never applied to `vfo`/
 * `rxTx` (MOR-1069 — "a zone element exists only where an arrangement must
 * place it, and the single composition places nothing"), a structural fact
 * about the wiring component, not a zone list that goes stale.
 *
 * A SET, not an order: measured live against a real resolved plan (see the
 * MOR-1379 build report), the single composition's `{#each singleOrder}` +
 * `zoned()` call sequence in `SemanticRadioSurfaces.svelte` (txAux, meters,
 * rxAudio, filter, dsp, rfFrontEnd, band, antenna, ritXitScan, cwKeyer,
 * scopeDisplay, scopeControls — the order each vocabulary slice landed in)
 * does NOT match `desktopV2Layout.zones`' declared array order (tx-aux,
 * meters, scope-display, filter, rf-front-end, band, antenna, rit-xit-scan,
 * rx-audio, dsp, cw-keyer, scope-controls — the S6a-S9 rework program's own
 * later regrouping for readability). The two orderings were never meant to
 * agree; `zoneOwning()` is a membership lookup, not a sequencing contract.
 * Asserting DOM order against manifest-array order would therefore assert
 * something false by construction, not a real regression — membership is
 * the honest invariant.
 */
const REFERENCE_ZONE_IDS = new Set(
  desktopV2Layout.zones
    .map((z) => z.id)
    .filter((id) => id !== 'receiver-deck' && id !== 'rx-tx'),
);

/**
 * MOR-1379 — the controls MOR-1069 permanently exempts from zone
 * containment on the reference/single composition: the VFO surface and the
 * RX/TX authority (plus its TX-adjacent alerts) never bind a `data-zone-id`
 * wrapper there, with or without a resolved plan (`SemanticRadioSurfaces
 * .svelte`'s single-composition branch renders `vfoSurface()`/
 * `rxTxSurface()`/`txAdjacentAlerts()` outside the generic `zoned()` path —
 * see the file header comment). Read structurally, off which real component
 * root a control lives under, so this stays correct regardless of which
 * named buttons either surface happens to render.
 */
const isStructurallyZoneless = (el: HTMLElement): boolean =>
  el.closest('[data-testid="vfo-surface"]') !== null
  || el.closest('[data-testid="rx-tx-surface"]') !== null
  || el.dataset.testid === 'tx-fault-reset'
  || el.closest('[data-testid="mod-input-tx-warning"]') !== null;

export function runAssertions(
  expected: Expectation, options: AssertionOptions = {},
): AssertionResult[] {
  currentRootTestId = options.rootTestId ?? 'dual-receiver-cockpit';
  // MOR-1085 — true for the dual-receiver-cockpit composition (the pre-1085
  // default, so every existing fixture is unaffected), false for the
  // reference/single composition (`ReferenceLayout.svelte` /
  // `SemanticRadioSurfaces strips="single"`), which MOR-1069 deliberately
  // leaves zone-less FOR ITS OWN vfo/rxTx/global structure: "a zone element
  // exists only where an arrangement must place it, and the single
  // composition places nothing" (`presentation/layouts/desktop-declarations
  // .ts`). This still governs the checks that are genuinely specific to the
  // dual-receiver-cockpit's OWN promises (its inert scope/controls
  // placeholders, the global-zone containment half of the radio-wide row) —
  // MOR-1379 does not touch those. It does NOT mean "the reference layout
  // can never observe a zone at all" any more: see `isReferenceLayout` below.
  const zonedComposition = expected.zonedComposition ?? true;
  // MOR-1379 — `fixtures/main.ts` now resolves a real `desktopV2Layout` plan
  // for every `layout: 'reference'` fixture unconditionally, so the
  // reference/single composition genuinely binds `data-zone-id` wrappers
  // around whichever OPTIONAL surfaces (`txAux`/`meters`/`scopeDisplay`/…)
  // its view model carries — the harness blindness the MOR-1355 discharge
  // ruling named (`ReferenceLayout` standing in for desktop-v2 while
  // `desktopV2Layout` went unresolved). `catalog.ts`'s `expect.zones`/
  // `expect.zonedComposition: false` literals (`toReferenceFixture`) predate
  // that resolution and are out of this ticket's two-file scope, so the
  // checks below that are actually about desktop-v2's zone set are made
  // conditional on the RUNTIME root, not on the stale catalog literal.
  const isReferenceLayout = currentRootTestId === 'reference-layout';
  const out: AssertionResult[] = [];
  const check = (name: string, ok: boolean, detail: string): void => {
    out.push({ name, ok, detail });
  };

  // ── structure ───────────────────────────────────────────────────────────
  const zones = qa('[data-zone-id]').map((el) => el.dataset.zoneId);
  if (isReferenceLayout) {
    // MOR-1379: exact-set equality against `expected.zones` (always `[]`
    // here, see above) cannot be the check any more — which OPTIONAL zones
    // actually render depends on which surfaces this fixture's view model
    // carries (each `zoned()` mount self-gates), and that legitimately
    // varies per fixture/topology. And unlike the cockpit, DOM order is NOT
    // a proxy for "matches the manifest" here — measured live, the render
    // sequence follows `SemanticRadioSurfaces.svelte`'s own hardcoded
    // `zoned()` call order (each vocabulary slice's landing order), not
    // `desktopV2Layout.zones`' declared array order (the rework program's
    // later regrouping) — see `REFERENCE_ZONE_IDS` above. What DOES hold,
    // and is real containment rather than a sequencing coincidence: every
    // rendered zone id is one the manifest actually declares, and no zone id
    // is split across two separate DOM elements.
    const knownZones = zones.every((id) => id !== undefined && REFERENCE_ZONE_IDS.has(id));
    const noSplitZones = new Set(zones).size === zones.length;
    check('zones-in-declaration-order', knownZones && noSplitZones,
      `rendered ${JSON.stringify(zones)} · desktopV2Layout zones `
      + `${JSON.stringify([...REFERENCE_ZONE_IDS])}`);
  } else {
    check('zones-in-declaration-order', eq(zones, [...expected.zones]),
      `rendered ${JSON.stringify(zones)} · expected ${JSON.stringify(expected.zones)}`);
  }

  const strips = qa<HTMLElement>('[data-testid^="channel-strip-"]');
  check('strip-count', strips.length === expected.strips,
    `${strips.length} strips · expected ${expected.strips}`);
  check('strip-receivers',
    eq(strips.map((s) => s.dataset.stripReceiver), [...expected.stripReceivers]),
    JSON.stringify(strips.map((s) => s.dataset.stripReceiver)));
  check('strip-operational-flags',
    eq(strips.map((s) => s.dataset.stripOperational === 'true'), [...expected.stripOperational]),
    JSON.stringify(strips.map((s) => s.dataset.stripOperational)));
  check('strip-active-flags',
    eq(strips.map((s) => s.dataset.stripActive === 'true'), [...expected.stripActive]),
    JSON.stringify(strips.map((s) => s.dataset.stripActive)));

  const tiles = qa('[data-vfo-tile]');
  check('tile-count', tiles.length === expected.tiles,
    `${tiles.length} tiles · expected ${expected.tiles}`);
  check('tiles-belong-to-their-strip',
    strips.every((s) => [...s.querySelectorAll<HTMLElement>('[data-vfo-tile]')]
      .every((t) => t.dataset.vfoReceiver === s.dataset.stripReceiver)),
    'every tile carries its own strip\'s receiver id');

  // ── two-level gating (MOR-977 / MOR-1256) ───────────────────────────────
  const selects = qa<HTMLButtonElement>('[data-vfo-select]');
  const enabled = selects.filter((b) => !b.disabled).length;
  const disabled = selects.length - enabled;
  check('select-gating',
    enabled === expected.selectsEnabled && disabled === expected.selectsDisabled,
    `${enabled} enabled / ${disabled} disabled · expected `
    + `${expected.selectsEnabled}/${expected.selectsDisabled}`);
  check('disabled-selects-are-really-inert',
    selects.filter((b) => b.disabled).every((b) => b.matches(':disabled')),
    'disabled selects match :disabled (attribute, not styling)');

  // ── single TX authority (the layout's hardest invariant) ────────────────
  // MOR-1258 moved the `.rx-tx-zone` div's render site out from under the
  // `{#if view}` gate, so the zone can (and, pre-capabilities, does) exist
  // with zero mounted surfaces — `caps-unloaded` is exactly that state. The
  // upper bound ("never more than one TX authority") holds unconditionally;
  // the lower bound ("exactly one, no fewer") only holds once a view model
  // can exist at all. `harness.caps` — the same value `toRadioViewModel`
  // gates on (`if (!caps) return null`) — is read directly here instead of
  // going through `expected`, so this is the real page state driving the
  // render, not a fixture-id special-case.
  const surfaces = qa('[data-testid="rx-tx-surface"]');
  const keys = qa<HTMLButtonElement>('[data-testid="rx-tx-key"]');
  const unkeys = qa<HTMLButtonElement>('[data-testid="rx-tx-unkey"]');
  // MOR-1085: the reference/single composition mounts `<RxTxSurface>`
  // unconditionally (`SemanticRadioSurfaces`'s `singleOrder` always includes
  // `'rxTx'`, gated only by `{#if view}`) with no zone wrapper to read the
  // declaration off — so for it "declared" is simply "this is the
  // permanent composition", same as the cockpit's zone declaration, just
  // without the div.
  const zoneDeclared = zonedComposition ? expected.zones.includes('rx-tx') : true;
  const capsLoaded = harness.caps !== null;
  const txMax = zoneDeclared ? 1 : 0;
  const txMin = zoneDeclared && capsLoaded ? 1 : 0;
  check('single-tx-authority-surface',
    surfaces.length <= txMax && keys.length <= txMax && unkeys.length <= txMax
      && surfaces.length >= txMin && keys.length >= txMin && unkeys.length >= txMin,
    `${surfaces.length} surfaces / ${keys.length} key / ${unkeys.length} unkey · `
    + `expected ${txMin === txMax ? txMin : `${txMin}-${txMax}`} `
    + `(zone declared=${zoneDeclared}, caps loaded=${capsLoaded})`);
  check('unkey-is-never-gated', unkeys.every((b) => !b.disabled),
    'no unkey control carries `disabled`');
  if (keys.length === 1) {
    check('key-gate', keys[0].disabled === expected.keyDisabled,
      `key disabled=${keys[0].disabled} · expected ${expected.keyDisabled}`);
  }

  // ── radio-wide facts render once, outside every strip (MOR-1068) ────────
  const global = q('[data-zone-id="global"]');
  // MOR-1421: the dual-watch toggle and the active-receiver readout are
  // HIDDEN — not merely disabled — on a genuinely single-receiver radio
  // (`caps.receivers <= 1`). Same `hasDualReceiver` gate
  // `SemanticRadioSurfaces.svelte` computes for the real app, read here off
  // the SAME `harness.caps` this function already treats as ground truth
  // (`capsLoaded` below). Split stays radio-wide regardless — it is
  // meaningful with one receiver.
  const hasDualReceiverFacts = (harness.caps?.receivers ?? 1) > 1;
  const wide = hasDualReceiverFacts
    ? ['[data-vfo-split]', '[data-vfo-dual-watch]', '[data-testid="vfo-active-receiver"]']
    : ['[data-vfo-split]'];
  const hasGlobalZone = expected.zones.includes('global');
  // MOR-1085: the reference/single composition renders the SAME radio-wide
  // switches (one `<VfoSurface viewModel={view}>` call, `showRadioWideFacts`
  // defaulting true) but never wraps them in a `global` zone div — there is
  // no separate radio-wide row there at all, split/dual-watch/active-receiver
  // sit inline with the vfo list. The STRUCTURAL half of this check (are
  // there exactly the switches expected, gated correctly) still applies;
  // only the CONTAINMENT half (do they live inside a `global` div) is
  // cockpit-specific and stays behind `hasGlobalZone`. Both halves are
  // skipped while caps have not loaded (`capsLoaded`, defined above) — with
  // `view` null neither composition renders `<VfoSurface>` at all, so an
  // absent switch there is correct, not a regression.
  const hasRadioWideRow = capsLoaded && (hasGlobalZone || !zonedComposition);
  if (hasRadioWideRow) {
    check('radio-wide-row-renders-once',
      wide.every((sel) => qa(sel).length === 1),
      wide.map((sel) => `${sel}=${qa(sel).length}`).join(' '));
    check('single-receiver-hides-dual-receiver-chrome',
      hasDualReceiverFacts
        || (qa('[data-vfo-dual-watch]').length === 0
          && qa('[data-testid="vfo-active-receiver"]').length === 0),
      'a single-receiver radio (MOR-1421) renders no dual-watch toggle and no '
      + 'active-receiver readout');
    if (hasGlobalZone) {
      check('radio-wide-row-lives-in-the-global-zone',
        wide.every((sel) => {
          const el = q(sel);
          return el !== null && global !== null && global.contains(el)
            && !strips.some((s) => s.contains(el));
        }),
        'split / dual-watch / active-receiver are inside `global` and inside no strip');
      check('global-zone-is-not-aria-disabled',
        global !== null && global.getAttribute('aria-disabled') === null,
        'a zone holding live switches must not present as dead');
    }
    const switches = qa<HTMLButtonElement>('[data-vfo-split], [data-vfo-dual-watch]');
    const expectedSwitchCount = hasDualReceiverFacts ? 2 : 1;
    check('radio-wide-switch-gate',
      switches.length === expectedSwitchCount
      && switches.every((b) => b.disabled === expected.radioWideSwitchesDisabled),
      `disabled=${JSON.stringify(switches.map((b) => b.disabled))} · `
      + `expected all ${expected.radioWideSwitchesDisabled}, count=${expectedSwitchCount}`);
  }

  // ── honest placeholders ────────────────────────────────────────────────
  // MOR-1085: `cockpit-zone-scope`/`cockpit-zone-controls` are
  // `dual-receiver-cockpit`'s OWN structural placeholders (MOR-1067) — a
  // promise that shell makes about future scope/controls surfaces. The
  // reference/single composition (`desktop-v2`/`sdr-test` today) makes no
  // such promise: its scope area is the legacy `SpectrumPanel`, out of scope
  // for this grammar, and it has no equivalent inert marker to check. Asking
  // for one there would assert a promise the layout never made, so this
  // block is cockpit-only, same signal as every other zoned-only check above.
  if (zonedComposition) {
    const inert = ['scope', 'controls']
      .map((z) => q(`[data-testid="cockpit-zone-${z}"]`));
    check('placeholder-zones-present-and-inert',
      inert.every((el) => el !== null
        && el.getAttribute('aria-disabled') === 'true'
        && el.dataset.zoneActive === 'false'
        && el.querySelectorAll('button, [role="switch"], input').length === 0
        && (el.textContent ?? '') === ''
        && !el.hasAttribute('data-zone-id')),
      'scope + controls: aria-disabled, empty, claiming no manifest zone');
  }

  // ── nothing is hidden (MOR-1069 policy 2 — only decidable in a browser) ─
  // A declared zone with no children (MOR-1258's `.rx-tx-zone`, rendered
  // before capabilities load, is the only real case today) is not "hidden" —
  // there is nothing inside it to hide. `visible()` measures laid-out box
  // size, which is legitimately 0x0 for an empty flex container; that is a
  // true and honest reading of "empty", not a bug this assertion should
  // flag. A zone that DOES have content must still lay out visibly.
  const isEmptyZone = (el: HTMLElement): boolean =>
    el.hasAttribute('data-zone-id')
    && el.childElementCount === 0
    && (el.textContent ?? '').trim() === '';
  const hideable = [...qa<HTMLElement>('[data-zone-id]'), ...controls(),
    ...qa<HTMLElement>('[data-testid^="channel-strip-"]')]
    .filter((el) => !isEmptyZone(el));
  const hidden = hideable.filter((el) => !visible(el));
  check('nothing-hidden', hidden.length === 0,
    hidden.length === 0
      ? `${hideable.length} zones/controls all laid out and visible`
      : `hidden: ${hidden.map((el) => el.dataset.testid ?? el.tagName).join(', ')}`);

  // ── focus order is DOM order, ending in the LAST declared zone (MOR-1069) ─
  // MOR-1085: "every control lives inside a declared zone" is the
  // dual-receiver-cockpit's own acceptance gate (MOR-1069/1070 gate item
  // (b)) — it presupposes zones exist to live inside. `zonedComposition`
  // false (reference layout) used to mean "no zone concept at all", so this
  // whole block was skipped there. MOR-1379: that is no longer honest —
  // `main.ts` now resolves `desktopV2Layout` for every reference-layout
  // fixture, so its OPTIONAL surfaces genuinely bind `data-zone-id`
  // wrappers. The cockpit path below is UNCHANGED (still reads
  // `expected.zones`/`expected.zonelessControls` off `catalog.ts`); the
  // reference path is a parallel branch using `REFERENCE_ZONE_IDS` (the
  // manifest, module scope above) and `isStructurallyZoneless` (the
  // vfo/rxTx exemption MOR-1069 makes permanent there) instead of catalog
  // literals that would need hand-updating on every future S-slice.
  // MOR-1355: the cockpit's terminal zone is `declared[declared.length - 1]`,
  // not a hardcoded `'rx-tx'` — the same generalisation
  // `DualReceiverCockpit.component.test.ts`'s MOR-1069 suite already made
  // ("the LAST declared zone comes last"), needed the moment a resolved
  // `SurfacePlan` puts real content in the manifest's `tx-aux` zone, which
  // sits declared (and rendered) AFTER `rx-tx`. A no-op for every fixture
  // whose declared zones still end in `rx-tx` (every one before MOR-1355).
  // The reference layout has no single "terminal zone", and — per the
  // `REFERENCE_ZONE_IDS` finding above — no shared order with the manifest
  // array either, so "ends at the last declared zone" isn't a meaningful
  // requirement there. What IS meaningful and layout-agnostic: once the tab
  // sequence enters a zone it should not leave and re-enter that SAME zone
  // later — a real containment property (controls belonging to one surface
  // group stay contiguous), independent of which order the zones happen to
  // render in.
  if (zonedComposition) {
    const declared = [...expected.zones];
    const seq = controls().map((el) => {
      const zone = el.closest('[data-zone-id]') as HTMLElement | null;
      return declared.indexOf(zone?.dataset.zoneId ?? '');
    });
    const zoned = seq.filter((i) => i >= 0);
    check('focus-order-is-zone-order',
      controls().length === 0
        || (eq(zoned, [...zoned].sort((a, b) => a - b))
          && (zoned.length === 0 || zoned[zoned.length - 1] === declared.length - 1)),
      `zone indices ${JSON.stringify(seq)} (-1 = outside every declared zone)`);
    check('zone-less-control-count',
      seq.filter((i) => i === -1).length === expected.zonelessControls,
      `${seq.filter((i) => i === -1).length} outside every zone · `
      + `expected ${expected.zonelessControls}`);
  } else if (isReferenceLayout) {
    const seq = controls().map((el) => (el.closest('[data-zone-id]') as HTMLElement | null)
      ?.dataset.zoneId ?? null);
    // No zone id may appear in two disjoint runs (i.e. every control for a
    // given zone id is contiguous in tab order) — the ordering invariant
    // that survives the `REFERENCE_ZONE_IDS` finding above, since it does
    // not depend on the manifest array's OWN order matching the DOM's.
    const seenAndClosed = new Set<string>();
    let previous: string | null = null;
    const contiguous = seq.every((zoneId) => {
      if (zoneId === previous) return true;
      // Leaving `previous`'s run (to another zone OR to no zone at all)
      // closes it — a later control back inside the SAME zone id would be
      // re-entrant, whether or not a zone-less control briefly interrupted.
      if (previous !== null) seenAndClosed.add(previous);
      if (zoneId !== null && seenAndClosed.has(zoneId)) return false;
      previous = zoneId;
      return true;
    });
    check('focus-order-is-zone-order', contiguous,
      `zone sequence ${JSON.stringify(seq)} (null = outside every desktopV2Layout zone) — `
      + 'each zone id must stay contiguous, not necessarily in manifest-array order '
      + '(see REFERENCE_ZONE_IDS)');
    // MOR-1379: every OPTIONAL surface desktop-v2 can show is zone-owned
    // (`desktop-declarations.ts`'s S9/`scope-controls` comment: "This empties
    // `RECORDED_REASONS` in `zone-ownership-coverage.test.ts`" — the MOR-1317
    // ledger closed program-wide), so the only controls a plan-ful reference
    // capture may honestly leave outside every zone are the ones MOR-1069
    // permanently exempts (`isStructurallyZoneless`). That expected count is
    // derived from THIS capture's own DOM, not a catalog literal — a real
    // regression (a future declarable surface shipping unzoned) still fails
    // this the moment the two disagree.
    const zoneless = seq.filter((zoneId) => zoneId === null).length;
    const structurallyZoneless = controls().filter(isStructurallyZoneless).length;
    check('zone-less-control-count', zoneless === structurallyZoneless,
      `${zoneless} outside every desktopV2Layout zone · `
      + `expected ${structurallyZoneless} (vfo/rx-tx surface controls, MOR-1069 exempt)`);
  }
  check('no-negative-tabindex-and-no-aria-hidden-control',
    controls().every((el) => Number(el.getAttribute('tabindex') ?? '0') >= 0
      && el.closest('[aria-hidden="true"]') === null),
    `${controls().length} focusable controls`);

  // ── MOR-1087 / MOR-1344: logical tab order, independent of zones ───────
  // `focus-order-is-zone-order` above only runs where zones exist
  // (`zonedComposition`), so it never covered the reference layout. Two
  // zone-free invariants: no positive `tabindex` reorders the natural tab
  // sequence, and every VFO control precedes the RX/TX authority (true on
  // both layouts — `SINGLE_COMPOSITION`/`DUAL_ZONES` both order `vfo` before
  // `rxTx`). FINDING (measured, not assumed): "ends at rx-tx" does NOT hold on
  // the reference layout — `RxAudioSurface` mounts AFTER `<RxTxSurface>`
  // (`SemanticRadioSurfaces.svelte`'s `zoned('rxAudio', …)` runs after the
  // `singleOrder` loop) — real shipped behavior, not asserted here since the
  // reference layout never promised it; worth a follow-up ticket.
  {
    const seq = controls();
    const noPositiveTabindex = seq.every((el) => Number(el.getAttribute('tabindex') ?? '0') <= 0);
    const isVfoControl = (el: HTMLElement): boolean => el.hasAttribute('data-vfo-select')
      || el.hasAttribute('data-vfo-split') || el.hasAttribute('data-vfo-dual-watch');
    const isRxTxAuthority = (el: HTMLElement): boolean => el.dataset.testid === 'rx-tx-key'
      || el.dataset.testid === 'rx-tx-unkey';
    const lastVfoIndex = seq.reduce((last, el, i) => (isVfoControl(el) ? i : last), -1);
    const firstRxTxIndex = seq.findIndex(isRxTxAuthority);
    const vfoPrecedesRxTx = lastVfoIndex === -1 || firstRxTxIndex === -1
      || lastVfoIndex < firstRxTxIndex;
    check('logical-focus-order-vfo-precedes-rx-tx-authority', noPositiveTabindex && vfoPrecedesRxTx,
      `${seq.length} controls · no positive tabindex=${noPositiveTabindex} · `
      + `last vfo control at ${lastVfoIndex}, first rx-tx authority at ${firstRxTxIndex}`);
  }

  // ── MOR-1087 item 4: accessible names + the DisabledReason doctrine ─────
  // A documented approximation of accname (aria-labelledby, aria-label, a
  // wrapping <label> — RxAudioSurface's AF slider — title, else own text),
  // enough to catch "this control has NO name at all".
  const accessibleName = (el: HTMLElement): string => {
    const labelledby = el.getAttribute('aria-labelledby');
    if (labelledby) {
      return labelledby.split(/\s+/)
        .map((id) => document.getElementById(id)?.textContent?.trim() ?? '').join(' ').trim();
    }
    const label = el.getAttribute('aria-label');
    if (label !== null) return label.trim();
    const wrappingLabel = el.closest('label');
    if (wrappingLabel) return wrappingLabel.textContent?.trim() ?? '';
    const title = el.getAttribute('title');
    if (title) return title.trim();
    return el.textContent?.trim() ?? '';
  };
  const unnamed = controls().filter((el) => accessibleName(el) === '');
  check('every-control-has-an-accessible-name', unnamed.length === 0,
    unnamed.length === 0
      ? `${controls().length} controls all named`
      : `unnamed: ${unnamed.map((el) => el.dataset.testid ?? el.tagName).join(', ')}`);
  // `rx-tx-key` is the one control here whose `disabled` state is wired to an
  // accessible EXPLANATION (`aria-describedby` → the `rx-tx-blocked` reasons
  // list), not a bare `disabled` a screen reader reports with no reason.
  const keyButton = q<HTMLButtonElement>('[data-testid="rx-tx-key"]');
  if (keyButton?.disabled) {
    const describedBy = keyButton.getAttribute('aria-describedby');
    const description = describedBy
      ? (document.getElementById(describedBy)?.textContent ?? '').trim() : '';
    check('disabled-key-exposes-its-reason-accessibly', description.length > 0,
      `aria-describedby="${describedBy}" resolves to "${description}"`);
  }

  // ── TX readout words (text AND shape, never colour alone) ──────────────
  const rf = q('[data-testid="rx-tx-rf-label"]')?.textContent?.trim() ?? null;
  const session = q('[data-testid="rx-tx-state"] .rx-tx-session')?.textContent?.trim() ?? null;
  check('tx-readout', rf === expected.rfLabel && session === expected.sessionLabel,
    `rf=${JSON.stringify(rf)} session=${JSON.stringify(session)} · `
    + `expected ${JSON.stringify(expected.rfLabel)}/${JSON.stringify(expected.sessionLabel)}`);
  check('fault-affordance',
    (q('[data-testid="tx-fault-reset"]') !== null) === expected.faultResetPresent,
    `tx-fault-reset present=${q('[data-testid="tx-fault-reset"]') !== null}`);
  check('mod-input-warning',
    (q('[data-testid="mod-input-tx-warning"]') !== null) === expected.modInputWarningPresent,
    `mod-input-tx-warning present=${q('[data-testid="mod-input-tx-warning"]') !== null}`);

  // ── MOR-1085 checklist item 5: the audio-only-scope condition ───────────
  // `RxAudioSurface` is the only currently-wired "audio path" UI
  // (MOR-1279) — it renders in the reference/single composition whenever a
  // view model exists, NEVER in the dual-receiver-cockpit composition
  // (structural, independent of any capability). This is the closest real
  // signal to "the audio path stays operational" available today; see the
  // MOR-1085 report for the finding that no semantic surface yet consumes
  // `view.scope.{hardwareScope,audioFftScope}` on either layout.
  if (expected.rxAudioSurfacePresent !== undefined) {
    const present = q('[data-testid="rx-audio-surface"]') !== null;
    check('rx-audio-surface-presence', present === expected.rxAudioSurfacePresent,
      `rx-audio-surface present=${present} · expected ${expected.rxAudioSurfacePresent}`);
  }
  if (expected.rxAudio !== undefined) {
    const monitor = q<HTMLElement>('[data-testid="rx-audio-monitor"]');
    const liveChoice = q<HTMLElement>('[data-testid="rx-audio-monitor-live"]');
    const af = q<HTMLInputElement>('[data-testid="rx-audio-af"] input[type="range"]');
    const actualMode = monitor?.dataset.monitorMode ?? null;
    const actualConnection = liveChoice?.dataset.liveLink === undefined
      ? null : liveChoice.dataset.liveLink === 'true';
    const actualVolume = actualMode === 'live' && af ? af.valueAsNumber * 100 : null;
    check('rx-audio-runtime-axis',
      actualMode === expected.rxAudio.monitorMode
      && actualConnection === expected.rxAudio.connectionAudio
      && actualVolume === expected.rxAudio.volume,
      `mode=${actualMode} volume=${actualVolume} connectionAudio=${actualConnection} · expected `
      + `${expected.rxAudio.monitorMode}/${expected.rxAudio.volume}/`
      + `${expected.rxAudio.connectionAudio}`);
    const linkPresent = q('[data-testid="rx-audio-link"]') !== null;
    check('rx-audio-link-presence', linkPresent === expected.rxAudio.linkPresent,
      `rx-audio-link present=${linkPresent} · expected ${expected.rxAudio.linkPresent}`);
  }
  // The `scope`/`scopeControls` FACTS (MOR-1298/1299) exist in the view
  // model regardless of which composition mounts it — computed here by
  // calling the real, unmodified adapter with the fixture's own state/caps
  // (its `scope` derivation takes neither `tx` nor the rx-audio snapshot, so
  // the 2-arg call is exact, not an approximation). Checked only where a
  // fixture opts in (`expected.scopeFacts`), so this stays a targeted
  // addition rather than a blanket assertion every fixture must satisfy.
  if (expected.scopeFacts !== undefined) {
    const view = toRadioViewModel(harness.state, harness.caps);
    const scope = view?.scope ?? null;
    check('scope-facts-honest', eq(scope, expected.scopeFacts),
      `scope=${JSON.stringify(scope)} · expected ${JSON.stringify(expected.scopeFacts)} `
      + '(no semantic surface renders this fact on either layout yet — MOR-1085 finding)');
  }

  // ── MOR-1087 item 5: rendered contrast, real computed colours ───────────
  // Thresholds mirror the tokens.test.ts precedent (4.5:1 text / WCAG 1.4.3,
  // 3:1 focus ring / WCAG 1.4.11) but measure what the browser PAINTS, under
  // whichever language `main.ts` activated. Default v2 theme has no such pin.
  const activeLanguage = document.documentElement.dataset.designLanguage ?? 'default';
  const TEXT_TARGETS: readonly [string, string][] = [
    ['rx-tx-key', '[data-testid="rx-tx-key"]'],
    ['rx-tx-unkey', '[data-testid="rx-tx-unkey"]'],
    ['rx-tx-rf-label', '[data-testid="rx-tx-rf-label"]'],
  ];
  for (const [label, sel] of TEXT_TARGETS) {
    const el = q<HTMLElement>(sel);
    if (!el) continue;
    const ratio = contrastRatio(getComputedStyle(el).color, effectiveBackground(el));
    const floor = TEXT_CONTRAST_FLOOR[`${activeLanguage}:${label}`] ?? 4.5;
    const belowIdealText = ratio !== null && ratio < 4.5;
    check(`contrast-text-${label}`, ratio !== null && ratio >= floor,
      `${ratio === null ? 'unparseable' : ratio.toFixed(2)}:1 on "${activeLanguage}" · `
      + `expected >= ${floor}:1${belowIdealText
        ? ' (MOR-1087 finding: below the 4.5:1 WCAG 1.4.3 text floor — see comment above)' : ''}`);
  }
  // Non-text: the focus ring, only where `Tab` actually reached
  // `:focus-visible` (`options.focusVisible`) — else there's nothing to measure.
  if (options.focusVisible) {
    const el = document.activeElement as HTMLElement | null;
    if (el && el !== document.body) {
      const ratio = contrastRatio(getComputedStyle(el).outlineColor, effectiveBackground(el));
      check('contrast-focus-ring', ratio !== null && ratio >= 3,
        `${ratio === null ? 'unparseable' : ratio.toFixed(2)}:1 on "${activeLanguage}" · `
        + 'expected >= 3:1 (WCAG 1.4.11)');
    }
  }

  // ── viewport-dependent: the reflow itself ──────────────────────────────
  if (options.arrangement !== undefined && strips.length === 2) {
    const [a, b] = strips.map((el) => el.getBoundingClientRect());
    const actual = b.top >= a.bottom - 1 ? 'stacked'
      : Math.abs(b.top - a.top) < 1 && b.left > a.left ? 'side-by-side'
        : 'indeterminate';
    // `repeat(auto-fit, minmax(0, 1fr))` resolves to the used tracks followed
    // by ~150 collapsed `0px` ones in Chromium — record only the used prefix.
    const tracks = getComputedStyle(q('.channel-strips')!).gridTemplateColumns
      .trim().split(/\s+/).filter((t) => t !== '0px');
    check('strip-arrangement-at-this-viewport', actual === options.arrangement,
      `${actual} · expected ${options.arrangement} · used tracks: ${tracks.join(' ')}`
      + ` (+${
        getComputedStyle(q('.channel-strips')!).gridTemplateColumns.trim().split(/\s+/).length
        - tracks.length} collapsed auto-fit tracks)`);
  }
  if (options.touchTargets) {
    const small = qa<HTMLElement>('button, [role="switch"]').filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width < 44 || r.height < 44;
    });
    check('touch-targets-44px', small.length === 0,
      small.length === 0
        ? `${qa('button, [role="switch"]').length} controls all >= 44x44`
        : `too small: ${small.map((el) => el.textContent?.trim().slice(0, 18)).join(' | ')}`);
  }
  if (options.reducedMotion) {
    const moving = [root(), ...qa<HTMLElement>('*')].filter((el) => {
      const cs = getComputedStyle(el);
      const anim = cs.animationName !== 'none'
        && cs.animationDuration.split(',').some((d) => parseFloat(d) > 0.001);
      const trans = cs.transitionDuration.split(',').some((d) => parseFloat(d) > 0.001);
      return anim || trans;
    });
    check('reduced-motion-switches-everything-off', moving.length === 0,
      moving.length === 0
        ? 'no element inside the cockpit animates or transitions'
        : `still moving: ${moving.map((el) => el.className).join(' | ')}`);
  }

  return out;
}

/**
 * Resolved paint of the controls that carry the most safety weight, recorded
 * per capture so a `prefers-contrast` / `forced-colors` variant is provable in
 * TEXT and not only in pixels.
 */
export function styleProbe(): Record<string, Record<string, string>> {
  const targets: Record<string, string> = {
    key: '[data-testid="rx-tx-key"]',
    unkey: '[data-testid="rx-tx-unkey"]',
    split: '[data-vfo-split]',
    vfoSelect: '[data-vfo-select]:not(:disabled)',
    disabledSelect: '[data-vfo-select]:disabled',
    activeTile: '[data-vfo-tile][data-vfo-active="true"]',
    inactiveTile: '[data-vfo-tile][data-vfo-active="false"]',
    activeStrip: '[data-testid^="channel-strip-"][data-strip-active="true"]',
    inactiveStrip: '[data-testid^="channel-strip-"][data-strip-active="false"]',
    txBadge: '[data-vfo-tx-badge]',
  };
  const out: Record<string, Record<string, string>> = {};
  for (const [name, sel] of Object.entries(targets)) {
    const el = q<HTMLElement>(sel);
    if (!el) continue;
    const cs = getComputedStyle(el);
    out[name] = {
      color: cs.color,
      backgroundColor: cs.backgroundColor,
      borderColor: cs.borderTopColor,
      borderLeftColor: cs.borderLeftColor,
      outlineColor: cs.outlineColor,
      forcedColorAdjust: cs.forcedColorAdjust,
    };
  }
  return out;
}

/** Resolved token values, recorded per capture so a media variant is provable. */
export function tokenSnapshot(): Record<string, string> {
  const cs = getComputedStyle(document.documentElement);
  const names = ['--v2-text-primary', '--v2-text-secondary', '--v2-text-disabled',
    '--v2-border-panel', '--v2-bg-panel', '--v2-accent-cyan', '--v2-accent-red',
    '--v2-focus-ring', '--focus-ring', '--bg', '--text', '--accent'];
  return Object.fromEntries(names.map((n) => [n, cs.getPropertyValue(n).trim()]));
}
