import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import KeyboardHandler from '../KeyboardHandler.svelte';
import type { KeyboardConfig, KeyboardActionConfig } from '../keyboard-map';

describe('KeyboardHandler', () => {
  let components: ReturnType<typeof mount>[] = [];

  function mountHandler(props: Partial<{
    config: KeyboardConfig;
    onAction: (action: KeyboardActionConfig) => void;
    enabled: boolean;
  }> = {}) {
    const target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(KeyboardHandler, {
      target,
      props: {
        config: props.config,
        onAction: props.onAction ?? vi.fn(),
        enabled: props.enabled ?? true,
      },
    });
    flushSync();
    components.push(component);
    return target;
  }

  const config: KeyboardConfig = {
    leaderKey: 'g',
    leaderTimeoutMs: 1000,
    altHints: true,
    helpTitle: 'Test Keyboard Help',
    bindings: [
      {
        id: 'tune-up',
        section: 'Tuning',
        label: 'Tune Up',
        sequence: ['ArrowUp'],
        action: 'tune',
        repeatable: true,
        params: { direction: 'up', fine: false },
      },
      {
        id: 'help',
        section: 'System',
        label: 'Show help',
        sequence: ['?'],
        action: 'toggle_help',
      },
      {
        id: 'focus-vfo',
        section: 'Focus',
        label: 'Focus VFO',
        sequence: ['g', 'v'],
        action: 'focus_target',
        params: { target: 'vfo' },
      },
    ],
  };

  beforeEach(() => {
    components = [];
    document.body.removeAttribute('data-shortcut-hints');
  });

  afterEach(() => {
    components.forEach((component) => unmount(component));
    document.body.innerHTML = '';
    document.body.removeAttribute('data-shortcut-hints');
  });

  it('dispatches a configured single-key action', () => {
    const onAction = vi.fn();
    mountHandler({ config, onAction });

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp' }));

    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'tune', params: { direction: 'up', fine: false } }),
    );
  });

  it('supports leader sequences for focus actions', () => {
    const onAction = vi.fn();
    mountHandler({ config, onAction });

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'g' }));
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'v' }));

    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'focus_target', params: { target: 'vfo' } }),
    );
  });

  it('renders the help overlay for the help shortcut', () => {
    const target = mountHandler({ config });

    window.dispatchEvent(new KeyboardEvent('keydown', { key: '?' }));
    flushSync();

    expect(target.querySelector('.keyboard-help-overlay')).not.toBeNull();
    expect(target.textContent).toContain('Test Keyboard Help');
  });

  it('toggles body shortcut hints while Alt is held', () => {
    mountHandler({ config });

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Alt' }));
    expect(document.body.dataset.shortcutHints).toBe('true');

    window.dispatchEvent(new KeyboardEvent('keyup', { key: 'Alt' }));
    expect(document.body.dataset.shortcutHints).toBeUndefined();
  });

  // MOR-1449 — rigs/_keyboard-default.toml (loaded into production keyboard
  // configs for every rig, including ic7300.toml's own copy) binds the bare
  // "Tab" key to the `vfo_swap` action ("swap-vfo"). Because `resolveAction`
  // matched it like any other single-key binding, `handleKeydown` called
  // `event.preventDefault()` on every Tab press outside a form field — which
  // silently eats the browser's native focus-traversal everywhere in the app,
  // not just on the bound action. Tab must never be assignable to a shortcut,
  // regardless of what a rig profile's config declares.
  const configWithTabBinding: KeyboardConfig = {
    ...config,
    bindings: [
      ...config.bindings,
      {
        id: 'swap-vfo',
        section: 'VFO',
        label: 'Swap VFO',
        sequence: ['Tab'],
        action: 'vfo_swap',
      },
    ],
  };

  it('never intercepts Tab, even when a rig config binds it to a shortcut', () => {
    const onAction = vi.fn();
    mountHandler({ config: configWithTabBinding, onAction });

    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    window.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
    expect(onAction).not.toHaveBeenCalled();
  });

  it('never intercepts Shift+Tab, even when a rig config binds Tab to a shortcut', () => {
    const onAction = vi.fn();
    mountHandler({ config: configWithTabBinding, onAction });

    const event = new KeyboardEvent('keydown', {
      key: 'Tab', shiftKey: true, bubbles: true, cancelable: true,
    });
    window.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
    expect(onAction).not.toHaveBeenCalled();
  });

  it('still dispatches other single-key actions when a Tab binding is present', () => {
    const onAction = vi.fn();
    mountHandler({ config: configWithTabBinding, onAction });

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp' }));

    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'tune', params: { direction: 'up', fine: false } }),
    );
  });
});