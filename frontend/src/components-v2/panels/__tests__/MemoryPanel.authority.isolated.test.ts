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
afterEach(() => { if (component) unmount(component); component = undefined; localStorage.clear(); vi.clearAllMocks(); });

describe('MOR-1409 A05a MemoryPanel authority boundary', () => {
  it('uses the adapter-bound handlers instead of raw runtime command sends', () => {
    const source = readFileSync('src/components-v2/panels/MemoryPanel.svelte', 'utf8');
    expect(source).toContain('getMemoryHandlers');
    expect(source).not.toContain("from '$lib/runtime'");
    expect(source).not.toMatch(/\bruntime\.send\b/);
    expect(source).toMatch(/if \(!memory\.onRecall\(ch\)\) return;\s+selectedChannel = ch;/);
    expect(source).toMatch(/if \(!memory\.onStore\(ch, freq, mode\)\) return;/);
    expect(source).toMatch(/if \(!memory\.onClear\(ch\)\) return;/);
    expect(source).not.toMatch(/(?:getCommandLifecycles|onCommandDelivery|onControlSessionTransition)/);
  });

  it('does not mutate action-local state or persistence after a rejected clear', async () => {
    handlers.onStore.mockReturnValueOnce(true);
    component = mount(MemoryPanel, { target: document.body });
    await tick();
    (document.querySelector('.store-btn') as HTMLButtonElement).click();
    await tick();
    (document.querySelector('.store-confirm') as HTMLButtonElement).click();
    await tick();
    (document.querySelector('.clear-btn') as HTMLButtonElement).click();
    await tick();
    (document.querySelector('.clear-confirm') as HTMLButtonElement).click();
    await tick();
    expect(handlers.onClear).toHaveBeenCalledWith(1);
    expect(document.querySelector('.ch-freq')?.textContent).toContain('14.074');
    expect(localStorage.getItem('rigplane:memory-channels')).toContain('14074000');
  });

  it('updates accepted recall/store/clear local state and persists the catalog changes', async () => {
    localStorage.setItem('rigplane:memory-channels', JSON.stringify({
      2: { freq: 7_100_000, mode: 'LSB', name: 'saved' },
    }));
    handlers.onRecall.mockReturnValueOnce(true);
    handlers.onStore.mockReturnValueOnce(true);
    handlers.onClear.mockReturnValueOnce(true);
    component = mount(MemoryPanel, { target: document.body });
    await tick();

    (document.querySelector('[data-channel="2"] .recall-btn') as HTMLButtonElement).click();
    await tick();
    expect(document.querySelector('[data-channel="2"]')?.classList.contains('selected')).toBe(true);

    (document.querySelector('.store-btn') as HTMLButtonElement).click();
    await tick();
    (document.querySelector('.store-confirm') as HTMLButtonElement).click();
    await tick();
    expect(document.querySelector('[data-channel="1"] .ch-freq')?.textContent).toContain('14.074');
    expect(JSON.parse(localStorage.getItem('rigplane:memory-channels') ?? '{}')).toMatchObject({
      1: { freq: 14_074_000, mode: 'USB', name: '' },
    });

    (document.querySelector('[data-channel="1"] .clear-btn') as HTMLButtonElement).click();
    await tick();
    (document.querySelector('[data-channel="1"] .clear-confirm') as HTMLButtonElement).click();
    await tick();
    expect(document.querySelector('[data-channel="1"]')).toBeNull();
    expect(JSON.parse(localStorage.getItem('rigplane:memory-channels') ?? '{}')).not.toHaveProperty('1');
  });

  it('keeps local selection and catalog unchanged after rejected or exceptional actions', async () => {
    const original = JSON.stringify({ 2: { freq: 7_100_000, mode: 'LSB', name: 'saved' } });
    localStorage.setItem('rigplane:memory-channels', original);
    component = mount(MemoryPanel, { target: document.body });
    await tick();

    (document.querySelector('[data-channel="2"] .recall-btn') as HTMLButtonElement).click();
    await tick();
    expect(document.querySelector('[data-channel="2"]')?.classList.contains('selected')).toBe(false);
    (document.querySelector('.store-btn') as HTMLButtonElement).click();
    await tick();
    (document.querySelector('.store-confirm') as HTMLButtonElement).click();
    await tick();
    (document.querySelector('[data-channel="2"] .clear-btn') as HTMLButtonElement).click();
    await tick();
    (document.querySelector('[data-channel="2"] .clear-confirm') as HTMLButtonElement).click();
    await tick();
    expect(localStorage.getItem('rigplane:memory-channels')).toBe(original);
    expect(document.querySelector('[data-channel="2"] .ch-freq')?.textContent).toContain('7.100');

    const preventUnhandled = (event: ErrorEvent) => event.preventDefault();
    window.addEventListener('error', preventUnhandled);
    try {
      handlers.onRecall.mockImplementationOnce(() => { throw new Error('recall failure'); });
      (document.querySelector('[data-channel="2"] .recall-btn') as HTMLButtonElement).click();
      await tick();
      expect(document.querySelector('[data-channel="2"]')?.classList.contains('selected')).toBe(false);
      handlers.onStore.mockImplementationOnce(() => { throw new Error('store failure'); });
      (document.querySelector('.store-confirm') as HTMLButtonElement).click();
      await tick();
      handlers.onClear.mockImplementationOnce(() => { throw new Error('clear failure'); });
      (document.querySelector('[data-channel="2"] .clear-confirm') as HTMLButtonElement).click();
      await tick();
      expect(localStorage.getItem('rigplane:memory-channels')).toBe(original);
    } finally {
      window.removeEventListener('error', preventUnhandled);
    }
  });
});
