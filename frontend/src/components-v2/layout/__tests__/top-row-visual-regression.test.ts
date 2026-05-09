import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

vi.mock('$lib/stores/layout.svelte', () => ({
  useLcdLayout: vi.fn(() => false),
  getLayoutMode: vi.fn(() => 'standard'),
  cycleLayoutMode: vi.fn(),
  setLayoutMode: vi.fn(),
}));

vi.mock('../../../skins/registry', () => ({
  resolveSkinId: vi.fn(() => 'desktop-v2'),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    state: null,
    caps: null,
    connectionStatus: 'disconnected',
    radioPowerOn: null,
    connection: { status: 'disconnected', radioPowerOn: null },
    audio: { rxEnabled: false, txEnabled: false, volume: 50, muted: false },
    send: vi.fn(),
  },
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => ({ connected: false })),
  getRadioPowerOn: vi.fn(() => null),
  getRadioStatus: vi.fn(() => 'disconnected'),
  isScopeConnected: vi.fn(() => false),
  isAudioConnected: vi.fn(() => false),
  getHttpConnected: vi.fn(() => false),
  getRigConnected: vi.fn(() => false),
  getRadioHealth: vi.fn(() => null),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  applyModeDefault: vi.fn(),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasDualReceiver: vi.fn(() => true),
  hasTx: vi.fn(() => true),
  hasAudio: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false),
  hasAnyScope: vi.fn(() => false),
  isAudioFftScope: vi.fn(() => false),
  hasAudioFft: vi.fn(() => false),
  getScopeSource: vi.fn(() => null),
  hasCapability: vi.fn(() => false),
  getKeyboardConfig: vi.fn(() => null),
  getVfoScheme: vi.fn(() => 'main_sub'),
  vfoLabel: vi.fn((slot: 'A' | 'B') => (slot === 'A' ? 'MAIN' : 'SUB')),
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id),
  vfoSlotLabel: vi.fn((slot: 'A' | 'B') => (slot === 'A' ? 'VFO A' : 'VFO B')),
  getCapabilities: vi.fn(() => ({
    freqRanges: [
      {
        start: 7000000,
        end: 7300000,
        bands: [{ name: '40m', start: 7000000, end: 7300000, default: 7074000 }],
      },
      {
        start: 14000000,
        end: 14350000,
        bands: [{ name: '20m', start: 14000000, end: 14350000, default: 14074000 }],
      },
    ],
  })),
  setCapabilities: vi.fn(),
  getAgcModes: vi.fn(() => [0, 1, 2, 3]),
  getAgcLabels: vi.fn(() => ({ 0: 'OFF', 1: 'FAST', 2: 'MID', 3: 'SLOW' })),
  getSupportedModes: vi.fn(() => ['USB', 'LSB', 'CW', 'AM', 'FM']),
  getSupportedFilters: vi.fn(() => ['FIL1', 'FIL2', 'FIL3']),
  getAttValues: vi.fn(() => [0, 10, 20]),
  getAttLabels: vi.fn(() => ({ 0: '0dB', 10: '10dB', 20: '20dB' })),
  getPreValues: vi.fn(() => [0, 1, 2]),
  getPreLabels: vi.fn(() => ({ 0: 'OFF', 1: 'PRE1', 2: 'PRE2' })),
  getAntennaCount: vi.fn(() => 1),
  getSmeterCalibration: vi.fn(() => null),
  getSmeterRedline: vi.fn(() => null),
  getMeterCalibration: vi.fn(() => null),
  getMeterRedline: vi.fn(() => null),
  getControlRange: vi.fn(() => ({ min: 0, max: 255 })),
}));

vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});

import VfoHeader from '../VfoHeader.svelte';
import RadioLayout from '../RadioLayout.svelte';
import { vfoLayoutStyleVars } from '../vfo-layout-tokens';

let components: ReturnType<typeof mount>[] = [];

function mountWithCleanup(component: typeof VfoHeader | typeof RadioLayout, props: Record<string, unknown> = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const instance = mount(component as never, { target, props });
  flushSync();
  components.push(instance);
  return target;
}

function normalizeWhitespace(value: string): string {
  return value.replace(/\s+/g, ' ').trim();
}

function topRowSnapshot(target: HTMLElement) {
  const root = target.querySelector('.vfo-header');
  if (!root) {
    throw new Error('vfo-header not found');
  }

  const panels = Array.from(root.querySelectorAll('.panel')).map((panel) => ({
    profile: panel.getAttribute('data-layout-profile'),
    label: panel.querySelector('.vfo-label')?.textContent?.trim(),
    meterVariant: panel.querySelector('svg')?.getAttribute('data-variant'),
    frequency: normalizeWhitespace(panel.querySelector('.freq')?.textContent ?? ''),
    mode: panel.querySelector('.mode-badge')?.textContent?.trim(),
    filter: panel.querySelector('.filter-badge')?.textContent?.trim(),
  }));

  const ops = Array.from(root.querySelectorAll('.vfo-ops .bridge-button')).map((button) => ({
    label: button.textContent?.trim(),
    active: button.getAttribute('data-active'),
    color: button.getAttribute('data-color'),
  }));

  const splitStatus = {
    title: root.querySelector('.split-status-title')?.textContent?.trim(),
    row: normalizeWhitespace(root.querySelector('.split-status-row')?.textContent ?? ''),
  };

  return {
    wrapperStyle: target.getAttribute('style'),
    panels,
    ops,
    splitStatus,
  };
}

const vfoProps = {
  mainVfo: {
    receiver: 'main' as const,
    freq: 14214000,
    mode: 'USB',
    filter: 'FIL1',
    sValue: 132,
    isActive: true,
    badges: { digisel: 'DIGI-SEL', anf: 'ANF' },
  },
  subVfo: {
    receiver: 'sub' as const,
    freq: 7170000,
    mode: 'LSB',
    filter: 'FIL1',
    sValue: 78,
    isActive: false,
    badges: { digisel: 'DIGI-SEL' },
  },
  splitActive: true,
  dualWatchActive: false,
  txVfo: 'main' as const,
};

class ResizeObserverStub {
  callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }

  observe(target: Element) {
    this.callback([
      {
        target,
        contentRect: { width: 1600 } as DOMRectReadOnly,
      } as ResizeObserverEntry,
    ], this as unknown as ResizeObserver);
  }

  disconnect() {}
  unobserve() {}
}

beforeEach(() => {
  components = [];
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  components = [];
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

describe('VfoHeader visual regression', () => {
  it('matches the baseline top-row snapshot', () => {
    const target = document.createElement('div');
    target.setAttribute('style', vfoLayoutStyleVars('baseline'));
    document.body.appendChild(target);
    const instance = mount(VfoHeader, {
      target,
      props: { ...vfoProps, layoutProfile: 'baseline' },
    });
    flushSync();
    components.push(instance);

    expect(topRowSnapshot(target)).toMatchInlineSnapshot(`
      {
        "ops": [
          {
            "active": "false",
            "color": "muted",
            "label": "M→S",
          },
          {
            "active": "true",
            "color": "cyan",
            "label": "SPLIT",
          },
          {
            "active": "false",
            "color": "green",
            "label": "DW",
          },
          {
            "active": "false",
            "color": "muted",
            "label": "M↔S",
          },
        ],
        "panels": [
          {
            "filter": undefined,
            "frequency": "14 . 214 . 000",
            "label": "MAIN",
            "meterVariant": "vfo",
            "mode": undefined,
            "profile": "baseline",
          },
          {
            "filter": undefined,
            "frequency": "7 . 170 . 000",
            "label": "SUB",
            "meterVariant": "vfo",
            "mode": undefined,
            "profile": "baseline",
          },
        ],
        "splitStatus": {
          "row": "RX 7.170 TX 14.214",
          "title": "SPLIT",
        },
        "wrapperStyle": "--vfo-bridge-width: 132px; --vfo-bridge-pad-x: 4px; --vfo-panel-header-height: 18px; --vfo-header-badge-height: 12px; --vfo-badge-inset-y: 3px; --vfo-header-group-gap: 5px; --vfo-header-badge-gap: 3px; --vfo-panel-meter-height: 58px; --vfo-panel-body-height: 64px; --vfo-display-row-height: 38px; --vfo-control-strip-height: 22px; --vfo-control-strip-gap: 4px; --vfo-panel-pad-x: 10px; --vfo-panel-meter-pad-x: 6px; --vfo-panel-body-pad-x: 10px; --vfo-panel-body-pad-bottom: 0px; --vfo-panel-body-gap: 4px; --vfo-display-row-gap: 12px; --vfo-frequency-size: 22px; --vfo-frequency-letter-spacing: 0.025em; --vfo-ops-gap: 4px; --vfo-ops-padding-y: 4px; --vfo-ops-stack-gap: 4px; --vfo-ops-secondary-margin-top: 0px; --vfo-ops-secondary-padding-top: 4px; --vfo-ops-badge-width: 62px; --vfo-ops-badge-height: 21px; --vfo-ops-badge-padding-x: 8px; --vfo-ops-badge-radius: 4px; --vfo-ops-badge-font-size: 10px; --vfo-header-badge-padding-x: 5px; --vfo-control-badge-padding-x: 6px; --vfo-panel-badge-radius: 3px; --vfo-control-badge-height: 16px; --vfo-control-badge-min-height: 16px; --vfo-control-badge-font-size: 7px",
      }
    `);
  });

  it('matches the wide top-row snapshot', () => {
    const target = document.createElement('div');
    target.setAttribute('style', vfoLayoutStyleVars('wide'));
    document.body.appendChild(target);
    const instance = mount(VfoHeader, {
      target,
      props: { ...vfoProps, layoutProfile: 'wide' },
    });
    flushSync();
    components.push(instance);

    expect(topRowSnapshot(target)).toMatchInlineSnapshot(`
      {
        "ops": [
          {
            "active": "false",
            "color": "muted",
            "label": "M→S",
          },
          {
            "active": "true",
            "color": "cyan",
            "label": "SPLIT",
          },
          {
            "active": "false",
            "color": "green",
            "label": "DW",
          },
          {
            "active": "false",
            "color": "muted",
            "label": "M↔S",
          },
        ],
        "panels": [
          {
            "filter": undefined,
            "frequency": "14 . 214 . 000",
            "label": "MAIN",
            "meterVariant": "vfo-wide",
            "mode": undefined,
            "profile": "wide",
          },
          {
            "filter": undefined,
            "frequency": "7 . 170 . 000",
            "label": "SUB",
            "meterVariant": "vfo-wide",
            "mode": undefined,
            "profile": "wide",
          },
        ],
        "splitStatus": {
          "row": "RX 7.170 TX 14.214",
          "title": "SPLIT",
        },
        "wrapperStyle": "--vfo-bridge-width: 132px; --vfo-bridge-pad-x: 5px; --vfo-panel-header-height: 18px; --vfo-header-badge-height: 12px; --vfo-badge-inset-y: 3px; --vfo-header-group-gap: 5px; --vfo-header-badge-gap: 3px; --vfo-panel-meter-height: 60px; --vfo-panel-body-height: 62px; --vfo-display-row-height: 36px; --vfo-control-strip-height: 22px; --vfo-control-strip-gap: 4px; --vfo-panel-pad-x: 10px; --vfo-panel-meter-pad-x: 6px; --vfo-panel-body-pad-x: 10px; --vfo-panel-body-pad-bottom: 0px; --vfo-panel-body-gap: 4px; --vfo-display-row-gap: 12px; --vfo-frequency-size: 22px; --vfo-frequency-letter-spacing: 0.025em; --vfo-ops-gap: 4px; --vfo-ops-padding-y: 4px; --vfo-ops-stack-gap: 4px; --vfo-ops-secondary-margin-top: 0px; --vfo-ops-secondary-padding-top: 5px; --vfo-ops-badge-width: 64px; --vfo-ops-badge-height: 21px; --vfo-ops-badge-padding-x: 8px; --vfo-ops-badge-radius: 4px; --vfo-ops-badge-font-size: 10px; --vfo-header-badge-padding-x: 5px; --vfo-control-badge-padding-x: 6px; --vfo-panel-badge-radius: 3px; --vfo-control-badge-height: 16px; --vfo-control-badge-min-height: 16px; --vfo-control-badge-font-size: 7px",
      }
    `);
  });
});

describe('RadioLayout top-row profile switching', () => {
  beforeEach(() => {
    // JSDOM defaults to 0x0 — force desktop dimensions so isMobile stays false
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1440 });
    Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 900 });
  });

  it('promotes the top row to wide profile when the deck width crosses the threshold', () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverStub);

    const target = mountWithCleanup(RadioLayout);
    const receiverDeck = target.querySelector('.receiver-deck');
    const mainPanel = target.querySelector('.vfo-main-panel .panel');

    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-frequency-size: 22px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-panel-meter-height: 60px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-badge-inset-y: 3px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-control-strip-height: 22px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-control-strip-gap: 4px');
    expect(mainPanel?.getAttribute('data-layout-profile')).toBe('wide');
    expect(mainPanel?.querySelector('svg')?.getAttribute('data-variant')).toBe('vfo-wide');
  });

  it('applies manual URL overrides to the shared top-row scale', () => {
    vi.stubGlobal('ResizeObserver', ResizeObserverStub);

    const previousUrl = window.location.href;
    window.history.replaceState({}, '', '/?vfoScale=1.05&vfoFreqScale=0.9&vfoMeterScale=1.1');

    const target = mountWithCleanup(RadioLayout);
    const receiverDeck = target.querySelector('.receiver-deck');

    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-panel-meter-height: 69px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-panel-header-height: 19px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-control-strip-height: 23px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-header-badge-padding-x: 5.25px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-control-badge-padding-x: 6.3px');
    expect(receiverDeck?.getAttribute('style')).toContain('--vfo-frequency-size: 20.79px');

    window.history.replaceState({}, '', previousUrl);
  });
});
