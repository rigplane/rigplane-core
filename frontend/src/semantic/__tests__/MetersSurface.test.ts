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
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import MetersSurface, { fixtureMeterAppearance } from './fixtures/StationMeterInstrumentHostFixture.svelte';
import type { MeterAppearance } from '../../../component-kit-api/src/index';
const selectedMeter = vi.hoisted(() => ({ current: undefined as MeterAppearance | undefined }));
vi.mock('../../component-kits/activation', () => ({
  getSelectedMeterAppearance: () => selectedMeter.current,
}));
import { projectBarMeters, projectSwrMeter, type BarMeterKey } from '../bar-meter-projector';
import { topologyFixtures, withMeters, withTxAux } from '../fixtures/topologies';
import type {
  Availability, MeterField, MeterRfState, MetersViewModel, MeterValueDomain, RadioViewModel,
} from '../radio-view-model';
import { RF_LABEL, RF_MARK } from '../rx-tx-surface';
import { dimColor } from '../../components-v2/meters/bar-gauge-utils';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { getDesignLanguage, registerDesignLanguage } from '../../presentation/languages/contract';

// MOR-1470: swr/alc tables for the fault-highlighting suite — fault
// predicates only fire in the calibrated engineering domain.
function makeFaultCaps(): Capabilities {
  return {
    model: 'IC-7610',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'tx'],
    receivers: 2,
    vfoScheme: 'main_sub',
    freqRanges: [{ start: 1800000, end: 30000000, label: 'HF' }],
    modes: ['USB', 'LSB', 'CW', 'AM', 'FM'],
    filters: ['FIL1', 'FIL2', 'FIL3'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] },
    webrtc: { available: true, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    meterCalibrations: {
      swr: [
        { raw: 0, actual: 1.0, label: '1.0' },
        { raw: 48, actual: 1.5, label: '1.5' },
        { raw: 80, actual: 2.0, label: '2.0' },
        { raw: 120, actual: 3.0, label: '3.0' },
      ],
      alc: [
        { raw: 0, actual: 0, label: '0' },
        { raw: 120, actual: 100, label: '100' },
      ],
    },
  };
}

/** Source scans below run over the CODE, with comments stripped — the same
 *  instrument (and the same reason) as `TxAuxSurface.test.ts`: a behavioural
 *  test cannot prove the ABSENCE of an input the component could reach for. */
const SOURCE = readFileSync('src/semantic/MetersSurface.svelte', 'utf8')
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');
const PROJECTOR_SOURCE = readFileSync('src/semantic/bar-meter-projector.ts', 'utf8');
const HOST_SOURCE = readFileSync('src/semantic/StationMeterInstrumentHost.svelte', 'utf8');
const PLACEMENT_SOURCE = readFileSync('src/semantic/StationMeterBarPlacement.svelte', 'utf8');

const AVAIL: Availability = { structural: true, operational: true };
const RF_STATES: readonly MeterRfState[] = ['receiving', 'transmitting', 'uncertain', 'unknown'];

/** Every meter field this surface can render, S first. */
type MeterKey = Exclude<keyof MetersViewModel, 'rfState'>;
const BAR_KEYS = [
  'power', 'alc', 'drainCurrent', 'drainVoltage', 'compression',
] as const satisfies readonly BarMeterKey[];

it('keeps native station readouts synchronized with projected values and availability', () => {
  const initial = base('transmitting');
  initial.meters!.drainVoltage = {
    ...initial.meters!.drainVoltage,
    reading: { status: 'known', value: 13.8 },
    domain: { kind: 'engineering', unit: 'v' },
  };
  const props: { view: RadioViewModel } = proxy({ view: initial });
  const component = mount(MetersSurface, { target, props });
  const readout = () => target.querySelector('[data-meter="drainVoltage"] .meter-native-value');
  try {
    flushSync();
    const projected = () => projectBarMeters(props.view).find(({ key }) => key === 'drainVoltage')!;
    expect(readout()?.textContent).toBe(projected().displayText);
    expect(readout()?.textContent).toBe('13.8 V');
    expect(target.querySelector('[data-meter="drainVoltage"] .meter-native-label')?.textContent).toBe('Vd');
    props.view = withRaw(props.view, 'drainVoltage', 14.2);
    flushSync();
    expect(readout()?.textContent).toBe(projected().displayText);
    props.view = withField(props.view, 'drainVoltage', { unknown: true });
    flushSync();
    expect(readout()?.textContent).toBe(projected().displayText);
    props.view = withField(props.view, 'drainVoltage', { availability: { structural: false, operational: false } });
    flushSync();
    expect(target.querySelector('[data-meter="drainVoltage"]')).toBeNull();
  } finally { unmount(component); }
});
it('keeps the SWR-only caption on its independent relevance and projected value', () => {
  let view = withField(base(), 'signal', { availability: { structural: false, operational: false }, relevant: false });
  view = withMeterDomain(withRaw(view, 'swr', 1.5), 'swr', { kind: 'engineering', unit: 'ratio' });
  view = { ...view, meters: { ...view.meters!, swr: { ...view.meters!.swr, relevant: true } } };
  withSurface(view, (surface) => {
    const caption = surface.tile('swr')!.querySelector('.meter-native-caption')!;
    expect(caption.getAttribute('data-relevant')).toBe('true');
    expect(caption.querySelector('.meter-native-label')!.textContent).toBe('SWR');
    expect(caption.querySelector('.meter-native-value')!.textContent)
      .toBe(projectSwrMeter(view)!.displayText);
    expect(surface.tile('swr')!.querySelectorAll('[data-lower-relevant] text').length).toBeGreaterThan(0);
  });
});

it('keeps paired S and SWR captions independently relevant through SWR updates', () => {
  let view = withField(base('transmitting'), 'signal', { relevant: false });
  view = withMeterDomain(withRaw(view, 'swr', 1.5), 'swr', { kind: 'engineering', unit: 'ratio' });
  view = { ...view, meters: { ...view.meters!, swr: { ...view.meters!.swr, relevant: true } } };
  const props: { view: RadioViewModel } = proxy({ view });
  const component = mount(MetersSurface, { target, props });
  const caption = (label: string) => [...target.querySelectorAll('.meter-native-caption')]
    .find((node) => node.querySelector('.meter-native-label')?.textContent === label);
  try {
    flushSync();
    expect(caption('S')?.getAttribute('data-relevant')).toBe('false');
    expect(caption('SWR')?.getAttribute('data-relevant')).toBe('true');
    expect(caption('SWR')?.querySelector('.meter-native-value')?.textContent).toBe('1.5');
    props.view = withRaw(props.view, 'swr', 2.5);
    flushSync();
    expect(caption('SWR')?.querySelector('.meter-native-value')?.textContent)
      .toBe(projectSwrMeter(props.view)!.displayText);
    props.view = withField(props.view, 'swr', { relevant: false, unknown: true });
    flushSync();
    expect(caption('SWR')?.getAttribute('data-relevant')).toBe('false');
    expect(caption('SWR')?.querySelector('.meter-native-value')?.textContent)
      .toBe(projectSwrMeter(props.view)!.displayText);
    expect(caption('S')?.getAttribute('data-relevant')).toBe('false');
    props.view = withField(props.view, 'swr', { availability: { structural: false, operational: false } });
    flushSync();
    expect(caption('SWR')).toBeUndefined();
    expect(caption('S')).toBeDefined();
  } finally { unmount(component); }
});

it('leaves selected external meter renderers free of native captions', () => {
  selectedMeter.current = fixtureMeterAppearance;
  try {
    withSurface(base(), () => {
      expect(target.querySelector('[data-fixture-signal]')).not.toBeNull();
      expect(target.querySelector('[data-fixture-level]')).not.toBeNull();
      expect(target.querySelector('.meter-native-caption')).toBeNull();
    });
  } finally { selectedMeter.current = undefined; }
});

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

function withSignalDomain(view: RadioViewModel, domain: MeterValueDomain): RadioViewModel {
  return {
    ...view,
    meters: {
      ...view.meters!,
      signal: { ...view.meters!.signal, domain },
    },
  };
}

function withMeterDomain(
  view: RadioViewModel, field: MeterKey | 'swr', domain: MeterValueDomain,
): RadioViewModel {
  return {
    ...view,
    meters: {
      ...view.meters!,
      [field]: { ...view.meters![field], domain },
    },
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
let originalMatchMedia: typeof window.matchMedia;
beforeEach(() => { target = document.createElement('div'); document.body.appendChild(target);
  originalMatchMedia = window.matchMedia; window.matchMedia = vi.fn().mockReturnValue({ matches: true }); });
afterEach(() => { target.remove(); window.matchMedia = originalMatchMedia; });

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
    // MOR-2250 (PR 2 of 2): SWR moved off its own `BarGauge` tile onto the
    // shared lower-scale row inside the `signal` tile's `LinearSMeter` — its
    // fault color and fill now read off THAT svg, not `tile('swr')` (there
    // is no such tile any more).
    signalSvg: () => target.querySelector<SVGSVGElement>('[data-testid="meter-signal"] svg'),
    lowerFillCount: () => target.querySelectorAll('[data-testid="meter-signal"] [data-lower-fill]').length,
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

  // MUTATION KILLED: adding a `data-zone-id` here. The PURE surface never
  // binds a zone id of its own — that belongs to the `zoned()` wrapper in
  // `components-v2/wiring/SemanticRadioSurfaces.svelte`, which reads the
  // active layout's plan. Binding one here would put a zone id in the DOM no
  // layout asked for (the MOR-1069 lesson, carried forward from MOR-1265),
  // and would duplicate the one `desktop-v2` already declares (MOR-1341, S5).
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

  it.each(ALL_KEYS)('keeps an operationally-unavailable "%s" PRESENT and marked unobserved', (field) => {
    const view = withField(base(), field, {
      availability: { structural: true, operational: false },
    });
    withSurface(view, (s) => {
      const tile = s.tile(field)!;
      expect(tile).not.toBeNull();
      expect(tile.dataset.observed).toBe('false');
      // The S meter ('signal') has its own vocabulary for an
      // operationally-unavailable reading and still shows its own '?'
      // placeholder.
      if (field === 'signal') {
        expect(tile.querySelectorAll('svg')).toHaveLength(1);
        expect(tile.textContent).toContain('?');
        return;
      }
      // R32: every structurally-present bar meter always draws its gauge
      // frame — an empty scale (no digits, no 'IDLE'/'?' token) when there
      // is no reading, never a hidden/placeholder span.
      expect(tile.querySelectorAll('svg')).toHaveLength(1);
      expect(tile.textContent).not.toMatch(/IDLE|\?/);
    });
  });

  it.each(ALL_KEYS)('retains supported instruments for an unobserved "%s" reading', (field) => {
    withSurface(withField(base(), field, { unknown: true }), (s) => {
      const tile = s.tile(field)!;
      expect(tile.dataset.observed).toBe('false');
      // R32: an empty scale still draws its gauge frame, not a hidden span.
      expect(tile.querySelectorAll('svg')).toHaveLength(1);
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
    // MOR-2250 (PR 2 of 2): was 'swr' — SWR no longer has its own tile (it
    // renders on the shared lower-scale row instead), so this row now
    // exercises 'compression', the remaining projected bar field this table
    // had not already covered.
    ['receiving', 'compression', true],
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
    // Only owner-hosted handles cross this presentation boundary: no raw
    // authority, runtime, view model, source, or session input is accepted.
    expect(SOURCE).toMatch(/interface Props\s*\{\s*handles:\s*StationMeterInstrumentHandles;\s*\}/);
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
  // MUTATION KILLED: a local rAF/interval ballistics loop. The persistent host
  // owns canonical signal/bar motion; the SVG meters draw passive hosted frames.
  it('schedules no animation loop of its own', () => {
    expect(SOURCE).not.toMatch(/requestAnimationFrame|setInterval|setTimeout/);
    expect(SOURCE).not.toMatch(/createSmoother|updatePeakHold|peakHoldDisplay/);
  });

  it('delegates every gauge to the shipped SVG meter components', () => {
    expect(SOURCE).toMatch(/import StationMeterBarPlacement from '\.\/StationMeterBarPlacement\.svelte'/);
    expect(PLACEMENT_SOURCE).toMatch(/import BarGauge from '\.\.\/components-v2\/meters\/BarGauge\.svelte'/);
    expect(SOURCE).toMatch(/import LinearSMeter from '\.\.\/components-v2\/meters\/LinearSMeter\.svelte'/);
  });

  it('places the shared meter seat inside the station continuation without changing the owner', () => {
    expect(SOURCE).toMatch(/import MeterRendererSeat from '\.\.\/component-kits\/MeterRendererSeat\.svelte'/);
    expect(SOURCE.match(/handles\.swr\(swr\)/g)).toHaveLength(1);
    expect(SOURCE).toMatch(/#snippet station\([\s\S]*handles\.power\(power\)[\s\S]*handles\.swr\(swr\)/);
    expect(SOURCE).not.toMatch(/getSelectedMeterAppearance/);
    expect(HOST_SOURCE).not.toMatch(/MeterRendererSeat/);
    expect(HOST_SOURCE).toMatch(/@render renderer\(signalOwner\?\.frame/);
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
  // and ATTRIBUTES (MOR-977/1250) — the RF word, the accessible no-reading
  // description (R32 dropped the visible '?' placeholder) and the data
  // attributes all do.
  it('encodes state as text and attributes, never colour alone', () => {
    // MOR-2250 (PR 2 of 2): was 'swr' — SWR no longer has its own tile, so
    // this now exercises 'alc', an unrelated projected bar field the
    // no-reading treatment applies to identically.
    const view = withField(base('transmitting'), 'alc', { unknown: true });
    withSurface(view, (s) => {
      expect(s.tile('alc')!.textContent).not.toMatch(/\?/);
      expect(s.tile('alc')!.querySelector('svg')?.getAttribute('aria-label')).toContain('No reading');
      expect(s.tile('alc')!.dataset.observed).toBe('false');
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
  // MOR-2250 (PR 2 of 2): `ALL_KEYS` (the five bar keys plus 'signal')
  // is now six, not seven — 'swr' left the standalone bar list for the shared
  // lower-scale row inside `LinearSMeter`, so it no longer has a tile of its
  // own for `ALL_KEYS`/`s.tile` purposes. It still exists as a real
  // `MeterField` in the fact group, so this test adds it back explicitly to
  // prove no field was silently dropped, rather than just shrinking the
  // expectation to match whatever the surface happens to render.
  it('covers exactly the seven meters the fact group carries (six tiled via ALL_KEYS, swr via the shared bar)', () => {
    expect(ALL_KEYS).not.toContain('swr');
    expect([...ALL_KEYS, 'swr'].sort()).toEqual(
      Object.keys(withMeters(topologyFixtures['1/single']).meters!)
        .filter((k) => k !== 'rfState').sort(),
    );
  });

  it('reads its levels and formatters from the shipped meter-utils', () => {
    expect(PROJECTOR_SOURCE).toMatch(/from '\.\.\/components-v2\/panels\/meter-utils'/);
    expect(HOST_SOURCE).toMatch(/from '\.\/bar-meter-projector'/);
  });

  // MOR-2250 (PR 2 of 2): 'SWR' dropped from the expected list — it left
  // the standalone bar list for the shared lower-scale row.
  it('labels each bar with the dock\'s own short name', () => {
    expect(projectBarMeters(base('transmitting')).map(({ label }) => label))
      .toEqual(['Po', 'ALC', 'Id', 'Vd', 'COMP']);
  });
});

// ── 9. Peak-hold channel (MOR-1282) — the surface passes it through ────────

describe('BarGauge peak channel (MOR-1282)', () => {
  it('keeps source/session private and supplies only hosted frames', () => {
    expect(SOURCE).not.toMatch(/source=|session=|showPeak=|value=\{bar\.|projection=\{/);
    expect(SOURCE).toMatch(/<LinearSMeter\s+[\s\S]*?frame=\{signalFrame\.motion\}/);
    expect(PLACEMENT_SOURCE).toMatch(/<BarGauge frame=\{frame\.motion\}/);
    expect(projectBarMeters(base('transmitting')).map(({ key }) => key)).toEqual([
      'power', 'alc', 'drainCurrent', 'drainVoltage', 'compression',
    ]);
  });

  // MUTATION KILLED: enabling (or dropping) the peak flag on the wrong
  // meters. Matches the dock's own peak-held set RESTRICTED to what
  // the five projected bars still carry post-MOR-2250 (PR 2 of 2) — SWR left this
  // table for the shared lower-scale row, and this PR does not give that row
  // a peak-hold marker (not asked for, not implemented — MetersDockPanel
  // itself, untouched, still peak-holds its own SWR bar). Vd (continuous
  // supply rail) and COMP were never peak-held there either, so the surface
  // must not invent peak-hold for them.
  it('enables the peak marker on exactly the meters the dock peak-holds (Po/ALC/Id)', () => {
    const withPeak = projectBarMeters(base('transmitting'))
      .filter(({ showPeak }) => showPeak).map(({ key }) => key);
    expect(withPeak).toEqual(['power', 'alc', 'drainCurrent']);
  });

  // MUTATION KILLED: `showPeak` not reaching `<BarGauge>` (freezing it to
  // its `false` default) — the marker would never render regardless of the
  // flag table above. This mounts the REAL BarGauge (no stub), so the
  // marker's presence proves the prop actually threads through.
  it('renders a peak marker on a peak-tracked meter and none on Vd/COMP', () => {
    withSurface(base('transmitting'), (s) => {
      expect(s.tile('power')!.querySelector('[data-testid="bar-gauge-peak-marker"]')).not.toBeNull();
      expect(s.tile('alc')!.querySelector('[data-testid="bar-gauge-peak-marker"]')).not.toBeNull();
      expect(s.tile('drainVoltage')!.querySelector('[data-testid="bar-gauge-peak-marker"]')).toBeNull();
      expect(s.tile('compression')!.querySelector('[data-testid="bar-gauge-peak-marker"]')).toBeNull();
    });
  });
});

// ── 10. Same channel as the dock (MOR-1282) — no independent reimplementation ─

describe('MetersSurface and MetersDockPanel are on the same peak-hold channel', () => {
  // MUTATION KILLED: either side reimplementing the hold/decay math instead
  // of calling the shared `meter-utils` functions. `MetersSurface` itself may
  // never import them (block 7 above); the bar-motion binding used by every
  // `BarGauge` and `MetersDockPanel` must both import the SAME functions from
  // the SAME module, so a raw sample can never decay differently depending on
  // which surface is rendering it.
  it('both bar meter motion and MetersDockPanel import updatePeakHold/peakHoldDisplay from the shared meter-utils module', () => {
    const barMotionSource = readFileSync('src/components-v2/meters/bar-meter-motion.svelte.ts', 'utf8');
    const dockSource = readFileSync('src/components-v2/panels/MetersDockPanel.svelte', 'utf8');
    expect(barMotionSource).toMatch(/updatePeakHold/);
    expect(barMotionSource).toMatch(/peakHoldDisplay/);
    expect(barMotionSource).toMatch(/from '\.\.\/panels\/meter-utils'/);
    expect(dockSource).toMatch(/updatePeakHold/);
    expect(dockSource).toMatch(/peakHoldDisplay/);
    expect(dockSource).toMatch(/from '\.\/meter-utils'/);
  });

  // MUTATION KILLED (F1): either consumer re-declaring a local `PEAK_DECAY_MS`
  // literal instead of importing the shared one from `meter-utils` — the
  // exact duplicated-window drift the verifier proved was previously pinned
  // by NOTHING. Both must import the SAME binding; neither may shadow it.
  it('both bar meter motion and MetersDockPanel import the shared PEAK_DECAY_MS instead of declaring their own', () => {
    const barMotionSource = readFileSync('src/components-v2/meters/bar-meter-motion.svelte.ts', 'utf8');
    const dockSource = readFileSync('src/components-v2/panels/MetersDockPanel.svelte', 'utf8');
    expect(barMotionSource).toMatch(/import\s*\{[^}]*PEAK_DECAY_MS[^}]*\}\s*from\s*'\.\.\/panels\/meter-utils'/);
    expect(dockSource).toMatch(/import\s*\{[^}]*PEAK_DECAY_MS[^}]*\}\s*from\s*'\.\/meter-utils'/);
    expect(barMotionSource).not.toMatch(/const\s+PEAK_DECAY_MS\s*=/);
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

describe('station signal rendering honors the explicit sample domain (MOR-2425)', () => {
  const S_METER_CAL = [
    { raw: 0, actual: -54, label: 'S0' },
    { raw: 130, actual: 0, label: 'S9' },
    { raw: 240, actual: 40, label: 'S9+40' },
  ];

  const PROBE_ZONES = [
    { end: 0.5, color: '#102030' },
    { end: 1, color: '#D0E0F0' },
  ] as const;
  const BAR_FIELDS = ['power', 'alc', 'drainCurrent', 'drainVoltage', 'compression'] as const;

  function withProbeMeterLanguage(fn: () => void): void {
    const original = getDesignLanguage('fieldline')!;
    registerDesignLanguage({
      ...original,
      renderers: {
        ...original.renderers,
        meters: () => ({
          kind: 'domain-probe-meter', segmentCount: 12, segmentGapPx: 3,
          toneBelowS9: '#112233', toneAboveS9: '#AABBCC', zones: PROBE_ZONES,
          unknown: false,
        }),
      },
    });
    document.documentElement.dataset.designLanguage = 'fieldline';
    try {
      fn();
    } finally {
      registerDesignLanguage(original);
      delete document.documentElement.dataset.designLanguage;
    }
  }

  it.each([
    [{ kind: 'raw' } as const, 53, 'uncalibrated'],
    [{ kind: 'unknown' } as const, 53, 'unit unknown'],
  ])('keeps selected bar palettes but withholds S semantics for explicit %j', (domain, value, stateText) => {
    const caps = makeFaultCaps();
    caps.meterCalibrations!.s_meter = S_METER_CAL;
    setCapabilities(caps);
    try {
      withProbeMeterLanguage(() => {
        const view = withSignalDomain(withRaw(base(), 'signal', value), domain);
        withSurface(view, (s) => {
          const signal = s.tile('signal')!;
          expect(signal.textContent).toContain(String(value));
          expect(signal.textContent).toContain(stateText);
          expect(signal.textContent).not.toMatch(/S[0-9]|dBm/);
          expect(signal.querySelectorAll('[data-main-relevant] line')).toHaveLength(0);
          expect(signal.querySelectorAll('[data-segment]')).toHaveLength(20);
          expect([...signal.attributes].some((attribute) => attribute.name.startsWith('data-dl-')))
            .toBe(false);

          for (const field of BAR_FIELDS) {
            const fills = [...s.tile(field)!.querySelectorAll('rect')]
              .map((rect) => rect.getAttribute('fill'));
            expect(fills).toContain(dimColor(PROBE_ZONES[0].color));
            expect(fills).toContain(dimColor(PROBE_ZONES[1].color));
          }
        });
      });
    } finally {
      clearCapabilities();
    }
  });

  it('retains engineering text but suppresses unsupported motion without a table', () => {
    setCapabilities(makeFaultCaps());
    try {
      const view = withSignalDomain(withRaw(base(), 'signal', -12), {
        kind: 'engineering', unit: 'db',
      });
      withSurface(view, (s) => {
        const tile = s.tile('signal')!;
        expect(tile.textContent).toContain('\u221212 dB rel S9');
        expect(tile.textContent).toContain('scale unavailable');
        expect(tile.querySelectorAll('[data-meter-fill], [data-meter-peak]')).toHaveLength(0);
        expect(tile.querySelectorAll('[data-main-relevant] line')).toHaveLength(0);
      });
    } finally {
      clearCapabilities();
    }
  });

});

describe('the host descriptor and LinearSMeter share one signal projection', () => {
  const NONUNIFORM_S_METER_CAL = [
    { raw: 0, actual: -54, label: 'S0' },
    { raw: 26, actual: -48, label: 'S1' },
    { raw: 52, actual: -36, label: 'S3' },
    { raw: 78, actual: -24, label: 'S5' },
    { raw: 103, actual: -12, label: 'S7' },
    { raw: 130, actual: 0, label: 'S9' },
    { raw: 165, actual: 10, label: 'S9+10' },
    { raw: 200, actual: 20, label: 'S9+20' },
    { raw: 240, actual: 40, label: 'S9+40' },
  ];

  const REPLACEMENT_S_METER_CAL = [
    { raw: 0, actual: -60, label: 'S0' },
    { raw: 20, actual: -50, label: 'S1' },
    { raw: 40, actual: -48, label: 'S2' },
    { raw: 90, actual: -30, label: 'S3' },
    { raw: 110, actual: -20, label: 'S5' },
    { raw: 125, actual: -10, label: 'S7' },
    { raw: 150, actual: 0, label: 'S9' },
    { raw: 175, actual: 10, label: 'S9+10' },
    { raw: 200, actual: 20, label: 'S9+20' },
    { raw: 250, actual: 40, label: 'S9+40' },
  ];

  it('projects a known nonuniform reading identically into fieldline and the real LinearSMeter, distinct from unknown', () => {
    const caps = makeFaultCaps();
    caps.meterCalibrations!.s_meter = NONUNIFORM_S_METER_CAL;
    setCapabilities(caps);
    document.documentElement.dataset.designLanguage = 'fieldline';
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = vi.fn().mockReturnValue({
      matches: true,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as typeof window.matchMedia;

    try {
      withSurface(withSignalDomain(withRaw(base(), 'signal', -48), {
        kind: 'engineering', unit: 'db',
      }), (s) => {
        const tile = s.tile('signal')!;
        expect(tile.dataset.dlUnknown).toBe('false');
        expect(tile.dataset.dlLitCount).toBe('1');
        expect(tile.querySelectorAll('[data-meter-fill]')).toHaveLength(1);
        expect(tile.textContent).toContain('\u2212121 dBm');
      });

      withSurface(withField(base(), 'signal', { unknown: true }), (s) => {
        const tile = s.tile('signal')!;
        expect(tile.dataset.dlUnknown).toBe('true');
        expect(tile.querySelectorAll('[data-meter-fill]')).toHaveLength(0);
        expect(tile.textContent).toContain('S ?');
      });
    } finally {
      clearCapabilities();
      delete document.documentElement.dataset.designLanguage;
      window.matchMedia = originalMatchMedia;
      vi.restoreAllMocks();
    }
  });

  it('refreshes descriptor, readout, fill, labels, and dense ticks coherently on a retained capability update', () => {
    const initialCaps = makeFaultCaps();
    initialCaps.meterCalibrations!.s_meter = NONUNIFORM_S_METER_CAL;
    setCapabilities(initialCaps);
    document.documentElement.dataset.designLanguage = 'fieldline';
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = vi.fn().mockReturnValue({
      matches: true,
      addEventListener: () => {},
      removeEventListener: () => {},
    }) as unknown as typeof window.matchMedia;

    const props: { view: RadioViewModel } = proxy({
      view: withSignalDomain(withRaw(base(), 'signal', -48), {
        kind: 'engineering', unit: 'db',
      }),
    });
    const component = mount(MetersSurface, { target, props });
    flushSync();
    const tickXs = () => [...target.querySelectorAll<SVGLineElement>('[data-main-relevant] line')]
      .filter((line) => line.getAttribute('y2') === '36')
      .map((line) => Number(line.getAttribute('x1')));
    const labelX = (text: string) => Number([...target.querySelectorAll<SVGTextElement>('text')]
      .find((label) => label.textContent === text)?.getAttribute('x'));

    try {
      const tile = target.querySelector<HTMLElement>('[data-testid="meter-signal"]')!;
      const retainedSvg = tile.querySelector('svg')!;
      const initialTicks = tickXs();
      const initialPlus20 = labelX('+20');
      expect(tile.dataset.dlLitCount).toBe('1');
      expect(tile.querySelectorAll('[data-meter-fill]')).toHaveLength(1);
      expect(tile.textContent).toContain('S1');

      const replacementCaps = makeFaultCaps();
      replacementCaps.meterCalibrations!.s_meter = REPLACEMENT_S_METER_CAL;
      setCapabilities(replacementCaps);
      props.view = { ...props.view };
      flushSync();

      const replacementTicks = tickXs();
      const replacementPlus20 = labelX('+20');
      const majorTickXs = [...tile.querySelectorAll<SVGLineElement>('[data-main-relevant] line')]
        .filter((line) => line.getAttribute('y1') === '24' && line.getAttribute('y2') === '36')
        .map((line) => Number(line.getAttribute('x1')));
      expect(retainedSvg.isConnected).toBe(true);
      expect(tile.querySelector('svg')).toBe(retainedSvg);
      expect(tile.dataset.dlLitCount).toBe('2');
      expect(tile.querySelectorAll('[data-meter-fill]')).toHaveLength(2);
      expect(tile.textContent).toContain('S2');
      expect(replacementTicks).not.toEqual(initialTicks);
      expect(replacementPlus20).not.toBe(initialPlus20);
      expect(majorTickXs.some((x) => Math.abs(x - replacementPlus20) < 1e-9)).toBe(true);
      expect(majorTickXs.some((x) => Math.abs(x - labelX('S9')) < 1e-9)).toBe(true);
    } finally {
      unmount(component);
      clearCapabilities();
      delete document.documentElement.dataset.designLanguage;
      window.matchMedia = originalMatchMedia;
      vi.restoreAllMocks();
    }
  });

  it('creates one shared descriptor while permissioning its S-specific consumers separately', () => {
    expect(HOST_SOURCE.match(/\bprojectSignalMeter\(/g)).toHaveLength(1);
    expect(SOURCE.match(/\brenderSlot\(/g)).toHaveLength(1);
    expect(SOURCE).toMatch(/value:\s*signalProjection\.motionFraction/);
    expect(SOURCE).toMatch(/s9:\s*signalProjection\.crossoverFraction/);
    expect(HOST_SOURCE).toMatch(/projectSignalMeter\([\s\S]*?meters\.signal\.domain/);
    expect(SOURCE).toMatch(/<LinearSMeter[\s\S]*?frame=\{signalFrame\.motion\}/);
    expect(SOURCE).toMatch(/zones=\{display\?\.display\?\.zones\}/);
    expect(SOURCE).toMatch(/display=\{signalDisplay\?\.display\s*\?\?\s*undefined\}/);
    expect(SOURCE).not.toMatch(/\bsLevel\(/);
  });
});

describe('SWR/ALC fault highlighting reuses the dock\'s own threshold', () => {
  // MOR-1470: fault predicates are only claimable in the calibrated
  // engineering domain (an uncalibrated raw byte never asserts a fault) —
  // seed swr/alc tables so the values below are ratio / normalized ALC,
  // exactly what the backend publishes for a rig with the tables.
  beforeEach(() => {
    setCapabilities(makeFaultCaps());
  });

  afterEach(() => {
    clearCapabilities();
  });

  // MOR-2250 (PR 2 of 2): SWR no longer has its own `BarGauge` tile — its
  // fault color now reads off the `signal` tile's `LinearSMeter` svg
  // (`data-lower-fault`), not `s.tile('swr')` (there is no such tile any
  // more). ALC is untouched: still its own `BarGauge` tile, same as before.
  //
  // MUTATION KILLED: a locally-invented threshold instead of the shared
  // predicate. Ratio 2.0 is the dock's own boundary fixture
  // (MetersDockPanel.isolated.test.ts) — not a fault.
  it('does not fault SWR at exactly the 2.0 boundary', () => {
    const view = withRaw(base('transmitting'), 'swr', 2.0);
    withSurface(view, (s) => {
      expect(s.signalSvg()!.getAttribute('data-lower-fault')).toBe('false');
    });
  });

  it('faults SWR just above the boundary (ratio 2.25)', () => {
    const view = withRaw(base('transmitting'), 'swr', 2.25);
    withSurface(view, (s) => {
      expect(s.signalSvg()!.getAttribute('data-lower-fault')).toBe('true');
    });
  });

  it('does not fault ALC at exactly the 90% boundary (0.9)', () => {
    const view = withRaw(base('transmitting'), 'alc', 0.9);
    withSurface(view, (s) => {
      expect(s.tile('alc')!.dataset.fault).toBe('false');
    });
  });

  it('faults ALC just above the boundary (0.95)', () => {
    const view = withRaw(base('transmitting'), 'alc', 0.95);
    withSurface(view, (s) => {
      expect(s.tile('alc')!.dataset.fault).toBe('true');
    });
  });

  // MUTATION KILLED (wrong channel): swapping which predicate gates which
  // field. The two fixtures below DISAGREE under a swapped predicate:
  // isAlcFault(1.5) clamps to 1.0 -> true (vs the correct
  // isSwrFault(1.5)=false), and isSwrFault(0.95)=false (vs the correct
  // isAlcFault(0.95)=true) — a swap flips both assertions.
  it('never cross-applies the SWR predicate to ALC or vice-versa', () => {
    const view = withRaw(withRaw(base('transmitting'), 'swr', 1.5), 'alc', 0.95);
    withSurface(view, (s) => {
      expect(s.signalSvg()!.getAttribute('data-lower-fault')).toBe('false');
      expect(s.tile('alc')!.dataset.fault).toBe('true');
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
    const view = withField(withRaw(base('transmitting'), 'swr', 3.0), 'swr', { relevant: false });
    withSurface(view, (s) => {
      expect(s.signalSvg()!.getAttribute('data-lower-fault')).toBe('false');
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
  //
  // MOR-2250 (PR 2 of 2): an unobserved `swr` no longer removes any svg —
  // the `signal` tile's `LinearSMeter` mounts on `meters.signal`'s OWN
  // observed status, unrelated to `meters.swr`. What DOES still hold, and is
  // the layout-stability property this PR adds: the lower row stays fully
  // structural (0 lit fill segments, no fault) rather than disappearing.
  it('does not fault an unobserved (unknown) SWR reading even when relevant, and still renders the lower row\'s structure with zero fill', () => {
    const view = withField(base('transmitting'), 'swr', { unknown: true, relevant: true });
    withSurface(view, (s) => {
      expect(s.signalSvg()).not.toBeNull();
      expect(s.signalSvg()!.getAttribute('data-lower-fault')).toBe('false');
      expect(s.lowerFillCount()).toBe(0);
    });
  });

  // Projection occurs in the host, so this presentation surface has no raw
  // value fallback or local fault predicate.
  it('has no surface-local raw fallback that can feed fault predicates', () => {
    expect(SOURCE).not.toMatch(/rawOf|isSwrFault|isAlcFault/);
    expect(HOST_SOURCE).not.toMatch(/isSwrFault|isAlcFault/);
  });

  // Threads the boolean through to the REAL LinearSMeter (no stub) — mirrors
  // the MOR-1282 peak-marker test's own "real component" discipline, updated
  // for MOR-2250's move off BarGauge.
  it('threads the fault flag into the real LinearSMeter svg (lower-scale row)', () => {
    const view = withRaw(base('transmitting'), 'swr', 3.0);
    withSurface(view, (s) => {
      expect(s.signalSvg()!.getAttribute('data-lower-fault')).toBe('true');
    });
    const clean = withRaw(base('transmitting'), 'swr', 1.1);
    withSurface(clean, (s) => {
      expect(s.signalSvg()!.getAttribute('data-lower-fault')).toBe('false');
    });
  });

  // MUTATION KILLED: either surface/projector reimplementing the threshold
  // instead of importing the shared predicates — the same instrument as the
  // PEAK_DECAY_MS parity test above (block 10).
  it('the shared projector and MetersDockPanel import their fault predicates from shared meter-utils', () => {
    const dockSource = readFileSync('src/components-v2/panels/MetersDockPanel.svelte', 'utf8');
    expect(HOST_SOURCE).toMatch(/projectSwrMeter/);
    expect(PROJECTOR_SOURCE).toMatch(/import\s*\{[^}]*isAlcFault[^}]*isSwrFault[^}]*\}\s*from\s*'\.\.\/components-v2\/panels\/meter-utils'/);
    expect(dockSource).toMatch(/import\s*\{[^}]*isSwrFault[^}]*\}\s*from\s*'\.\/meter-utils'/);
    expect(dockSource).toMatch(/import\s*\{[^}]*isAlcFault[^}]*\}\s*from\s*'\.\/meter-utils'/);
    expect(SOURCE).not.toMatch(/function\s+isSwrFault|function\s+isAlcFault/);
  });

  // The base surface itself stays colour-free (MOR-977) — the highlight's
  // actual colour is drawn by whichever component this file hands the
  // boolean to: `BarGauge` for ALC, `LinearSMeter`'s lower-scale row
  // (MOR-2250, PR 2 of 2) for SWR.
  it('carries the fault as a boolean/attribute only — its own stylesheet stays colour-free', () => {
    const styles = SOURCE.slice(SOURCE.indexOf('<style>'));
    expect(styles).not.toMatch(/#[0-9a-f]{3,8}\b|\brgb\(|\bhsl\(/i);
  });
});

describe('station level meters honor explicit sample domains (MOR-2425)', () => {
  it('keeps calibrated metadata from turning explicit raw ALC/SWR into physical claims', () => {
    setCapabilities(makeFaultCaps());
    try {
      let view = withRaw(base('transmitting'), 'alc', 255);
      view = withMeterDomain(view, 'alc', { kind: 'raw' });
      view = withRaw(view, 'swr', 120);
      view = withMeterDomain(view, 'swr', { kind: 'raw' });
      withSurface(view, (s) => {
        expect(s.tile('alc')!.textContent).toContain('255 raw');
        expect(s.tile('alc')!.dataset.fault).toBe('false');
        expect(s.tile('alc')!.querySelector('[data-testid="bar-gauge-peak-marker"]')).toBeNull();
        expect(s.signalSvg()!.getAttribute('data-lower-fault')).toBe('false');
        expect(s.signalSvg()!.textContent).toContain('120 raw');
        expect(s.signalSvg()!.querySelectorAll('[data-lower-tick-mark]')).toHaveLength(0);
        expect(s.lowerFillCount()).toBeGreaterThan(0);
      });
    } finally {
      clearCapabilities();
    }
  });

  it('keeps a known unknown-domain value visible but suppresses motion, fault, and peak', () => {
    setCapabilities(makeFaultCaps());
    try {
      let view = withRaw(base('transmitting'), 'power', 50);
      view = withMeterDomain(view, 'power', { kind: 'unknown' });
      view = withRaw(view, 'swr', 3);
      view = withMeterDomain(view, 'swr', { kind: 'unknown' });
      withSurface(view, (s) => {
        expect(s.tile('power')!.textContent).toContain('50 unit unknown');
        expect(s.tile('power')!.querySelectorAll('[data-gauge-fill]')).toHaveLength(0);
        expect(s.tile('power')!.querySelector('[data-testid="bar-gauge-peak-marker"]')).toBeNull();
        expect(s.signalSvg()!.textContent).toContain('3 unit unknown');
        expect(s.signalSvg()!.querySelectorAll('[data-lower-fill]')).toHaveLength(0);
        expect(s.signalSvg()!.getAttribute('data-lower-fault')).toBe('false');
      });
    } finally {
      clearCapabilities();
    }
  });

  it('keeps a usable engineering SWR scale mounted across current, stale, unknown, and idle', () => {
    setCapabilities(makeFaultCaps());
    try {
      const view = base('transmitting');
      view.meters!.swr = {
        ...view.meters!.swr,
        domain: { kind: 'engineering', unit: 'ratio' },
        display: { state: 'current', value: 2.25 },
      };
      const props: { view: RadioViewModel } = proxy({ view });
      const component = mount(MetersSurface, { target, props });
      flushSync();
      const ticks = () => target.querySelectorAll('[data-lower-tick-mark]').length;
      const fills = () => target.querySelectorAll('[data-lower-fill]').length;
      const fault = () => target.querySelector('[data-lower-fault]')?.getAttribute('data-lower-fault');
      expect(ticks()).toBe(6);
      expect(fills()).toBeGreaterThan(0);
      expect(fault()).toBe('true');

      // R29: a stale reading keeps the same fill/fault as when current —
      // only "never observed" (unknown) drops to an empty, fault-free scale.
      props.view = {
        ...props.view,
        meters: {
          ...props.view.meters!,
          swr: { ...props.view.meters!.swr, display: { state: 'stale', value: 2.25 } },
        },
      };
      flushSync();
      expect(ticks()).toBe(6);
      expect(fills()).toBeGreaterThan(0);
      expect(fault()).toBe('true');

      props.view = {
        ...props.view,
        meters: {
          ...props.view.meters!,
          swr: { ...props.view.meters!.swr, display: { state: 'unknown', reason: 'not-observed' } },
        },
      };
      flushSync();
      expect(ticks()).toBe(6);
      expect(fills()).toBe(0);
      expect(fault()).toBe('false');

      props.view = {
        ...props.view,
        meters: {
          ...props.view.meters!,
          rfState: 'receiving',
          swr: {
            ...props.view.meters!.swr,
            relevant: false,
            display: { state: 'current', value: 2.25 },
          },
        },
      };
      flushSync();
      expect(ticks()).toBe(6);
      expect(fills()).toBe(0);
      expect(fault()).toBe('false');
      unmount(component);
    } finally {
      clearCapabilities();
    }
  });

  // MUTATION KILLED: `swrLowerScale` in MetersSurface.svelte gating its
  // `stateText` (the digit readout shown in place of ticks on a non-ratio
  // scale) on `projection.state === 'current'` alone — a stale reading on
  // an uncalibrated/raw SWR domain then loses its digits while a current
  // one keeps them. R29: stale must render identically to current.
  it('keeps the SWR lower-row digits identical between current and stale on a non-ratio domain', () => {
    setCapabilities(makeFaultCaps());
    try {
      const view = base('transmitting');
      view.meters!.swr = {
        ...view.meters!.swr,
        domain: { kind: 'unknown' },
        display: { state: 'current', value: 120 },
      };
      const props: { view: RadioViewModel } = proxy({ view });
      const component = mount(MetersSurface, { target, props });
      flushSync();
      const lowerText = () => target.querySelector('[data-lower-relevant]')?.textContent ?? '';
      const currentText = lowerText();
      expect(currentText).toContain('120');

      props.view = {
        ...props.view,
        meters: {
          ...props.view.meters!,
          swr: { ...props.view.meters!.swr, display: { state: 'stale', value: 120 } },
        },
      };
      flushSync();
      expect(lowerText()).toBe(currentText);
      unmount(component);
    } finally {
      clearCapabilities();
    }
  });
});

// ── 12. MOR-2250 (PR 2 of 2) — the shared lower-scale bar ──────────────────

describe('the SWR shared lower-scale row (MOR-2250, PR 2 of 2)', () => {
  // MUTATION KILLED: SWR rendering through BOTH the old BarGauge path and
  // the new shared row (double-rendering), or through NEITHER (silently
  // dropped). It must render exactly once.
  it('swr no longer renders through the BarGauge projection path — it renders once, on the shared lower-scale row', () => {
    withSurface(base('transmitting'), (s) => {
      expect(s.tile('swr')).toBeNull();
      expect(projectBarMeters(base('transmitting')).map(({ key }) => key)).not.toContain('swr');
      expect(s.signalSvg()!.querySelector('[data-lower-row-label]')?.textContent).toBe('SWR');
    });
  });

  // MUTATION KILLED: building/passing `lowerScale` only when
  // `meters.rfState === 'transmitting'` (or any rfState-conditional guard) —
  // the exact regression the layout-stability owner ruling forbids: the
  // shared bar's bottom row must occupy the same geometry receiving or
  // transmitting. Checked in BOTH rfStates, not just TX.
  it.each(['receiving', 'transmitting'] as const)(
    'passes the lowerScale descriptor to LinearSMeter while %s, not only while transmitting',
    (rfState) => {
      withSurface(base(rfState), (s) => {
        const svg = s.signalSvg();
        expect(svg).not.toBeNull();
        expect(svg!.querySelector('[data-lower-row-label]')?.textContent).toBe('SWR');
        expect(svg!.querySelectorAll('[data-lower-segment]')).toHaveLength(20);
        expect(svg!.hasAttribute('data-lower-fault')).toBe(true);
      });
    },
  );

  // The RX/TX geometry itself must match — not just "both render something".
  it('renders the identical svg viewBox (tile height) receiving and transmitting', () => {
    // Each `withSurface` mounts into (and disposes from) the SAME shared
    // `target` div in sequence — the viewBox is read out and disposed
    // before the next mount, so the two reads can never cross-contaminate
    // by querying into each other's leftover DOM.
    let rxViewBox: string | null = null;
    let txViewBox: string | null = null;
    withSurface(base('receiving'), (s) => { rxViewBox = s.signalSvg()!.getAttribute('viewBox'); });
    withSurface(base('transmitting'), (s) => { txViewBox = s.signalSvg()!.getAttribute('viewBox'); });
    expect(rxViewBox).not.toBeNull();
    expect(rxViewBox).toBe(txViewBox);
  });
});

// ── 13. Fix cycle: SWR's own relevance dims its row, not the S-meter tile ──
//
// Replaces the pin the verifier found deleted without a replacement: before
// MOR-2250 (PR 2 of 2), `s.tile('swr')!.dataset.relevant === 'false'` covered
// this same fact through the (now-removed) standalone SWR `BarGauge` tile.
// SWR no longer has a tile of its own (block 12 above pins that), so this
// covers the fact through the shared row's own `data-lower-relevant`
// instead — set from `meters.swr.relevant`, never from `meters.signal`'s.
describe('SWR relevance drives the shared row\'s own dim (MOR-2250 fix cycle)', () => {
  // MUTATION KILLED: reading `meters.signal.relevant` (the S-meter tile's
  // own relevance) for the row instead of `meters.swr.relevant`. Under
  // `base('transmitting')`, `signal.relevant` is false and `swr.relevant`
  // is true (see `withMeters` in fixtures/topologies.ts) — a field swap
  // flips this assertion.
  it('marks the lower row NOT relevant when meters.swr.relevant is false while transmitting and observed', () => {
    const view = withField(base('transmitting'), 'swr', { relevant: false });
    withSurface(view, (s) => {
      const group = s.signalSvg()!.querySelector('[data-lower-relevant]');
      expect(group).not.toBeNull();
      expect(group!.getAttribute('data-lower-relevant')).toBe('false');
    });
  });

  it('marks the lower row relevant when meters.swr.relevant is true while transmitting and observed', () => {
    const view = withField(base('transmitting'), 'swr', { relevant: true });
    withSurface(view, (s) => {
      const group = s.signalSvg()!.querySelector('[data-lower-relevant]');
      expect(group).not.toBeNull();
      expect(group!.getAttribute('data-lower-relevant')).toBe('true');
    });
  });
});

// ── 14. Fix cycle 2: the main bar and the SWR row dim through independent, ──
//        non-compounding sibling groups (verifier-diagnosed compounding bug)
//
// The verifier proved (pixel measurement on real baseline PNGs, plus a
// compiled-CSS-selector check) that nesting the SWR row's own `<g opacity>`
// INSIDE the S-meter tile — while that tile ALSO carried the shared
// `.meter-tile[data-relevant='false'] { opacity: 0.4 }` rule — composes
// multiplicatively: RX landed at tile(1.0) * row(0.4) = 0.4, TX landed at
// tile(0.4) * row(1.0) = 0.4. The row could never reach full opacity in ANY
// state, in both the pre-fix code AND the first fix attempt (ae09a6b1),
// whose own tests asserted each group's attribute correctly IN ISOLATION —
// each group's own `opacity` attribute was already right in ae09a6b1, so an
// attribute-only test stays green through the still-broken composed result;
// only the composed/effective state (CSS on an ancestor PLUS opacity on a
// descendant) exposes the bug, and that is the state proving this fix cycle
// covers.
//
// jsdom does not apply a mounted Svelte component's own scoped `<style>`
// block to `getComputedStyle` (confirmed empirically here, and already
// documented as a known limitation by the F4 pattern in
// `mor1536-armed-adoption.test.ts`), so block (A) below injects the file's
// REAL `<style>` text as a literal `<style>` tag — the same technique — to
// get a genuine cascade computation rather than reading one group's own
// attribute in isolation.
describe('main-bar and SWR-row opacity are independent, non-compounding channels (MOR-2250 fix cycle 2)', () => {
  // ── (A) The composed/effective state: does the shared ancestor CSS rule ──
  //     actually reach the S-meter tile? This is the one jsdom CAN answer
  //     with a genuine cascade, because it is a real CSS rule (unlike the
  //     `<g opacity>` SVG presentation attributes below, which jsdom's
  //     computed style does not resolve at all — verified empirically).
  describe('the shared .meter-tile CSS rule (real cascade, F4-pattern injection)', () => {
    let styleEl: HTMLStyleElement;
    beforeEach(() => {
      const raw = readFileSync('src/semantic/MetersSurface.svelte', 'utf8');
      const start = raw.indexOf('<style>') + '<style>'.length;
      const end = raw.indexOf('</style>');
      styleEl = document.createElement('style');
      styleEl.textContent = raw.slice(start, end);
      document.head.appendChild(styleEl);
    });
    afterEach(() => { styleEl.remove(); });

    // Positive control: an ordinary bar tile (not excluded) still dims from
    // this rule — proves the injected CSS and selector matching are real,
    // so the negative result below is not just "nothing ever matches".
    it('dims an ordinary irrelevant bar tile (power) through the shared rule', () => {
      const view = withField(base(), 'power', { relevant: false });
      withSurface(view, (s) => {
        expect(s.tile('power')!.dataset.relevant).toBe('false');
        expect(getComputedStyle(s.tile('power')!).opacity).toBe('0.4');
      });
    });

    // MUTATION KILLED: dropping the whole
    // `:not([data-meter='signal'][data-observed='true'])` qualifier from the
    // CSS selector — the OBSERVED S-meter tile's computed opacity becomes
    // '0.4' again, reinstating the ancestor half of the compounding bug.
    // Verified by hand: with the qualifier removed, this assertion observes
    // '0.4' instead of the empty string below.
    it('does not dim the OBSERVED S-meter tile through the shared rule any more, even though data-relevant is false', () => {
      withSurface(base('transmitting'), (s) => {
        const tile = s.tile('signal')!;
        expect(tile.dataset.relevant).toBe('false'); // block 3's pin still holds: the fact reaches the DOM
        expect(tile.dataset.observed).toBe('true'); // `LinearSMeter` is mounted, so it owns the dim
        expect(getComputedStyle(tile).opacity).not.toBe('0.4');
      });
    });

    it('keeps the unavailable main row dim independent of the live lower row', () => {
      const view = withField(base('transmitting'), 'signal', { unknown: true, relevant: false });
      withSurface(view, (s) => {
        const tile = s.tile('signal')!;
        expect(tile.dataset.relevant).toBe('false');
        expect(tile.dataset.observed).toBe('false');
        expect(tile.querySelectorAll('svg')).toHaveLength(1);
        expect(tile.textContent).toContain('S ?');
        expect(getComputedStyle(tile).opacity).not.toBe('0.4');
        expect(tile.querySelector('[data-main-relevant]')?.getAttribute('opacity')).toBe('0.4');
        expect(tile.querySelector('[data-lower-relevant]')?.getAttribute('opacity')).toBe('1');
      });
    });
  });

  // ── (B) Each group's own opacity attribute reads off the CORRECT fact ───
  //     (`getAttribute`, not `getComputedStyle` — jsdom does not resolve SVG
  //     presentation attributes into computed style, verified empirically:
  //     `getComputedStyle(g).opacity` returns '' for every `<g opacity=...>`
  //     here regardless of the actual attribute).  Combined with (C) below
  //     (no main-bar group contains the lower row, or vice versa), a correct
  //     value on each group's OWN attribute plus no nesting is the full
  //     non-compounding guarantee: nothing else remains that COULD multiply
  //     them.
  //
  // MUTATION KILLED: feeding `meters.swr.relevant` into the main-bar group
  // or `meters.signal.relevant` into the lower-row group (a field swap) —
  // `base('transmitting')` gives them opposite values (signal.relevant=
  // false, swr.relevant=true; `withMeters` in fixtures/topologies.ts), so a
  // swap flips both assertions below.
  it('drives each group\'s own opacity attribute from its own fact, never the other one\'s', () => {
    const view = base('transmitting');
    expect(view.meters!.signal.relevant).toBe(false);
    expect(view.meters!.swr.relevant).toBe(true);
    withSurface(view, (s) => {
      const mainGroups = [...s.signalSvg()!.querySelectorAll('[data-main-relevant]')];
      const lowerGroup = s.signalSvg()!.querySelector('[data-lower-relevant]')!;
      expect(mainGroups).toHaveLength(2);
      for (const mainGroup of mainGroups) {
        expect(mainGroup.getAttribute('data-main-relevant')).toBe('false');
        expect(mainGroup.getAttribute('opacity')).toBe('0.4');
      }
      expect(lowerGroup.getAttribute('data-lower-relevant')).toBe('true');
      expect(lowerGroup.getAttribute('opacity')).toBe('1');
    });
  });

  it('reverses correctly while receiving (the complementary fact assignment)', () => {
    const view = base('receiving');
    expect(view.meters!.signal.relevant).toBe(true);
    expect(view.meters!.swr.relevant).toBe(false);
    withSurface(view, (s) => {
      const mainGroups = [...s.signalSvg()!.querySelectorAll('[data-main-relevant]')];
      const lowerGroup = s.signalSvg()!.querySelector('[data-lower-relevant]')!;
      expect(mainGroups).toHaveLength(2);
      for (const mainGroup of mainGroups) expect(mainGroup.getAttribute('opacity')).toBe('1');
      expect(lowerGroup.getAttribute('opacity')).toBe('0.4');
    });
  });

  // ── (C) Structural guarantee: neither group is an ancestor of the other ──
  // `LinearSMeter` emits the main-bar content as TWO `<g data-main-relevant>`
  // groups, split only because `{#if lowerScale}` sits between them in the
  // markup. Fix cycle 4 (F2): this test checks BOTH of them — a `querySelector`
  // (singular) reads only the first, so nesting the lower row inside the
  // SECOND main group reinstates the compounding bug undetected.
  //
  // MUTATION KILLED: moving the lower-row `<g data-lower-relevant>` inside
  // the SECOND `<g data-main-relevant>` in `LinearSMeter.svelte` — the count
  // assertion still passes (still two main groups) but the second group's
  // `contains(lowerGroup)` becomes true. Confirmed by hand that this
  // mutation left the pre-fix, `querySelector`-based version of this test
  // green.
  it('renders every data-main-relevant group as a sibling of data-lower-relevant, neither containing the other', () => {
    withSurface(base('transmitting'), (s) => {
      const mainGroups = [...s.signalSvg()!.querySelectorAll('[data-main-relevant]')];
      const lowerGroup = s.signalSvg()!.querySelector('[data-lower-relevant]')!;
      expect(mainGroups).toHaveLength(2);
      for (const mainGroup of mainGroups) {
        expect(mainGroup.contains(lowerGroup)).toBe(false);
        expect(lowerGroup.contains(mainGroup)).toBe(false);
      }
    });
  });
});

const TX_KEYS = ['power', 'alc', 'swr'] as const;
describe('persistent TX instruments', () => {
  for (const structural of [false, true]) for (const rf of RF_STATES)
    for (const relevant of [false, true]) for (const state of ['current', 'stale', 'unknown'] as const) {
      it(`${structural}/${rf}/${relevant}/${state}`, () => {
        const view = base(rf);
        for (const key of TX_KEYS) view.meters![key] = {
          availability: { structural, operational: state === 'current' }, relevant,
          reading: { status: 'known', value: 170 },
          display: state === 'unknown' ? { state, reason: 'not-observed' } : { state, value: 170 },
        };
        withSurface(view, () => {
          const idle = rf === 'receiving' && !relevant;
          const indeterminate = !idle && !(rf === 'transmitting' && relevant);
          for (const key of TX_KEYS) {
            const el = target.querySelector(key === 'swr' ? '[data-lower-relevant]' : `[data-meter="${key}"] svg`);
            expect(!!el).toBe(structural);
            if (!el) continue;
            const text = el.textContent ?? '';
            const description = el.getAttribute('aria-label') ?? '';
            // R29/R32: a stale reading keeps its digits and fill, same as a
            // current one — idle and never-observed are the only empty-scale
            // states, and neither shows a placeholder text token.
            if (idle) {
              expect(text).not.toMatch(/IDLE|170/);
              expect(description).toContain('Not measuring in receive');
              expect(description).not.toMatch(/170|\?/);
              expect(el.querySelectorAll(key === 'swr' ? '[data-lower-fill]' : '[data-gauge-fill]')).toHaveLength(0);
              expect(el.getAttribute('data-fault')).not.toBe('true');
            } else if (state === 'unknown') {
              expect(text).not.toMatch(/IDLE|170|\?/);
              expect(description).not.toContain('170');
              expect(el.querySelectorAll(key === 'swr' ? '[data-lower-fill]' : '[data-gauge-fill]')).toHaveLength(0);
              expect(el.getAttribute('data-fault')).not.toBe('true');
            } else {
              // current or stale (R29): the retained value renders
              // identically either way. This fixture leaves the SWR
              // `domain` unset, which `hasSwrRatioScale` treats as a ratio
              // scale (ticks, not raw digits) — see the non-ratio SWR test
              // above for the digit case — so the digit check here is
              // power/alc only.
              if (key !== 'swr') expect(text).toContain('170');
              expect(el.querySelectorAll(key === 'swr' ? '[data-lower-fill]' : '[data-gauge-fill]').length).toBeGreaterThan(0);
              if (indeterminate) expect(description).toContain('RF relevance indeterminate');
            }
          }
        });
      });
    }
  it.each([false, true])('SWR survives unavailable S (structurally absent=%s)', (absent) => {
    const view = withField(base('transmitting'), 'signal', {
      unknown: true, availability: { structural: !absent, operational: false },
    });
    withSurface(view, () => {
      expect(target.querySelectorAll('[data-lower-tick-mark]')).toHaveLength(6);
      expect(target.querySelectorAll('[data-lower-fill]').length).toBeGreaterThan(0);
      const svg = target.querySelector('[data-lower-fault]')!;
      expect(svg.textContent).not.toMatch(/dBm|uncalibrated/);
      expect(svg.querySelectorAll('[data-main-relevant]')).toHaveLength(absent ? 0 : 2);
    });
  });
  it('keeps SVG, tracks and ticks through retained TX → RX → smaller TX without a frame', () => {
    const props: { view: RadioViewModel } = proxy({ view: base('transmitting') });
    for (const key of TX_KEYS) props.view = withRaw(props.view, key, 200);
    const component = mount(MetersSurface, { target, props });
    flushSync();
    const nodes = [...target.querySelectorAll('svg, [data-lower-tick-mark], [data-lower-segment], [data-gauge-track]')];
    props.view = { ...props.view, meters: { ...props.view.meters!, rfState: 'receiving' } };
    for (const key of TX_KEYS) props.view = withField(props.view, key, { relevant: false });
    flushSync();
    expect(nodes.every((node) => node.isConnected)).toBe(true);
    expect(target.querySelectorAll('[data-meter="power"] [data-gauge-fill], [data-meter="alc"] [data-gauge-fill], [data-lower-fill], [data-meter="power"] [data-testid="bar-gauge-peak-marker"], [data-meter="alc"] [data-testid="bar-gauge-peak-marker"]')).toHaveLength(0);
    for (const key of ['power', 'alc']) expect(target.querySelector(`[data-meter="${key}"]`)?.textContent).not.toMatch(/200|\?/);
    props.view = { ...props.view, meters: { ...props.view.meters!, rfState: 'transmitting' } };
    for (const key of TX_KEYS) props.view = withRaw(withField(props.view, key, { relevant: true }), key, 10);
    flushSync();
    expect(nodes.every((node) => node.isConnected)).toBe(true);
    for (const marker of target.querySelectorAll('[data-meter="power"] [data-testid="bar-gauge-peak-marker"], [data-meter="alc"] [data-testid="bar-gauge-peak-marker"]')) {
      expect(Number(marker.getAttribute('x'))).toBeLessThan(100);
    }
    unmount(component);
  });
});

it('uses current display calibration while preserving VD/ID/COMP through RX idle', () => {
  const caps = makeFaultCaps();
  caps.meterCalibrations!.power = [{ raw: 0, actual: 0, label: '0' }, { raw: 255, actual: 200, label: '200' }];
  setCapabilities(caps);
  const originalMatchMedia = window.matchMedia;
  window.matchMedia = vi.fn().mockReturnValue({ matches: true });
  try {
    const view = base('transmitting');
    view.meters!.power = { ...view.meters!.power, reading: { status: 'unknown' },
      display: { state: 'current', value: 50 } };
    const props: { view: RadioViewModel } = proxy({ view });
    const component = mount(MetersSurface, { target, props });
    flushSync();
    try {
      const projected = projectBarMeters(view).find(({ key }) => key === 'power')!;
      expect(projected).toMatchObject({ motionFraction: 0.25, displayText: '50W', gauge: true });
      expect(target.querySelector('[data-meter="power"]')?.textContent)
        .toContain(projected.displayText);
      expect(target.querySelectorAll('[data-meter="power"] [data-gauge-fill]'))
        .toHaveLength(Math.ceil(projected.motionFraction! * 10));
      const other = ['drainVoltage', 'drainCurrent', 'compression'].map((key) =>
        target.querySelector(`[data-meter="${key}"]`)!.outerHTML);
      props.view = { ...props.view, meters: { ...props.view.meters!, rfState: 'receiving',
        power: { ...props.view.meters!.power, relevant: false } } };
      flushSync();
      expect(target.querySelector('[data-meter="power"]')?.textContent).not.toContain('IDLE');
      expect(target.querySelector('[data-meter="power"] svg')?.getAttribute('aria-label'))
        .toContain('Not measuring in receive');
      expect(['drainVoltage', 'drainCurrent', 'compression'].map((key) =>
        target.querySelector(`[data-meter="${key}"]`)!.outerHTML)).toEqual(other);
    } finally { unmount(component); }
  } finally { clearCapabilities(); window.matchMedia = originalMatchMedia; vi.restoreAllMocks(); }
});

it('a current calibrated zero remains a measurement, distinct from RX idle', () => {
  const caps = makeFaultCaps();
  caps.meterCalibrations!.power = [{ raw: 0, actual: 0, label: '0' }, { raw: 255, actual: 100, label: '100' }];
  setCapabilities(caps);
  try {
    const view = base('transmitting');
    view.meters!.power = { ...view.meters!.power, reading: { status: 'unknown' }, display: { state: 'current', value: 0 } };
    withSurface(view, (s) => {
      expect(s.tile('power')?.textContent).toContain('0W');
      expect(s.tile('power')?.textContent).not.toContain('IDLE');
      expect(s.tile('power')?.querySelector('svg')?.getAttribute('aria-label')).toContain('Observed. 0W');
    });
  } finally { clearCapabilities(); }
});


describe('station-local presence selection', () => {
  it.each([
    ['present', 'unavailable'], ['unavailable', 'present'], ['unavailable', 'unavailable'],
    ['present', 'present'], ['absent', 'present'],
  ] as const)('keeps signal=%s and SWR=%s independently', (signal, swr) => {
    const view = base();
    for (const [key, presence] of [['signal', signal], ['swr', swr]] as const) {
      view.meters![key] = { ...view.meters![key], presence,
        availability: { structural: presence !== 'absent', operational: false },
        reading: { status: 'unknown' } };
    }
    render(view);
    expect(target.querySelector('[data-testid="meter-signal"]') !== null).toBe(signal === 'present');
    expect(target.querySelector('[data-testid="meter-swr"]') !== null)
      .toBe(signal !== 'present' && swr === 'present');
    const captions = [...target.querySelectorAll('.meter-native-label')].map(n => n.textContent);
    expect(captions.includes('S')).toBe(signal === 'present');
    expect(captions.includes('SWR')).toBe(swr === 'present');
    expect(target.querySelector('[data-lower-relevant]') !== null).toBe(swr === 'present');
  });
  it('omits unavailable level seats while preserving declared missing empty seats', () => {
    const view = base();
    for (const key of BAR_KEYS) view.meters![key] = { ...view.meters![key],
      presence: 'unavailable', availability: { structural: true, operational: false },
      reading: { status: 'unknown' } };
    render(view);
    for (const key of BAR_KEYS) expect(target.querySelector('[data-testid="meter-' + key + '"]')).toBeNull();
  });
});
