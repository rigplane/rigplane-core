import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { writable, fromStore } from 'svelte/store';
import type { RadioViewModel } from '../../../semantic/radio-view-model';

import KeyboardHandler from '../KeyboardHandler.svelte';
import {
  resolveSequenceContinuation,
  resolveSequenceStarts,
  type KeyboardConfig,
  type KeyboardActionConfig,
} from '../keyboard-map';
import VfoSurface from '../../../semantic/VfoSurface.svelte';
import ValueControl from '../../controls/value-control/ValueControl.svelte';
import SegmentedButton from '../../controls/SegmentedButton.svelte';
import AttenuatorControl from '../../controls/AttenuatorControl.svelte';
import ActiveReceiverToggle from '../../vfo/ActiveReceiverToggle.svelte';
import { wheelControl } from '../../controls/value-control/wheel-control';
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
    vi.useRealTimers();
  });

  it('dispatches a configured single-key action', () => {
    const onAction = vi.fn();
    mountHandler({ config, onAction });

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp' }));

    expect(onAction).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'tune', params: { direction: 'up', fine: false } }),
    );
  });

  describe('retained inert frequency readouts (MOR-2364)', () => {
    const bindings: KeyboardConfig = { ...config, bindings: [
      ...config.bindings,
      ...['ArrowDown', 'PageUp', 'PageDown', 'j'].map((key) => ({
        id: key, section: 'Tuning', sequence: [key], action: 'tune',
      })),
      { id: 'leader-tune', section: 'Tuning', sequence: ['g', 't'], action: 'tune' },
      { id: 'leader-band', section: 'Tuning', sequence: ['g', 'b'], action: 'band_select' },
      { id: 'band-digit', section: 'Bands', sequence: ['7'], action: 'band_select' },
      { id: 'band-letter', section: 'Bands', sequence: ['b'], action: 'band_select' },
      { id: 'stop', section: 'Safety', sequence: ['Escape'], action: 'scan_stop' },
      { id: 'leader-stop', section: 'Safety', sequence: ['g', 's'], action: 'scan_stop' },
    ] };
    function harness(initial: 'current' | 'null' | 'disabled' = 'current') {
      const base = topologyFixtures['1/single'];
      const current: RadioViewModel = { ...base, vfos: base.vfos.map((vfo) => ({ ...vfo, display: {
        frequencyHz: { state: 'current', value: vfo.frequencyHz! },
        mode: { state: 'current', value: 'USB' }, filter: { state: 'current', value: 'FIL1' },
      } })) };
      const initialView = initial === 'null' ? { ...current, vfos: current.vfos.map((vfo) => ({ ...vfo, frequencyHz: null })) } : current;
      const state = writable({ viewModel: initialView, disabled: initial === 'disabled' });
      const live = fromStore(state);
      const target = document.createElement('div'); document.body.appendChild(target);
      const onTuneFrequency = vi.fn(); const onAction = vi.fn();
      components.push(mount(VfoSurface, { target, props: { onTuneFrequency,
        get viewModel() { return live.current.viewModel; }, get disabled() { return live.current.disabled; },
      } }));
      const handler = mountHandler({ config: bindings, onAction });
      const input = document.createElement('input'); input.setAttribute('data-freq-entry', ''); document.body.appendChild(input);
      flushSync(); const frequency = target.querySelector<HTMLElement>('.freq')!; frequency.focus();
      function set(kind: 'current' | 'null' | 'ancestor-stale' | 'disabled') {
        state.set({ disabled: kind === 'disabled', viewModel: { ...current, vfos: current.vfos.map((vfo) => ({ ...vfo,
          frequencyHz: kind === 'null' ? null : vfo.frequencyHz,
          display: { ...vfo.display!, frequencyHz: { state: kind === 'current' || kind === 'disabled' ? 'current' : 'stale', value: vfo.frequencyHz! } },
        })) } }); flushSync();
      }
      const key = (key: string, flags: KeyboardEventInit = {}) => {
        const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...flags });
        frequency.dispatchEvent(event); flushSync(); return event;
      };
      return { set, key, frequency, input, onAction, onTuneFrequency, handler };
    }
    it.each(['null', 'disabled'] as const)('blocks direct and leader tuning/digits while %s, then restores current routing', (kind) => {
      const h = harness(); h.set(kind);
      expect(document.activeElement).toBe(h.frequency);
      for (const key of ['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'j', 'b', '7']) h.key(key);
      for (const suffix of ['t', 'b', '7']) { h.key('g'); h.key(suffix); }
      expect(h.onAction).not.toHaveBeenCalled(); expect(h.onTuneFrequency).not.toHaveBeenCalled();
      expect(h.input.value).toBe(''); expect(document.activeElement).toBe(h.frequency);
      expect(h.handler.querySelector('.keyboard-leader-pill')).toBeNull();
      h.set('current'); h.key('ArrowUp');
      expect(h.onAction).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ action: 'tune' }));
      h.key('7'); expect(h.input.value).toBe('7'); expect(document.activeElement).toBe(h.input);
    });
    // MOR-2425/R29+R40: 'ancestor-stale' left the inert set.
    it('routes direct and leader tuning while ancestor-stale', () => {
      const h = harness(); h.set('ancestor-stale');
      h.key('ArrowUp');
      expect(h.onAction).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ action: 'tune' }));
      h.key('g'); h.key('t');
      expect(h.onAction).toHaveBeenCalledTimes(2);
    });

    it('checks a leader continuation against the current inert state', () => {
      const h = harness(); h.key('g'); h.set('null'); h.key('t');
      expect(h.onAction).not.toHaveBeenCalled(); expect(h.handler.querySelector('.keyboard-leader-pill')).toBeNull();
      h.set('current'); h.key('g'); h.key('t');
      expect(h.onAction).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ action: 'tune' }));
    });
    it.each(['null', 'disabled'] as const)('cannot arm a tuning leader from an initially %s readout', (initial) => {
      const h = harness(initial); h.key('ArrowUp'); h.key('7'); h.key('g');
      h.set('current'); h.key('t');
      expect(h.onAction).not.toHaveBeenCalled(); expect(h.input.value).toBe('');
      h.key('g'); h.key('t');
      expect(h.onAction).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ action: 'tune' }));
    });
    it('preserves Tab, native modifiers, stop/help and shortcuts outside an inert readout', () => {
      const h = harness(); h.set('ancestor-stale');
      h.key('g'); expect(h.key('Tab').defaultPrevented).toBe(false);
      expect(h.handler.querySelector('.keyboard-leader-pill')).toBeNull();
      for (const flags of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }]) {
        expect(h.key('7', flags).defaultPrevented).toBe(false);
        expect(h.key('ArrowUp', flags).defaultPrevented).toBe(false);
      }
      h.key('Escape'); h.key('g'); h.key('s');
      expect(h.onAction.mock.calls.map(([action]) => action.action)).toEqual(['scan_stop', 'scan_stop']);
      h.key('?'); expect(h.handler.querySelector('[role="dialog"]')).not.toBeNull();
      h.onAction.mockClear(); h.frequency.blur();
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp' }));
      window.dispatchEvent(new KeyboardEvent('keydown', { key: '7' }));
      expect(h.onAction.mock.calls.map(([action]) => action.action)).toEqual(['tune', 'band_select']);
      expect(h.input.value).toBe('');
    });
  });

  // MOR-2512 §2 — with a digit selected on the frequency readout, one
  // ArrowUp press was handled twice: the readout stepped the digit AND the
  // global binding fired, because the readout declared no arrow ownership.
  // The readout now declares data-owns-arrows="vertical" on its root ONLY
  // while a digit is selected, so the global layer yields exactly then and
  // keeps firing with no digit selected (MOR-2364's no-digit pins above).
  describe('frequency readout owns ArrowUp only while a digit is selected (MOR-2512)', () => {
    const stepConfig: KeyboardConfig = {
      ...config,
      bindings: [
        {
          id: 'step-up',
          section: 'Tuning',
          label: 'Increase tuning step',
          sequence: ['ArrowUp'],
          action: 'adjust_tuning_step',
          params: { direction: 'up' },
        },
      ],
    };

    /** Real semantic VfoSurface mount with a live, non-inert readout —
     * the MOR-2364 harness shape, minus its inert-state machinery. */
    function mountReadoutHarness() {
      const base = topologyFixtures['1/single'];
      const current: RadioViewModel = { ...base, vfos: base.vfos.map((vfo) => ({ ...vfo, display: {
        frequencyHz: { state: 'current', value: vfo.frequencyHz! },
        mode: { state: 'current', value: 'USB' }, filter: { state: 'current', value: 'FIL1' },
      } })) };
      const target = document.createElement('div');
      document.body.appendChild(target);
      const onTuneFrequency = vi.fn();
      const onAction = vi.fn();
      components.push(mount(VfoSurface, { target, props: { viewModel: current, onTuneFrequency } }));
      mountHandler({ config: stepConfig, onAction });
      flushSync();
      const frequency = target.querySelector<HTMLElement>('.freq')!;
      frequency.focus();
      return { frequency, onAction, onTuneFrequency };
    }

    it('fires adjust_tuning_step with no digit selected, and yields to the readout once a digit is selected', () => {
      const h = mountReadoutHarness();
      expect(document.activeElement).toBe(h.frequency);
      expect(h.frequency.hasAttribute('data-owns-arrows')).toBe(false);

      h.frequency.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));
      flushSync();
      expect(h.onAction).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ action: 'adjust_tuning_step', params: { direction: 'up' } }),
      );

      const digits = h.frequency.querySelectorAll<HTMLElement>('.digit');
      digits[digits.length - 1].dispatchEvent(new MouseEvent('click', { bubbles: true }));
      flushSync();
      expect(h.frequency.getAttribute('data-owns-arrows')).toBe('vertical');

      h.frequency.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));
      flushSync();
      expect(h.onAction).toHaveBeenCalledTimes(1);
      expect(h.onTuneFrequency).toHaveBeenCalledExactlyOnceWith('MAIN', 14_195_001);
    });
  });

  // MOR-2514 — the release half of the MOR-2512 ownership contract:
  // Escape while a digit is selected ends the selection and is consumed
  // (the global Escape binding must not also fire), and afterwards a
  // bare ArrowUp reaches the global map again. The hint title is pinned
  // as the en-US literal resolved through the real VfoSurface -> t()
  // chain, never as t(key).
  describe('Escape releases the selected digit and restores global ArrowUp (MOR-2514)', () => {
    const releaseConfig: KeyboardConfig = {
      ...config,
      bindings: [
        {
          id: 'step-up',
          section: 'Tuning',
          label: 'Increase tuning step',
          sequence: ['ArrowUp'],
          action: 'adjust_tuning_step',
          params: { direction: 'up' },
        },
        { id: 'stop', section: 'Safety', label: 'Stop scan', sequence: ['Escape'], action: 'scan_stop' },
      ],
    };

    function mountReleaseHarness() {
      const base = topologyFixtures['1/single'];
      const current: RadioViewModel = { ...base, vfos: base.vfos.map((vfo) => ({ ...vfo, display: {
        frequencyHz: { state: 'current', value: vfo.frequencyHz! },
        mode: { state: 'current', value: 'USB' }, filter: { state: 'current', value: 'FIL1' },
      } })) };
      const target = document.createElement('div');
      document.body.appendChild(target);
      const onTuneFrequency = vi.fn();
      const onAction = vi.fn();
      components.push(mount(VfoSurface, { target, props: { viewModel: current, onTuneFrequency } }));
      mountHandler({ config: releaseConfig, onAction });
      flushSync();
      const frequency = target.querySelector<HTMLElement>('.freq')!;
      frequency.focus();
      return { frequency, onAction, onTuneFrequency };
    }

    it('consumes Escape while selected, then returns bare ArrowUp to adjust_tuning_step', () => {
      const h = mountReleaseHarness();
      expect(h.frequency.hasAttribute('data-owns-arrows')).toBe(false);

      h.frequency.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));
      flushSync();
      expect(h.onAction).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ action: 'adjust_tuning_step', params: { direction: 'up' } }),
      );

      const lastDigit = h.frequency.querySelectorAll<HTMLElement>('.digit');
      const oneHzDigit = lastDigit[lastDigit.length - 1];
      oneHzDigit.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      flushSync();
      expect(h.frequency.getAttribute('data-owns-arrows')).toBe('vertical');
      expect(oneHzDigit.getAttribute('title')).toBe('Esc or click away to deselect');

      h.frequency.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));
      flushSync();
      expect(h.onAction).toHaveBeenCalledTimes(1);
      expect(h.onTuneFrequency).toHaveBeenCalledExactlyOnceWith('MAIN', 14_195_001);

      const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      h.frequency.dispatchEvent(escape);
      flushSync();
      expect(escape.defaultPrevented).toBe(true);
      expect(h.onAction).toHaveBeenCalledTimes(1);
      expect(h.frequency.hasAttribute('data-owns-arrows')).toBe(false);

      h.frequency.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));
      flushSync();
      expect(h.onAction).toHaveBeenCalledTimes(2);
      expect(h.onAction).toHaveBeenLastCalledWith(
        expect.objectContaining({ action: 'adjust_tuning_step', params: { direction: 'up' } }),
      );
    });
  });

  // MOR-2507 — bench-observed on IC-7610/IC-7300: with the AF volume slider
  // focused, one arrow press was handled twice — the slider moved AND the
  // global tuning shortcut fired. A widget that consumes arrow keys declares
  // it on its focusable element via data-owns-arrows (axis token
  // horizontal/vertical/both, plus "shift" for deliberate Shift gestures);
  // the global handler yields exactly those keys: a bare role is not
  // evidence of consumption, and modified arrows (the Ctrl+Arrow volume and
  // gain bindings from rigs/_keyboard-default.toml) stay global.
  describe('focused widget arrow ownership via data-owns-arrows (MOR-2507)', () => {
    const arrowConfig: KeyboardConfig = {
      ...config,
      bindings: [
        ...config.bindings,
        {
          id: 'tune-right',
          section: 'Tuning',
          label: 'Tune Right',
          sequence: ['ArrowRight'],
          action: 'tune',
          repeatable: true,
          params: { direction: 'up', fine: false },
        },
        {
          id: 'tune-left',
          section: 'Tuning',
          label: 'Tune Left',
          sequence: ['ArrowLeft'],
          action: 'tune',
          repeatable: true,
          params: { direction: 'down', fine: false },
        },
        {
          id: 'af-level-up',
          section: 'Audio',
          label: 'AF level up',
          sequence: ['ArrowUp'],
          modifiers: ['CTRL'],
          action: 'adjust_af_level',
          repeatable: true,
          params: { delta: 5 },
        },
        {
          id: 'rf-gain-up',
          section: 'RF',
          label: 'RF gain up',
          sequence: ['ArrowUp'],
          modifiers: ['CTRL', 'SHIFT'],
          action: 'adjust_rf_gain',
          repeatable: true,
          params: { delta: 5 },
        },
        {
          id: 'band-7',
          section: 'Bands',
          label: 'Select band 7',
          sequence: ['7'],
          action: 'band_select',
          params: { index: 7 },
        },
      ],
    };

    /** Dispatches on the focused element, as a real keypress would target it. */
    function press(key: string, flags: KeyboardEventInit = {}): KeyboardEvent {
      const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...flags });
      document.activeElement!.dispatchEvent(event);
      return event;
    }

    function appendFocusedOwner(declared: string, attrs: Record<string, string> = {}): HTMLElement {
      const el = document.createElement('div');
      el.setAttribute('data-owns-arrows', declared);
      for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, value);
      el.tabIndex = 0;
      document.body.appendChild(el);
      el.focus();
      expect(document.activeElement).toBe(el);
      return el;
    }

    /** The real shared value-control mount: focusable [role="slider"] that
     * consumes all four arrows (Shift = fine step). */
    function mountFocusedHBar(): HTMLElement {
      const target = document.createElement('div');
      document.body.appendChild(target);
      components.push(mount(ValueControl, {
        target,
        props: {
          value: 5, min: 0, max: 10, step: 1, renderer: 'hbar',
          label: 'AF', onChange: vi.fn(),
        },
      }));
      flushSync();
      const slider = target.querySelector<HTMLElement>('[role="slider"]')!;
      slider.focus();
      expect(document.activeElement).toBe(slider);
      return slider;
    }

    it('a real hbar value control owns its arrows', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });

      mountFocusedHBar();
      press('ArrowUp');
      press('ArrowRight');

      expect(onAction).not.toHaveBeenCalled();
    });

    it('a non-editable value control claims nothing: arrows keep tuning (RadioLayout focuses it programmatically)', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });
      const target = document.createElement('div');
      document.body.appendChild(target);
      components.push(mount(ValueControl, {
        target,
        props: {
          value: 5, min: 0, max: 10, step: 1, renderer: 'hbar', disabled: true,
          label: 'AF', onChange: vi.fn(),
        },
      }));
      flushSync();
      const slider = target.querySelector<HTMLElement>('[role="slider"]')!;
      slider.focus();
      expect(document.activeElement).toBe(slider);

      press('ArrowUp');

      expect(onAction).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ action: 'tune', params: { direction: 'up', fine: false } }),
      );
      expect(slider.getAttribute('aria-valuenow')).toBe('5');
    });

    it('modified arrows on an owning widget stay global (Ctrl/Ctrl+Shift+ArrowUp bindings)', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });

      mountFocusedHBar();
      press('ArrowUp', { ctrlKey: true });
      press('ArrowUp', { ctrlKey: true, shiftKey: true });

      expect(onAction).toHaveBeenCalledTimes(2);
      expect(onAction).toHaveBeenNthCalledWith(1,
        expect.objectContaining({ action: 'adjust_af_level', params: { delta: 5 } }));
      expect(onAction).toHaveBeenNthCalledWith(2,
        expect.objectContaining({ action: 'adjust_rf_gain', params: { delta: 5 } }));
    });

    it('a plain role="radio" button without the hook (real AttenuatorControl) still tunes', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });
      const target = document.createElement('div');
      document.body.appendChild(target);
      components.push(mount(AttenuatorControl, {
        target,
        props: { values: [0, 10, 20], selected: 0, onchange: vi.fn() },
      }));
      flushSync();
      const radio = target.querySelector<HTMLElement>('[role="radio"]')!;
      radio.focus();
      expect(document.activeElement).toBe(radio);

      press('ArrowUp');

      expect(onAction).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ action: 'tune', params: { direction: 'up', fine: false } }),
      );
    });

    it('bare roles are not owners: role=slider/radiogroup/separator without the hook still tune', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });

      for (const role of ['slider', 'radiogroup', 'separator']) {
        const el = document.createElement('div');
        el.setAttribute('role', role);
        el.tabIndex = 0;
        document.body.appendChild(el);
        el.focus();
        press('ArrowUp');
        el.remove();
      }

      expect(onAction).toHaveBeenCalledTimes(3);
    });

    it('the real segmented control owns its arrows', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });
      const target = document.createElement('div');
      document.body.appendChild(target);
      components.push(mount(SegmentedButton, {
        target,
        props: {
          options: [{ value: 'lsb', label: 'LSB' }, { value: 'usb', label: 'USB' }],
          selected: 'lsb',
          onchange: vi.fn(),
        },
      }));
      flushSync();
      const group = target.querySelector<HTMLElement>('[role="radiogroup"]')!;
      group.focus();
      expect(document.activeElement).toBe(group);

      press('ArrowRight');

      expect(onAction).not.toHaveBeenCalled();
    });

    it('the real active-receiver toggle owns its arrows', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });
      const target = document.createElement('div');
      document.body.appendChild(target);
      components.push(mount(ActiveReceiverToggle, {
        target,
        props: { active: 'MAIN', onChange: vi.fn() },
      }));
      flushSync();
      const radio = target.querySelector<HTMLElement>('[role="radio"]')!;
      radio.focus();
      expect(document.activeElement).toBe(radio);

      press('ArrowLeft');

      expect(onAction).not.toHaveBeenCalled();
    });

    it('a vertical-only owner (splitter shape) yields Up/Down but keeps Left/Right global', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });
      appendFocusedOwner('vertical', { role: 'separator' });

      press('ArrowUp');
      expect(onAction).not.toHaveBeenCalled();

      press('ArrowLeft');
      expect(onAction).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ action: 'tune', params: { direction: 'down', fine: false } }),
      );
    });

    it('a focused child inside an owning container is still owned', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });
      const owner = document.createElement('div');
      owner.setAttribute('data-owns-arrows', 'both');
      const child = document.createElement('div');
      child.tabIndex = 0;
      owner.appendChild(child);
      document.body.appendChild(owner);
      child.focus();
      expect(document.activeElement).toBe(child);

      press('ArrowUp');

      expect(onAction).not.toHaveBeenCalled();
    });

    it('an arrow dispatched on window with an owning widget focused does not tune (activeElement, not event.target)', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });
      appendFocusedOwner('both');

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));

      expect(onAction).not.toHaveBeenCalled();
    });

    it('a role="button" element without the hook still tunes', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });
      const el = document.createElement('div');
      el.setAttribute('role', 'button');
      el.tabIndex = 0;
      document.body.appendChild(el);
      el.focus();

      press('ArrowUp');

      expect(onAction).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ action: 'tune', params: { direction: 'up', fine: false } }),
      );
    });

    it('does not dispatch the tuning shortcut when a native range input is focused', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });
      const range = document.createElement('input');
      range.type = 'range';
      document.body.appendChild(range);
      range.focus();
      expect(document.activeElement).toBe(range);

      range.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true }));

      expect(onAction).not.toHaveBeenCalled();
    });

    it('still tunes when the same arrow keys are pressed with focus on document.body', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp' }));
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight' }));
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft' }));

      expect(onAction).toHaveBeenCalledTimes(3);
      expect(onAction).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'tune', params: { direction: 'up', fine: false } }),
      );
    });

    it('still dispatches non-arrow shortcuts while an owning widget holds focus', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });
      appendFocusedOwner('both');

      window.dispatchEvent(new KeyboardEvent('keydown', { key: '7', bubbles: true, cancelable: true }));

      expect(onAction).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'band_select', params: { index: 7 } }),
      );
    });

    it('disarms a pending leader sequence when an arrow is owned by the focused widget', () => {
      const onAction = vi.fn();
      const target = mountHandler({ config: arrowConfig, onAction });

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'g' }));
      flushSync();
      expect(target.querySelector('.keyboard-leader-pill')).not.toBeNull();

      const owner = appendFocusedOwner('both');
      press('ArrowUp');
      flushSync();
      expect(target.querySelector('.keyboard-leader-pill')).toBeNull();

      owner.blur();
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'v', bubbles: true, cancelable: true }));
      expect(onAction).not.toHaveBeenCalledWith(
        expect.objectContaining({ action: 'focus_target' }),
      );
    });

    it('the wheelControl mount retracts the declaration when editable flips while focused', () => {
      const onAction = vi.fn();
      mountHandler({ config: arrowConfig, onAction });
      const node = document.createElement('div');
      node.tabIndex = 0;
      document.body.appendChild(node);
      node.focus();
      const editableLease = { view: { editable: true }, wheel: vi.fn() };
      const action = wheelControl(node, { view: { editable: true }, lease: editableLease });
      expect(node.getAttribute('data-owns-arrows')).toBe('both shift');

      press('ArrowUp');
      expect(onAction).not.toHaveBeenCalled();

      action.update({ view: { editable: false }, lease: editableLease });
      expect(node.getAttribute('data-owns-arrows')).toBeNull();

      press('ArrowUp');
      expect(onAction).toHaveBeenCalledExactlyOnceWith(
        expect.objectContaining({ action: 'tune', params: { direction: 'up', fine: false } }),
      );

      action.destroy();
      node.remove();
    });
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

  describe('shared-prefix leader sequences (MOR-1636)', () => {
    const sharedPrefixConfig: KeyboardConfig = {
      ...config,
      bindings: [
        ...config.bindings,
        {
          id: 'focus-af',
          section: 'Focus',
          label: 'Focus AF',
          sequence: ['g', 'a'],
          action: 'focus_target',
          params: { target: 'af' },
        },
        {
          id: 'focus-rf',
          section: 'Focus',
          label: 'Focus RF',
          sequence: ['g', 'r'],
          action: 'focus_target',
          params: { target: 'rf' },
        },
        {
          id: 'focus-filter',
          section: 'Focus',
          label: 'Focus Filter',
          sequence: ['g', 'f'],
          action: 'focus_target',
          params: { target: 'filter' },
        },
      ],
    };

    it('keeps every same-prefix candidate available to the continuation resolver', () => {
      const starts = resolveSequenceStarts({ key: 'g' }, sharedPrefixConfig);

      expect(starts.map((binding) => binding.id)).toEqual([
        'focus-vfo', 'focus-af', 'focus-rf', 'focus-filter',
      ]);
      expect(resolveSequenceContinuation(starts, { key: 'f' })).toEqual(
        expect.objectContaining({ id: 'focus-filter', params: { target: 'filter' } }),
      );
    });

    it.each([
      ['a', 'af'],
      ['r', 'rf'],
      ['f', 'filter'],
    ])('dispatches g then %s to the declared focus target', (suffix, target) => {
      const onAction = vi.fn();
      mountHandler({ config: sharedPrefixConfig, onAction });

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'g' }));
      window.dispatchEvent(new KeyboardEvent('keydown', { key: suffix, bubbles: true, cancelable: true }));

      expect(onAction).toHaveBeenCalledTimes(1);
      expect(onAction).toHaveBeenCalledWith(
        expect.objectContaining({ action: 'focus_target', params: { target } }),
      );
    });

    it('clears the pending leader and dispatches nothing for an invalid suffix', () => {
      const onAction = vi.fn();
      const target = mountHandler({ config: sharedPrefixConfig, onAction });

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'g' }));
      flushSync();
      expect(target.querySelector('.keyboard-leader-pill')).not.toBeNull();

      const invalid = new KeyboardEvent('keydown', { key: 'x', bubbles: true, cancelable: true });
      window.dispatchEvent(invalid);
      flushSync();

      expect(invalid.defaultPrevented).toBe(false);
      expect(onAction).not.toHaveBeenCalled();
      expect(target.querySelector('.keyboard-leader-pill')).toBeNull();
    });

    it('clears every pending candidate on leader timeout and dispatches nothing', () => {
      vi.useFakeTimers();
      const onAction = vi.fn();
      const target = mountHandler({ config: sharedPrefixConfig, onAction });

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'g' }));
      flushSync();
      expect(target.querySelector('.keyboard-leader-pill')).not.toBeNull();

      vi.advanceTimersByTime(sharedPrefixConfig.leaderTimeoutMs);
      flushSync();
      expect(target.querySelector('.keyboard-leader-pill')).toBeNull();

      const continuation = new KeyboardEvent('keydown', { key: 'f', bubbles: true, cancelable: true });
      window.dispatchEvent(continuation);

      expect(continuation.defaultPrevented).toBe(false);
      expect(onAction).not.toHaveBeenCalled();
    });
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
  // (`VfoSurface.svelte:269`), so the fixture's SUB record
  // (`isActiveSlot: true`, `isActive: false`) mounts a focusable, tunable
  // `[data-vfo-freq]` display while MAIN is the radio's active receiver —
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

    // MOR-1480 owner ruling A, reproduced against the real tree: SUB is
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
