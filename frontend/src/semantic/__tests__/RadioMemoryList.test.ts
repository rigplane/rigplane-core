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

describe('RadioMemoryList controls in the memory rail', () => {
  it('keeps Recall and the toolbar controls available with a long channel name', () => {
    const onRecall = vi.fn(() => true);
    const onReload = vi.fn();
    const onGroupChange = vi.fn();
    const root = render({
      entries: [{ ...stored, name: 'A long repeater name in the narrow memory rail' }],
      onRecall, onReload, onGroupChange,
    });
    const toolbar = root.querySelector<HTMLElement>('.radio-memories-toolbar')!;
    const recall = root.querySelector<HTMLButtonElement>('[data-testid="radio-memory-4-recall"]')!;
    const reload = toolbar.querySelector<HTMLButtonElement>('[data-testid="radio-memories-reload"]')!;
    const group = toolbar.querySelector<HTMLInputElement>('[data-testid="radio-memories-group"]')!;
    for (const element of [toolbar, recall, reload, group]) {
      expect(getComputedStyle(element).display).not.toBe('none');
    }
    expect(recall.disabled).toBe(false);
    expect(reload.disabled).toBe(false);
    recall.focus();
    expect(document.activeElement).toBe(recall);
    recall.click();
    reload.click();
    group.value = '3';
    group.dispatchEvent(new Event('change', { bubbles: true }));
    expect(onRecall).toHaveBeenCalledWith(0, 4);
    expect(onReload).toHaveBeenCalledOnce();
    expect(onGroupChange).toHaveBeenCalledWith(3);
  });

  it('renders a long list with the last Recall and the toolbar still available', () => {
    const entries = Array.from({ length: 100 }, (_, channel) => ({ ...stored, channel }));
    const onRecall = vi.fn(() => true);
    const root = render({ entries, onRecall });
    const list = root.querySelector<HTMLElement>('[role="list"]')!;
    const toolbar = root.querySelector<HTMLElement>('.radio-memories-toolbar')!;
    expect(list.querySelectorAll('[role="listitem"]')).toHaveLength(entries.length);
    expect(list.contains(toolbar)).toBe(false);
    expect(toolbar.querySelector('[data-testid="radio-memories-reload"]')).not.toBeNull();
    expect(toolbar.querySelector('[data-testid="radio-memories-status"]')?.textContent).toContain('100 stored');
    const recall = list.querySelector<HTMLButtonElement>('[data-testid="radio-memory-99-recall"]')!;
    for (const element of [list, toolbar, recall]) {
      expect(getComputedStyle(element).display).not.toBe('none');
    }
    expect(recall.disabled).toBe(false);
    recall.focus();
    expect(document.activeElement).toBe(recall);
    recall.click();
    expect(onRecall).toHaveBeenCalledWith(0, 99);
  });
});
