import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { TRANSMIT_CAPABILITIES, NEVER_GUARDED } from '../transmit-capabilities';
import { setLocale } from '../i18n';
import { fixtureById } from '../../../fixtures/catalog';
import { harness, IDLE_TX } from '../../../fixtures/harness-state';
import { setCapabilities, clearCapabilities } from '../stores/capabilities.svelte';
import { setRadioState, resetRadioState } from '../stores/radio.svelte';
import { setRadioPowerOn } from '../stores/connection.svelte';
import { getLayout } from '../../presentation/layouts/contract';
import { readWorkspace } from '../../presentation/workspace/contract';
import { resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY } from '../../presentation/workspace/resolution';
import { loadSkin, resolveSkinId, type SkinId } from '../../skins/registry';
import { getAvailableThemes, setTheme } from '../../components-v2/theme/theme-switcher';
import { applyDesignLanguage, DESIGN_LANGUAGES } from '../../../fixtures/languages';
import HostedRadioLayoutFixture from '../../components-v2/layout/__tests__/fixtures/HostedRadioLayoutFixture.svelte';
import TxAuxScalarHostFixture from '../../semantic/__tests__/fixtures/TxAuxScalarHostFixture.svelte';
import RitXitScanInstrumentHostFixture from '../../semantic/__tests__/fixtures/RitXitScanInstrumentHostFixture.svelte';
import FiniteControlRendererFixture from '../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import { createFiniteRendererContext, type FiniteControlAppearance } from '../../primitives/control-instruments/control-instrument-renderer.svelte';
import { topologyFixtures, withTxAux, withRitXit, withCwKeyer } from '../../semantic/fixtures/topologies';
import RxTxSurface from '../../semantic/RxTxSurface.svelte';
import CwKeyerInstrumentHostFixture from '../../semantic/__tests__/fixtures/CwKeyerInstrumentHostFixture.svelte';
import RepeaterSurface from '../../semantic/RepeaterSurface.svelte';
import PttFab from '../../components-v2/controls/PttFab.svelte';
import TxPanel from '../../components-v2/panels/TxPanel.svelte';
import RitXitPanel from '../../components-v2/panels/RitXitPanel.svelte';
import VoxPanel from '../../components-v2/panels/VoxPanel.svelte';
import type { RepeaterReceiverViewModel, TxAuxField } from '../../semantic/radio-view-model';

vi.mock('../runtime', async () => {
  const fixture = await import('../../../fixtures/stubs/runtime');
  const { runtime } = await import('../runtime/frontend-runtime');
  return {
    ...fixture,
    runtime,
  };
});
vi.mock('../runtime/frontend-runtime', async () => {
  const fixture = await import('../../../fixtures/stubs/runtime');
  return {
    runtime: {
      ...fixture.runtime,
      get state() { return harness.state; },
      get caps() { return harness.caps; },
      pttStyle: 'latch',
      onTxAudioDied: () => () => {},
      acquireHardwareScope: () => ({}),
      releaseHardwareScope: () => {},
    },
  };
});
vi.mock('../runtime/tx-controller/managed-app-host', async () =>
  import('../../../fixtures/stubs/app-host'));
vi.mock('../runtime/adapters/mod-input-tx-guard.svelte', async () =>
  import('../../../fixtures/stubs/mod-input-tx-guard'));
vi.mock('../runtime/adapters/panel-adapters', async (importOriginal) => ({
  ...await importOriginal<typeof import('../runtime/adapters/panel-adapters')>(),
  ...await import('../../../fixtures/stubs/panel-adapters'),
}));
vi.mock('../../components/spectrum/SpectrumPanel.svelte', async () =>
  import('../../components-v2/layout/__tests__/SpectrumPanelStub.svelte'));
vi.mock('../local-extensions/LocalExtensionsHost.svelte', async () =>
  import('../../components-v2/layout/__tests__/SpectrumPanelStub.svelte'));

// Semantic hooks, not visible labels, identify the controls under test. The
// capability vocabulary and expected attribute values come only from Core.
const controls = {
  txKey: '.rx-tx-key:not(:has(.rx-tx-hold-label))',
  txHold: '.rx-tx-key:has(.rx-tx-hold-label), .ptt-button:not(.ptt-latched), .ptt-fab:not(.ptt-fab-latched), .m-ls-ptt',
  tune: '[data-testid="tx-aux-atu-tune"], .m-atu-btn',
  split: '[data-vfo-split], .m-vfo-ops > button:first-child',
  vfoTransfer: '[data-vfo-swap], [data-vfo-equalize], .m-vfo-ops > button:nth-child(2), .m-vfo-ops > button:nth-child(3)',
  ritXit: '[data-testid="ritxit-rit-toggle"], [data-testid="ritxit-xit-toggle"], [data-testid="ritxit-clear"], [data-testid="ritxit-offset"]',
  power: '.power-toggle-btn, .power-on-btn',
  rfPower: '[data-testid="tx-aux-rfPower"], .m-tx-info',
  atu: '[data-testid="tx-aux-atu"]:not(.tx-aux-level), [data-vfo-tuner]',
  vox: '[data-testid="tx-aux-vox"]:not(.tx-aux-level), [data-vfo-vox]',
  txSettings: '.tx-aux-level:not([data-field="rfPower"]), [data-testid="tx-aux-compressor"], [data-testid="tx-aux-monitor"], [data-testid="cw-keyer-keyerSpeed"], [data-testid="cw-keyer-reverse-paddle"], [data-testid="rx-audio-mod-select"], [data-testid="rx-audio-mod-set-lan"], [data-testid="mod-input-select"], [data-testid="mod-input-set-lan"], [data-testid="filter-data-mode"] button, [data-testid="standard-data-mode"] button',
  cwBreakIn: '[data-testid="cw-keyer-break-in"] button, [data-testid="cw-keyer-breakInDelay"], .lcd-btn-bk',
  cwAutoTune: '[data-testid="cw-keyer-auto-tune"]',
  txAntenna: '[data-testid="antenna-ports"] button',
  repeater: '.repeater-surface button',
} satisfies Record<keyof typeof TRANSMIT_CAPABILITIES, string>;

// Keep this total over the registry's type, including QA layouts and mobile.
const layouts = {
  'desktop-v2': true, 'sdr-test': true, 'lcd-cockpit': true, 'lcd-scope': true,
  'peer-split': true, 'unified-instrument': true, 'panadapter-first': true,
  'dual-receiver-cockpit': true, 'flagship-probe': true, 'dual-sdr-face': true,
  mobile: true,
} satisfies Record<SkinId, true>;

const entries = Object.entries(TRANSMIT_CAPABILITIES) as Array<
  [keyof typeof TRANSMIT_CAPABILITIES, (typeof TRANSMIT_CAPABILITIES)[keyof typeof TRANSMIT_CAPABILITIES]]
>;
const seen = new Set<string>();
const idleTx = { phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null } as const;
const finiteAppearance = {
  action: FiniteControlRendererFixture as FiniteControlAppearance['action'],
  toggle: FiniteControlRendererFixture as FiniteControlAppearance['toggle'],
  choice: FiniteControlRendererFixture as FiniteControlAppearance['choice'],
} satisfies FiniteControlAppearance;
const originalViewport = { width: window.innerWidth, height: window.innerHeight };
let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 204 })));
  const fixture = fixtureById('topology-2-main-sub')!;
  const state = fixture.state()!;
  const readings = {
    powerLevel: 0.5, tunerStatus: 0, voxOn: false, compressorOn: false, monitorOn: false,
    micGain: 128, driveGain: 128, compressorLevel: 64, monitorGain: 64,
    ritOn: true, ritTx: false, ritFreq: 125,
  };
  harness.state = {
    ...state, ...readings,
    fieldStatus: {
      ...state.fieldStatus,
      ...Object.fromEntries(Object.keys(readings).map((field) => [field, {
        ...state.fieldStatus!.split, storePath: field,
      }])),
    },
  };
  harness.caps = fixture.caps();
  harness.tx = { ...IDLE_TX };
  harness.calls = [];
  setCapabilities(harness.caps!);
  if (harness.state) setRadioState(harness.state);
  setRadioPowerOn(true);
  Object.defineProperty(window, 'innerWidth', { value: 390, configurable: true, writable: true });
  Object.defineProperty(window, 'innerHeight', { value: 844, configurable: true, writable: true });
  target = document.createElement('div');
  document.body.append(target);
});

afterEach(async () => {
  if (component) await unmount(component);
  component = undefined;
  target.remove();
  resetRadioState(); clearCapabilities(); setRadioPowerOn(null);
  setLocale('en-US');
  setTheme('default');
  window.innerWidth = originalViewport.width;
  window.innerHeight = originalViewport.height;
  delete document.documentElement.dataset.designLanguage;
  delete document.documentElement.dataset.languageMode;
  vi.unstubAllGlobals();
});

async function render(skin: SkinId) {
  const plan = resolveSurfacePlan(getLayout(skin)!, readWorkspace({ version: 1 }).workspace);
  const context = new Map([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]);
  if (skin === 'desktop-v2' || skin === 'sdr-test') {
    component = mount(HostedRadioLayoutFixture, { target, context, props: { skinId: skin } });
  } else {
    const Skin = await loadSkin(skin);
    component = mount(Skin, { target, context });
  }
  flushSync();
}

function assertIdentifiers() {
  const allowed = Object.values(TRANSMIT_CAPABILITIES).map(({ id }) => id);
  for (const [name, capability] of entries) {
    for (const control of target.querySelectorAll(controls[name])) {
      expect(control.closest('[data-rp-capability]')?.getAttribute('data-rp-capability'),
        `${name}: ${control.outerHTML}`).toBe(capability.id);
    }
  }
  for (const control of target.querySelectorAll('[data-rp-capability]')) {
    const id = control.getAttribute('data-rp-capability')!;
    expect(allowed).toContain(id);
    seen.add(id);
  }
  const unkeys = target.querySelectorAll('.rx-tx-unkey, .m-ls-unkey, .ptt-fab-latched, .ptt-latched');
  for (const unkey of unkeys) {
    expect(unkey.closest('[data-rp-capability]'), NEVER_GUARDED.unkey.reason).toBeNull();
  }
  expect(target.querySelector('[data-rp-capability]')).not.toBeNull();
}

describe('transmit capability DOM contract (MOR-4837)', () => {
  it.each(['auto', 'standard'] as const)('renders the %s layout preference', async (layoutPreference) => {
    const skin = resolveSkinId({ capabilities: harness.caps, layoutPreference, isMobile: false, hasAnyScope: true });
    await render(skin);
    assertIdentifiers();
  });
  it.each(Object.keys(layouts) as SkinId[])('%s uses registered IDs in both locales', async (skin) => {
    setLocale('en-US');
    await render(skin);
    assertIdentifiers();
    const identifiers = () => Array.from(target.querySelectorAll('[data-rp-capability]'),
      (element) => element.getAttribute('data-rp-capability')).sort();
    const english = identifiers();
    for (const locale of ['en-US', 'ru-RU'] as const) {
      setLocale(locale);
      for (const theme of getAvailableThemes()) {
        setTheme(theme.id);
        flushSync();
        assertIdentifiers();
        expect(identifiers(), `${skin}/${theme.id}/${locale}`).toEqual(english);
      }
      for (const language of DESIGN_LANGUAGES) {
        await applyDesignLanguage(new URLSearchParams({ language }));
        flushSync();
        assertIdentifiers();
        expect(identifiers(), `${skin}/${language}/${locale}`).toEqual(english);
      }
    }
  });
});

afterAll(() => {
  // A newly added capability cannot silently be absent from every fixture.
  expect([...seen].sort()).toEqual(Object.values(TRANSMIT_CAPABILITIES).map(({ id }) => id).sort());
});

describe('operation variants and composite renderers', () => {
  it('marks the phone setup power control', async () => {
    await render('mobile');
    target.querySelector<HTMLButtonElement>('.m-settings-btn')!.click();
    flushSync();
    expect(target.querySelector('.m-radio-power')?.getAttribute('data-rp-capability'))
      .toBe(TRANSMIT_CAPABILITIES.power.id);
    assertIdentifiers();
  });
  it.each(['essentials', 'rit', 'tx'])('marks controls in the phone %s tab and its RF-power sheet', async (tab) => {
    await render('mobile');
    const chip = target.querySelector<HTMLButtonElement>(`[aria-controls="m-chip-panel-${tab}"]`);
    expect(chip).not.toBeNull();
    chip!.click();
    flushSync();
    assertIdentifiers();
    if (tab === 'tx') {
      const power = target.querySelector<HTMLButtonElement>('.m-tx-info')!;
      expect(power).not.toBeNull();
      expect(power.disabled).toBe(false);
      power.click();
      flushSync();
      const slider = target.querySelector('[role="slider"]');
      expect(slider).not.toBeNull();
      expect(slider!.closest('[data-rp-capability]')?.getAttribute('data-rp-capability'))
        .toBe(TRANSMIT_CAPABILITIES.rfPower.id);
    }
  });
  it('marks the legacy TX panel controls and its open settings', () => {
    component = mount(TxPanel, { target });
    flushSync();
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.tx-button-grid > button'));
    expect(buttons).toHaveLength(6);
    const expected = [
      TRANSMIT_CAPABILITIES.atu, TRANSMIT_CAPABILITIES.tune, TRANSMIT_CAPABILITIES.vox,
      TRANSMIT_CAPABILITIES.txSettings, TRANSMIT_CAPABILITIES.txSettings,
    ];
    for (const [index, capability] of expected.entries()) {
      expect(buttons[index].getAttribute('data-rp-capability')).toBe(capability.id);
    }
    buttons.at(-1)!.click();
    flushSync();
    expect(target.querySelector('.tx-modal')).not.toBeNull();
    const slots = target.querySelectorAll('.tx-level-slot');
    expect(slots.length).toBeGreaterThan(0);
    for (const [index, slot] of Array.from(slots).entries()) {
      const capability = index === 0 ? TRANSMIT_CAPABILITIES.rfPower : TRANSMIT_CAPABILITIES.txSettings;
      expect(slot.closest('[data-rp-capability]')?.getAttribute('data-rp-capability')).toBe(capability.id);
    }
    assertIdentifiers();
  });

  it('keeps the legacy TX panel lock-release action unmarked', () => {
    harness.tx = { ...IDLE_TX, phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on' };
    component = mount(TxPanel, { target });
    flushSync();
    const release = target.querySelector('.ptt-button.ptt-latched');
    expect(release).not.toBeNull();
    expect(release!.closest('[data-rp-capability]'), NEVER_GUARDED.unkey.reason).toBeNull();
    assertIdentifiers();
  });

  it('marks every legacy RIT/XIT control', () => {
    component = mount(RitXitPanel, { target });
    flushSync();
    const controls = target.querySelectorAll('button, [role="slider"]');
    expect(controls.length).toBeGreaterThan(0);
    for (const control of controls) {
      expect(control.closest('[data-rp-capability]')?.getAttribute('data-rp-capability'))
        .toBe(TRANSMIT_CAPABILITIES.ritXit.id);
    }
    assertIdentifiers();
  });

  it('marks VOX enablement and its gain/delay controls', () => {
    component = mount(VoxPanel, { target, props: {
      voxOn: true, voxGain: 50, antiVoxGain: 20, voxDelay: 5,
      onVoxToggle() {}, onVoxGainChange() {}, onAntiVoxGainChange() {}, onVoxDelayChange() {},
    } });
    flushSync();
    expect(target.querySelector('button')?.getAttribute('data-rp-capability')).toBe(TRANSMIT_CAPABILITIES.vox.id);
    const settings = target.querySelectorAll('[role="slider"]');
    expect(settings.length).toBeGreaterThan(0);
    for (const control of settings) {
      expect(control.closest('[data-rp-capability]')?.getAttribute('data-rp-capability'))
        .toBe(TRANSMIT_CAPABILITIES.txSettings.id);
    }
    assertIdentifiers();
  });
  it.each([false, true])('keys explicitly and keeps Unkey unmarked (momentary=%s)', (momentary) => {
    const unkey = vi.fn();
    component = mount(RxTxSurface, { target, props: {
      view: topologyFixtures['1/single'], tx: idleTx, momentary,
      onRequestKey: vi.fn(), onRequestUnkey: unkey,
    } });
    flushSync();
    assertIdentifiers();
    const release = target.querySelector<HTMLButtonElement>('.rx-tx-unkey')!;
    expect(release).not.toBeNull();
    expect(release.disabled).toBe(false);
    release.click();
    expect(unkey).toHaveBeenCalledOnce();
  });

  it.each([false, true])('marks native and custom TX auxiliary renderers (custom=%s)', (custom) => {
    component = mount(TxAuxScalarHostFixture, { target, props: {
      view: withTxAux(topologyFixtures['1/single']), tx: idleTx, presentation: 'grouped',
      ...(custom ? { finiteAppearance, rendererContext: createFiniteRendererContext() } : {}),
    } });
    flushSync();
    assertIdentifiers();
    const expected = {
      ATU: TRANSMIT_CAPABILITIES.atu, VOX: TRANSMIT_CAPABILITIES.vox,
      COMP: TRANSMIT_CAPABILITIES.txSettings, MON: TRANSMIT_CAPABILITIES.txSettings,
      TUNE: TRANSMIT_CAPABILITIES.tune,
    };
    if (custom) for (const [label, capability] of Object.entries(expected)) {
      const control = target.querySelector(`[data-testid="external-${label}"]`);
      expect(control).not.toBeNull();
      expect(control!.closest('[data-rp-capability]')?.getAttribute('data-rp-capability')).toBe(capability.id);
    }
  });

  it.each([false, true])('marks RIT/XIT, offset and clear with either renderer (custom=%s)', (custom) => {
    component = mount(RitXitScanInstrumentHostFixture, { target, props: {
      view: withRitXit(topologyFixtures['1/single']),
      ...(custom ? { finiteAppearance, rendererContext: createFiniteRendererContext() } : {}),
    } });
    flushSync();
    assertIdentifiers();
    if (custom) for (const label of ['RIT', 'XIT', 'CLEAR']) {
      const control = target.querySelector(`[data-testid="external-${label}"]`);
      expect(control).not.toBeNull();
      expect(control!.closest('[data-rp-capability]')?.getAttribute('data-rp-capability'))
        .toBe(TRANSMIT_CAPABILITIES.ritXit.id);
    }
  });

  it('marks CW break-in, delay and the AUTO TUNE action', () => {
    component = mount(CwKeyerInstrumentHostFixture, { target, props: {
      view: withCwKeyer(topologyFixtures['1/single']), autoTuneAvailable: true,
      breakInChoices: [{ value: 0, label: 'OFF' }, { value: 1, label: 'SEMI' }, { value: 2, label: 'FULL' }],
    } });
    flushSync();
    assertIdentifiers();
    expect(target.querySelector('[data-testid="cw-keyer-auto-tune"]')).not.toBeNull();
    expect(target.querySelector('[data-testid="cw-keyer-break-in"] button')).not.toBeNull();
  });

  it('marks repeater tone, shift and offset controls', () => {
    const known = <T,>(value: T): TxAuxField<T> => ({
      reading: { status: 'known', value }, availability: { structural: true, operational: true },
    });
    const receiver: RepeaterReceiverViewModel = {
      inRepeaterBand: known(true), toneMode: known('tone'), toneFreq: known(885),
      shift: known('plus'), offsetHz: known(600000),
    };
    component = mount(RepeaterSurface, { target, props: { view: {
      ...topologyFixtures['1/single'], repeater: {
        main: receiver, sub: { ...receiver, inRepeaterBand: known(false) },
        shiftChoices: ['simplex', 'plus', 'minus'],
      },
    } } });
    flushSync();
    assertIdentifiers();
    expect(target.querySelector('.repeater-surface button')).not.toBeNull();
  });

  it('keeps the phone TX-lock release unmarked', () => {
    const hold = { attach: () => ({ update() {}, destroy() {} }), abort() {}, destroy() {} };
    component = mount(PttFab, { target, props: { mode: 'latched', hold } });
    flushSync();
    const release = target.querySelector('button')!;
    expect(release.closest('[data-rp-capability]'), NEVER_GUARDED.unkey.reason).toBeNull();
  });

  it('also marks the phone landscape PTT', async () => {
    window.innerWidth = 844; window.innerHeight = 390;
    await render('mobile');
    assertIdentifiers();
    expect(target.querySelector('.m-ls-ptt')).not.toBeNull();
  });
});
