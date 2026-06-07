import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const baseProps = {
  rfGain: 255,
  squelch: 0,
  att: 0,
  pre: 0,
  digiSel: false,
  ipPlus: false,
  rfGainAvailable: true,
  squelchAvailable: true,
  attAvailable: true,
  preAvailable: true,
  digiSelAvailable: true,
  ipPlusAvailable: true,
  attValues: [0, 6, 12],
  attLabels: {} as Record<string, string>,
  preValues: [0, 1, 2],
  preOptions: [
    { value: 0, label: 'OFF' },
    { value: 1, label: 'P1' },
    { value: 2, label: 'P2' },
  ],
  showRfGain: true,
  showSquelch: true,
  showAtt: true,
  showPre: true,
  preDisabled: false,
  preDisabledReason: '',
  showDigiSel: true,
  showIpPlus: true,
};

const mockProps = { ...baseProps };

const mockHandlers = {
  onRfGainChange: vi.fn(),
  onSquelchChange: vi.fn(),
  onAttChange: vi.fn(),
  onPreChange: vi.fn(),
  onDigiSelToggle: vi.fn(),
  onIpPlusToggle: vi.fn(),
};

vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  deriveRfFrontEndProps: () => mockProps,
  getRfFrontEndHandlers: () => mockHandlers,
}));

import RfFrontEnd from '../RfFrontEnd.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountPanel(overrides?: Partial<typeof baseProps>) {
  if (overrides) Object.assign(mockProps, overrides);
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(RfFrontEnd, { target: t });
  flushSync();
  components.push(component);
  return t;
}

function findPreButton(t: HTMLElement, label: string): HTMLButtonElement {
  const buttons = Array.from(t.querySelectorAll('button')) as HTMLButtonElement[];
  const match = buttons.find((b) => b.textContent?.trim() === label);
  if (!match) throw new Error(`PRE button "${label}" not found`);
  return match;
}

beforeEach(() => {
  components = [];
  Object.assign(mockProps, baseProps);
  mockHandlers.onRfGainChange = vi.fn();
  mockHandlers.onSquelchChange = vi.fn();
  mockHandlers.onAttChange = vi.fn();
  mockHandlers.onPreChange = vi.fn();
  mockHandlers.onDigiSelToggle = vi.fn();
  mockHandlers.onIpPlusToggle = vi.fn();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('RfFrontEnd preamp/digisel mutex (component)', () => {
  it('disables the PRE buttons and sends no preamp change when DIGI-SEL is on', () => {
    const t = mountPanel({ showPre: true, preDisabled: true, pre: 0 });
    const p1 = findPreButton(t, 'P1');

    expect(p1.disabled).toBe(true);

    p1.click();
    flushSync();

    expect(mockHandlers.onPreChange).not.toHaveBeenCalled();
  });

  it('enables the PRE buttons and forwards the preamp change when DIGI-SEL is off', () => {
    const t = mountPanel({ showPre: true, preDisabled: false, pre: 0 });
    const p1 = findPreButton(t, 'P1');

    expect(p1.disabled).toBe(false);

    p1.click();
    flushSync();

    expect(mockHandlers.onPreChange).toHaveBeenCalledWith(1);
  });
});
