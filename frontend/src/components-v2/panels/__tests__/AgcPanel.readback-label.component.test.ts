/**
 * MOR-4436 — AgcPanel readback-only state.
 *
 * Buttons always come from the settable `agcModes` only. When the radio
 * reports a readable-but-not-settable code with a contract label (e.g.
 * panel-selected Medium), the panel shows three buttons with none
 * selected plus a non-clickable "{label} (set on radio)" line — never
 * three dark disabled buttons, and no invented button for the code.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const testBed = vi.hoisted(() => ({ state: null as any, caps: null as any }));
const mockHandlers = vi.hoisted(() => ({ onAgcModeChange: vi.fn() }));

vi.mock('$lib/runtime/adapters/panel-adapters', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('$lib/runtime/adapters/panel-adapters')
  >();
  const { toAgcProps } = await import('$lib/runtime/props/panel-props');
  return {
    ...actual,
    deriveAgcProps: () => toAgcProps(testBed.state, testBed.caps),
    getAgcHandlers: () => mockHandlers,
    getAgcArmed: () => ({ armed: false, value: null }),
  };
});

import AgcPanel from '../AgcPanel.svelte';

let components: ReturnType<typeof mount>[] = [];

function availableAgcField() {
  return {
    storePath: 'main.agc',
    observed: true,
    freshness: 'fresh',
    availability: 'available',
  };
}

function stateWithAgc(value: number) {
  return {
    active: 'MAIN',
    main: { agc: value },
    sub: {},
    fieldStatus: { 'main.agc': availableAgcField() },
  };
}

function g7250Caps() {
  return {
    capabilities: ['agc'],
    agcModes: [0, 1, 3],
    agcLabels: { '0': 'OFF', '1': 'FAST', '3': 'SLOW' },
    agcReadback: { modes: [0, 1, 2, 3], labels: { '2': 'MID' } },
  };
}

function buttonLabels(t: HTMLElement): string[] {
  return Array.from(t.querySelectorAll('.button-grid button')).map(
    (b) => b.textContent?.trim() ?? '',
  );
}

function mountPanel(): HTMLElement {
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(AgcPanel, { target: t });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  mockHandlers.onAgcModeChange.mockClear();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  testBed.state = null;
  testBed.caps = null;
});

describe('AgcPanel readback-only state (MOR-4436)', () => {
  it('shows 3 settable buttons, none selected, plus the non-clickable label', () => {
    testBed.state = stateWithAgc(2);
    testBed.caps = g7250Caps();
    const t = mountPanel();

    expect(buttonLabels(t)).toEqual(['OFF', 'FAST', 'SLOW']);
    const buttons = Array.from(t.querySelectorAll('.button-grid button'));
    expect(buttons).toHaveLength(3);
    for (const button of buttons) {
      expect(button.getAttribute('data-active')).not.toBe('true');
    }
    expect(t.querySelector('.readback-label')?.textContent).toBe('MID (set on radio)');
  });

  it('shows no label when the reported code is settable', () => {
    testBed.state = stateWithAgc(1);
    testBed.caps = g7250Caps();
    const t = mountPanel();

    expect(buttonLabels(t)).toEqual(['OFF', 'FAST', 'SLOW']);
    expect(t.querySelector('.readback-label')).toBeNull();
  });

  it('shows no label without a readback contract (IC-7300 shape)', () => {
    testBed.state = stateWithAgc(2);
    testBed.caps = {
      capabilities: ['agc'],
      agcModes: [1, 2, 3],
      agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' },
      agcReadback: null,
    };
    const t = mountPanel();

    expect(buttonLabels(t)).toEqual(['FAST', 'MID', 'SLOW']);
    expect(t.querySelector('.readback-label')).toBeNull();
  });
});
