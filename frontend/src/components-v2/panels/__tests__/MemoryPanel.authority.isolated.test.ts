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
});
