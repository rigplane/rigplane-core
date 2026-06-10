import { describe, it, expect } from 'vitest';

import {
  MOD_INPUT_SOURCES,
  modInputCommand,
  modInputSourceLabel,
  modInputStateKey,
} from './mod-input';

describe('MOD_INPUT_SOURCES (MOR-616)', () => {
  it('lists the six IC-7610 sources in enum order', () => {
    expect(MOD_INPUT_SOURCES.map((option) => option.value)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(MOD_INPUT_SOURCES.map((option) => option.label)).toEqual([
      'MIC',
      'ACC',
      'MIC+ACC',
      'USB',
      'MIC+USB',
      'LAN',
    ]);
  });
});

describe('modInputSourceLabel', () => {
  it('maps enum values to human labels', () => {
    expect(modInputSourceLabel(0)).toBe('MIC');
    expect(modInputSourceLabel(5)).toBe('LAN');
  });

  it('returns null for unknown or unread values', () => {
    expect(modInputSourceLabel(null)).toBeNull();
    expect(modInputSourceLabel(undefined)).toBeNull();
    expect(modInputSourceLabel(99)).toBeNull();
  });
});

describe('modInputStateKey / modInputCommand', () => {
  it('maps each DATA group to its state key and set command', () => {
    expect(modInputStateKey(0)).toBe('dataOffModInput');
    expect(modInputStateKey(1)).toBe('data1ModInput');
    expect(modInputStateKey(2)).toBe('data2ModInput');
    expect(modInputStateKey(3)).toBe('data3ModInput');

    expect(modInputCommand(0)).toBe('set_data_off_mod_input');
    expect(modInputCommand(1)).toBe('set_data1_mod_input');
    expect(modInputCommand(2)).toBe('set_data2_mod_input');
    expect(modInputCommand(3)).toBe('set_data3_mod_input');
  });

  it('falls back to the DATA OFF group for out-of-range data modes', () => {
    expect(modInputStateKey(7)).toBe('dataOffModInput');
    expect(modInputStateKey(-1)).toBe('dataOffModInput');
    expect(modInputCommand(7)).toBe('set_data_off_mod_input');
    expect(modInputCommand(-1)).toBe('set_data_off_mod_input');
  });
});
