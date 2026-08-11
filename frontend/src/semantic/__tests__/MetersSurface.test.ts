/**
 * MOR-1273 — the semantic meters surface (vocabulary slice 2B).
 *
 * SAFETY-ADJACENT. The meters are the operator's only continuous read of what
 * the transmitter is actually doing, so every test below names the mutation it
 * kills. The four BINDING carry-forwards from the slice-2A verification each
 * get their own discriminating test:
 *
 *  (1) MOR-1235 must not come back. TX relevance reaches this surface through
 *      the fact layer (`meters.rfState` / `meters.<field>.relevant`) and
 *      NOWHERE else — this component has no access to `radioState.ptt`, no TX
 *      authority prop, and no second derivation. See `describe` block 5.
 *  (2) The COMP tile is gated on the MOR-1244 `txAux.compressor` FACT, not on
 *      `meters.compression.availability` — a radio can report a compression
 *      meter while the compressor is off, and a COMP reading with the
 *      compressor off is meaningless. Block 4.
 *  (3) `relevant` is CONSUMED, never recomputed. Block 3 feeds deliberately
 *      self-inconsistent facts (an rfState that disagrees with `relevant`) so
 *      any re-derivation from `rfState` shows up as a red test.
 *  (4) The cold-start `unknown` window renders sanely and fail-closed: no
 *      flicker to RX styling while the authority has not spoken. Block 6.
 *
 * Presentation-only (v3 ADR invariant 11 / R9): this surface renders no
 * control of any kind and decides no TX state. Block 1.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import MetersSurface, { METER_BARS } from '../MetersSurface.svelte';
import { topologyFixtures, withMeters, withTxAux } from '../fixtures/topologies';
import type {
  Availability, MeterField, MeterRfState, MetersViewModel, RadioViewModel,
} from '../radio-view-model';
import { RF_LABEL, RF_MARK } from '../rx-tx-surface';
import { isAlcFault, isSwrFault } from '../../components-v2/panels/meter-utils';

/** Source scans below run over the CODE, with comments stripped — the same
 *  instrument (and the same reason) as `TxAuxSurface.test.ts`: a behavioural
 *  test cannot prove the ABSENCE of an input the component could reach for. */
const SOURCE = readFileSync('src/semantic/MetersSurface.svelte', 'utf8')
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

const AVAIL: Availability = { structural: true, operational: true };
const RF_STATES: readonly MeterRfState[] = ['receiving', 'transmitting', 'uncertain', 'unknown'];

/** Every meter field this surface can render, S first. */
type MeterKey = Exclude<keyof MetersViewModel, 'rfState'>;
const BAR_KEYS = METER_BARS.map(([field]) => field);
const ALL_KEYS: readonly MeterKey[] = ['signal', ...BAR_KEYS] as readonly MeterKey[];

/** `1/single` + a fully-observed meters group + a fully-observed txAux group,
 *  with the compressor ON so the COMP tile is reachable by default. */
function base(rfState: MeterRfState = 'receiving'): RadioViewModel {
  const view = withMeters(withTxAux(topologyFixtures['1/single']), rfState);
  return compressor(view, true);
}

/** Re-shape ONE meter field of an otherwise fully-available fixture. */
function withField(
  view: RadioViewModel, field: MeterKey,
  over: { availability?: Availability; unknown?: boolean; relevant?: boolean },
): RadioViewModel {
  const meters = view.meters!;
  const current: MeterField = meters[field];
  return {
    ...view,
    meters: {
      ...meters,
      [field]: {
        reading: over.unknown ? { status: 'unknown' } : current.reading,
        availability: over.availability ?? current.availability,
        relevant: over.relevant ?? current.relevant,
      } satisfies MeterField,
    } as MetersViewModel,
  };
}

/** Drives ONE meter field's raw KNOWN reading (MOR-1345 fault fixtures need
 *  specific SWR/ALC amplitudes `withField` was never asked to carry). */
function withRaw(view: RadioViewModel, field: MeterKey, value: number): RadioViewModel {
  const meters = view.meters!;
  const current: MeterField = meters[field];
  return {
    ...view,
    meters: {
      ...meters,
      [field]: { ...current, reading: { status: 'known', value } } satisfies MeterField,
    } as MetersViewModel,
  };
}

/** Sets the MOR-1244 `txAux.compressor` fact, or drops the whole group. */
function compressor(view: RadioViewModel, value: boolean | 'unknown' | 'no-group'): RadioViewModel {
  if (value === 'no-group') {
    const { txAux: _dropped, ...rest } = view;
    return rest as RadioViewModel;
  }
  return {
    ...view,
    txAux: {
      ...view.txAux!,
      compressor: {
        reading: value === 'unknown' ? { status: 'unknown' } : { status: 'known', value },
        availability: AVAIL,
      },
    },
  };
}

let target: HTMLDivElement;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target); });
afterEach(() => { target.remove(); });

function render(view: RadioViewModel) {
  const component = mount(MetersSurface, { target, props: { view } });
  flushSync();
  const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
  return {
    dispose: () => unmount(component),
    root: () => q('[data-testid="meters-surface"]'),
    tile: (field: string) => q<HTMLElement>(`[data-testid="meter-${field}"]`),
    tiles: () => [...target.querySelectorAll<HTMLElement>('[data-meter-tile]')]
      .map((el) => el.dataset.meter!),
    rfLabel: () => q('[data-testid="meters-rf-label"]')?.textContent?.trim() ?? null,
    rfMark: () => q('[data-testid="meters-rf-mark"]')?.textContent?.trim() ?? null,
  };
}

function withSurface(view: RadioViewModel, fn: (s: ReturnType<typeof render>) => void): void {
  const s = render(view);
  try { fn(s); } finally { s.dispose(); }
}

// ── 1. Display only (R9) + optional-group self-gating (risk R3) ────────────

describe('the meters surface is display-only and self-gates on group presence', () => {
  // MUTATION KILLED: rendering a placeholder / empty dock for a radio the
  // MOR-1269 evidence gate declined. Absent group means absent surface — the
  // S0 optional-group doctrine, same as TxAuxSurface's structural mount gate.
  it('renders NOTHING at all when the view model carries no meters group', () => {
    const bare = topologyFixtures['1/single'];
    expect(bare.meters).toBeUndefined();
    withSurface(bare, (s) => {
      expect(s.root()).toBeNull();
      expect(target.innerHTML).not.toContain('meter');
    });
  });

  it('renders the surface when the group is present', () => {
    withSurface(base(), (s) => {
      expect(s.root()).not.toBeNull();
      expect(target.querySelectorAll('[data-testid="meters-surface"]')).toHaveLength(1);
    });
  });

  // MUTATION KILLED: growing a reset/peak-clear/source-select control. R9 —
  // this surface displays; it never becomes an action surface, and it can
  // never become a TX path.
  it('renders no interactive control of any kind', () => {
    withSurface(base('transmitting'), () => {
      expect(target.querySelectorAll(
        'button, input, select, textarea, a[href], [tabindex], [role="button"], [role="switch"]',
      )).toHaveLength(0);
    });
  });

  // MUTATION KILLED: adding a `data-zone-id` here. `meters` is declarable as a
  // semantic surface after this slice, but no manifest declares a meters zone
  // — binding one would put a zone id in the DOM no layout asked for
  // (the MOR-1069 lesson, carried forward from MOR-1265).
  it('binds no zone id', () => {
    withSurface(base(), () => {
      expect(target.querySelectorAll('[data-zone-id]')).toHaveLength(0);
    });
  });
});

// ── 2. Two-level availability (MOR-977/1256) ──────────────────────────────

describe('structural availability decides whether a meter EXISTS', () => {
  // MUTATION KILLED: rendering a structurally-absent meter as an empty or
  // dimmed tile. "This radio has no SWR meter" and "the SWR meter is
  // unreadable right now" are different claims and render differently.
  it.each(ALL_KEYS)('renders no tile at all for a structurally absent "%s"', (field) => {
    const view = withField(base(), field, {
      availability: { structural: false, operational: false },
    });
    withSurface(view, (s) => {
      expect(s.tile(field)).toBeNull();
      expect(s.tiles()).not.toContain(field);
    });
  });

  it('renders every structurally present meter of a fully-observed radio', () => {
    withSurface(base(), (s) => {
      expect(s.tiles()).toEqual([...ALL_KEYS]);
    });
  });

  // MUTATION KILLED: hiding an unobserved meter (a reflow on every dropout) or
  // — far worse — drawing its gauge at zero, which reads as "0 W into the
  // antenna" when the truth is "not measured".
  it.each(ALL_KEYS)('keeps an operationally-unavailable "%s" PRESENT and marked unobserved', (field) => {
    const view = withField(base(), field, {
      availability: { structural: true, operational: false },
    });
    withSurface(view, (s) => {
      const tile = s.tile(field)!;
      expect(tile).not.toBeNull();
      expect(tile.dataset.observed).toBe('false');
      expect(tile.querySelector('svg')).toBeNull();
      expect(tile.textContent).toContain('?');
    });
  });

  it.each(ALL_KEYS)('never draws a gauge for an unobserved "%s" reading', (field) => {
    withSurface(withField(base(), field, { unknown: true }), (s) => {
      const tile = s.tile(field)!;
      expect(tile.dataset.observed).toBe('false');
      expect(tile.querySelector('svg')).toBeNull();
    });
  });

  it('draws an SVG gauge for every observed meter', () => {
    withSurface(base('transmitting'), (s) => {
      for (const field of ALL_KEYS) {
        expect(s.tile(field)!.querySelector('svg')).not.toBeNull();
      }
    });
  });
});

// ── 3. Carry-forward (3): `relevant` is CONSUMED, never re-derived ─────────

describe('relevance is read from the facts, not recomputed', () => {
  /**
   * The discriminating fixtures: `rfState` and `relevant` are made to
   * DISAGREE. Any surface that recomputes relevance from `rfState` (the
   * obvious "TX meters matter on TX" shortcut) produces the opposite answer
   * on every row below; a surface that reads the fact produces `relevant`.
   */
  it.each([
    ['receiving', 'power', true],
    ['receiving', 'swr', true],
    ['transmitting', 'signal', true],
    ['transmitting', 'power', false],
    ['unknown', 'drainVoltage', true],
    ['uncertain', 'alc', false],
  ] as const)('rfState=%s: renders "%s" with the fact\'s own relevant=%s', (rf, field, relevant) => {
    const view = withField(base(rf), field, { relevant });
    withSurface(view, (s) => {
      expect(s.tile(field)!.dataset.relevant).toBe(String(relevant));
    });
  });

  // MUTATION KILLED: `relevant = rfState !== 'receiving'` (or any variant of
  // it) computed in the surface. Every TX-gated meter is marked NOT relevant
  // here even though the radio is transmitting, because that is what the fact
  // layer said — the surface has no standing to overrule it.
  it('marks every meter irrelevant while transmitting when the facts say so', () => {
    let view = base('transmitting');
    for (const field of ALL_KEYS) view = withField(view, field, { relevant: false });
    withSurface(view, (s) => {
      for (const field of ALL_KEYS) {
        expect(s.tile(field)!.dataset.relevant).toBe('false');
      }
    });
  });

  // MUTATION KILLED: hiding irrelevant meters. Dimming keeps the dock's
  // geometry stable across an RX<->TX transition (MOR-485); hiding reflows the
  // layout under the operator's eyes at the exact moment they key up.
  it('dims rather than hides an irrelevant meter', () => {
    const view = withField(base(), 'power', { relevant: false });
    withSurface(view, (s) => {
      expect(s.tile('power')).not.toBeNull();
      expect(s.tile('power')!.dataset.relevant).toBe('false');
    });
  });
});

// ── 4. Carry-forward (2): the COMP tile is gated on txAux.compressor ───────

describe('the COMP tile is gated on the txAux compressor fact', () => {
  /** The compression METER is fully present and observed in every case here —
   *  so anything that gates on `meters.compression.availability` renders the
   *  tile in all four, and only the txAux-gated surface passes. */
  it('renders COMP when the compressor fact says it is ON', () => {
    withSurface(compressor(base(), true), (s) => {
      expect(s.tile('compression')).not.toBeNull();
    });
  });

  // MUTATION KILLED: `if (meters.compression.availability.structural)` — the
  // gate slice 2A explicitly refused. A COMP reading with the compressor off
  // is not a measurement of anything.
  it('renders no COMP tile when the compressor fact says it is OFF', () => {
    const view = compressor(base(), false);
    expect(view.meters!.compression.availability).toEqual(AVAIL);
    withSurface(view, (s) => {
      expect(s.tile('compression')).toBeNull();
      expect(s.tiles()).not.toContain('compression');
    });
  });

  // MUTATION KILLED: treating an unobserved compressor as "probably on".
  // Fail-closed: an unknown fact never enables a tile.
  it('renders no COMP tile when the compressor fact is unobserved', () => {
    withSurface(compressor(base(), 'unknown'), (s) => {
      expect(s.tile('compression')).toBeNull();
    });
  });

  // MUTATION KILLED: `view.txAux?.compressor... ?? true` — a radio with no
  // txAux group at all (the MOR-1244 evidence gate declined it) has never
  // told us the compressor is on.
  it('renders no COMP tile when the radio reports no txAux group at all', () => {
    const view = compressor(base(), 'no-group');
    expect(view.txAux).toBeUndefined();
    withSurface(view, (s) => {
      expect(s.tile('compression')).toBeNull();
      expect(s.root()).not.toBeNull();
    });
  });

  // The gate is one-directional: txAux says WHETHER, the meter fact says WHAT.
  it('still respects the meter fact once the compressor gate opens', () => {
    const view = withField(compressor(base(), true), 'compression', {
      availability: { structural: false, operational: false },
    });
    withSurface(view, (s) => {
      expect(s.tile('compression')).toBeNull();
    });
  });
});

// ── 5. Carry-forward (1): MOR-1235 stays fixed — no second TX derivation ──

describe('TX truth reaches this surface only through the fact layer (R9)', () => {
  // MUTATION KILLED: reintroducing a `ptt` read — the exact disagreement
  // MOR-1235 reported and MOR-1269 fixed one layer down. A source scan is the
  // right instrument: a behavioural test cannot prove the ABSENCE of an input
  // the component could reach for.
  it('never mentions ptt, and takes no radio-state or TX-authority prop', () => {
    expect(SOURCE).not.toMatch(/\bptt\b/i);
    expect(SOURCE).not.toMatch(/TxAuthoritySnapshot/);
    expect(SOURCE).not.toMatch(/radioState/);
    // `view` is the ONE prop. A second one would be a second TX input.
    expect(SOURCE).toMatch(/interface Props\s*\{\s*view:\s*RadioViewModel;?\s*\}/);
  });

  // MUTATION KILLED: computing an RF state locally (e.g. from txPermit, or
  // from a `tx` snapshot). The rendered RF word is `meters.rfState` mapped
  // through the SHARED `RF_LABEL`, so it cannot disagree with RxTxSurface.
  it.each(RF_STATES)('renders the shared RF label/mark for rfState=%s', (state) => {
    withSurface(base(state), (s) => {
      expect(s.root()!.dataset.rfState).toBe(state);
      expect(s.rfLabel()).toBe(RF_LABEL[state]);
      expect(s.rfMark()).toBe(RF_MARK[state]);
    });
  });

  // MUTATION KILLED: forking RF_LABEL/RF_MARK into a local copy that could
  // drift from the key button's wording.
  it('imports the RF vocabulary from rx-tx-surface rather than copying it', () => {
    expect(SOURCE).toMatch(/import\s*\{[^}]*RF_LABEL[^}]*\}\s*from\s*'\.\/rx-tx-surface'/);
    expect(SOURCE).not.toMatch(/RF_LABEL\s*(:|=)\s*\{/);
  });
});

// ── 6. Carry-forward (4): the cold-start `unknown` window ─────────────────

describe('the cold-start unknown window renders fail-closed', () => {
  /** `withMeters(..., 'unknown')` marks every TX-gated meter relevant (the
   *  adapter's own fail-closed choice: unknown is treated as "may be on"),
   *  and the S meter NOT relevant. What matters here is that the surface
   *  renders the unknown state as unknown — never as RX. */
  it('never presents an unknown RF state as receiving', () => {
    withSurface(base('unknown'), (s) => {
      expect(s.root()!.dataset.rfState).toBe('unknown');
      expect(s.rfLabel()).not.toBe(RF_LABEL.receiving);
      expect(s.rfMark()).not.toBe(RF_MARK.receiving);
      expect(target.innerHTML).not.toContain('data-rf-state="receiving"');
    });
  });

  // MUTATION KILLED: a boolean `txActive`-shaped attribute (the shipped v2
  // dock's shape, and the reason MOR-1235 was invisible) — it collapses
  // 'uncertain' and 'unknown' onto 'receiving'. Four states in, four out.
  it('keeps all four RF states distinguishable in the DOM', () => {
    const seen = RF_STATES.map((state) => {
      const s = render(base(state));
      const shown = `${s.root()!.dataset.rfState}|${s.rfMark()}|${s.rfLabel()}`;
      s.dispose();
      return shown;
    });
    expect(new Set(seen).size).toBe(RF_STATES.length);
  });

  // MUTATION KILLED: rendering the surface only once the authority is known
  // (a flash of nothing), or blanking the readings. The group's presence is
  // the only mount condition; every structurally present meter still renders.
  it('renders every meter tile while the RF state is still unknown', () => {
    withSurface(base('unknown'), (s) => {
      expect(s.tiles()).toEqual([...ALL_KEYS]);
    });
  });
});

// ── 7. Motion / forced-colors carry-overs (MOR-1249 / 1252 / 1250) ────────

describe('motion and forced-colors mechanisms are reused, not forked', () => {
  // MUTATION KILLED: a local rAF/interval ballistics loop. Smoothing and
  // peak-hold — including their `prefers-reduced-motion` behaviour — belong to
  // the reused SVG meter components (LinearSMeter / BarGauge, both driving
  // `$lib/utils/smoothing.svelte`'s `createSmoother`, which snaps instead of
  // animating under reduce). A copy here would be a second, unaudited loop.
  it('schedules no animation loop of its own', () => {
    expect(SOURCE).not.toMatch(/requestAnimationFrame|setInterval|setTimeout/);
    expect(SOURCE).not.toMatch(/createSmoother|updatePeakHold|peakHoldDisplay/);
  });

  it('delegates every gauge to the shipped SVG meter components', () => {
    expect(SOURCE).toMatch(/import BarGauge from '\.\.\/components-v2\/meters\/BarGauge\.svelte'/);
    expect(SOURCE).toMatch(/import LinearSMeter from '\.\.\/components-v2\/meters\/LinearSMeter\.svelte'/);
  });

  // MUTATION KILLED: a CSS transition/animation on the relevance dim — under
  // `prefers-reduced-motion` the cockpit's harness assertion requires that
  // NOTHING inside it animates, and a surface that transitions its own opacity
  // would break that page-level guarantee.
  it('declares no transition or animation in its own styles', () => {
    const styles = SOURCE.slice(SOURCE.indexOf('<style>'));
    expect(styles).not.toMatch(/transition|animation|@keyframes/);
  });

  // MUTATION KILLED: encoding relevance/unknown by colour alone. Under
  // forced-colors the palette is overridden, so state must survive as TEXT
  // and ATTRIBUTES (MOR-977/1250) — the RF word, the '?' placeholder and the
  // data attributes all do.
  it('encodes state as text and attributes, never colour alone', () => {
    const view = withField(base('transmitting'), 'swr', { unknown: true });
    withSurface(view, (s) => {
      expect(s.tile('swr')!.textContent).toContain('?');
      expect(s.tile('swr')!.dataset.observed).toBe('false');
      expect(s.rfLabel()).toBe(RF_LABEL.transmitting);
    });
    // The surface's own stylesheet carries structure, not a palette.
    const styles = SOURCE.slice(SOURCE.indexOf('<style>'));
    expect(styles).not.toMatch(/#[0-9a-f]{3,8}\b|\brgb\(|\bhsl\(/i);
  });
});

// ── 8. The meter table is the shipped one, not a re-derivation ────────────

describe('the meter table matches the shipped dock', () => {
  // MUTATION KILLED: inventing a new scale/formatter here. Every bar reads its
  // level and its number from `components-v2/panels/meter-utils` — the same
  // calibrated functions MetersDockPanel uses — so the two never disagree
  // about what 0.6 on the SWR meter means.
  it('covers exactly the seven meters the fact group carries', () => {
    expect([...ALL_KEYS].sort()).toEqual(
      Object.keys(withMeters(topologyFixtures['1/single']).meters!)
        .filter((k) => k !== 'rfState').sort(),
    );
  });

  it('reads its levels and formatters from the shipped meter-utils', () => {
    expect(SOURCE).toMatch(/from '\.\.\/components-v2\/panels\/meter-utils'/);
  });

  it('labels each bar with the dock\'s own short name', () => {
    expect(METER_BARS.map(([, label]) => label))
      .toEqual(['Po', 'SWR', 'ALC', 'Id', 'Vd', 'COMP']);
  });
});

// ── 9. Peak-hold channel (MOR-1282) — the surface passes it through ────────

describe('BarGauge peak channel (MOR-1282)', () => {
  // MUTATION KILLED: enabling (or dropping) the peak flag on the wrong
  // meters. Matches the dock's own peak-held set exactly (MetersDockPanel's
  // `PeakKey`) — Vd (continuous supply rail) and COMP were never peak-held
  // there either, so the surface must not invent peak-hold for them.
  it('enables the peak marker on exactly the meters the dock peak-holds (Po/SWR/ALC/Id)', () => {
    const withPeak = METER_BARS.filter(([, , , , showPeak]) => showPeak).map(([field]) => field);
    expect(withPeak).toEqual(['power', 'swr', 'alc', 'drainCurrent']);
  });

  // MUTATION KILLED: `showPeak` not reaching `<BarGauge>` (freezing it to
  // its `false` default) — the marker would never render regardless of the
  // flag table above. This mounts the REAL BarGauge (no stub), so the
  // marker's presence proves the prop actually threads through.
  it('renders a peak marker on a peak-tracked meter and none on Vd/COMP', () => {
    withSurface(base('transmitting'), (s) => {
      expect(s.tile('power')!.querySelector('[data-testid="bar-gauge-peak-marker"]')).not.toBeNull();
      expect(s.tile('swr')!.querySelector('[data-testid="bar-gauge-peak-marker"]')).not.toBeNull();
      expect(s.tile('drainVoltage')!.querySelector('[data-testid="bar-gauge-peak-marker"]')).toBeNull();
      expect(s.tile('compression')!.querySelector('[data-testid="bar-gauge-peak-marker"]')).toBeNull();
    });
  });
});

// ── 10. Same channel as the dock (MOR-1282) — no independent reimplementation ─

describe('MetersSurface and MetersDockPanel are on the same peak-hold channel', () => {
  // MUTATION KILLED: either side reimplementing the hold/decay math instead
  // of calling the shared `meter-utils` functions. `MetersSurface` itself may
  // never import them (block 7 above); `BarGauge` (which it delegates every
  // gauge to) and `MetersDockPanel` must both import the SAME functions from
  // the SAME module, so a raw sample can never decay differently depending on
  // which surface is rendering it.
  it('both BarGauge and MetersDockPanel import updatePeakHold/peakHoldDisplay from the shared meter-utils module', () => {
    const barGaugeSource = readFileSync('src/components-v2/meters/BarGauge.svelte', 'utf8');
    const dockSource = readFileSync('src/components-v2/panels/MetersDockPanel.svelte', 'utf8');
    expect(barGaugeSource).toMatch(/updatePeakHold/);
    expect(barGaugeSource).toMatch(/peakHoldDisplay/);
    expect(barGaugeSource).toMatch(/from '\.\.\/panels\/meter-utils'/);
    expect(dockSource).toMatch(/updatePeakHold/);
    expect(dockSource).toMatch(/peakHoldDisplay/);
    expect(dockSource).toMatch(/from '\.\/meter-utils'/);
  });

  // MUTATION KILLED (F1): either consumer re-declaring a local `PEAK_DECAY_MS`
  // literal instead of importing the shared one from `meter-utils` — the
  // exact duplicated-window drift the verifier proved was previously pinned
  // by NOTHING. Both must import the SAME binding; neither may shadow it.
  it('both BarGauge and MetersDockPanel import the shared PEAK_DECAY_MS instead of declaring their own', () => {
    const barGaugeSource = readFileSync('src/components-v2/meters/BarGauge.svelte', 'utf8');
    const dockSource = readFileSync('src/components-v2/panels/MetersDockPanel.svelte', 'utf8');
    expect(barGaugeSource).toMatch(/import\s*\{[^}]*PEAK_DECAY_MS[^}]*\}\s*from\s*'\.\.\/panels\/meter-utils'/);
    expect(dockSource).toMatch(/import\s*\{[^}]*PEAK_DECAY_MS[^}]*\}\s*from\s*'\.\/meter-utils'/);
    expect(barGaugeSource).not.toMatch(/const\s+PEAK_DECAY_MS\s*=/);
    expect(dockSource).not.toMatch(/const\s+PEAK_DECAY_MS\s*=/);
  });
});

// ── 11. Fault highlighting (MOR-1345) — dock's border, ported honestly ─────

// ── MOR-1451: raw sMeter must never render as a fabricated S-unit ──────────
// Live evidence: `meters.signal` carried raw CI-V byte 53 while the S meter
// rendered "S9+40" — the raw byte was being fed straight into the
// calibrated-dB-rel-S9 `LinearSMeter` contract instead of through
// `rawToDbm` first. No capability mock is installed in this file (real
// store, no calibration loaded in jsdom), so the radio here is
// UNCALIBRATED — the honest-fallback path (MOR-1451) applies: the S meter
// renders the plain raw number, never a fabricated S-unit.

describe('raw sMeter renders honestly, never a fabricated S-unit (MOR-1451)', () => {
  it('does not render S9+40 for the live-evidence raw value (53) that triggered the bug', () => {
    const view = withRaw(base(), 'signal', 53);
    withSurface(view, (s) => {
      const text = s.tile('signal')!.textContent ?? '';
      expect(text).not.toContain('S9+40');
    });
  });

  it('renders the honest raw-scale reading (53), not a fabricated S-unit, when uncalibrated', () => {
    const view = withRaw(base(), 'signal', 53);
    withSurface(view, (s) => {
      const text = s.tile('signal')!.textContent ?? '';
      expect(text).toContain('53');
    });
  });
});

describe('SWR/ALC fault highlighting reuses the dock\'s own threshold', () => {
  // MUTATION KILLED: a locally-invented threshold instead of the shared
  // predicate. Raw 80 is the dock's own "exactly SWR 2.0" boundary fixture
  // (MetersDockPanel.isolated.test.ts) — not a fault.
  it('does not fault SWR at exactly the 2.0 boundary (raw=80)', () => {
    const view = withRaw(base('transmitting'), 'swr', 80);
    withSurface(view, (s) => {
      expect(s.tile('swr')!.dataset.fault).toBe('false');
    });
  });

  it('faults SWR just above the boundary (raw=90 -> ratio 2.25)', () => {
    const view = withRaw(base('transmitting'), 'swr', 90);
    withSurface(view, (s) => {
      expect(s.tile('swr')!.dataset.fault).toBe('true');
    });
  });

  it('does not fault ALC at exactly the 90% boundary (raw=108)', () => {
    const view = withRaw(base('transmitting'), 'alc', 108);
    withSurface(view, (s) => {
      expect(s.tile('alc')!.dataset.fault).toBe('false');
    });
  });

  it('faults ALC just above the boundary (raw=115 -> 95.8%)', () => {
    const view = withRaw(base('transmitting'), 'alc', 115);
    withSurface(view, (s) => {
      expect(s.tile('alc')!.dataset.fault).toBe('true');
    });
  });

  // MUTATION KILLED (wrong channel): swapping which predicate gates which
  // field. At raw=90 the two predicates DISAGREE (isSwrFault(90)=true,
  // isAlcFault(90)=false), so a swapped FAULT_CHECKS map flips both
  // assertions below.
  it('never cross-applies the SWR predicate to ALC or vice-versa (raw=90 on both)', () => {
    const view = withRaw(withRaw(base('transmitting'), 'swr', 90), 'alc', 90);
    withSurface(view, (s) => {
      expect(s.tile('swr')!.dataset.fault).toBe('true');
      expect(s.tile('alc')!.dataset.fault).toBe('false');
    });
  });

  // MUTATION KILLED: firing fault on a field with no threshold at all
  // (Po/Id/Vd/COMP never have one — the dock never highlights them either).
  it.each(['power', 'drainCurrent', 'drainVoltage'] as const)(
    'never marks "%s" as a fault regardless of amplitude',
    (field) => {
      const view = withRaw(base('transmitting'), field, 255);
      withSurface(view, (s) => {
        expect(s.tile(field)!.dataset.fault).toBe('false');
      });
    },
  );

  // MUTATION KILLED: dropping the `relevant` gate — an over-threshold SWR
  // reading that lingers while the fact layer says the meter is NOT relevant
  // (e.g. RX) must not highlight, exactly like the dock's own TX gate.
  it('does not fault an over-threshold reading the fact layer marks irrelevant', () => {
    const view = withField(withRaw(base('transmitting'), 'swr', 120), 'swr', { relevant: false });
    withSurface(view, (s) => {
      expect(s.tile('swr')!.dataset.relevant).toBe('false');
      expect(s.tile('swr')!.dataset.fault).toBe('false');
    });
  });

  // Unknown is not a fault (omission doctrine).
  //
  // HONEST SCOPE (verify-MOR-1345): dropping the `isObserved` conjunct alone
  // does NOT go red here, and that is not a weakness in this test — it is an
  // EQUIVALENT mutant under `rawOf`'s current contract. `rawOf` returns `0`
  // for an unknown reading, `swrRatio(0)` is 1.0 and `alcLevel(0)` is 0, so
  // neither predicate can fire on an unobserved field however the conjunction
  // is written. What this test DOES kill is the dangerous combination: change
  // `rawOf`'s fallback to a hazardous value AND drop the guard, and it goes
  // red (verifier mutant M6b). The guard is therefore load-bearing the moment
  // that fallback changes — which is why the next test pins the fallback
  // itself, so the two edits can never pass independently.
  it('does not fault an unobserved (unknown) reading even when relevant', () => {
    const view = withField(base('transmitting'), 'swr', { unknown: true, relevant: true });
    withSurface(view, (s) => {
      expect(s.tile('swr')!.dataset.observed).toBe('false');
      expect(s.tile('swr')!.dataset.fault).toBe('false');
      expect(s.tile('swr')!.querySelector('svg')).toBeNull();
    });
  });

  // MUTATION KILLED (verifier mutant M6a, verify-MOR-1345): changing `rawOf`'s
  // unknown-fallback to a value either predicate would fault on. Today the
  // whole meters suite stays GREEN through such a change, because the
  // `isObserved` conjunct above absorbs it — so the fallback is the silent
  // assumption the "unknown never faults" property actually rests on, and
  // nothing else in the repo pins it. Source-scanned rather than behavioural
  // because `rawOf` is module-private to the component: a behavioural test
  // cannot observe a fallback the render path never reaches.
  it('pins rawOf\'s unknown-fallback to a value neither fault predicate fires on', () => {
    const fallback = /reading\.status === 'known' \? f\.reading\.value : (-?\d+(?:\.\d+)?)/.exec(SOURCE);
    expect(fallback, 'rawOf no longer has a numeric literal fallback').not.toBeNull();
    const raw = Number(fallback![1]);
    expect(raw).toBe(0);
    expect(isSwrFault(raw)).toBe(false);
    expect(isAlcFault(raw)).toBe(false);
  });

  // Threads the boolean through to the REAL BarGauge (no stub) — mirrors the
  // MOR-1282 peak-marker test's own "real component" discipline.
  it('threads the fault flag into the real BarGauge SVG', () => {
    const view = withRaw(base('transmitting'), 'swr', 120);
    withSurface(view, (s) => {
      const svg = s.tile('swr')!.querySelector('svg');
      expect(svg?.getAttribute('data-fault')).toBe('true');
    });
    const clean = withRaw(base('transmitting'), 'swr', 20);
    withSurface(clean, (s) => {
      const svg = s.tile('swr')!.querySelector('svg');
      expect(svg?.getAttribute('data-fault')).toBe('false');
    });
  });

  // MUTATION KILLED: either surface reimplementing the threshold instead of
  // importing the shared predicates — the same instrument as the PEAK_DECAY_MS
  // parity test above (block 10), now for `isSwrFault`/`isAlcFault`.
  it('both MetersSurface and MetersDockPanel import isSwrFault/isAlcFault from the shared meter-utils module', () => {
    const dockSource = readFileSync('src/components-v2/panels/MetersDockPanel.svelte', 'utf8');
    expect(SOURCE).toMatch(/import\s*\{[^}]*isSwrFault[^}]*\}\s*from\s*'\.\.\/components-v2\/panels\/meter-utils'/);
    expect(SOURCE).toMatch(/import\s*\{[^}]*isAlcFault[^}]*\}\s*from\s*'\.\.\/components-v2\/panels\/meter-utils'/);
    expect(dockSource).toMatch(/import\s*\{[^}]*isSwrFault[^}]*\}\s*from\s*'\.\/meter-utils'/);
    expect(dockSource).toMatch(/import\s*\{[^}]*isAlcFault[^}]*\}\s*from\s*'\.\/meter-utils'/);
    expect(SOURCE).not.toMatch(/function\s+isSwrFault|function\s+isAlcFault/);
  });

  // The base surface itself stays colour-free (MOR-977) — the highlight's
  // actual colour is drawn by BarGauge, which already owns colour.
  it('carries the fault as a boolean/attribute only — its own stylesheet stays colour-free', () => {
    const styles = SOURCE.slice(SOURCE.indexOf('<style>'));
    expect(styles).not.toMatch(/#[0-9a-f]{3,8}\b|\brgb\(|\bhsl\(/i);
  });
});
