/**
 * Component-level render tests for AmberMemoryStrip (MOR-2659).
 *
 * The repo has no frontend memory store (grep evidence in the ticket: the
 * only "memory store" hits are rig-side store-into-memory commands in
 * `MemorySurface.svelte`, not a per-slot readout source), so the M1-M5
 * memory cells are NOT drawn — what the app does not have is not drawn.
 * The strip keeps its QSY-recall half.
 *
 * An empty QSY history renders an unlit, EMPTY placeholder in a reserved
 * slot — never a dash (MOR-2659 / MOR-2651).
 *
 * Uses native svelte mount() in jsdom. The `deriveQsyRecent` adapter is
 * mocked to feed deterministic entries.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';
import path from 'node:path';

interface MockQsyEntry {
  freqHz: number;
  mode: string;
  at: number;
}

const mockRecent: MockQsyEntry[] = [];

vi.mock('$lib/runtime/adapters/qsy-history-adapter', () => ({
  deriveQsyRecent: () => mockRecent,
}));

import AmberMemoryStrip from '../AmberMemoryStrip.svelte';

let target: HTMLDivElement;

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  mockRecent.length = 0;
});

afterEach(() => {
  document.body.removeChild(target);
});

describe('AmberMemoryStrip (MOR-2659)', () => {
  it('draws no memory cells while the app has no memory store', () => {
    const component = mount(AmberMemoryStrip, { target, props: {} });
    expect(target.querySelector('.mem-section')).toBeNull();
    expect(target.querySelector('.slot-empty')).toBeNull();
    expect(target.textContent).not.toContain('MEM');
    expect(target.textContent).not.toContain('M1');
    unmount(component);
  });

  it('renders an unlit, empty QSY section before the first QSY — never a dash', () => {
    const component = mount(AmberMemoryStrip, { target, props: {} });
    const placeholder = target.querySelector('.qsy-placeholder');
    expect(placeholder).not.toBeNull();
    expect(placeholder!.textContent).toBe('');
    expect(target.textContent).not.toContain('—');
    expect(target.querySelector('.qsy-section')?.classList.contains('qsy-empty')).toBe(true);
    unmount(component);
  });

  it('renders the last QSY entries as chips with formatted frequencies and no dash', () => {
    mockRecent.push(
      { freqHz: 14_074_000, mode: 'USB', at: 1 },
      { freqHz: 7_074_000, mode: 'CW', at: 2 },
      { freqHz: 144_520_000, mode: 'FM', at: 3 },
    );
    const component = mount(AmberMemoryStrip, { target, props: {} });
    const chips = Array.from(target.querySelectorAll('.slot-qsy .slot-value'))
      .map((v) => v.textContent);
    expect(chips).toEqual(['144.520', '7.074', '14.074']);
    expect(target.textContent).not.toContain('—');
    expect(target.querySelector('.qsy-section')?.classList.contains('qsy-empty')).toBe(false);
    unmount(component);
  });

  // Reserved-slot pins (AmberIndStrip MOR-2546 precedent: readFileSync of
  // the component + a regex on its `<style>`, #3591): the QSY section must
  // keep its flex-filled box and the placeholder a fixed inline size, so a
  // first reading cannot move the layout.
  it('reserves the QSY section box and the empty placeholder slot', () => {
    const stripSource = readFileSync(
      path.resolve(process.cwd(), 'src/components-v2/panels/lcd/AmberMemoryStrip.svelte'),
      'utf8',
    );
    expect(stripSource).toMatch(
      /\.qsy-section\s*\{[^}]*flex:\s*1/,
    );
    expect(stripSource).toMatch(
      /\.qsy-section\s*\{[^}]*min-width:\s*0/,
    );
    expect(stripSource).toMatch(
      /\.qsy-placeholder\s*\{[^}]*min-inline-size:\s*6ch/,
    );
    expect(stripSource).toMatch(
      /\.qsy-placeholder\s*\{[^}]*box-sizing:\s*content-box/,
    );
  });
});
