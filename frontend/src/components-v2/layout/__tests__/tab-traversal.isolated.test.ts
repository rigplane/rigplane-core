/**
 * MOR-1449 — Tab focus traversal was completely dead on the default
 * desktop-v2 composition. Root cause: `rigs/_keyboard-default.toml` used to
 * bind the bare "Tab" key to the `vfo_swap` action ("swap-vfo").
 * `profiles/rig_loader.py:1259` loads that shared default TOML for EVERY
 * rig profile and merges each rig's local overrides on top — so all eight
 * rig profiles inherited the binding this way, not because any individual
 * rig (ic7300.toml included — it declares no `[ui.keyboard]` section of its
 * own) redeclared it. `KeyboardHandler`'s `handleKeydown` resolved it like
 * any other single-key shortcut and called `event.preventDefault()`, which
 * silently ate the browser's native focus-traversal everywhere in the app
 * outside a form field — arrow-key tuning kept working (a different
 * binding) while Tab produced zero visible reaction: no traversal, no focus
 * ring, no reachable VFO. The dead TOML binding has since been deleted; the
 * frontend fix (reserving Tab unconditionally) is what these tests pin,
 * reconstructing the pre-fix config shape rather than depending on the
 * TOML no longer declaring it.
 *
 * These tests mount the REAL desktop-v2 `RadioLayout` composition with a
 * keyboard config that reproduces the production rig-profile shape
 * (including the "swap-vfo"/Tab binding), so they exercise the exact
 * composition and config path the live walkthrough hit — not just the
 * `KeyboardHandler` unit in isolation (see `KeyboardHandler.test.ts` for
 * that narrower pin).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import {
  IC7300_STATE, IC7300_CAPABILITIES,
} from '../../../lib/runtime/adapters/__tests__/fixtures/ic7300-profile';

vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});

vi.mock('../../../lib/local-extensions/LocalExtensionsHost.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});

vi.mock('$lib/stores/layout.svelte', () => ({
  useLcdLayout: vi.fn(() => false),
  getLayoutMode: vi.fn(() => 'standard'),
  cycleLayoutMode: vi.fn(),
  setLayoutMode: vi.fn(),
}));

vi.mock('../../../lib/utils/battery', () => ({
  initBatteryMonitor: vi.fn(async () => vi.fn()),
}));

vi.mock('../../../lib/media/media-session', () => ({
  initMediaSession: vi.fn(),
  destroyMediaSession: vi.fn(),
}));

const rt = vi.hoisted(() => ({ state: null as unknown, caps: null as unknown }));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return rt.state; },
    get caps() { return rt.caps; },
    connectionStatus: 'disconnected',
    radioPowerOn: null,
    connection: { status: 'disconnected', radioPowerOn: null },
    audio: { rxEnabled: false, txEnabled: false, volume: 50, muted: false },
    connectionAudio: false,
    defaultScopeStatus: {
      source: null, available: false, resourceSelected: false, demand: 0,
      lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
    },
    scope: { hardwareScopeConnected: false },
  },
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => ({ connected: false })),
  getWsConnected: vi.fn(() => false),
  getRadioPowerOn: vi.fn(() => null),
  getRadioStatus: vi.fn(() => 'disconnected'),
  isScopeConnected: vi.fn(() => false),
  isAudioConnected: vi.fn(() => false),
  getRigConnected: vi.fn(() => false),
  getRadioReady: vi.fn(() => false),
  getRadioHealth: vi.fn(() => null),
  markScopeFrame: vi.fn(),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  applyModeDefault: vi.fn(),
}));

vi.mock('$lib/runtime/tx-controller/app-host', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/tx-controller/app-host')>();
  return {
    ...actual,
    getAppTxController: () => ({
      snapshot: () => ({ phase: 'idle', intent: null, guard: null, radioTx: 'unknown', txRisk: 'none', mayOwnKey: false, fault: null }),
      subscribe: () => () => {},
      start: vi.fn(),
      setIntent: vi.fn(),
      release: vi.fn(),
      resetFault: vi.fn(),
    }),
  };
});

// Mirrors the real production shape: a keyboard config with the
// "swap-vfo"/Tab binding from rigs/_keyboard-default.toml, plus the tuning
// arrows so the "arrow keys still work" acceptance criterion is exercised
// through the same composition.
const KEYBOARD_CONFIG_WITH_TAB_BINDING = {
  leaderKey: 'g',
  leaderTimeoutMs: 1000,
  altHints: true,
  helpTitle: 'Radio Keyboard',
  bindings: [
    {
      id: 'nudge-right', section: 'Tuning', label: 'Tune up',
      sequence: ['ArrowRight'], action: 'tune', repeatable: true,
      params: { direction: 'up', fine: false },
    },
    {
      id: 'swap-vfo', section: 'VFO', label: 'Swap VFO',
      sequence: ['Tab'], action: 'vfo_swap',
    },
  ],
};

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => true),
  hasDualReceiver: vi.fn(() => false),
  hasAudio: vi.fn(() => false),
  hasSpectrum: vi.fn(() => true),
  hasAnyScope: vi.fn(() => false),
  isAudioFftScope: vi.fn(() => false),
  hasAudioFft: vi.fn(() => false),
  getScopeSource: vi.fn(() => null),
  hasCapability: vi.fn(() => false),
  vfoLabel: vi.fn((slot: 'A' | 'B') => (slot === 'A' ? 'MAIN' : 'SUB')),
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id),
  vfoSlotLabel: vi.fn((slot: 'A' | 'B') => (slot === 'A' ? 'VFO A' : 'VFO B')),
  getCapabilities: vi.fn(() => ({ freqRanges: [], modes: [], filters: [] })),
  setCapabilities: vi.fn(),
  getAgcModes: vi.fn(() => [0, 1, 2, 3]),
  getAgcLabels: vi.fn(() => ({ 0: 'OFF', 1: 'FAST', 2: 'MID', 3: 'SLOW' })),
  getSupportedModes: vi.fn(() => ['USB', 'LSB', 'CW', 'AM', 'FM']),
  getSupportedFilters: vi.fn(() => ['FIL1', 'FIL2', 'FIL3']),
  getAttValues: vi.fn(() => [0, 10, 20]),
  getAttLabels: vi.fn(() => ({ 0: '0dB', 10: '10dB', 20: '20dB' })),
  getPreValues: vi.fn(() => [0, 1, 2]),
  getPreLabels: vi.fn(() => ({ 0: 'OFF', 1: 'PRE1', 2: 'PRE2' })),
  getKeyboardConfig: vi.fn(() => KEYBOARD_CONFIG_WITH_TAB_BINDING),
  getVfoScheme: vi.fn(() => 'ab'),
  getAntennaCount: vi.fn(() => 1),
  getSmeterCalibration: vi.fn(() => null),
  getSmeterRedline: vi.fn(() => null),
  getMeterCalibration: vi.fn(() => null),
  getMeterRedline: vi.fn(() => null),
  getControlRange: vi.fn(() => ({ min: 0, max: 255 })),
}));

import RadioLayout from '../RadioLayout.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountLayout(skinId: any = 'desktop-v2') {
  const t = document.createElement('div');
  document.body.appendChild(t);
  const component = mount(RadioLayout, { target: t, props: { skinId } });
  flushSync();
  components.push(component);
  return t;
}

beforeEach(() => {
  components = [];
  rt.state = null;
  rt.caps = null;
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1440 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 900 });
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
  rt.state = null;
  rt.caps = null;
});

describe('MOR-1449: Tab focus traversal on the desktop-v2 composition', () => {
  it('does not prevent a bare Tab keydown, even though the production keyboard config binds Tab to vfo_swap', () => {
    mountLayout('desktop-v2');

    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    window.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
  });

  it('does not prevent Shift+Tab either', () => {
    mountLayout('desktop-v2');

    const event = new KeyboardEvent('keydown', {
      key: 'Tab', shiftKey: true, bubbles: true, cancelable: true,
    });
    window.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
  });

  it('still dispatches the arrow-key tuning shortcut through the same config', () => {
    const t = mountLayout('desktop-v2');
    // Sanity: the composition mounted the real layout, not an error branch.
    expect(t.querySelector('.radio-layout')).not.toBeNull();

    const event = new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true });
    window.dispatchEvent(event);

    // The tuning shortcut still calls preventDefault (unchanged behaviour) —
    // it is a real binding, unlike Tab.
    expect(event.defaultPrevented).toBe(true);
  });

  it('conformance pin: the desktop-v2 composition exposes real tabbable elements', () => {
    const t = mountLayout('desktop-v2');

    const focusable = Array.from(
      t.querySelectorAll<HTMLElement>('button, input, textarea, select, a[href], [tabindex]'),
    );

    // Primary surfaces (StatusBar controls, band selector, panel toggles, …)
    // must produce a non-trivial set of genuinely tabbable elements — an
    // empty or near-empty set would mean Tab has nowhere to land regardless
    // of whether it is prevented.
    expect(focusable.length).toBeGreaterThan(5);

    // Scoped, not blanket: `tabindex="-1"` is a legitimate pattern for
    // programmatically-focusable containers that are NOT part of the tab
    // order by design (e.g. RadioLayout's `.settings-modal`,
    // StatusBar's `.np-detail` — both real dialogs already in this tree). A
    // blanket "zero tabindex=-1 anywhere" assertion would false-red on those
    // and on any future modal. Instead, pin specific PRIMARY interactive
    // controls this composition is known to expose — the ones an operator
    // actually needs to reach — and require each to be a real tab stop.
    const PRIMARY_CONTROL_SELECTORS = [
      '.status-bar .report-btn',
      '.status-bar .settings-btn',
      '.status-bar .skin-select',
      '.status-bar .power-toggle-btn',
      '.band-tab',
    ];
    for (const selector of PRIMARY_CONTROL_SELECTORS) {
      const matches = Array.from(t.querySelectorAll<HTMLElement>(selector));
      expect(matches.length, `expected at least one match for ${selector}`).toBeGreaterThan(0);
      for (const el of matches) {
        expect(
          el.getAttribute('tabindex'),
          `${selector} must not be pinned out of the tab order (tabindex=-1)`,
        ).not.toBe('-1');
      }
    }
  });

  it('wired-state mount: the VFO frequency control is a real tab stop (tabindex=0) once the radio view model is live', () => {
    // Uses the byte-faithful IC-7300 capture (same fixture MOR-1428's
    // conformance suite pins) rather than a synthetic all-observed state —
    // this is the shape a live walkthrough actually produces, and the
    // disconnected/no-caps mount above (rt.state=null) renders the VFO tile
    // as a plain, non-interactive text span (`hasTunableFrequency` requires
    // an observed frequency), so it could never have caught a regression
    // that makes the VFO itself untabbable — the ticket's actual concern.
    rt.state = IC7300_STATE;
    rt.caps = IC7300_CAPABILITIES;
    const t = mountLayout('desktop-v2');

    const freq = t.querySelector<HTMLElement>('.vfo-freq .freq');
    expect(freq, 'expected the interactive frequency control to mount, not the plain-text fallback').not.toBeNull();
    expect(freq!.getAttribute('tabindex')).toBe('0');
  });
});
