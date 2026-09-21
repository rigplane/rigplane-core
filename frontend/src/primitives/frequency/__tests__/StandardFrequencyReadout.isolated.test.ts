import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its test-only effect harness.
import { effect_root } from 'svelte/internal/client';
import type { ComponentProps } from 'svelte';
import type { FrequencyRenderer } from '../../../../component-kit-api/src/index';

const selectedFrequency = vi.hoisted(() => ({ current: undefined as unknown }));
vi.mock('../../../component-kits/activation', () => ({
  getSelectedFrequencyReadout: () => selectedFrequency.current,
}));

import StandardFrequencyReadout from '../StandardFrequencyReadout.svelte';
import AlternateFrequencyReadoutHarness, {
  clearRetainedInteractions, retainedInteractions,
} from './AlternateFrequencyReadoutHarness.svelte';
import { projectFrequencyReadout } from '../frequency-readout';
import {
  createFrequencyInteraction,
  type FrequencyInteraction,
} from '../frequency-interaction.svelte';
import FrequencyDisplay from '../../../components-v2/display/FrequencyDisplay.svelte';

const mounted: ReturnType<typeof mount>[] = [];
const roots: (() => void)[] = [];

function mountReadout(props: ComponentProps<typeof StandardFrequencyReadout>): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  mounted.push(mount(StandardFrequencyReadout, { target, props }));
  flushSync();
  return target;
}

beforeEach(() => {
  selectedFrequency.current = undefined;
  clearRetainedInteractions();
});

afterEach(() => {
  mounted.forEach((component) => unmount(component));
  mounted.length = 0;
  roots.forEach((dispose) => dispose());
  roots.length = 0;
  selectedFrequency.current = undefined;
  clearRetainedInteractions();
  document.body.innerHTML = '';
});

describe('frequency readout projection', () => {
  it('keeps confirmed, historical display, and pending values distinct', () => {
    const model = projectFrequencyReadout({
      confirmedHz: 14_250_000,
      displayHz: 7_100_000,
      pendingDisplayHz: 14_260_000,
      pendingAnnouncement: 'Pending, not yet confirmed',
    });

    expect(model).toMatchObject({
      confirmedHz: 14_250_000,
      displayHz: 7_100_000,
      pendingDisplayHz: 14_260_000,
      shownHz: 14_260_000,
      source: 'pending',
      status: 'pending',
      pendingAnnouncement: 'Pending, not yet confirmed',
    });
    expect(model.digits.map((digit) => digit.char).join('')).toBe('14260000');
  });

  it('preserves an explicit null display and valid zero', () => {
    expect(projectFrequencyReadout({ confirmedHz: 14_250_000, displayHz: null })).toMatchObject({
      displayHz: null,
      shownHz: null,
      source: 'display',
    });
    expect(projectFrequencyReadout({ confirmedHz: 0 })).toMatchObject({
      shownHz: 0,
      textGroups: { mhz: '0', khz: '000', hz: '000' },
    });
  });
});

describe('StandardFrequencyReadout', () => {
  it('keeps the passive FrequencyDisplay facade grouped and non-interactive', () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    mounted.push(mount(FrequencyDisplay, {
      target,
      props: { freq: 14_235_000, compact: true, active: false },
    }));
    flushSync();
    const root = target.querySelector<HTMLElement>('.freq')!;

    expect(root.textContent?.replace(/\s/g, '')).toBe('14.235.000');
    expect(root.classList.contains('compact')).toBe(true);
    expect(root.classList.contains('inactive')).toBe(true);
    expect(root.querySelectorAll('.digits')).toHaveLength(3);
    expect(root.hasAttribute('aria-disabled')).toBe(false);
    expect(root.hasAttribute('data-vfo-freq')).toBe(false);
  });

  it('renders the passive presentation with its grouped zero and appearance classes', () => {
    const target = mountReadout({
      model: projectFrequencyReadout({ confirmedHz: 0 }),
      presentation: 'passive',
      compact: true,
      active: false,
    });
    const root = target.querySelector<HTMLElement>('.freq')!;

    expect(root.textContent?.replace(/\s/g, '')).toBe('0.000.000');
    expect(root.classList.contains('compact')).toBe(true);
    expect(root.classList.contains('inactive')).toBe(true);
    expect(root.querySelectorAll('.digits')).toHaveLength(3);
    expect(root.hasAttribute('tabindex')).toBe(false);
    expect(root.hasAttribute('data-freq-status')).toBe(false);
  });

  it('renders interactive pending text, status, and linked announcement from the model', () => {
    const target = mountReadout({
      model: projectFrequencyReadout({
        confirmedHz: 14_250_000,
        pendingDisplayHz: 14_260_000,
        pendingAnnouncement: 'Pending, not yet confirmed',
      }),
      presentation: 'interactive',
      active: true,
    });
    const root = target.querySelector<HTMLElement>('.freq')!;
    const descriptionId = root.getAttribute('aria-describedby');

    expect(root.dataset.freqStatus).toBe('pending');
    expect(Array.from(root.querySelectorAll('.digit')).map((digit) => digit.textContent).join('')).toBe('14260000');
    expect(descriptionId).toBeTruthy();
    expect(target.querySelector(`#${descriptionId}`)?.textContent).toBe('Pending, not yet confirmed');
  });

  it('renders the presentation-specific unknown text', () => {
    const unknown = projectFrequencyReadout({ confirmedHz: null });
    expect(mountReadout({ model: unknown, presentation: 'interactive' }).textContent?.trim()).toBe('—');
    expect(mountReadout({ model: unknown, presentation: 'passive' }).textContent?.replace(/\s/g, '')).toBe('--.---.---');
  });
});

// MOR-2512 §2 — the readout owns the arrow keys ONLY while a digit is
// selected: with no digit selected the global keyboard map keeps its
// arrows (MOR-2364's no-digit path), and the ownership declaration is the
// data-owns-arrows hook the global layer's focusedElementOwnsArrowKey
// reads off the closest ancestor of the active element.
describe('digit-scoped arrow ownership on the readout root (MOR-2512)', () => {
  function mountInteractiveReadout() {
    const model = projectFrequencyReadout({ confirmedHz: 14_250_000 });
    const onFreqChange = vi.fn();
    let interaction!: FrequencyInteraction;
    roots.push(effect_root(() => {
      interaction = createFrequencyInteraction({
        confirmedHz: 14_250_000,
        digits: model.digits,
        disabled: false,
        minFreq: 0,
        maxFreq: 999_000_000,
        onFreqChange,
      });
    }));
    flushSync();
    const target = mountReadout({ model, presentation: 'interactive', interaction });
    const root = target.querySelector<HTMLElement>('.freq')!;
    const digits = Array.from(target.querySelectorAll<HTMLElement>('.digit'));
    return { root, oneHzDigit: digits[digits.length - 1], onFreqChange };
  }

  it('has no data-owns-arrows with no digit selected, and "vertical" once one is', () => {
    const { root, oneHzDigit } = mountInteractiveReadout();

    expect(root.hasAttribute('data-owns-arrows')).toBe(false);

    oneHzDigit.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    flushSync();
    expect(root.getAttribute('data-owns-arrows')).toBe('vertical');
  });

  it('steps a selected digit on plain ArrowUp and consumes the event', () => {
    const { root, oneHzDigit, onFreqChange } = mountInteractiveReadout();
    oneHzDigit.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    flushSync();

    const event = new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true, cancelable: true });
    root.dispatchEvent(event);

    expect(onFreqChange).toHaveBeenCalledExactlyOnceWith(14_250_001);
    expect(event.defaultPrevented).toBe(true);
  });

  // MOR-2512 §2: modified arrows belong to the global keyboard map (the
  // Ctrl/Cmd/Alt+Arrow bindings); a selected digit must not step on them
  // and must not eat them. Plain ArrowUp stepping is pinned by the test
  // above.
  it('ignores modified arrows while a digit is selected', () => {
    const { root, oneHzDigit, onFreqChange } = mountInteractiveReadout();
    oneHzDigit.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    flushSync();

    const modified = new KeyboardEvent('keydown', {
      key: 'ArrowUp', ctrlKey: true, bubbles: true, cancelable: true,
    });
    root.dispatchEvent(modified);

    expect(onFreqChange).not.toHaveBeenCalled();
    expect(modified.defaultPrevented).toBe(false);
  });
});

// MOR-2514 — the live-page defect: selecting a digit was a one-way door.
// The readout declared data-owns-arrows for as long as the selection
// lived and nothing ever ended it. The pins below cover the three
// release paths (Escape, focusout past the group, toggle click) and the
// two non-releases (focus moving between digits inside the group, and
// the no-digit state's unconsumed Escape).
describe('releasing a selected digit (MOR-2514)', () => {
  const HINT = 'Esc or click away to deselect';

  function mountInteractiveReadout() {
    const model = projectFrequencyReadout({ confirmedHz: 14_250_000 });
    const onFreqChange = vi.fn();
    let interaction!: FrequencyInteraction;
    roots.push(effect_root(() => {
      interaction = createFrequencyInteraction({
        confirmedHz: 14_250_000,
        digits: model.digits,
        disabled: false,
        minFreq: 0,
        maxFreq: 999_000_000,
        onFreqChange,
        selectedDigitHint: HINT,
      });
    }));
    flushSync();
    const target = mountReadout({ model, presentation: 'interactive', interaction });
    const root = target.querySelector<HTMLElement>('.freq')!;
    const digits = Array.from(target.querySelectorAll<HTMLElement>('.digit'));
    return { root, digits, onFreqChange };
  }

  function selectDigit(digit: HTMLElement): void {
    digit.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    flushSync();
  }

  it('releases on Escape and consumes it only while a digit is selected', () => {
    const { root, digits } = mountInteractiveReadout();
    selectDigit(digits[0]);
    expect(root.getAttribute('data-owns-arrows')).toBe('vertical');

    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    root.dispatchEvent(escape);
    flushSync();

    expect(escape.defaultPrevented).toBe(true);
    expect(digits[0].classList.contains('selected')).toBe(false);
    expect(root.hasAttribute('data-owns-arrows')).toBe(false);

    const bare = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    root.dispatchEvent(bare);
    expect(bare.defaultPrevented).toBe(false);
  });

  it('releases on focusout past the group and keeps digit-to-digit focus inside it selected', () => {
    const { root, digits } = mountInteractiveReadout();
    selectDigit(digits[0]);

    root.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: digits[1] }));
    flushSync();
    expect(digits[0].classList.contains('selected')).toBe(true);

    const outside = document.createElement('button');
    document.body.appendChild(outside);
    root.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: outside }));
    flushSync();
    expect(digits[0].classList.contains('selected')).toBe(false);
    expect(root.hasAttribute('data-owns-arrows')).toBe(false);

    selectDigit(digits[2]);
    root.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
    flushSync();
    expect(digits[2].classList.contains('selected')).toBe(false);
  });

  it('toggles off when the selected digit is clicked again and moves on a different digit', () => {
    const { root, digits } = mountInteractiveReadout();
    selectDigit(digits[0]);
    selectDigit(digits[0]);
    expect(digits[0].classList.contains('selected')).toBe(false);
    expect(root.hasAttribute('data-owns-arrows')).toBe(false);

    selectDigit(digits[0]);
    selectDigit(digits[3]);
    expect(digits[0].classList.contains('selected')).toBe(false);
    expect(digits[3].classList.contains('selected')).toBe(true);
  });

  it('carries the release hint as a title on the selected digit only', () => {
    const { digits } = mountInteractiveReadout();
    expect(digits[0].hasAttribute('title')).toBe(false);
    selectDigit(digits[0]);
    expect(digits[0].getAttribute('title')).toBe(HINT);
  });

  it('focuses the readout group when a digit click selects it', () => {
    const { root, digits } = mountInteractiveReadout();
    selectDigit(digits[0]);
    expect(document.activeElement).toBe(root);
  });
});

describe('alternate frequency renderer', () => {
  it('receives the real passive interaction and stays inert after teardown', () => {
    selectedFrequency.current = AlternateFrequencyReadoutHarness as FrequencyRenderer;
    const target = document.createElement('div');
    document.body.appendChild(target);
    mounted.push(mount(FrequencyDisplay, {
      target,
      props: { freq: 14_250_000, compact: true, active: false, receiver: 'sub' },
    }));
    flushSync();

    const root = target.querySelector<HTMLElement>('[data-alternate-frequency-readout]')!;
    const interaction = retainedInteractions()[0];
    const digit = projectFrequencyReadout({ confirmedHz: 14_250_000 }).digits[0];
    expect(root.dataset.presentation).toBe('passive');
    expect(root.dataset.compact).toBe('true');
    expect(root.dataset.active).toBe('false');
    expect(root.dataset.receiver).toBe('sub');
    expect(root.dataset.vfoFreqHook).toBe('false');
    expect(Array.from(target.querySelectorAll('button')).map((digit) => digit.textContent).join('')).toBe('14250000');
    const wheel = new WheelEvent('wheel', { deltaY: -1, cancelable: true });
    interaction.handleDigitClick(digit, new MouseEvent('click'));
    interaction.handleWheel(digit, wheel);
    interaction.handleKeyDown(new KeyboardEvent('keydown', { key: 'ArrowUp', cancelable: true }));
    expect(wheel.defaultPrevented).toBe(false);
    expect(interaction.inert).toBe(true);
    unmount(mounted.pop()!);
    expect(interaction.inert).toBe(true);
  });
});
