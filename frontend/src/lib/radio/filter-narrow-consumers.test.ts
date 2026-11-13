/**
 * MOR-4434: the narrow-selected filter table through every table consumer.
 *
 * Each consumer must thread the observed narrow state into
 * `resolveFilterModeConfig`; removing any one call's narrow argument keeps
 * that consumer on the wide table while narrow is on and fails its `on`
 * row below (the call site is named per describe). Stale reads unknown, so
 * the held value can never disagree with the backend's admission hint.
 *
 * Capabilities come from the producer-shaped single-RX fixture (see
 * `filter-narrow-table.isolated.test.ts`). Python companions:
 * `tests/test_filter_width_admitted_width.py` (the filterConfig pin) and
 * `tests/web/test_narrow_width_seam.py` (the shared exchange document).
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities, FilterModeConfig } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import {
  resolveFilterModeConfig,
  toAudioSpectrumProps,
  toFilterProps,
} from '../runtime/props/panel-props';
import { toRadioViewModel } from '../runtime/adapters/radio-view-model-adapter';
import { validateRadioViewModel } from '../../semantic/radio-view-model';
import {
  snapSpectrumFilterWidth,
  toSpectrumAuthority,
} from '../runtime/adapters/scope-adapter';
import { FIXTURE_NARROW_CAPABILITIES } from '../runtime/adapters/__tests__/fixtures/fixture-narrow-profile';

const SSB_WIDE = [2400, 2600, 3000, 3200, 4000];
const SSB_NARROW = [1500, 1800, 2000];

const fresh = (storePath = 'test.path'): FieldStatus => ({
  storePath, observed: true, freshness: 'fresh', availability: 'available',
});
const stale = (storePath = 'test.path'): FieldStatus => ({
  storePath, observed: true, freshness: 'stale', availability: 'stale',
});

type NarrowCase = 'on' | 'off' | 'missing' | 'stale';

function narrowState(narrowCase: NarrowCase, width: number, caps: Capabilities): ServerState {
  const fieldStatus: Record<string, FieldStatus> = {
    active: fresh('active'),
    'main.freqHz': fresh('main.freqHz'),
    'main.mode': fresh('main.mode'),
    'main.filter': fresh('main.filter'),
    'main.filterWidth': fresh('main.filterWidth'),
  };
  const narrow = narrowCase === 'off' ? false : true;
  if (narrowCase === 'on' || narrowCase === 'off') fieldStatus['main.narrow'] = fresh('main.narrow');
  else if (narrowCase === 'stale') fieldStatus['main.narrow'] = stale('main.narrow');
  return {
    stateContractVersion: 1,
    providerGeneration: caps.providerGeneration,
    active: 'MAIN',
    split: false,
    dualWatch: false,
    ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14_074_000 },
    main: { freqHz: 14_074_000, mode: 'USB', filter: 1, dataMode: 0, filterWidth: width, narrow },
    sub: { freqHz: 7_074_000, mode: 'LSB', filter: 1, dataMode: 0 },
    fieldStatus: { ...fieldStatus, txTarget: fresh('txTarget') },
  } as unknown as ServerState;
}

const cases: Array<{
  case: NarrowCase; table: number[]; min: number; max: number;
  cfgMin: number | null; cfgMax: number | null;
}> = [
  { case: 'on', table: SSB_NARROW, min: 1500, max: 2000, cfgMin: 1500, cfgMax: 2000 },
  { case: 'off', table: SSB_WIDE, min: 2400, max: 4000, cfgMin: null, cfgMax: null },
  // Unobserved and stale both read unknown (fresh-only gate), like the backend.
  { case: 'missing', table: SSB_WIDE, min: 2400, max: 4000, cfgMin: null, cfgMax: null },
  { case: 'stale', table: SSB_WIDE, min: 2400, max: 4000, cfgMin: null, cfgMax: null },
];

describe.each(cases)('narrow $case selects the $min-$max table', ({ case: narrowCase, table, min, max, cfgMin, cfgMax }) => {
  // Fails if the consumer stops forwarding narrow (panel-props.ts:
  // activeFreshNarrow calls; radio-view-model-adapter.ts: the two
  // isFieldFreshlyObserved reads; scope-adapter.ts: the narrowFact read).
  const caps = structuredClone(FIXTURE_NARROW_CAPABILITIES) as unknown as Capabilities;
  const width = narrowCase === 'on' ? 1800 : 2600;
  const state = narrowState(narrowCase, width, caps);

  it('toFilterProps publishes the active table and bounds', () => {
    const props = toFilterProps(state, caps);

    expect(props.filterConfig?.table).toEqual(table);
    expect(props.filterWidthMin).toBe(min);
    expect(props.filterWidthMax).toBe(max);
  });

  it('toAudioSpectrumProps caps the ceiling at the active table', () => {
    expect(toAudioSpectrumProps(state, caps).filterWidthMax).toBe(max);
  });

  it('deriveModeFilter resolves the active configuration', () => {
    const view = toRadioViewModel(state, caps);
    expect(view).not.toBeNull();
    const modeFilter = validateRadioViewModel(view).modeFilter;

    expect(modeFilter?.activeFilterConfiguration?.table).toEqual(table);
    // The producer fixture declares no explicit min/maxHz on the wide SSB
    // rule, so the copied configuration carries the contract's null bounds
    // (ActiveFilterConfiguration.minHz/maxHz are nullable); only the narrow
    // selector synthesizes bounds from the narrow table endpoints. The
    // display bounds below still resolve from the table endpoints either way.
    expect(modeFilter?.activeFilterConfiguration?.minHz).toBe(cfgMin);
    expect(modeFilter?.activeFilterConfiguration?.maxHz).toBe(cfgMax);
    expect(modeFilter?.filterWidthMin.reading).toEqual({ status: 'known', value: min });
    expect(modeFilter?.filterWidthMax.reading).toEqual({ status: 'known', value: max });
  });

  it('toSpectrumAuthority selects the active rule and snapping agrees', () => {
    const authority = toSpectrumAuthority(state, caps);

    expect(authority?.rule).toMatchObject({ minHz: min, maxHz: max, values: table });
    expect(snapSpectrumFilterWidth(min + 100, authority!.rule)).toBe(table.includes(min + 100) ? min + 100 : min);
  });
});

describe('narrow without the interactive capability tag', () => {
  it('spectrum and display agree on the narrow table (P2)', () => {
    // A profile may publish narrow tables and poll get_narrow without
    // exposing the control: spectrum must still select narrow. Fails if the
    // authority reads passband.narrow (structural to the tag) instead of the
    // observed receiver state; the commit half is pinned in the intent spec.
    const caps = structuredClone(FIXTURE_NARROW_CAPABILITIES) as unknown as Capabilities;
    caps.capabilities = caps.capabilities.filter((tag) => tag !== 'narrow');
    const state = narrowState('on', 1800, caps);
    const authority = toSpectrumAuthority(state, caps);
    const props = toFilterProps(state, caps);

    expect(authority?.rule).toMatchObject({ minHz: 1500, maxHz: 2000, values: SSB_NARROW });
    expect(props.filterConfig?.table).toEqual(SSB_NARROW);
    expect(props.filterWidthMin).toBe(1500);
    expect(props.filterWidthMax).toBe(2000);
  });
});

describe('explicit wide bounds resolve from the selected table', () => {
  // A rule carrying explicit wide minHz/maxHz beside its narrow variant.
  // Fails if activeFilterTableConfig keeps the wide bounds on the narrow
  // table: display stays wide and normalizeFilterRule rejects the mismatch.
  const caps = structuredClone(FIXTURE_NARROW_CAPABILITIES) as unknown as Capabilities;
  const bounded: FilterModeConfig = {
    defaults: [2400, 1800, 500],
    fixed: false,
    minHz: 2400,
    maxHz: 4000,
    table: [...SSB_WIDE],
    narrowTable: [...SSB_NARROW],
  };
  caps.filterConfig = { ...caps.filterConfig, SSB: bounded };

  it('display, spectrum and snapping follow the narrow endpoints while narrow is on', () => {
    const state = narrowState('on', 1800, caps);
    const props = toFilterProps(state, caps);
    const authority = toSpectrumAuthority(state, caps);

    expect(props.filterConfig?.table).toEqual(SSB_NARROW);
    expect(props.filterWidthMin).toBe(1500);
    expect(props.filterWidthMax).toBe(2000);
    expect(authority?.rule).toMatchObject({ minHz: 1500, maxHz: 2000, values: SSB_NARROW });
    expect(snapSpectrumFilterWidth(1600, authority!.rule)).toBe(1500);
  });

  it('leaves radios without narrow data untouched', () => {
    expect(resolveFilterModeConfig(caps, 'FM', undefined, true)?.table).toEqual([9000]);
  });
});
