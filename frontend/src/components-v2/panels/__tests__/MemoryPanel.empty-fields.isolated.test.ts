/**
 * MOR-2668 — empty memory fields render as empty cells, never '---'.
 *
 * The memory catalog is local-only: the IC-7610 class cannot report memory
 * channel contents over CI-V (`memory-channels.ts`), so an empty mode or
 * name is truly empty (never stored / never named), not an unread radio
 * value. `storeVfoToChannel` keeps `name: ... ?? ''` and the semantic twin
 * `MemorySurface.svelte` already renders `{entry.mode}` / `{entry.name}`
 * with no fallback — this panel does the same.
 *
 * Seeding/mount idiom mirrors `MemoryPanel.authority.isolated.test.ts`
 * (mocked panel-adapters, localStorage seed before mount); the
 * `.textContent?.trim()` literal pin mirrors `MemorySurface.test.ts`.
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

afterEach(() => {
  if (component) unmount(component);
  component = undefined;
  target?.remove();
  target = undefined;
  localStorage.clear();
  vi.clearAllMocks();
});

describe('MemoryPanel empty fields render as empty cells (MOR-2668)', () => {
  it('renders an empty mode as an empty cell in its reserved slot, never a dash', async () => {
    seed({ 1: { freq: 14_074_000, mode: '', name: '' } });
    const t = await render();
    const cell = t.querySelector('[data-channel="1"] .ch-mode');
    expect(cell).not.toBeNull();
    expect(cell?.textContent).toBe('');
  });

  it('renders an empty name as an empty cell that keeps its edit affordance, never a dash', async () => {
    seed({ 1: { freq: 14_074_000, mode: '', name: '' } });
    const t = await render();
    const cell = t.querySelector('[data-channel="1"] .ch-name');
    expect(cell).not.toBeNull();
    expect(cell?.textContent?.trim()).toBe('');
    expect(cell?.getAttribute('title')).toBe('Click to edit name');
  });

  it('renders no dash-family placeholder anywhere in a row with empty fields', async () => {
    seed({ 1: { freq: 14_074_000, mode: '', name: '' } });
    const t = await render();
    const row = t.querySelector('[data-channel="1"]');
    expect(row?.textContent).not.toContain('---');
    expect(row?.textContent).not.toContain('NaN');
  });

  it('still renders a stored mode, name and frequency exactly as today', async () => {
    seed({ 2: { freq: 7_100_000, mode: 'LSB', name: 'saved' } });
    const t = await render();
    expect(t.querySelector('[data-channel="2"] .ch-mode')?.textContent).toBe('LSB');
    expect(t.querySelector('[data-channel="2"] .ch-name')?.textContent?.trim()).toBe('saved');
    expect(t.querySelector('[data-channel="2"] .ch-freq')?.textContent).toContain('7.100');
  });

  it('reserves the mode cell slot so an empty mode keeps the same width', async () => {
    const source = readFileSync('src/components-v2/panels/MemoryPanel.svelte', 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '');
    const rule = source.match(/\.ch-mode \{([^}]*)\}/);
    expect(rule).not.toBeNull();
    expect(rule![1]).toContain('min-width: 36px');
  });

  it('keeps the channel row geometry with empty cells: centered, fixed height, name fills the gap', async () => {
    const source = readFileSync('src/components-v2/panels/MemoryPanel.svelte', 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '');
    // Centered, not baseline-aligned: an empty flex item has no baseline
    // and must not re-seat the row. The row height never depends on cells.
    const row = source.match(/\.channel-row \{([^}]*)\}/);
    expect(row).not.toBeNull();
    expect(row![1]).toContain('align-items: center');
    expect(row![1]).toContain('min-height: 22px');
    // The name cell grows into the same free space whether it holds text,
    // so an empty name keeps the same width as a filled one.
    const name = source.match(/\.ch-name \{([^}]*)\}/);
    expect(name).not.toBeNull();
    expect(name![1]).toContain('flex: 1 1 auto');
  });
});
