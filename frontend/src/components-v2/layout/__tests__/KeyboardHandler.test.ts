import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import KeyboardHandler from '../KeyboardHandler.svelte';
import type { KeyboardConfig, KeyboardActionConfig } from '../keyboard-map';
import VfoSurface from '../../../semantic/VfoSurface.svelte';
import { topologyFixtures } from '../../../semantic/fixtures/topologies';

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

    // MOR-1480 owner ruling A (post-verifier BLOCKED, explicit): reverses
    // this test's pre-ruling expectation. A digit typed while the VFO
    // display has focus must NEVER resolve as a band hotkey, even when the
    // entry surface isn't mounted at all (no band group on this skin, or
    // BandSurface not yet rendered) — it is swallowed instead.
    it('swallows the digit — never the band hotkey — when the VFO display is focused but no entry input exists', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const vfoFocusTarget = appendVfoFreqDisplay();
      vfoFocusTarget.focus();

      const event = new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true });
      window.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);
      expect(onAction).not.toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select' }),
      );
      // Nothing to route into — focus stays put, exactly as routing would
      // leave it when the entry input is absent.
      expect(document.activeElement).toBe(vfoFocusTarget);
    });

    // MOR-1480 owner ruling A: reverses this test's pre-ruling expectation.
    // A digit typed while the VFO display has focus must NEVER resolve as a
    // band hotkey, even when BandSurface's own MOR-1322/rule-5 fail-closed
    // gates have disabled the entry input (active receiver or tuning bounds
    // not yet known) — it is swallowed instead of falling through.
    it('swallows the digit — never the band hotkey — when the entry input is disabled', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const vfoFocusTarget = appendVfoFreqDisplay();
      const entryInput = appendFreqEntryInput();
      entryInput.disabled = true;
      vfoFocusTarget.focus();

      const event = new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true });
      window.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);
      expect(onAction).not.toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select' }),
      );
      expect(entryInput.value).toBe('');
    });

    // MOR-1444 B1 (round-2 review) — REPRODUCED: on a dual-receiver cockpit
    // (2/main_sub) both receivers mount a focusable [data-vfo-freq], since
    // VfoSurface.svelte's hasTunableFrequency gates on isActiveSlot, not the
    // radio-wide isActive. enterFrequency() always writes view.activeReceiver
    // (SemanticRadioSurfaces.svelte), so routing a digit typed on the
    // INACTIVE receiver's display would silently commit to the WRONG VFO —
    // reopening the MOR-1322 B1 / MOR-1335 G4 cross-dispatch class.
    //
    // MOR-1480 owner ruling A (post-verifier BLOCKED, explicit): REVERSES
    // this test's original "falls back to the band hotkey" expectation.
    // Owner ruled that a digit typed while focus is on ANY VFO display —
    // including the INACTIVE receiver's — must never band-hop either. The
    // digit is now swallowed: no routing (still protects against the
    // wrong-VFO commit above) AND no band hotkey.
    it('swallows the digit — routing nothing and band-hopping nothing — when the focused VFO tile is not the active receiver', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const vfoFocusTarget = appendVfoFreqDisplay({ active: false });
      const entryInput = appendFreqEntryInput();
      vfoFocusTarget.focus();

      const event = new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true });
      window.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);
      expect(onAction).not.toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select' }),
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

  // MOR-1480 verifier finding F1 (confirmed): `VfoHeader.svelte`/
  // `VfoPanel.svelte` — the desktop-v2 HEADER VFO display this block
  // originally targeted — never actually renders on any shipping skin
  // (`RadioLayout.svelte:83` `semanticDeck` is true for every registered
  // manifest; `VfoHeader` is the dead else-branch, pinned by
  // `semantic-desktop-migration.component.test.ts` "drops the legacy twin").
  // The live bug lives in the SEMANTIC tree instead — see the
  // "digit routing against the real semantic VfoSurface tree" describe block
  // below, which is the actual mechanism reproduction and fix verification.
  //
  // This block is kept as DEFENSE-IN-DEPTH coverage for
  // `FrequencyDisplayInteractive`'s own `vfoFreqHook` self-attribution
  // (`data-vfo-freq` + `data-vfo-active` on its own focusable root): it
  // makes the primitive self-sufficient for any FUTURE non-semantic mount
  // (e.g. a revived header), even though no current mount depends on it —
  // `VfoSurface.svelte` opts out via `vfoFreqHook={false}` and supplies its
  // own equivalent `[data-vfo-tile]`-wrapped `[data-vfo-freq]` hook instead.
  describe('digit routing from a bare FrequencyDisplayInteractive mount, no [data-vfo-tile] ancestor — defense-in-depth for the primitive-level self-hook, not the live bug (MOR-1480)', () => {
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
      ],
    };

    function appendFreqEntryInput(): HTMLInputElement {
      const input = document.createElement('input');
      input.setAttribute('data-freq-entry', '');
      document.body.appendChild(input);
      return input;
    }

    async function mountBareFrequencyDisplay(active = true) {
      const { default: FrequencyDisplayInteractive } = await import(
        '../../../primitives/frequency/FrequencyDisplayInteractive.svelte'
      );
      const target = document.createElement('div');
      document.body.appendChild(target);
      const component = mount(FrequencyDisplayInteractive, {
        target,
        props: { freq: 14074000, active },
      });
      flushSync();
      components.push(component);
      return target.querySelector<HTMLElement>('.freq')!;
    }

    it('routes a digit typed on a standalone active FrequencyDisplayInteractive to the frequency-entry input, not the band hotkey', async () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const freqDisplay = await mountBareFrequencyDisplay(true);
      const entryInput = appendFreqEntryInput();
      freqDisplay.focus();
      expect(document.activeElement).toBe(freqDisplay);

      const event = new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true });
      window.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);
      expect(onAction).not.toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select' }),
      );
      expect(document.activeElement).toBe(entryInput);
      expect(entryInput.value).toBe('7');
    });

    // MOR-1480 owner ruling A: REVERSES this test's original "falls through
    // to the band hotkey" expectation for the inactive-receiver case — a
    // standalone mount marked inactive must swallow the digit (routing
    // nothing, to avoid committing to the wrong VFO) AND never band-hop.
    it('swallows the digit — routing nothing and band-hopping nothing — when the standalone display is marked inactive', async () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const freqDisplay = await mountBareFrequencyDisplay(false);
      const entryInput = appendFreqEntryInput();
      freqDisplay.focus();

      const event = new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true });
      window.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);
      expect(onAction).not.toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select' }),
      );
      expect(entryInput.value).toBe('');
      expect(document.activeElement).toBe(freqDisplay);
    });
  });

  // MOR-1480 — the ACTUAL live mechanism (verifier F1a) and owner ruling A,
  // reproduced and verified against the REAL semantic tree instead of
  // hand-rolled DOM or the dead header path: `VfoSurface.svelte`, mounted
  // for real via the `2/main_sub` topology fixture. `hasTunableFrequency`
  // gates on `isActiveSlot`, not radio-wide `isActive`
  // (`VfoSurface.svelte:269`), so the fixture's SUB-A tile
  // (`isActiveSlot: true`, `isActive: false`) mounts a focusable, tunable
  // `[data-vfo-freq]` display while MAIN-A is the radio's active receiver —
  // exactly the dual-receiver shape `BandSurface.svelte` and
  // `SemanticRadioSurfaces.svelte` produce on the bench.
  describe('digit routing against the real semantic VfoSurface tree (MOR-1480 mechanism + owner ruling A)', () => {
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
      ],
    };

    function mountRealVfoSurface(): HTMLElement {
      const target = document.createElement('div');
      document.body.appendChild(target);
      const component = mount(VfoSurface, {
        target,
        props: { viewModel: topologyFixtures['2/main_sub'], onTuneFrequency: vi.fn() },
      });
      flushSync();
      components.push(component);
      return target;
    }

    function appendFreqEntryInput(): HTMLInputElement {
      const input = document.createElement('input');
      input.setAttribute('data-freq-entry', '');
      document.body.appendChild(input);
      return input;
    }

    /** The focusable root FrequencyDisplayInteractive mounts for a given receiver's active-slot tile. */
    function focusTargetFor(surface: HTMLElement, receiver: 'MAIN' | 'SUB'): HTMLElement {
      const el = surface.querySelector<HTMLElement>(
        `[data-vfo-tile][data-vfo-receiver="${receiver}"] .freq`,
      );
      if (!el) throw new Error(`no tunable .freq found for receiver ${receiver}`);
      return el;
    }

    // Verifier F1a mechanism, reproduced against the real tree: BandSurface
    // disables/removes `[data-freq-entry]` when the active receiver or
    // tuning bounds aren't yet known (`BandSurface.svelte:367-368`).
    // `routeDigitToFrequencyEntry` returns `false` in both cases — pre-fix,
    // the caller fell through to `resolveAction()` and fired `band_select`.
    it('swallows the digit — no band_select — when the entry input is absent, focus on the real ACTIVE tile', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const surface = mountRealVfoSurface();
      const freqDisplay = focusTargetFor(surface, 'MAIN');
      freqDisplay.focus();
      expect(document.activeElement).toBe(freqDisplay);

      const event = new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true });
      window.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);
      expect(onAction).not.toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select' }),
      );
    });

    it('swallows the digit — no band_select, no routing — when the entry input is disabled, focus on the real ACTIVE tile', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const surface = mountRealVfoSurface();
      const entryInput = appendFreqEntryInput();
      entryInput.disabled = true;
      const freqDisplay = focusTargetFor(surface, 'MAIN');
      freqDisplay.focus();

      const event = new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true });
      window.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);
      expect(onAction).not.toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select' }),
      );
      expect(entryInput.value).toBe('');
    });

    // Repairs/keeps the "active display routes into the entry" case against
    // the real tree (not just the hand-rolled MOR-1444 block above).
    it('routes the digit into the frequency-entry input when the real ACTIVE tile is focused and the entry is enabled', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const surface = mountRealVfoSurface();
      const entryInput = appendFreqEntryInput();
      const freqDisplay = focusTargetFor(surface, 'MAIN');
      freqDisplay.focus();

      const event = new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true });
      window.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);
      expect(onAction).not.toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select' }),
      );
      expect(document.activeElement).toBe(entryInput);
      expect(entryInput.value).toBe('7');
    });

    // MOR-1480 owner ruling A, reproduced against the real tree: SUB-A is
    // tunable (`isActiveSlot: true`) but NOT the active receiver
    // (`isActive: false`, MAIN is active in this fixture). Pre-rework code
    // (isFrequencyDisplayFocused gating the swallow decision) fell through
    // to resolveAction() and fired band_select here — this test must FAIL
    // on that pre-rework code (see red-phase verification).
    it('swallows the digit — no routing, no band_select — when the real INACTIVE-receiver tile is focused', () => {
      const onAction = vi.fn();
      mountHandler({ config: configWithDigitBinding, onAction });
      const surface = mountRealVfoSurface();
      const entryInput = appendFreqEntryInput();
      const freqDisplay = focusTargetFor(surface, 'SUB');
      freqDisplay.focus();
      expect(document.activeElement).toBe(freqDisplay);

      const event = new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true });
      window.dispatchEvent(event);

      expect(event.defaultPrevented).toBe(true);
      expect(onAction).not.toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select' }),
      );
      expect(entryInput.value).toBe('');
      expect(document.activeElement).toBe(freqDisplay);
    });
  });
});
