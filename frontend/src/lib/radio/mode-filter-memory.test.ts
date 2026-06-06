/**
 * MOR-495 — per-mode filter memory map.
 *
 * The web mode-change must recall the destination mode's last-known filter,
 * mirroring the radio front panel.  Without it a mode-only 0x06 frame makes
 * the radio apply its mode-DEFAULT filter (e.g. USB → FIL2), losing the
 * filter the operator had on that mode.
 *
 * This suite locks the shared map itself: record/lookup semantics and the
 * `seedFromState` seeding that records map[mode] = filter for the ACTIVE
 * receiver.  The handler-wiring side (onModeChange sending the remembered
 * filter / falling back to mode-only) is asserted in the co-located handler
 * test files: `vfo-wiring.test.ts` (command-bus / mobile) and
 * `pending-focus.test.ts` (panel-commands / desktop-v2).
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// Stub the store subscription so the lazy seeder does not attach to the REAL
// shared radio store — other parallel test files mutate it, which would seed
// stray entries into this session-scoped map and make these assertions flaky.
vi.mock('$lib/stores/radio.svelte', () => ({
  subscribeRadioState: vi.fn(() => () => {}),
}));

import {
  seedFromState,
  recordModeFilter,
  getModeFilter,
  _resetModeFilterMemory,
} from './mode-filter-memory';

describe('mode-filter-memory map', () => {
  beforeEach(() => {
    _resetModeFilterMemory();
  });

  it('records and recalls a mode → filter pairing', () => {
    recordModeFilter('USB', 1);
    expect(getModeFilter('USB')).toBe(1);
  });

  it('returns undefined for an unseen mode', () => {
    expect(getModeFilter('AM')).toBeUndefined();
  });

  it('is case-insensitive on the mode key', () => {
    recordModeFilter('usb', 2);
    expect(getModeFilter('USB')).toBe(2);
    recordModeFilter('Cw', 3);
    expect(getModeFilter('cw')).toBe(3);
  });

  it('ignores non-finite filters', () => {
    recordModeFilter('FM', Number.NaN);
    expect(getModeFilter('FM')).toBeUndefined();
  });

  it('seeds map[mode] = filter from the active (MAIN) receiver', () => {
    seedFromState({
      active: 'MAIN',
      main: { mode: 'USB', filter: 1 },
      sub: { mode: 'CW', filter: 3 },
    } as any);
    expect(getModeFilter('USB')).toBe(1);
    // Only the ACTIVE receiver is recorded.
    expect(getModeFilter('CW')).toBeUndefined();
  });

  it('seeds from the SUB receiver when SUB is active', () => {
    seedFromState({
      active: 'SUB',
      main: { mode: 'USB', filter: 1 },
      sub: { mode: 'RTTY', filter: 2 },
    } as any);
    expect(getModeFilter('RTTY')).toBe(2);
    expect(getModeFilter('USB')).toBeUndefined();
  });

  it('tolerates null / partial state without throwing', () => {
    expect(() => seedFromState(null)).not.toThrow();
    expect(() => seedFromState({ active: 'MAIN' } as any)).not.toThrow();
    expect(getModeFilter('USB')).toBeUndefined();
  });
});
