/**
 * MOR-2682 — an unpopulated memory channel renders as an empty row, never '-- empty --'.
 *
 * A real radio's memory list shows an empty channel as an empty row (or
 * skips it); it never prints a dash-framed word. The treatment follows
 * MOR-2668's decision for empty mode/name cells: the unpopulated branch
 * renders the same three value cells as a populated row (`.ch-freq` /
 * `.ch-mode` / `.ch-name`), empty, in their reserved slots. The row keeps
 * the channel number and its store affordance; the value carries no
 * "empty" wording, so the accessible name derives from the number and the
 * store action alone, with no override.
 *
 * Seeding/mount idiom mirrors `MemoryPanel.empty-fields.isolated.test.ts`
 * (mocked panel-adapters, localStorage seed before mount); the "All"
 * checkbox click mirrors `MemorySurface.test.ts` (`checkbox.click()` +
 * flush).
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mount, tick, unmount } from 'svelte';
import { readFileSync } from 'node:fs';

const handlers = vi.hoisted(() => ({ onRecall: vi.fn(() => false), onStore: vi.fn(() => false), onClear: vi.fn(() => false) }));
const props = vi.hoisted(() => ({ activeFreqHz: 14_074_000, activeMode: 'USB', vfoIdentityKnown: true }));
vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveMemoryPanelProps: () => props,
  getMemoryHandlers: () => handlers,
}));

import MemoryPanel from '../MemoryPanel.svelte';

let component: ReturnType<typeof mount> | undefined;
let target: HTMLElement | undefined;

async function render(): Promise<HTMLElement> {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(MemoryPanel, { target });
  await tick();
  return target;
}

function seed(entries: Record<number, { freq: number; mode: string; name: string }>): void {
  localStorage.setItem('rigplane:memory-channels', JSON.stringify(entries));
}

async function showAll(t: HTMLElement): Promise<void> {
  (t.querySelector('.show-empty input') as HTMLInputElement).click();
  await tick();
}

afterEach(() => {
  if (component) unmount(component);
  component = undefined;
  target?.remove();
  target = undefined;
  localStorage.clear();
  vi.clearAllMocks();
});

describe('MemoryPanel unpopulated rows render as empty rows (MOR-2682)', () => {
  it('shows every channel once "All" is checked, populated and unpopulated alike', async () => {
    seed({ 2: { freq: 7_100_000, mode: 'LSB', name: 'saved' } });
    const t = await render();
    await showAll(t);
    expect(t.querySelectorAll('[role="listitem"]')).toHaveLength(99);
    expect(t.querySelector('[data-channel="1"]')).not.toBeNull();
    expect(t.querySelector('[data-channel="2"]')).not.toBeNull();
  });

  it('renders no "-- empty --" label element for an unpopulated channel', async () => {
    seed({ 2: { freq: 7_100_000, mode: 'LSB', name: 'saved' } });
    const t = await render();
    await showAll(t);
    const row = t.querySelector('[data-channel="1"]');
    expect(row).not.toBeNull();
    expect(row?.querySelector('.ch-empty-label')).toBeNull();
    expect(row?.textContent).not.toContain('-- empty --');
  });

  it('keeps the channel number and renders the value cells empty', async () => {
    seed({ 2: { freq: 7_100_000, mode: 'LSB', name: 'saved' } });
    const t = await render();
    await showAll(t);
    const row = t.querySelector('[data-channel="1"]');
    expect(row?.querySelector('.ch-number')?.textContent).toBe('01');
    expect(row?.querySelector('.ch-freq')?.textContent).toBe('');
    expect(row?.querySelector('.ch-mode')?.textContent).toBe('');
    expect(row?.querySelector('.ch-name')?.textContent).toBe('');
  });

  it('renders the empty value as nothing at all: no dash-framed word, no "empty" wording', async () => {
    seed({ 2: { freq: 7_100_000, mode: 'LSB', name: 'saved' } });
    const t = await render();
    await showAll(t);
    const row = t.querySelector('[data-channel="1"]');
    const value = ['.ch-freq', '.ch-mode', '.ch-name']
      .map((sel) => row?.querySelector(sel)?.textContent ?? 'MISSING')
      .join('|');
    expect(value).toBe('||');
    expect(row?.getAttribute('aria-label')).toBeNull();
    expect(row?.textContent).not.toContain('empty');
  });

  it('gives the empty name slot no edit affordance: a span, never the rename button', async () => {
    seed({ 2: { freq: 7_100_000, mode: 'LSB', name: 'saved' } });
    const t = await render();
    await showAll(t);
    expect(t.querySelector('[data-channel="1"] .ch-name')?.tagName).toBe('SPAN');
    expect(t.querySelector('[data-channel="2"] .ch-name')?.tagName).toBe('BUTTON');
  });

  it('keeps the store affordance on the empty row', async () => {
    seed({ 2: { freq: 7_100_000, mode: 'LSB', name: 'saved' } });
    const t = await render();
    await showAll(t);
    const store = t.querySelector('[data-channel="1"] .store-btn-inline') as HTMLButtonElement | null;
    expect(store).not.toBeNull();
    expect(store?.textContent?.trim()).toBe('<<VFO');
    expect(store?.disabled).toBe(false);
  });

  it('reserves the frequency slot the empty row shares with populated rows', async () => {
    const source = readFileSync('src/components-v2/panels/MemoryPanel.svelte', 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '');
    const rule = source.match(/\.ch-freq \{([^}]*)\}/);
    expect(rule).not.toBeNull();
    expect(rule![1]).toContain('min-width: 80px');
  });

  it('keeps the pointer cursor on the name button only, never on the empty name slot', async () => {
    const source = readFileSync('src/components-v2/panels/MemoryPanel.svelte', 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '');
    const name = source.match(/\.ch-name \{([^}]*)\}/);
    expect(name).not.toBeNull();
    expect(name![1]).not.toContain('cursor');
    const button = source.match(/button\.ch-name \{([^}]*)\}/);
    expect(button).not.toBeNull();
    expect(button![1]).toContain('cursor: pointer');
  });
});
