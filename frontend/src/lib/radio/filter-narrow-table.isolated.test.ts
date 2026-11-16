/**
 * MOR-4434: the narrow-selected filter table through the shared resolver.
 *
 * `resolveFilterModeConfig(caps, mode, dataMode, narrow)` publishes the
 * NARROW-selected table as `table`: observed narrow-on with a declared
 * non-empty `narrowTable` selects it, every other narrow state keeps the
 * wide table. The capabilities come from the producer-shaped fixture
 * (`../runtime/adapters/__tests__/fixtures/fixture-narrow-profile`, built
 * from `WebServer._serialize_filter_config` over the single-RX fixture);
 * the FTX-1/IC-7300 captures pin the unchanged behavior for radios without
 * narrow data.
 */
import { describe, expect, it } from 'vitest';
import { resolveFilterModeConfig } from '../runtime/props/panel-props';
import { FIXTURE_NARROW_CAPABILITIES } from '../runtime/adapters/__tests__/fixtures/fixture-narrow-profile';
import { FTX1_CAPABILITIES } from '../runtime/adapters/__tests__/fixtures/ftx1-profile';
import { IC7300_CAPABILITIES } from '../runtime/adapters/__tests__/fixtures/ic7300-profile';

const SSB_WIDE = [2400, 2600, 3000, 3200, 4000];
const SSB_NARROW = [1500, 1800, 2000];
const CW_NARROW = [200, 300, 500];

describe('resolveFilterModeConfig narrow selection', () => {
  it('selects the narrow table when narrow is observed on', () => {
    const rule = resolveFilterModeConfig(FIXTURE_NARROW_CAPABILITIES, 'USB', undefined, true);

    expect(rule?.table).toEqual(SSB_NARROW);
  });

  it('returns a copy, leaving the declared caps entry on the wide table', () => {
    const caps = structuredClone(FIXTURE_NARROW_CAPABILITIES);
    const rule = resolveFilterModeConfig(caps, 'USB', undefined, true);

    expect(rule).not.toBe(caps.filterConfig?.SSB);
    expect(rule?.table).toEqual(SSB_NARROW);
    expect(caps.filterConfig?.SSB?.table).toEqual(SSB_WIDE);
  });

  it.each([false, null, undefined])('keeps the wide table when narrow is %s', (narrow) => {
    const caps = structuredClone(FIXTURE_NARROW_CAPABILITIES);
    const rule = resolveFilterModeConfig(caps, 'USB', undefined, narrow);

    expect(rule).toBe(caps.filterConfig?.SSB);
    expect(rule?.table).toEqual(SSB_WIDE);
  });

  it('selects the narrow table for CW and through the USB->SSB fallback with a data mode', () => {
    expect(resolveFilterModeConfig(FIXTURE_NARROW_CAPABILITIES, 'CW', undefined, true)?.table)
      .toEqual(CW_NARROW);
    expect(resolveFilterModeConfig(FIXTURE_NARROW_CAPABILITIES, 'USB', 1, true)?.table)
      .toEqual(SSB_NARROW);
  });

  it('keeps the fixed FM rule untouched while narrow is on', () => {
    const caps = structuredClone(FIXTURE_NARROW_CAPABILITIES);
    const rule = resolveFilterModeConfig(caps, 'FM', undefined, true);

    expect(rule).toBe(caps.filterConfig?.FM);
    expect(rule?.table).toEqual([9000]);
  });

  it('keeps the wide table when the narrow table is empty', () => {
    const caps = structuredClone(FIXTURE_NARROW_CAPABILITIES);
    caps.filterConfig = {
      ...caps.filterConfig,
      SSB: { ...caps.filterConfig!.SSB!, narrowTable: [] },
    };

    expect(resolveFilterModeConfig(caps, 'USB', undefined, true))
      .toBe(caps.filterConfig?.SSB);
  });

  it('resolves no config for an unknown mode even with narrow on', () => {
    expect(resolveFilterModeConfig(FIXTURE_NARROW_CAPABILITIES, 'AM', undefined, true))
      .toBeNull();
  });

  it.each([
    ['FTX-1', FTX1_CAPABILITIES],
    ['IC-7300', IC7300_CAPABILITIES],
  ] as const)('leaves %s without narrow data on the wide table', (_name, caps) => {
    const clone = structuredClone(caps);
    const rule = resolveFilterModeConfig(clone, 'USB', undefined, true);

    expect(rule).not.toBeNull();
    expect(rule?.narrowTable).toBeUndefined();
    // Narrow-on resolves to the very declared entry: no swap happened.
    expect(rule).toBe(resolveFilterModeConfig(clone, 'USB', undefined, undefined));
  });
});
