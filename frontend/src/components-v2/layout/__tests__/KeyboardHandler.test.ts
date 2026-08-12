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

  // MOR-1444 — every rig profile's keyboard config binds "1".."9" to
  // `band_select` (rigs/_keyboard-default.toml). Typing a frequency with the
  // VFO/frequency display focused used to hop bands on every digit instead
  // of reaching BandSurface's typed-entry box. These pins cover both sides:
  // digits route to the entry input while the VFO display has focus, and
  // keep firing band hotkeys exactly as before everywhere else.
  describe('digit routing to the frequency-entry input (MOR-1444)', () => {
    const configWithDigitBinding: KeyboardConfig = {
      ...config,
      bindings: [
        ...config.bindings,
        {
          id: 'band-7',
          section: 'Band',
          label: 'Select band 7',
          sequence: ['7'],
          action: 'band_select',
          params: { index: 7 },
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

    /**
     * Mirrors VfoSurface.svelte's real DOM shape: a `[data-vfo-tile]` with
     * the RECEIVER-wide `data-vfo-active` flag wrapping the `[data-vfo-freq]`
     * span. `active` defaults to true so every pre-existing test below keeps
     * exercising "the active receiver's display has focus".
     */
    function appendVfoFreqDisplay({ active = true }: { active?: boolean } = {}): HTMLElement {
      const tile = document.createElement('div');
      tile.setAttribute('data-vfo-tile', '');
      tile.setAttribute('data-vfo-active', String(active));
      const wrapper = document.createElement('span');
      wrapper.setAttribute('data-vfo-freq', '');
      const focusTarget = document.createElement('div');
      focusTarget.tabIndex = 0;
      wrapper.appendChild(focusTarget);
      tile.appendChild(wrapper);
      document.body.appendChild(tile);
      return focusTarget;
    }

    function appendFreqEntryInput(): HTMLInputElement {
      const input = document.createElement('input');
      input.setAttribute('data-freq-entry', '');
      document.body.appendChild(input);
      return input;
    }

    it('feeds the digit into the frequency-entry input and does not fire the band hotkey', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const vfoFocusTarget = appendVfoFreqDisplay();
      const entryInput = appendFreqEntryInput();
      vfoFocusTarget.focus();
      expect(document.activeElement).toBe(vfoFocusTarget);

      const event = new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true });
      window.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);
      expect(onAction).not.toHaveBeenCalled();
      expect(document.activeElement).toBe(entryInput);
      expect(entryInput.value).toBe('7');
    });

    it('resets the entry to the freshly typed digit rather than appending to a stale value', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const vfoFocusTarget = appendVfoFreqDisplay();
      const entryInput = appendFreqEntryInput();
      entryInput.value = '14250000';
      vfoFocusTarget.focus();

      window.dispatchEvent(new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true }));

      expect(entryInput.value).toBe('7');
    });

    it('still dispatches the band hotkey when focus is elsewhere', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      appendVfoFreqDisplay();
      appendFreqEntryInput();
      // Focus stays on document.body (the jsdom default) — nowhere near the
      // VFO display.

      window.dispatchEvent(new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true }));

      expect(onAction).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select', params: { index: 7 } }),
      );
    });

    it('falls back to the band hotkey when the VFO display is focused but no entry input exists', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const vfoFocusTarget = appendVfoFreqDisplay();
      vfoFocusTarget.focus();

      window.dispatchEvent(new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true }));

      expect(onAction).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select', params: { index: 7 } }),
      );
    });

    it('falls back to the band hotkey when the entry input is disabled', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const vfoFocusTarget = appendVfoFreqDisplay();
      const entryInput = appendFreqEntryInput();
      entryInput.disabled = true;
      vfoFocusTarget.focus();

      window.dispatchEvent(new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true }));

      expect(onAction).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select', params: { index: 7 } }),
      );
      expect(entryInput.value).toBe('');
    });

    // MOR-1444 B1 (round-2 review) — REPRODUCED: on a dual-receiver cockpit
    // (2/main_sub) both receivers mount a focusable [data-vfo-freq], since
    // VfoSurface.svelte's hasTunableFrequency gates on isActiveSlot, not the
    // radio-wide isActive. enterFrequency() always writes view.activeReceiver
    // (SemanticRadioSurfaces.svelte), so routing a digit typed on the
    // INACTIVE receiver's display would silently commit to the WRONG VFO —
    // reopening the MOR-1322 B1 / MOR-1335 G4 cross-dispatch class. Digits
    // must fall through to the band hotkey here exactly as if no VFO display
    // were focused at all.
    it('falls back to the band hotkey when the focused VFO tile is not the active receiver', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const vfoFocusTarget = appendVfoFreqDisplay({ active: false });
      const entryInput = appendFreqEntryInput();
      vfoFocusTarget.focus();

      window.dispatchEvent(new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true }));

      expect(onAction).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select', params: { index: 7 } }),
      );
      expect(entryInput.value).toBe('');
      expect(document.activeElement).toBe(vfoFocusTarget);
    });

    // MOR-1444 B2 (round-2 review) — REPRODUCED: the digit guard returned
    // above the `if (pendingSequence)` block without disarming it, unlike
    // the MOR-1449 Tab guard 12 lines below which explicitly does. Repro:
    // "g" arms the leader pill, "7" (VFO focused) routes to the entry input
    // and returns early — the pill must NOT still be armed afterward, or a
    // later "a" within leaderTimeoutMs completes "g a" into an unintended
    // focus_target once focus leaves the (ignored-tag) entry input.
    it('disarms a pending leader sequence when a digit routes to the frequency-entry input', () => {
      const onAction = vi.fn();
      const target = mountHandler({ config: configWithDigitBinding, onAction });
      const vfoFocusTarget = appendVfoFreqDisplay();
      const entryInput = appendFreqEntryInput();

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'g' }));
      flushSync();
      expect(target.querySelector('.keyboard-leader-pill')).not.toBeNull();

      vfoFocusTarget.focus();
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true }));
      flushSync();
      expect(target.querySelector('.keyboard-leader-pill')).toBeNull();

      // The repro's "blur → next key within the timeout": had the leader
      // stayed armed, this "a" would complete "g a" -> focus_target once
      // focus leaves the entry input (an ignored tag while it holds focus).
      entryInput.blur();
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true, cancelable: true }));
      expect(onAction).not.toHaveBeenCalledWith(
        expect.objectContaining({ action: 'focus_target' }),
      );
    });

    // MOR-1444 B3 (round-2 review) — a Ctrl/Cmd/Alt-modified digit is a
    // BROWSER OR OS shortcut (Cmd+1 switches tabs in most browsers), not a
    // frequency-entry keystroke. Routing it would both hijack focus into the
    // entry input AND swallow the browser's own shortcut via preventDefault.
    it.each(['ctrlKey', 'metaKey', 'altKey'] as const)(
      'does not route a %s-modified digit to the entry input',
      (modifier) => {
        const onAction = vi.fn();
        mountHandler({ config: configWithDigitBinding, onAction });
        const vfoFocusTarget = appendVfoFreqDisplay();
        const entryInput = appendFreqEntryInput();
        vfoFocusTarget.focus();

        const event = new KeyboardEvent('keydown', {
          key: '7', [modifier]: true, bubbles: true, cancelable: true,
        });
        window.dispatchEvent(event);

        expect(event.defaultPrevented).toBe(false);
        expect(entryInput.value).toBe('');
        expect(document.activeElement).toBe(vfoFocusTarget);
      },
    );
  });
});