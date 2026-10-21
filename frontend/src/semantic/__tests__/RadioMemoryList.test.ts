import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import RadioMemoryList from '../RadioMemoryList.svelte';
import { formatShift, formatTone } from '../memory-list-format';
import type { MemoryEntryDto } from '../../lib/types/memory-list';

const stored: MemoryEntryDto = {
  group: 0, channel: 4, empty: false, name: 'TEST RPT', frequencyHz: 146_940_000, mode: 'FM',
  duplex: 'plus', offsetHz: 600_000, toneMode: 'tone', toneCentiHz: 13_650, tsqlCentiHz: 13_650, dtcsCode: 23,
};
const empty: MemoryEntryDto = { group: 0, channel: 5, empty: true };

const mounted: Array<() => void> = [];
function render(props: Record<string, unknown> = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(RadioMemoryList, {
    target,
    props: { status: 'ready', error: null, entries: [stored, empty], group: 0, groups: { first: 0, last: 99 }, recall: true, ...props },
  });
  flushSync();
  mounted.push(() => { unmount(component); target.remove(); });
  return target;
}
afterEach(() => { while (mounted.length) mounted.pop()?.(); });

describe('formatShift / formatTone', () => {
  it('prints the shift with its offset and the tone with its frequency', () => {
    expect(formatShift(stored)).toBe('+0.600');
    expect(formatShift({ ...stored, duplex: 'minus', offsetHz: 5_000_000 })).toBe('-5.000');
    expect(formatShift({ ...stored, offsetHz: 600_100 })).toBe('+0.6001'); // the radio keeps 100 Hz
    expect(formatShift({ ...stored, duplex: 'simplex' })).toBe('');
    expect(formatTone(stored)).toBe('T 136.5');
    expect(formatTone({ ...stored, toneMode: 'tsql', tsqlCentiHz: 8_850 })).toBe('TSQL 88.5');
    expect(formatTone({ ...stored, toneMode: 'dtcs' })).toBe('DCS 023');
    expect(formatTone({ ...stored, toneMode: 'off' })).toBe('');
  });
});

describe('RadioMemoryList', () => {
  it('lists stored channels only, with name, frequency, mode, shift and tone', () => {
    const root = render();
    expect(root.querySelector('[data-testid="radio-memory-5"]')).toBeNull();
    const row = root.querySelector('[data-testid="radio-memory-4"]') as HTMLElement;
    expect(row.textContent).toContain('TEST RPT');
    expect(row.textContent).toContain('FM');
    expect(row.textContent).toContain('+0.600');
    expect(row.textContent).toContain('T 136.5');
    expect(root.querySelector('[data-testid="radio-memories-status"]')?.textContent).toContain('1 stored');
  });

  it('tells a DATA channel from a voice channel of the same mode', () => {
    const root = render({ entries: [stored, { ...stored, channel: 6, dataMode: true }] });
    expect(root.querySelector('[data-testid="radio-memory-4"] .rm-mode')?.textContent).toBe('FM');
    expect(root.querySelector('[data-testid="radio-memory-6"] .rm-mode')?.textContent).toBe('FM DATA');
  });

  it('recall is an explicit click that passes the slot to the callback', () => {
    const onRecall = vi.fn(() => true);
    const root = render({ onRecall });
    expect(onRecall).not.toHaveBeenCalled();
    (root.querySelector('[data-testid="radio-memory-4-recall"]') as HTMLButtonElement).click();
    expect(onRecall).toHaveBeenCalledWith(0, 4);
  });

  it('shows the group chooser only for radios with groups and reports a change', () => {
    const onGroupChange = vi.fn();
    const root = render({ onGroupChange });
    const input = root.querySelector('[data-testid="radio-memories-group"]') as HTMLInputElement;
    input.value = '3';
    input.dispatchEvent(new Event('change', { bubbles: true }));
    expect(onGroupChange).toHaveBeenCalledWith(3);
    const none = render({ groups: { first: 0, last: 0 } });
    expect(none.querySelector('[data-testid="radio-memories-group"]')).toBeNull();
  });

  it('shows loading and the failure text', () => {
    expect(render({ status: 'loading', entries: [] }).textContent).toContain('Reading');
    expect(render({ status: 'error', error: 'fetchMemoryPage: 503', entries: [] }).textContent).toContain('503');
  });
});

describe('RadioMemoryList layout contract (the 208-228px memory rail)', () => {
  const css = readFileSync('src/semantic/RadioMemoryList.svelte', 'utf8').split('<style>')[1];
  const rule = (selector: string) => css.match(new RegExp(`${selector.replace('.', '\\.')}\\s*\\{([^}]*)\\}`))?.[1] ?? '';

  it('wraps its rows and toolbar so Recall and the controls stay visible', () => {
    expect(rule('.radio-memory-row')).toContain('flex-wrap: wrap');
    expect(rule('.radio-memories-toolbar')).toContain('flex-wrap: wrap');
  });

  it('scrolls a long list inside the panel instead of being clipped by it', () => {
    expect(rule('.radio-memories-list')).toMatch(/max-height:[^;]+;[^}]*overflow-y: auto/);
  });
});
