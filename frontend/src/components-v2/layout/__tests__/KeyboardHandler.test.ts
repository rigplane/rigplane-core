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

  // MOR-1449 — rigs/_keyboard-default.toml used to bind the bare "Tab" key
  // to the `vfo_swap` action ("swap-vfo"). rig_loader.py loads that shared
  // default for EVERY rig profile and merges rig-local overrides on top
  // (profiles/rig_loader.py:1259), so all eight rig profiles inherited the
  // binding — not just ic7300.toml, which never declared it itself. Because
  // `resolveAction` matched it like any other single-key binding,
  // `handleKeydown` called `event.preventDefault()` on every Tab press
  // outside a form field — which silently ate the browser's native
  // focus-traversal everywhere in the app, not just on the bound action.
  // The dead binding has since been deleted from the TOML (MOR-1449 fix),
  // but Tab must never be assignable to a shortcut regardless of what any
  // current or future rig profile's config declares — this config object
  // reconstructs the pre-fix shape to pin that invariant directly.
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
      {
        id: 'focus-af',
        section: 'Focus',
        label: 'Go to AF',
        sequence: ['g', 'a'],
        action: 'focus_target',
        params: { target: 'af' },
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

  // Reproduces the round-2 review finding: an early `return` on Tab BEFORE
  // the `if (pendingSequence)` branch would skip `clearLeaderState()`. Pre-fix,
  // Tab mid-sequence disarmed the leader fine (it simply didn't match the
  // recorded second key, so `resolveSequenceContinuation` returned null and
  // `clearLeaderState()` still ran). A naive Tab guard placed above that
  // branch would regress it: the leader pill would stay armed and the NEXT
  // keystroke ('a' here) would be swallowed for up to leaderTimeoutMs and
  // could fire an unintended `focus_target` action instead of doing nothing.
  it('disarms a pending leader sequence on Tab instead of leaving it armed', () => {
    const onAction = vi.fn();
    const target = mountHandler({ config: configWithTabBinding, onAction });

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'g' }));
    flushSync();
    expect(target.querySelector('.keyboard-leader-pill')).not.toBeNull();

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }));
    flushSync();
    expect(target.querySelector('.keyboard-leader-pill')).toBeNull();

    const followUp = new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true });
    window.dispatchEvent(followUp);

    expect(onAction).not.toHaveBeenCalled();
    expect(followUp.defaultPrevented).toBe(false);
  });
});