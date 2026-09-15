import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
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
import FrequencyDisplay from '../../../components-v2/display/FrequencyDisplay.svelte';

const mounted: ReturnType<typeof mount>[] = [];

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
