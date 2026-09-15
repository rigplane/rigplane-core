import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import VoxPanel from '../VoxPanel.svelte';

const components: ReturnType<typeof mount>[] = [];

afterEach(() => {
  components.splice(0).forEach((component) => unmount(component));
  vi.useRealTimers();
  document.body.innerHTML = '';
});

function mountPanel(overrides: Record<string, unknown> = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(VoxPanel, { target, props: {
    voxOn: true,
    voxGain: 128,
    antiVoxGain: 64,
    voxDelay: 7,
    onVoxToggle: vi.fn(),
    onVoxGainChange: vi.fn(),
    onAntiVoxGainChange: vi.fn(),
    onVoxDelayChange: vi.fn(),
    ...overrides,
  } }));
  flushSync();
  return target;
}

describe('VoxPanel Discrete delay mount', () => {
  it('renders the delay through Discrete with the established seconds formatter', () => {
    const target = mountPanel();
    const control = target.querySelector<HTMLElement>('[aria-label="VOX Delay"]')!;

    expect(control.closest('.vc-discrete')).not.toBeNull();
    expect(control.closest('.vc-discrete')?.querySelector('.vc-value')?.textContent).toBe('0.7s');
  });

  it('dispatches one declared delay step through the facade binding', () => {
    vi.useFakeTimers();
    const onVoxDelayChange = vi.fn();
    const target = mountPanel({ onVoxDelayChange });
    const control = target.querySelector<HTMLElement>('[aria-label="VOX Delay"]')!;

    control.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    flushSync();
    expect(control.closest('.vc-discrete')?.querySelector('.vc-value')?.textContent).toBe('0.8s');
    vi.advanceTimersByTime(50);

    expect(onVoxDelayChange).toHaveBeenCalledExactlyOnceWith(8);
  });
});
