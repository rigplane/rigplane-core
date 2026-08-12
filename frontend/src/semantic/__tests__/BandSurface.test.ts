/**
 * MOR-1307 — the semantic band + frequency-entry surface (vocabulary slice 7B).
 *
 * SAFETY-ADJACENT. This surface renders TX-PERMISSION information, so every
 * test below names the mutation it kills:
 *   (a) the band-scoped TX answer sourced from anything but the LIVE fact
 *       `band.currentBandTx` — re-inheriting `defaultHzTxPermit` is the exact
 *       fail-open defect MOR-1294's F1 round removed one layer down, and 7B's
 *       carry-forward F3 forbids reintroducing it here;
 *   (b) a point-sample permit presented as band-wide permission;
 *   (c) a denial softened because `disabledReasons` carries no band entry (by
 *       design it never does — the denial IS the field value);
 *   (d) frequency entry accepted outside `tuneMinHz`/`tuneMaxHz`, or accepted
 *       at all while those bounds are unknown;
 *   (e) a receiver-scoped write dispatched while the active receiver is
 *       unobserved (the MOR-1322 B1 wrong-VFO class).
 *
 * Fast-pool-safe by construction (MOR-1272): no `vi.mock`, no `vi.stubGlobal`,
 * no global spy. The composed-tree pins for BandSurface's PROP wiring (how
 * SemanticRadioSurfaces feeds it a view model and callbacks) live in the
 * isolated-pool wiring file
 * `components-v2/wiring/__tests__/semantic-band-wiring.component.test.ts`.
 *
 * One deliberate exception (MOR-1444 round-3 review): the last describe
 * block below mounts the real `KeyboardHandler` alongside the real
 * `BandSurface` to pin a DOM EVENT-BUBBLING contract between them (Escape
 * must not propagate past this component into the window keyboard seam).
 * That is not prop wiring — no store, no transport, no `vi.mock` — so it
 * stays fast-pool-safe and belongs with the Escape behavior it guards.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import BandSurface, {
  ACTIVE_RECEIVER_UNCONFIRMED_REASON, REASON_LABEL, UNKNOWN_TEXT, UNRESOLVED_REASON,
  defaultPermitLabel, mhz,
} from '../BandSurface.svelte';
import { topologyFixtures, withBand } from '../fixtures/topologies';
import type { FrequencyPermit } from '$lib/utils/tx-permit';
import type {
  BandChoice, BandViewModel, DisabledReason, RadioViewModel,
} from '../radio-view-model';
import KeyboardHandler from '../../components-v2/layout/KeyboardHandler.svelte';
import type { KeyboardConfig } from '../../components-v2/layout/keyboard-map';

const SOURCE = readFileSync('src/semantic/BandSurface.svelte', 'utf8');
/** Comments stripped, so the file's own doctrine prose can never be what a
 *  source-scanning test matches. */
const CODE = SOURCE
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

const ALLOWED: FrequencyPermit = { status: 'allowed', band: '20m' };
const DENIED: FrequencyPermit = { status: 'denied', reason: 'outside-configured-ranges' };
const AVAIL = { structural: true, operational: true } as const;

const base = (): RadioViewModel => withBand(topologyFixtures['1/single']);

/** Re-shape the band group of an otherwise fully-observed fixture. */
function withB(over: Partial<BandViewModel>, view: RadioViewModel = base()): RadioViewModel {
  return { ...view, band: { ...(view.band as BandViewModel), ...over } };
}
const knownBand = (name: string) =>
  ({ reading: { status: 'known' as const, value: name }, availability: AVAIL });
const unreadBand = () => ({ reading: { status: 'unknown' as const }, availability: AVAIL });

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

type Handlers = {
  onSelectBand?: (name: string, defaultHz: number, bsrCode: number | null) => void;
  onEnterFrequency?: (frequencyHz: number) => void;
};

function render(view: RadioViewModel, handlers: Handlers = {}) {
  const component = mount(BandSurface, { target, props: { view, ...handlers } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => unmount(component),
    root: () => q('[data-testid="band-surface"]'),
    el: (id: string) => q<HTMLElement>(`[data-testid="band-${id}"]`),
    btn: (id: string) => q<HTMLButtonElement>(`[data-testid="band-${id}"]`),
    input: () => q<HTMLInputElement>('[data-testid="band-entry-input"]'),
    text: (id: string) => q<HTMLElement>(`[data-testid="band-${id}"]`)?.textContent?.trim(),
  };
}

/** The MOR-1304 F3 recipe: a real `.click()` is swallowed by `disabled`, so a
 *  handler guard can only be pinned on its OWN by bypassing the attribute. */
function bypassClick(node: HTMLElement): void {
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  flushSync();
}
function typeFrequency(input: HTMLInputElement, value: string): void {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  flushSync();
}

/* ── purity: no second permit derivation can even be reachable ──── */

describe('the band surface derives nothing (7B carry-forward 1)', () => {
  // Kills: importing the permit function, the band plan, capabilities or the
  // command bus — any of which would let a second permit derivation exist.
  it('imports nothing but the fact contract', () => {
    const specifiers = [...CODE.matchAll(/from\s+'([^']+)'/g)].map((m) => m[1]);
    expect(specifiers.length).toBeGreaterThan(0);
    expect([...new Set(specifiers)]).toEqual(['./radio-view-model']);
  });

  it('never mentions the permit derivation, the band plan or a capability read', () => {
    for (const forbidden of [
      'getFrequencyPermit', 'getTxPermit', 'txBands', 'freqRanges', 'flattenBands',
      'findActiveBand', 'capabilities', 'hasCap', '$lib/', 'sendCommand',
    ]) {
      expect(CODE).not.toContain(forbidden);
    }
  });

  it('declares no lifecycle hook and no effect', () => {
    for (const forbidden of ['onMount', 'onDestroy', '$effect', 'import(']) {
      expect(CODE).not.toContain(forbidden);
    }
  });

  it('takes exactly one state prop — the view model — plus intent callbacks', () => {
    const props = CODE.slice(CODE.indexOf('interface Props'), CODE.indexOf('}: Props'));
    expect([...props.matchAll(/^\s{4}(\w+)[?]?:/gm)].map((m) => m[1]))
      .toEqual(['view', 'onSelectBand', 'onEnterFrequency']);
  });

  it('renders nothing at all when the radio declares no band plan', () => {
    const { band: _drop, ...noBand } = base();
    const r = render(noBand as RadioViewModel);
    expect(r.root()).toBeNull();
    r.dispose();
  });
});

/* ── (a) the band-scoped TX answer is the LIVE fact ─────────────── */

describe('currentBandTx is the live-frequency answer (carry-forwards 1 + 2)', () => {
  // THE F1/F3 REGRESSION PIN. The current band's own default-frequency permit
  // says `allowed`; the live fact says `denied` (a txBands segment narrower
  // than the plan band — 14.300 MHz against a 14.000–14.150 allocation). A
  // surface that sourced the answer from `defaultHzTxPermit` renders
  // "allowed" here and this test dies.
  it('renders denied while the current band default-frequency permit reads allowed', () => {
    const view = withB({ currentBand: knownBand('20m'), currentBandTx: 'denied' });
    const current = (view.band as BandViewModel).bandChoices
      .find((c) => c.name === '20m') as BandChoice;
    expect(current.defaultHzTxPermit.status).toBe('allowed');
    const r = render(view);
    expect(r.text('tx-value')).toBe('denied');
    expect(r.el('tx')!.dataset.tx).toBe('denied');
    r.dispose();
  });

  // Kills: collapsing the branch the other way (allowed rendered as denied).
  it('renders allowed when the live fact allows, on the same choice set', () => {
    const r = render(withB({ currentBand: knownBand('20m'), currentBandTx: 'allowed' }));
    expect(r.text('tx-value')).toBe('allowed');
    expect(r.el('tx')!.dataset.tx).toBe('allowed');
    r.dispose();
  });

  // Kills: dropping the whole TX readout while the band itself is unknown —
  // "no answer" must never be how a denial is presented.
  it('still states the denial while the current band is unreadable', () => {
    const r = render(withB({ currentBand: unreadBand(), currentBandTx: 'denied' }));
    expect(r.text('current-value')).toBe(UNKNOWN_TEXT);
    expect(r.text('tx-value')).toBe('denied');
    r.dispose();
  });

  // Carry-forward 2: the tri-state nuance the binary collapse cannot carry is
  // taken from the TOP-LEVEL txPermit + disabledReasons, not re-derived.
  it.each([
    ['out-of-band', REASON_LABEL['out-of-band']],
    ['tx-target-unknown', REASON_LABEL['tx-target-unknown']],
    ['capability-unavailable', REASON_LABEL['capability-unavailable']],
  ])('explains a denial with the recorded %s reason', (code, label) => {
    const view = withB({ currentBandTx: 'denied' });
    const r = render({
      ...view,
      txPermit: DENIED,
      disabledReasons: [{ field: 'txPermit', code }] as readonly DisabledReason[],
    });
    expect(r.text('tx-reason')).toBe(label);
    r.dispose();
  });

  it('annotates the top-level tri-state without collapsing it', () => {
    const view = withB({ currentBandTx: 'denied' });
    const r = render({ ...view, txPermit: { status: 'unknown', reason: 'ranges-unconfigured' } });
    expect(r.root()!.dataset.txPermitStatus).toBe('unknown');
    r.dispose();
  });

  it('offers no reason line at all while TX is allowed', () => {
    const r = render(withB({ currentBandTx: 'allowed' }));
    expect(r.el('tx-reason')).toBeNull();
    r.dispose();
  });
});

/* ── fix-round F1: the band answer can disagree with the authoritative
   TX-target permit, and that disagreement must be VISIBLE, not just a
   `data-` attribute ────────────────────────────────────────────────── */

describe('the TX-target permit caveat (fix-round F1)', () => {
  // THE F1 REGRESSION PIN. `band.currentBandTx` answers for the ACTIVE
  // RECEIVER's frequency; `view.txPermit` is the authoritative TX-TARGET
  // permit and can disagree (e.g. under split). Before the fix this
  // disagreement was visible only in `data-tx-permit-status` — an operator
  // reading the text saw an unqualified "TX HERE: allowed".
  it('renders a visible caveat when the band answer is allowed but the TX-target permit is denied', () => {
    const view = withB({ currentBand: knownBand('20m'), currentBandTx: 'allowed' });
    const r = render({
      ...view,
      txPermit: DENIED,
      disabledReasons: [{ field: 'txPermit', code: 'out-of-band' }] as readonly DisabledReason[],
    });
    expect(r.text('tx-value')).toBe('allowed');
    expect(r.el('tx-caveat')).not.toBeNull();
    expect(r.text('tx-caveat')).toContain(REASON_LABEL['out-of-band']);
    r.dispose();
  });

  // Same disagreement, `unknown` case — the far more common shape: an
  // unobserved TX target. Every other TX-adjacent surface in the program
  // treats `unknown` as fail-closed (MOR-1296 O2); this surface must not be
  // the one place that prints an unqualified "allowed".
  it('renders a visible caveat when the band answer is allowed but the TX-target permit is unknown', () => {
    const view = withB({ currentBand: knownBand('20m'), currentBandTx: 'allowed' });
    const r = render({
      ...view,
      txPermit: { status: 'unknown', reason: 'tx-target-unknown' },
      disabledReasons: [{ field: 'txPermit', code: 'tx-target-unknown' }] as readonly DisabledReason[],
    });
    expect(r.text('tx-value')).toBe('allowed');
    expect(r.el('tx-caveat')).not.toBeNull();
    expect(r.text('tx-caveat')).toContain(REASON_LABEL['tx-target-unknown']);
    r.dispose();
  });

  // Negative guard: when both permits agree on `allowed`, no caveat appears
  // — the caveat is the DISAGREEMENT signal, not a permanent fixture.
  it('renders no caveat when both the band answer and the TX-target permit are allowed', () => {
    const view = withB({ currentBand: knownBand('20m'), currentBandTx: 'allowed' });
    const r = render({ ...view, txPermit: ALLOWED, disabledReasons: [] });
    expect(r.text('tx-value')).toBe('allowed');
    expect(r.el('tx-caveat')).toBeNull();
    r.dispose();
  });
});

/* ── (c) no band-scoped disabledReason exists, by design ────────── */

describe('the denial signal is the field value itself (carry-forward 3)', () => {
  // Kills: gating the denial readout on a `band.*` disabledReasons entry. No
  // such entry is ever emitted (MOR-1294 §4), so a surface that waited for one
  // would silently present a denied band as unremarkable.
  it('states a denial with an EMPTY disabledReasons list', () => {
    const view = withB({ currentBand: knownBand('20m'), currentBandTx: 'denied' });
    const r = render({ ...view, txPermit: ALLOWED, disabledReasons: [] });
    expect(r.text('tx-value')).toBe('denied');
    expect(r.text('tx-reason')).toBe(UNRESOLVED_REASON);
    r.dispose();
  });

  it('never looks for a band-scoped reason code', () => {
    expect(CODE).not.toContain("'band.");
  });

  // THE F2 REGRESSION PIN. `capability-unavailable` is NOT TX-exclusive — the
  // adapter also emits it for `scope.hardwareScope`/`scope.audioFftScope` and
  // each non-operational `receiver.<id>`. A denial explanation that matches
  // on CODE ALONE (ignoring `field`) renders this scope-capability gap as a
  // TX-configuration statement, which is false: `txPermit` here is allowed,
  // so the real cause is that the band itself could not be resolved.
  it('ignores a non-TX reason that happens to carry a TX code', () => {
    const view = withB({ currentBand: knownBand('20m'), currentBandTx: 'denied' });
    const r = render({
      ...view,
      txPermit: ALLOWED,
      disabledReasons: [
        { field: 'scope.audioFftScope', code: 'capability-unavailable' },
      ] as readonly DisabledReason[],
    });
    expect(r.text('tx-reason')).toBe(UNRESOLVED_REASON);
    r.dispose();
  });
});

/* ── three-way denial reason (MOR-1389, MOR-1356 verify §4.4) ──────
   `deriveBand`'s MOR-1356 `activeConfirmed` gate can force `currentBandTx`
   to 'denied' while the CURRENT BAND is resolved and rendered right above
   the readout. Before this ticket, `txDeniedReason`'s F2 field==='txPermit'
   match found no entry (the true reason is `field: 'activeReceiver'`) and
   fell through to the "band could not be resolved" fallback — false, since
   the band IS resolved. These three tests pin the resulting three-way
   split; the middle one is the ticket's own repro. ────────────────── */

describe('the three-way denial reason distinguishes an unconfirmed receiver (MOR-1389)', () => {
  // Row 1 — genuinely out of band: band resolved, receiver confirmed, a
  // recorded TX-scoped reason explains it. UNCHANGED by this ticket — F2's
  // field==='txPermit' match still owns this text and must keep owning it.
  it('keeps the recorded out-of-band explanation when the receiver IS confirmed', () => {
    const view = withB({ currentBand: knownBand('20m'), currentBandTx: 'denied' });
    const r = render({
      ...view,
      txPermit: DENIED,
      disabledReasons: [{ field: 'txPermit', code: 'out-of-band' }] as readonly DisabledReason[],
    });
    expect(r.text('current-value')).toBe('20m');
    expect(r.text('tx-reason')).toBe(REASON_LABEL['out-of-band']);
    r.dispose();
  });

  // Row 2 — THE TICKET. `activeReceiver` is unknown, the band is resolved,
  // and nothing in `disabledReasons` carries a `field: 'txPermit'` entry
  // (`txPermit` itself reads allowed — MOR-1356's exact rendered repro).
  // The old fallback said "current band could not be resolved" while
  // `BAND` showed `20m` directly above it. The new text must be a TRUE
  // sentence about the receiver, not the band.
  it('names the unconfirmed receiver instead of the band-unresolved fallback', () => {
    const view = withB({ currentBand: knownBand('20m'), currentBandTx: 'denied' });
    const r = render({
      ...view,
      activeReceiver: { status: 'unknown' },
      txPermit: ALLOWED,
      disabledReasons: [
        { field: 'activeReceiver', code: 'field-not-observed' },
      ] as readonly DisabledReason[],
    });
    expect(r.text('current-value')).toBe('20m');
    expect(r.text('tx-reason')).toBe(ACTIVE_RECEIVER_UNCONFIRMED_REASON);
    expect(r.text('tx-reason')).not.toBe(UNRESOLVED_REASON);
    r.dispose();
  });

  // Row 3 — the band genuinely could not be resolved: receiver confirmed,
  // no out-of-band code. The fallback text is TRUE here and must survive
  // untouched.
  it('keeps the band-unresolved fallback when the band itself is unread', () => {
    const view = withB({ currentBand: unreadBand(), currentBandTx: 'denied' });
    const r = render({ ...view, txPermit: ALLOWED, disabledReasons: [] });
    expect(r.text('current-value')).toBe(UNKNOWN_TEXT);
    expect(r.text('tx-reason')).toBe(UNRESOLVED_REASON);
    r.dispose();
  });

  // ORDER PIN (added in verification). The two explanations can hold AT ONCE:
  // a receiver that was never confirmed AND a recorded out-of-band fault. The
  // F2 TX-scoped match must keep winning — it names a specific, actionable TX
  // fault and is equally a cause of the denial, whereas the receiver sentence
  // would bury it. Kills: hoisting the `activeReceiver` branch above the
  // TX-scoped search. No other test in this file reaches that state — row 1
  // above uses a CONFIRMED receiver — so without this the branch ORDER, which
  // is what makes the three-way split safe, is unpinned.
  it('lets a recorded out-of-band fault outrank an unconfirmed receiver', () => {
    const r = render({
      ...withB({ currentBand: knownBand('MW'), currentBandTx: 'denied' }),
      activeReceiver: { status: 'unknown' },
      txPermit: DENIED,
      disabledReasons: [
        { field: 'activeReceiver', code: 'field-not-observed' },
        { field: 'txPermit', code: 'out-of-band' },
      ] as readonly DisabledReason[],
    });
    expect(r.text('current-value')).toBe('MW');
    expect(r.text('tx-reason')).toBe(REASON_LABEL['out-of-band']);
    expect(r.text('tx-reason')).not.toBe(ACTIVE_RECEIVER_UNCONFIRMED_REASON);
    r.dispose();
  });

  // The distinguishing signal named in rule (4) — band-choice buttons dim
  // while the active receiver is unconfirmed — must still hold in exactly
  // the state row 2 renders the new sentence for.
  it('still dims every band-choice button in the unconfirmed-receiver denial state', () => {
    const r = render({
      ...withB({ currentBand: knownBand('20m'), currentBandTx: 'denied' }),
      activeReceiver: { status: 'unknown' },
      txPermit: ALLOWED,
      disabledReasons: [
        { field: 'activeReceiver', code: 'field-not-observed' },
      ] as readonly DisabledReason[],
    });
    for (const name of ['40m', '20m', 'MW']) expect(r.btn(`choice-${name}`)!.disabled).toBe(true);
    r.dispose();
  });
});

/* ── (b) the point sample is labelled as one ────────────────────── */

describe('defaultHzTxPermit is never presented as band-wide permission (carry-forward 1/F3)', () => {
  // Kills: labelling the choice "TX allowed" with no frequency — the misread
  // the MOR-1294 rename exists to prevent.
  it('names the sampled frequency in every choice permit label', () => {
    const view = base();
    const r = render(view);
    for (const choice of (view.band as BandViewModel).bandChoices) {
      const label = r.text(`choice-permit-${choice.name}`)!;
      expect(label).toContain(mhz(choice.defaultHz));
      expect(label).toBe(defaultPermitLabel(choice));
      expect(label).toContain(choice.defaultHzTxPermit.status);
    }
    r.dispose();
  });

  // Kills: reusing the point sample as the band-scoped answer. The MW choice
  // is denied at its default while the live answer is allowed — the two are
  // rendered by two different elements and must not agree by construction.
  it('keeps the choice permits independent of the live TX answer', () => {
    const r = render(withB({ currentBand: knownBand('20m'), currentBandTx: 'allowed' }));
    expect(r.el('choice-MW')!.dataset.defaultPermit).toBe('denied');
    expect(r.el('choice-20m')!.dataset.defaultPermit).toBe('allowed');
    expect(r.text('tx-value')).toBe('allowed');
    r.dispose();
  });

  // Kills: disabling a band whose default frequency has no TX permit. Picking
  // a band is a TUNING action; a receive-only band stays perfectly selectable.
  it('leaves a TX-denied band selectable, because tuning is not transmitting', () => {
    const onSelectBand = vi.fn();
    const r = render(base(), { onSelectBand });
    expect(r.btn('choice-MW')!.disabled).toBe(false);
    r.btn('choice-MW')!.click();
    flushSync();
    expect(onSelectBand).toHaveBeenCalledExactlyOnceWith('MW', 1000000, null);
    r.dispose();
  });

  it('marks the current band pressed and no other', () => {
    const r = render(withB({ currentBand: knownBand('40m') }));
    expect(r.el('choice-40m')!.getAttribute('aria-pressed')).toBe('true');
    expect(r.el('choice-20m')!.getAttribute('aria-pressed')).toBe('false');
    r.dispose();
  });

  it('presses nothing while the current band is unread', () => {
    const r = render(withB({ currentBand: unreadBand() }));
    for (const name of ['40m', '20m', 'MW']) {
      expect(r.el(`choice-${name}`)!.getAttribute('aria-pressed')).toBe('false');
    }
    r.dispose();
  });
});

/* ── (d) frequency entry validates against the envelope ─────────── */

describe('frequency entry validates against tuneMinHz/tuneMaxHz (carry-forward 5)', () => {
  // MOR-1462: a realistic BAND-SCOPED range (20m), not the general-coverage
  // 30 kHz … 60 MHz fixture used elsewhere in this file — every boundary
  // value below is 8 digits, so its ×1000 kHz reading is always far outside
  // this range and the smart-unit heuristic falls through to the Hz path
  // unambiguously. That keeps this block a pin on the Hz BOUNDARY check
  // itself, not on unit selection (MOR-1462 owns that, in its own block).
  const BOUNDS = { tuneMinHz: 14000000, tuneMaxHz: 14350000 } as const;

  it.each([
    ['one hertz below the minimum', 13999999, false],
    ['exactly the minimum', 14000000, true],
    ['exactly the maximum', 14350000, true],
    ['one hertz above the maximum', 14350001, false],
  ])('%s is accepted=%s', (_label, hz, accepted) => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(BOUNDS), { onEnterFrequency });
    typeFrequency(r.input()!, String(hz));
    expect(r.btn('entry-set')!.disabled).toBe(!accepted);
    r.btn('entry-set')!.click();
    flushSync();
    if (accepted) expect(onEnterFrequency).toHaveBeenCalledExactlyOnceWith(hz);
    else expect(onEnterFrequency).not.toHaveBeenCalled();
    r.dispose();
  });

  // Kills: dropping the handler-side range check. `disabled` alone satisfies a
  // `.click()`-based assertion, so the guard is bypassed here on its own.
  it.each([13999999, 14350001])('refuses %i even when the disabled attribute is bypassed', (hz) => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(BOUNDS), { onEnterFrequency });
    typeFrequency(r.input()!, String(hz));
    bypassClick(r.btn('entry-set')!);
    expect(onEnterFrequency).not.toHaveBeenCalled();
    r.dispose();
  });

  // MOR-1462: the input's native min/max is GONE — a kHz-scale keystroke
  // (e.g. `7100`) is a valid, in-range reading despite sitting numerically
  // far below a Hz-stated `tuneMinHz`, so a native constraint would flag it
  // invalid for a reason this file's own JS-side interpretation disagrees
  // with. `entry-range` still publishes the envelope in prose.
  it('publishes the envelope in the entry-range text, with no native min/max to contradict unit-aware validation', () => {
    const r = render(withB(BOUNDS));
    expect(r.input()!.getAttribute('min')).toBeNull();
    expect(r.input()!.getAttribute('max')).toBeNull();
    expect(r.input()!.getAttribute('inputmode')).toBe('decimal');
    expect(r.text('entry-range')).toBe(`${mhz(14000000)} … ${mhz(14350000)}`);
    r.dispose();
  });

  // Kills: coercing an empty or malformed entry to 0 Hz and dispatching it.
  it.each(['', '   ', 'abc'])('never dispatches the malformed entry %o', (typed) => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(BOUNDS), { onEnterFrequency });
    typeFrequency(r.input()!, typed);
    expect(r.btn('entry-set')!.disabled).toBe(true);
    bypassClick(r.btn('entry-set')!);
    expect(onEnterFrequency).not.toHaveBeenCalled();
    r.dispose();
  });
});

/* ── smart kHz/MHz/Hz interpretation of a typed entry (MOR-1462) ──
   Owner ruling B, the standard ham direct-entry heuristic: a decimal point
   means MHz (exact decimal-string-to-Hz, no float round-trip); a bare
   integer is kHz when ×1000 is in range, Hz when only the raw reading is,
   and kHz wins the ambiguous case where both are. Commit still lands on the
   MOR-1425 exact-jump path (`onEnterFrequency` receives the interpreted
   Hz), and Enter/Set share the one `entryReady` guard, same as MOR-1444. */

describe('smart kHz/MHz/Hz interpretation of a typed entry (MOR-1462, owner ruling B)', () => {
  // General-coverage range (30 kHz … 60 MHz) — wide enough that a bare
  // integer's kHz reading and its raw-Hz reading can BOTH be in range,
  // which is exactly the ambiguity the ticket rules on.
  const BOUNDS = { tuneMinHz: 30000, tuneMaxHz: 60000000 } as const;

  it.each([
    ['bare kHz-scale integer', '7100', 7100000],
    ['another bare kHz-scale integer', '14074', 14074000],
    ['MHz decimal, one fractional digit', '7.1', 7100000],
    ['MHz decimal, exact to the Hz (no float round-trip)', '14.0741', 14074100],
    // Verifier-flagged: `14.0741` alone does NOT distinguish digit-scaling
    // from `Math.trunc(Number(text) * 1_000_000)` — `Number('14.0741') *
    // 1e6 === 14074100` exactly in IEEE-754. `8.000001`/`1.000001` are
    // witnesses where the float product genuinely lands a fraction below
    // the integer (e.g. 8000000.999999999), so a truncating float
    // round-trip is off by exactly 1 Hz. THESE are the exactness pin.
    ['MHz decimal where float*1e6 truncates 1 Hz low', '8.000001', 8000001],
    ['MHz decimal where float*1e6 truncates 1 Hz low (2)', '1.000001', 1000001],
    ['already-Hz integer, out of kHz range', '7100000', 7100000],
  ])('interprets %s (%s) as %i Hz and dispatches it exactly', (_label, typed, expectedHz) => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(BOUNDS), { onEnterFrequency });
    typeFrequency(r.input()!, typed);
    expect(r.btn('entry-set')!.disabled).toBe(false);
    r.btn('entry-set')!.click();
    flushSync();
    expect(onEnterFrequency).toHaveBeenCalledExactlyOnceWith(expectedHz);
    r.dispose();
  });

  // THE AMBIGUITY PIN. Both readings of `40000` are in range (40 kHz raw,
  // 40 MHz ×1000) — the ticket rules kHz wins.
  it('resolves a kHz-vs-Hz ambiguous entry to kHz', () => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(BOUNDS), { onEnterFrequency });
    typeFrequency(r.input()!, '40000');
    r.btn('entry-set')!.click();
    flushSync();
    expect(onEnterFrequency).toHaveBeenCalledExactlyOnceWith(40000000);
    r.dispose();
  });

  it('refuses an entry that is out of range under every reading, dispatching nothing', () => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(BOUNDS), { onEnterFrequency });
    // ~100 MHz raw, ~100 GHz ×1000 — both out of the 30 kHz … 60 MHz range.
    typeFrequency(r.input()!, '99999999');
    expect(r.btn('entry-set')!.disabled).toBe(true);
    bypassClick(r.btn('entry-set')!);
    expect(onEnterFrequency).not.toHaveBeenCalled();
    r.dispose();
  });

  it('refuses a decimal entry finer than 1 Hz (more than 6 fractional digits)', () => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(BOUNDS), { onEnterFrequency });
    typeFrequency(r.input()!, '14.1234567');
    expect(r.btn('entry-set')!.disabled).toBe(true);
    bypassClick(r.btn('entry-set')!);
    expect(onEnterFrequency).not.toHaveBeenCalled();
    r.dispose();
  });

  it('refuses an in-progress decimal with no fractional digits yet', () => {
    const r = render(withB(BOUNDS));
    typeFrequency(r.input()!, '14.');
    expect(r.btn('entry-set')!.disabled).toBe(true);
    r.dispose();
  });

  it('shows the interpreted MHz value as a commit hint before Set is pressed', () => {
    const r = render(withB(BOUNDS));
    typeFrequency(r.input()!, '7100');
    expect(r.text('entry-hint')).toBe(`→ ${mhz(7100000)}`);
    r.dispose();
  });

  it('shows no hint while the entry cannot be resolved', () => {
    const r = render(withB(BOUNDS));
    typeFrequency(r.input()!, 'abc');
    expect(r.el('entry-hint')).toBeNull();
    r.dispose();
  });

  // Review hardening: the hint must not render a target next to a Set
  // button the MOR-1322 receiver-known gate has disabled — an interpretable
  // entry alone is not enough, `entryReady` (the SAME gate Set/Enter use)
  // decides the hint too.
  it('hides the hint when the entry parses but the active receiver is unknown', () => {
    const r = render({ ...withB(BOUNDS), activeReceiver: { status: 'unknown' } });
    typeFrequency(r.input()!, '7100');
    expect(r.btn('entry-set')!.disabled).toBe(true);
    expect(r.el('entry-hint')).toBeNull();
    r.dispose();
  });

  // Commit still lands on the SAME MOR-1425 exact-jump path Enter/Set
  // already share for an integer entry — no second dispatch shape for MHz.
  it('commits an MHz decimal entry on Enter, exactly like Set', () => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(BOUNDS), { onEnterFrequency });
    typeFrequency(r.input()!, '14.0741');
    r.input()!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }),
    );
    flushSync();
    expect(onEnterFrequency).toHaveBeenCalledExactlyOnceWith(14074100);
    r.dispose();
  });
});

describe('unknown tuning bounds fail closed (carry-forward 5, rule 5)', () => {
  const NO_BOUNDS = { tuneMinHz: null, tuneMaxHz: null } as const;

  // Kills: substituting `frequency-tuning.ts`'s fabricated 0 … 999 MHz, or
  // accepting the entry unvalidated when the radio declared no range.
  it('disables the entry field and the Set button, and says why', () => {
    const r = render(withB(NO_BOUNDS));
    expect(r.input()!.disabled).toBe(true);
    expect(r.btn('entry-set')!.disabled).toBe(true);
    expect(r.el('entry')!.dataset.bounds).toBe('false');
    expect(r.text('entry-reason')).toContain('tuning limits unknown');
    expect(r.text('entry-range')).toBe(UNKNOWN_TEXT);
    r.dispose();
  });

  it('refuses the dispatch with the disabled attribute bypassed', () => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(NO_BOUNDS), { onEnterFrequency });
    typeFrequency(r.input()!, '14250000');
    bypassClick(r.btn('entry-set')!);
    expect(onEnterFrequency).not.toHaveBeenCalled();
    r.dispose();
  });

  it.each([
    [{ tuneMinHz: null, tuneMaxHz: 60000000 }],
    [{ tuneMinHz: 30000, tuneMaxHz: null }],
  ])('fails closed on a half-declared envelope %o', (bounds) => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(bounds), { onEnterFrequency });
    expect(r.input()!.disabled).toBe(true);
    typeFrequency(r.input()!, '14250000');
    bypassClick(r.btn('entry-set')!);
    expect(onEnterFrequency).not.toHaveBeenCalled();
    r.dispose();
  });
});

/* ── (e) the wrong-VFO dispatch class ───────────────────────────── */

describe('a receiver-scoped write needs a known active receiver (MOR-1322 B1 class)', () => {
  const unknownRx = (view: RadioViewModel): RadioViewModel =>
    ({ ...view, activeReceiver: { status: 'unknown' } });

  // Kills: dropping the markup gate.
  it('disables every band choice and the entry while the active receiver is unobserved', () => {
    const r = render(unknownRx(withB({ tuneMinHz: 30000, tuneMaxHz: 60000000 })));
    for (const name of ['40m', '20m', 'MW']) expect(r.btn(`choice-${name}`)!.disabled).toBe(true);
    expect(r.input()!.disabled).toBe(true);
    expect(r.btn('entry-set')!.disabled).toBe(true);
    expect(r.text('entry-reason')).toContain('active receiver not observed');
    r.dispose();
  });

  // Kills: dropping the band-select handler guard — independently of the
  // attribute, which a `.click()` assertion alone would have satisfied.
  it('refuses a band select with the disabled attribute bypassed', () => {
    const onSelectBand = vi.fn();
    const r = render(unknownRx(base()), { onSelectBand });
    bypassClick(r.btn('choice-20m')!);
    expect(onSelectBand).not.toHaveBeenCalled();
    r.dispose();
  });

  // Kills: dropping the frequency-entry handler guard.
  it('refuses a frequency entry with the disabled attribute bypassed', () => {
    const onEnterFrequency = vi.fn();
    const r = render(unknownRx(withB({ tuneMinHz: 30000, tuneMaxHz: 60000000 })),
      { onEnterFrequency });
    typeFrequency(r.input()!, '14250000');
    bypassClick(r.btn('entry-set')!);
    expect(onEnterFrequency).not.toHaveBeenCalled();
    r.dispose();
  });

  it('dispatches normally once the active receiver is observed', () => {
    const onSelectBand = vi.fn();
    const r = render(base(), { onSelectBand });
    expect(r.btn('choice-20m')!.disabled).toBe(false);
    r.btn('choice-20m')!.click();
    flushSync();
    expect(onSelectBand).toHaveBeenCalledExactlyOnceWith('20m', 14195000, 5);
    r.dispose();
  });
});

/* ── honest unknown, and the F2 standing convention ─────────────── */

describe('unknown is rendered as unknown (and F2 gets no local workaround)', () => {
  it('renders an unread current band as the unknown text, marked unobserved', () => {
    const r = render(withB({ currentBand: unreadBand() }));
    expect(r.text('current-value')).toBe(UNKNOWN_TEXT);
    expect(r.el('current')!.dataset.observed).toBe('false');
    r.dispose();
  });

  it('renders nothing for a structurally-absent current band', () => {
    const r = render(withB({
      currentBand: { reading: { status: 'unknown' }, availability: { structural: false, operational: false } },
    }));
    expect(r.el('current')).toBeNull();
    expect(r.el('tx')).not.toBeNull();
    r.dispose();
  });

  // Carry-forward 4 (F2): the permissive `topFieldAvailable` default for
  // `main.freqHz` is a standing repo convention, deliberately not special-cased
  // here. Kills: adding a local re-gate that second-guesses the fact.
  it('takes the availability flags at face value, with no local freshness workaround', () => {
    for (const forbidden of ['fieldStatus', 'topFieldAvailable', 'freqObserved', 'freshness']) {
      expect(CODE).not.toContain(forbidden);
    }
    const r = render(withB({ currentBand: knownBand('20m'), currentBandTx: 'allowed' }));
    expect(r.el('current')!.dataset.observed).toBe('true');
    expect(r.text('current-value')).toBe('20m');
    r.dispose();
  });

  it('renders no choice row at all for an empty band plan', () => {
    const r = render(withB({ bandChoices: [] }));
    expect(r.el('choices')).toBeNull();
    expect(r.el('tx')).not.toBeNull();
    r.dispose();
  });
});

/* ── keyboard entry: Enter commits via the same path as Set, Escape
   cancels without dispatch (MOR-1444) ──────────────────────────── */

describe('keyboard entry commits on Enter and cancels on Escape (MOR-1444)', () => {
  const BOUNDS = { tuneMinHz: 30000, tuneMaxHz: 60000000 } as const;

  function keydown(el: HTMLElement, key: string): void {
    el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    flushSync();
  }

  it('commits the typed frequency on Enter, exactly like clicking Set', () => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(BOUNDS), { onEnterFrequency });
    typeFrequency(r.input()!, '14250000');
    keydown(r.input()!, 'Enter');
    expect(onEnterFrequency).toHaveBeenCalledExactlyOnceWith(14250000);
    r.dispose();
  });

  // Kills: an Enter-commit path that bypasses the entryReady guard the Set
  // button already goes through (carry-forward 5 / rule 5). '29' is out of
  // range under BOTH readings (29 Hz and 29 kHz = 29000 Hz both sit below
  // `tuneMinHz`), so this stays a pure entryReady-guard pin, independent of
  // MOR-1462's unit selection.
  it('refuses to commit an out-of-range entry on Enter', () => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(BOUNDS), { onEnterFrequency });
    typeFrequency(r.input()!, '29');
    keydown(r.input()!, 'Enter');
    expect(onEnterFrequency).not.toHaveBeenCalled();
    r.dispose();
  });

  it('clears the entry on Escape without dispatching', () => {
    const onEnterFrequency = vi.fn();
    const r = render(withB(BOUNDS), { onEnterFrequency });
    typeFrequency(r.input()!, '14250000');
    keydown(r.input()!, 'Escape');
    expect(onEnterFrequency).not.toHaveBeenCalled();
    expect(r.input()!.value).toBe('');
    r.dispose();
  });

  // Round-2 review, recorder item 3: leaving focus in the (now-emptied)
  // entry input keeps it an "ignored tag" for KeyboardHandler's
  // shouldIgnoreEvent, so band hotkeys would stay suppressed until the
  // operator tabs away. Escape blurs the input so hotkeys work again
  // immediately — it does not attempt to return focus to whatever
  // originated the gesture (BandSurface has no reference to that; the
  // gesture may not even have come through the keyboard seam), so the next
  // Tab starts from the document's normal order rather than resuming where
  // the operator was. That's a documented, honest trade-off, not a claim of
  // full focus restoration.
  it('blurs the entry input on Escape so band hotkeys are no longer suppressed by it', () => {
    const r = render(withB(BOUNDS));
    const input = r.input()!;
    typeFrequency(input, '14250000');
    input.focus();
    expect(document.activeElement).toBe(input);
    keydown(input, 'Escape');
    expect(document.activeElement).not.toBe(input);
    r.dispose();
  });
});

/**
 * Round-3 review — REQUIRED PIN. preventDefault() never stops propagation:
 * before this fix, Escape in the entry input blurred it (moving
 * document.activeElement to <body>, an un-ignored tag) and then kept
 * bubbling — reaching KeyboardHandler's window-level listener with nothing
 * left to suppress it. Every rig profile SHIPS "Escape -> clear_rit_xit"
 * (rigs/_keyboard-default.toml), a REAL radio write
 * (makeRitXitHandlers().onClear()) — so cancelling a frequency entry could
 * silently clear the operator's RIT/XIT offset. The fixture below carries
 * that exact shipped binding: without it, a passing test would prove
 * nothing about whether Escape leaks past this component, since there'd be
 * nothing on the other end to (not) fire.
 *
 * This mounts the REAL KeyboardHandler alongside the REAL BandSurface (not
 * a hand-simulated stand-in for either), so a future change that drops
 * `stopPropagation()` from BandSurface's Escape branch — or reorders it
 * after the blur — fails this test via actual DOM event bubbling, the same
 * mechanism that carries the bug in production.
 */
describe('Escape does not leak past BandSurface into the window keyboard seam (MOR-1444, round-3 review)', () => {
  const BOUNDS = { tuneMinHz: 30000, tuneMaxHz: 60000000 } as const;

  const configWithClearRitXit: KeyboardConfig = {
    leaderKey: 'g',
    leaderTimeoutMs: 1000,
    altHints: true,
    helpTitle: 'Test Keyboard Help',
    bindings: [
      {
        id: 'clear-rit-xit',
        section: 'RIT/XIT',
        label: 'Clear offset',
        sequence: ['Escape'],
        action: 'clear_rit_xit',
      },
    ],
  };

  it('fires zero window-level actions and stays blurred when Escape cancels the entry', () => {
    const onAction = vi.fn();
    const khTarget = document.createElement('div');
    document.body.appendChild(khTarget);
    const kh = mount(KeyboardHandler, {
      target: khTarget,
      props: { config: configWithClearRitXit, onAction },
    });
    flushSync();

    const onEnterFrequency = vi.fn();
    const r = render(withB(BOUNDS), { onEnterFrequency });
    const input = r.input()!;
    typeFrequency(input, '14250000');
    input.focus();
    expect(document.activeElement).toBe(input);

    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    flushSync();

    // THE REGRESSION PIN: the shipped Escape -> clear_rit_xit binding must
    // never fire from a frequency-entry cancel.
    expect(onAction).not.toHaveBeenCalled();
    // The ticket's own words: "Esc cancels entry without dispatch."
    expect(onEnterFrequency).not.toHaveBeenCalled();
    // Blur is retained (round-2 fix) — Escape still un-suppresses band
    // hotkeys for whatever the operator does next.
    expect(document.activeElement).not.toBe(input);

    r.dispose();
    unmount(kh);
    khTarget.remove();
  });
});
