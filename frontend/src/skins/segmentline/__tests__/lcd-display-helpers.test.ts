import { describe, expect, it } from 'vitest';
import type {
  DisplayOffset, DisplayTelemetry, DisplayValue, PeerSplitReceiverDisplay,
} from '../../../semantic/radio-display-model';
import {
  fftInputState,
  filterEnvelopes,
  formatBandwidth,
  formatOffset,
  meterFill,
  notchIndicators,
  stateText, telemetryText, telemetryDescription,
} from '../lcd-display-helpers';

const known = <T>(value: T): DisplayValue<T> => ({ state: 'known', value });

const receiver = {
  receiver: 'MAIN',
  label: 'VFO A',
  activity: 'active',
  operational: true,
  frequency: known(14_250_000),
  mode: known('USB'),
  filter: known('FIL1'),
  band: known('20m'),
  sMeter: known(-18),
  bandwidthHz: known(2400),
  ifShiftHz: known(0),
  pbtInnerHz: known(400),
  pbtOuterHz: known(-400),
  spectrum: 'waiting',
  dsp: {
    agc: known('MID'),
    nb: { state: 'inactive' },
    nr: { state: 'active' },
    notch: known('off'),
  },
  front: {
    preamp: known(1),
    attenuator: known(0),
    rfGain: known(0.7),
    digiSel: { state: 'unsupported' },
    ipPlus: { state: 'inactive' },
  },
} satisfies PeerSplitReceiverDisplay;

describe('LCD display helpers', () => {
  it('renders known values exactly and keeps unread slots unlit in a reserved box', () => {
    expect(stateText(known('USB'))).toBe('USB');
    // MOR-2650: an unread value is unlit segments — the empty string — never
    // '?'. The slot's box is reserved by the caller's `min-width`; the
    // helper only supplies the absence of text.
    expect(stateText({ state: 'unknown' })).toBe('');
    expect(formatBandwidth(known(2400))).toBe('2.4k');
    expect(formatBandwidth(known(500))).toBe('500');
    expect(formatBandwidth({ state: 'unknown' })).toBe('');
  });

  it('never prints a placeholder token for any value state', () => {
    const fields = [
      stateText({ state: 'unknown' }),
      stateText({ state: 'unsupported' }),
      formatBandwidth({ state: 'unknown' }),
      formatBandwidth({ state: 'unsupported' }),
      formatOffset({ state: 'unknown' }),
      formatOffset({ state: 'unsupported' }),
      formatOffset({ state: 'inactive' }),
    ];
    for (const text of fields) expect(text).not.toMatch(/[?—–]|UNKNOWN|unknown|N\/A|null|undefined|NaN/);
  });

  it.each([
    [{ state: 'active', offsetHz: 250 }, '+0.250'],
    [{ state: 'active', offsetHz: -54_500 }, '−54.500'],
    [{ state: 'inactive', offsetHz: 250 }, '+0.250'],
    // MOR-2650: a split-off rail keeps its reserved slot but draws no
    // digits — never a fabricated 0.000 or an '—' placeholder.
    [{ state: 'inactive' }, ''],
    [{ state: 'unknown' }, ''],
    [{ state: 'unsupported' }, ''],
  ] satisfies readonly (readonly [DisplayOffset, string])[])(
    'formats offset state %j truthfully',
    (field, expected) => expect(formatOffset(field)).toBe(expected),
  );

  it('reserves every census slot structurally against the widest text it can render', async () => {
    const { readFileSync } = await import('node:fs');
    const styleOf = (file: string) => {
      const source = readFileSync(`src/skins/segmentline/${file}`, 'utf8');
      return source.match(/<style>([\s\S]*?)<\/style>/)?.[1] ?? '';
    };
    // NOTE: jsdom has no layout, so the reserved box is pinned by rule, not
    // pixels: each slot's `min-width` must cover its widest text —
    // mode `DATA-FM-N` (8 glyphs + letter-spacing → 10ch floor), offset
    // `−54.500` (7ch), flag `ATT 0` (10ch floor with its label).
    expect(styleOf('CenterstageDisplay.svelte')).toMatch(/\.orbit-value\s*\{[^}]*min-width:\s*10ch/);
    expect(styleOf('PanadapterDisplay.svelte')).toContain('min-width: 10ch;');
    expect(styleOf('LcdOffsetRail.svelte')).toMatch(/\.offset-value\s*\{[^}]*min-width:\s*7ch/);
    expect(styleOf('LcdFlagRail.svelte')).toMatch(/\.status-flag\s*\{[^}]*min-width:\s*10ch/);
    // Peer/Dominant pills and facts reserve their box by structure (min-width
    // over the widest pill/fact text) — an empty string keeps the box, so
    // unread === read width without layout.
    expect(styleOf('PeerSplitDisplay.svelte')).toMatch(/\.vfo-tag,\s*\.lcd-pill\s*\{[^}]*min-width/);
    expect(styleOf('DominantUnifiedDisplay.svelte')).toMatch(/\.fact\s*\{[^}]*min-width:\s*max\(58px,\s*10ch\)/);
  });

  it('keeps unknown meters at empty geometry and clamps calibrated fill', () => {
    expect(meterFill({ state: 'unknown' })).toBe(0);
    expect(meterFill(known(-999))).toBe(0);
    expect(meterFill(known(999))).toBe(1);
  });

  it('keeps PBT envelopes distinct and refuses unknown geometry', () => {
    expect(filterEnvelopes(receiver).map(({ kind }) => kind)).toEqual(['inner', 'outer']);
    expect(filterEnvelopes({ ...receiver, pbtInnerHz: { state: 'unknown' } })).toEqual([]);
    expect(filterEnvelopes({
      ...receiver,
      pbtInnerHz: { state: 'unsupported' },
      pbtOuterHz: { state: 'unsupported' },
      ifShiftHz: { state: 'unknown' },
    })).toEqual([]);
  });

  it.each([
    ['manual', 'active', 'inactive'],
    ['auto', 'inactive', 'active'],
    ['off', 'inactive', 'inactive'],
  ] as const)('maps %s to mutually exclusive NOTCH/ANF indicators', (
    mode, notchState, anfState,
  ) => {
    expect(notchIndicators(known(mode))).toEqual({
      notch: { state: notchState },
      anf: { state: anfState },
    });
  });

  it('does not enrich unknown notch state and keeps FFT admission passive', () => {
    expect(notchIndicators({ state: 'unknown' })).toEqual({
      notch: { state: 'unknown' },
      anf: { state: 'unknown' },
    });
    expect(fftInputState(receiver, [0, 0.5, 1])).toBe('live');
    expect(fftInputState({ ...receiver, activity: 'inactive' }, [0, 0.5, 1])).toBe('live');
    expect(fftInputState({ ...receiver, spectrum: 'unsupported' }, [0, 0.5, 1]))
      .toBe('unsupported');
    expect(fftInputState(receiver, undefined)).toBe('missing');
  });
});

it.each([
  [{ state: 'known', value: 12.345, relevant: false }, '12.35'],
  [{ state: 'known', value: 0, relevant: true }, '0'],
  [{ state: 'unknown', relevant: true }, ''],
  [{ state: 'unsupported', relevant: false }, ''],
] satisfies [DisplayTelemetry, string][])('keeps legacy telemetry formatting %j (MOR-2425/MOR-2540: no reading and unsupported are an empty scale, not `?`)', (field, text) => {
  expect(telemetryText(field)).toBe(text);
});

// MOR-2705 part 2: an accessible name never carries a status word. An
// unsupported item is not drawn, so it has no accessible name; an unread
// one names only what it is — its label: no 'Unsupported', no 'No
// reading', and no 'RF relevance indeterminate' cue either.
it('names an unsupported or unread telemetry item by its label only', () => {
  expect(telemetryDescription('PWR', { state: 'unknown', relevant: true })).toBe('PWR');
  expect(telemetryDescription('PWR', { state: 'unsupported', relevant: false })).toBe('PWR');
  expect(telemetryDescription('PWR', {
    state: 'known', value: 207, relevant: true,
    txDisplay: { supported: false },
  })).toBe('PWR');
});

for (const relevance of ['idle', 'relevant', 'indeterminate'] as const)
  for (const observation of [{ state: 'current', value: 12.345 }, { state: 'current', value: 0 },
    { state: 'stale', value: 87.654 }, { state: 'unknown', reason: 'not-observed' }] as const) {
    it(`formats TX ${relevance}/${observation.state}/${'value' in observation ? observation.value : ''}`, () => {
      const field: DisplayTelemetry = { state: 'known', value: 207, relevant: true,
        txDisplay: { supported: true, relevance, observation } };
      const text = telemetryText(field);
      const description = telemetryDescription('PWR', field);
      // MOR-2425 (R29/R32): stale keeps its digits, same as current; idle
      // and never-observed collapse to an empty scale, not a glyph.
      // MOR-2540: the ' ?' indeterminate cue is gone too — digits only.
      expect(text).toBe(relevance === 'idle' ? '' : observation.state === 'unknown' ? ''
        : `${Number(observation.value.toFixed(2))}`);
      expect(description).toContain('PWR');
      expect(description).not.toMatch(/207|87.65/);
      if (relevance === 'idle') expect(description).toContain('Not measuring in receive');
      if (relevance !== 'idle' && observation.state === 'stale') expect(description).toContain('Stale observation');
      // MOR-2705 part 2 (coordinator ruling): no status word in an
      // accessible name — neither 'No reading' nor the 'RF relevance
      // indeterminate' cue ("indeterminate" means "unknown").
      expect(description).not.toContain('No reading');
      expect(description).not.toContain('indeterminate');
      // An unknown observation names only the label, the same as unread.
      if (relevance !== 'idle' && observation.state === 'unknown') expect(description).toBe('PWR');
    });
  }
