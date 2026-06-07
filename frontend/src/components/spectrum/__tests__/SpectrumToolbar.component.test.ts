/**
 * Component-level test for SpectrumToolbar.svelte.
 * Mounts the real component and verifies DOM output + interactions.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

// ── Mocks (must be before imports) ──────────────────────────────────────────

const radioStoreMock = vi.hoisted(() => ({
  current: {
    scopeControls: { mode: 0, span: 3, speed: 1, hold: false, dual: false, receiver: 0, refDb: 0, edge: 1 },
  } as any,
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  radio: radioStoreMock,
  getRadioState: vi.fn(() => null),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasCapability: vi.fn(() => true),
  hasDualReceiver: vi.fn(() => true),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  getTuningStep: vi.fn(() => 1000),
  adjustTuningStep: vi.fn(),
  isAutoStep: vi.fn(() => false),
  formatStep: vi.fn(() => '1.0k'),
}));

vi.mock('../ScopeSettingsPopover.svelte', () => ({
  default: vi.fn(),
}));

// Stub fetch for band-plan API calls
globalThis.fetch = vi.fn(() =>
  Promise.resolve({ ok: false, json: () => Promise.resolve({}) } as Response),
);

import SpectrumToolbar from '../SpectrumToolbar.svelte';
import { sendCommand } from '$lib/transport/ws-client';

// ── Helpers ─────────────────────────────────────────────────────────────────

let components: ReturnType<typeof mount>[] = [];

function mountToolbar(props: Record<string, unknown> = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(SpectrumToolbar, {
    target,
    props: {
      enableAvg: true,
      enablePeakHold: true,
      brtLevel: 0,
      colorScheme: 'classic',
      fullscreen: false,
      showBandPlan: true,
      hiddenLayers: [],
      showEiBi: false,
      ...props,
    },
  });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  components = [];
  radioStoreMock.current = {
    scopeControls: { mode: 0, span: 3, speed: 1, hold: false, dual: false, receiver: 0, refDb: 0, edge: 1 },
  };
  vi.clearAllMocks();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

// ── Tests ───────────────────────────────────────────────────────────────────

describe('SpectrumToolbar component', () => {
  it('mounts without errors', () => {
    const target = mountToolbar();
    expect(target.querySelector('.spectrum-toolbar')).not.toBeNull();
  });

  it('renders scope mode buttons (CTR, FIX, S-C, S-F)', () => {
    const target = mountToolbar();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const labels = buttons.map((b) => b.textContent?.trim());
    expect(labels).toContain('CTR');
    expect(labels).toContain('FIX');
    expect(labels).toContain('S-C');
    expect(labels).toContain('S-F');
  });

  it('renders SPAN selector in center mode', () => {
    const target = mountToolbar();
    const labels = Array.from(target.querySelectorAll('.toolbar-label'));
    const spanLabel = labels.find((el) => el.textContent?.trim() === 'SPAN');
    expect(spanLabel).toBeDefined();
  });

  it('renders speed selector with SPEED label', () => {
    const target = mountToolbar();
    const labels = Array.from(target.querySelectorAll('.toolbar-label'));
    const speedLabel = labels.find((el) => el.textContent?.trim() === 'SPEED');
    expect(speedLabel).toBeDefined();
  });

  it('renders wash-background group containers (B, C, D)', () => {
    const target = mountToolbar();
    expect(target.querySelector('.toolbar-group-b')).not.toBeNull();
    expect(target.querySelector('.toolbar-group-c')).not.toBeNull();
    expect(target.querySelector('.toolbar-group-d')).not.toBeNull();
  });

  it('renders 1px sub-separator inside group containers', () => {
    const target = mountToolbar();
    expect(target.querySelector('.toolbar-sub-separator')).not.toBeNull();
  });

  it('renders STEP control', () => {
    const target = mountToolbar();
    const labels = Array.from(target.querySelectorAll('.toolbar-label'));
    const stepLabel = labels.find((el) => el.textContent?.trim() === 'STEP');
    expect(stepLabel).toBeDefined();
  });

  it('renders AVG and PEAK toggles', () => {
    const target = mountToolbar();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const labels = buttons.map((b) => b.textContent?.trim());
    expect(labels).toContain('AVG');
    expect(labels).toContain('PEAK');
  });

  it('renders brightness controls', () => {
    const target = mountToolbar();
    const labels = Array.from(target.querySelectorAll('.toolbar-label'));
    const brtLabel = labels.find((el) => el.textContent?.trim() === 'BRT');
    expect(brtLabel).toBeDefined();
  });

  it('renders HOLD button', () => {
    const target = mountToolbar();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const holdBtn = buttons.find((b) => b.textContent?.trim() === 'HOLD');
    expect(holdBtn).toBeDefined();
  });

  it('renders DUAL + MAIN/SUB scope-source buttons by default (mobile/v1 fallback for #832)', () => {
    const target = mountToolbar();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const labels = buttons.map((b) => b.textContent?.trim());
    expect(labels).toContain('DUAL');
    // receiver starts at 0 → button label is 'MAIN'
    expect(labels).toContain('MAIN');
  });

  it('hides DUAL + MAIN/SUB when hideSourceControls is true (v2 desktop; VfoHeader bridge owns them, #832)', () => {
    const target = mountToolbar({ hideSourceControls: true });
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const labels = buttons.map((b) => b.textContent?.trim());
    expect(labels).not.toContain('DUAL');
    expect(labels).not.toContain('MAIN');
    expect(labels).not.toContain('SUB');
  });

  it('DUAL button click dispatches set_scope_dual', () => {
    const target = mountToolbar();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const dualBtn = buttons.find((b) => b.textContent?.trim() === 'DUAL');
    expect(dualBtn).toBeDefined();
    dualBtn!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledWith('set_scope_dual', { dual: true });
  });

  it('receiver-switch button click dispatches switch_scope_receiver', () => {
    const target = mountToolbar();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    // With receiver=0, the label is 'MAIN'; clicking flips to receiver=1.
    const rxBtn = buttons.find((b) => b.textContent?.trim() === 'MAIN');
    expect(rxBtn).toBeDefined();
    rxBtn!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledWith('switch_scope_receiver', { receiver: 1 });
  });

  it('renders color scheme selector', () => {
    const target = mountToolbar();
    const select = target.querySelector<HTMLSelectElement>('.toolbar-select');
    expect(select).not.toBeNull();
    const options = Array.from(select!.querySelectorAll('option')).map((o) => o.value);
    expect(options).toEqual(['classic', 'thermal', 'grayscale']);
  });

  it('renders BANDS button', () => {
    const target = mountToolbar();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const bandsBtn = buttons.find((b) => b.textContent?.trim() === 'BANDS');
    expect(bandsBtn).toBeDefined();
  });

  it('renders fullscreen toggle button', () => {
    const target = mountToolbar();
    const iconBtn = target.querySelector<HTMLButtonElement>('.icon-btn');
    expect(iconBtn).not.toBeNull();
  });

  it('mode button click dispatches sendCommand', () => {
    const target = mountToolbar();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const fixBtn = buttons.find((b) => b.textContent?.trim() === 'FIX');
    expect(fixBtn).toBeDefined();
    fixBtn!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledWith('set_scope_mode', { mode: 1 });
  });

  it('disables missing scope controls instead of presenting defaults as confirmed', () => {
    radioStoreMock.current = {
      scopeControls: { mode: 0, span: 3, speed: 1, hold: false, dual: false, receiver: 0, refDb: 0, edge: 1 },
      fieldStatus: {
        'scopeControls.mode': {
          storePath: 'scope_controls.global.display.mode',
          observed: false,
          freshness: 'unknown',
          availability: 'missing',
        },
        'scopeControls.span': {
          storePath: 'scope_controls.global.display.span',
          observed: true,
          freshness: 'stale',
          availability: 'stale',
        },
      },
    };

    const target = mountToolbar();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const ctrBtn = buttons.find((b) => b.textContent?.trim() === 'CTR');
    const spanDown = buttons.find((b) => b.title === 'Decrease span');
    const spanValue = Array.from(target.querySelectorAll('.toolbar-value'))
      .find((el) => el.textContent?.includes('±25k') || el.textContent?.includes('—'));

    expect(ctrBtn?.disabled).toBe(true);
    expect(ctrBtn?.classList.contains('active')).toBe(false);
    expect(spanDown?.disabled).toBe(true);
    expect(spanValue?.textContent?.trim()).toBe('—');
  });

  it('disables scope controls when the scopeControls parent is unobserved (no child entries)', () => {
    // Mirrors the real backend payload for an unobserved scope group: the
    // parent `scopeControls` carries a `missing` status, the individual
    // children (mode/span/speed/…) have NO own entries. Parent/child
    // resolution must treat each leaf as unavailable so defaults
    // (CTR/MID/±25k/…) are not presented as confirmed (MOR-429).
    radioStoreMock.current = {
      scopeControls: { mode: 0, span: 3, speed: 1, hold: false, dual: false, receiver: 0, refDb: 0, edge: 1 },
      fieldStatus: {
        scopeControls: {
          storePath: 'global.slow_state.scope_controls',
          observed: false,
          freshness: 'unknown',
          availability: 'missing',
        },
      },
    };

    const target = mountToolbar();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const ctrBtn = buttons.find((b) => b.textContent?.trim() === 'CTR');
    const holdBtn = buttons.find((b) => b.textContent?.trim() === 'HOLD');
    const dualBtn = buttons.find((b) => b.textContent?.trim() === 'DUAL');
    const spanDown = buttons.find((b) => b.title === 'Decrease span');
    const speedDown = buttons.find((b) => b.title === 'Decrease speed');
    const spanValue = Array.from(target.querySelectorAll('.toolbar-value'))
      .find((el) => el.textContent?.includes('±25k') || el.textContent?.includes('—'));

    expect(ctrBtn?.disabled).toBe(true);
    expect(ctrBtn?.classList.contains('active')).toBe(false);
    expect(holdBtn?.disabled).toBe(true);
    expect(dualBtn?.disabled).toBe(true);
    expect(spanDown?.disabled).toBe(true);
    expect(speedDown?.disabled).toBe(true);
    expect(spanValue?.textContent?.trim()).toBe('—');
  });

  it('HOLD button click dispatches sendCommand', () => {
    const target = mountToolbar();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const holdBtn = buttons.find((b) => b.textContent?.trim() === 'HOLD');
    expect(holdBtn).toBeDefined();
    holdBtn!.click();
    flushSync();
    expect(sendCommand).toHaveBeenCalledWith('set_scope_hold', { on: true });
  });

  it('unmounts cleanly', () => {
    const target = mountToolbar();
    expect(target.querySelector('.spectrum-toolbar')).not.toBeNull();
    const comp = components.pop()!;
    unmount(comp);
    expect(target.querySelector('.spectrum-toolbar')).toBeNull();
  });
});
