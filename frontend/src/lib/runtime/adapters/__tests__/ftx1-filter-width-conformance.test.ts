/**
 * MOR-1679 — FTX-1 filter-width browser conformance against CAT 2508-C
 * Table 5, on the GENERATED capabilities fixture (see
 * `fixtures/ftx1-profile.ts` for how it was produced).
 *
 * Pins the mode-resolution layer (`resolveFilterModeConfig`) and the
 * props layer (`toFilterProps`) for every FTX-1 mode name the state can
 * carry. Literal expectations only — never derived from the fixture under
 * test.
 */
import { describe, expect, it } from 'vitest';
import { validateCapabilities } from '$lib/types/capabilities';
import { resolveFilterModeConfig, toFilterProps } from '$lib/runtime/props/panel-props';
import type { ServerState } from '$lib/types/state';
import { FTX1_CAPABILITIES } from './fixtures/ftx1-profile';

const caps = validateCapabilities(FTX1_CAPABILITIES);

function ftx1State(mode: string, filterWidth: number | null) {
  const rx = {
    freqHz: 14_074_000, mode, filter: 1, dataMode: 0, filterWidth,
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 0.5, rfGain: 128,
    squelch: 0, sMeter: 0,
  };
  return {
    stateContractVersion: 1, providerGeneration: 1, active: 'MAIN',
    main: rx, sub: { ...rx },
  } as unknown as ServerState;
}

describe('MOR-1679 resolveFilterModeConfig for every FTX-1 state mode', () => {
  it('parses the generated payload: 14 filterConfig keys, width bounds 50..4000', () => {
    expect(caps.model).toBe('FTX-1');
    expect(caps.filterWidthMin).toBe(50);
    expect(caps.filterWidthMax).toBe(4000);
    expect(Object.keys(caps.filterConfig ?? {}).sort()).toEqual([
      'AM', 'AM-N', 'CW-L', 'CW-U', 'DATA-FM', 'DATA-FM-N', 'DATA-L',
      'DATA-U', 'FM', 'FM-N', 'PSK', 'RTTY-L', 'RTTY-U', 'SSB',
    ]);
  });

  // profile key each state mode resolves to; C4FM resolves to none.
  const expectedKey: ReadonlyArray<[string, string | null]> = [
    ['LSB', 'SSB'], ['USB', 'SSB'],
    ['CW-L', 'CW-L'], ['CW-U', 'CW-U'],
    ['RTTY-L', 'RTTY-L'], ['RTTY-U', 'RTTY-U'],
    ['DATA-L', 'DATA-L'], ['DATA-U', 'DATA-U'],
    ['PSK', 'PSK'],
    ['FM', 'FM'], ['FM-N', 'FM-N'],
    ['DATA-FM', 'DATA-FM'], ['DATA-FM-N', 'DATA-FM-N'],
    ['AM', 'AM'], ['AM-N', 'AM-N'],
    ['C4FM-DN', null], ['C4FM-VW', null],
  ];

  it.each(expectedKey)('%s resolves to the %s filterConfig entry', (mode, key) => {
    const resolved = resolveFilterModeConfig(caps, mode, undefined);
    if (key === null) {
      expect(resolved).toBeNull();
      return;
    }
    expect(resolved).toBe(caps.filterConfig?.[key]);
  });

  it('resolves LSB and USB to the shared SSB table: 23 entries, 300..4000', () => {
    const ssb = resolveFilterModeConfig(caps, 'LSB', undefined);
    expect(ssb?.fixed).toBe(false);
    expect(ssb?.table).toHaveLength(23);
    expect(ssb?.table?.[0]).toBe(300);
    expect(ssb?.table?.[22]).toBe(4000);
  });

  it.each(['CW-L', 'CW-U', 'DATA-L', 'DATA-U', 'RTTY-L', 'RTTY-U', 'PSK'])(
    '%s carries its own 21-entry Table 5 row: 50..4000',
    (mode) => {
      const rule = resolveFilterModeConfig(caps, mode, undefined);
      expect(rule?.fixed).toBe(false);
      expect(rule?.table).toHaveLength(21);
      expect(rule?.table?.[0]).toBe(50);
      expect(rule?.table?.[20]).toBe(4000);
    },
  );

  it.each([
    ['AM-N', 6000], ['AM', 9000], ['FM-N', 9000],
    ['DATA-FM-N', 9000], ['FM', 16000], ['DATA-FM', 16000],
  ] as const)('%s is fixed at %i Hz', (mode, hz) => {
    const rule = resolveFilterModeConfig(caps, mode, undefined);
    expect(rule?.fixed).toBe(true);
    expect(rule?.table).toEqual([hz]);
  });
});

describe('MOR-1679 toFilterProps width bounds for the FTX-1', () => {
  it.each([
    ['LSB', 300, 4000], ['USB', 300, 4000],
    ['CW-L', 50, 4000], ['DATA-U', 50, 4000],
  ] as const)('%s bounds are the table ends %i..%i, never 4500', (mode, min, max) => {
    const props = toFilterProps(ftx1State(mode, 2400), caps);
    expect(props.filterWidthMin).toBe(min);
    expect(props.filterWidthMax).toBe(max);
    expect(props.filterWidthMax).not.toBe(4500);
  });

  it.each([
    ['AM-N', 6000], ['AM', 9000], ['FM', 16000],
  ] as const)('fixed %s collapses its bounds onto the fixed width %i', (mode, hz) => {
    const props = toFilterProps(ftx1State(mode, hz), caps);
    expect(props.filterWidthMin).toBe(hz);
    expect(props.filterWidthMax).toBe(hz);
  });

  it('a null filterWidth (C4FM, code 00) reaches props as the NaN sentinel, never 0', () => {
    const props = toFilterProps(ftx1State('C4FM-DN', null), caps);
    expect(props.filterConfig).toBeNull();
    expect(props.filterWidth).toBeNaN();
  });
});
