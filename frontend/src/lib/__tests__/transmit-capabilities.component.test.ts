import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { TRANSMIT_CAPABILITIES, NEVER_GUARDED, CAPABILITY_ATTRIBUTE } from '../transmit-capabilities';
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
import { topologyFixtures, withTxAux, withRitXit, withCwKeyer, withAntenna } from '../../semantic/fixtures/topologies';
import RxTxSurface from '../../semantic/RxTxSurface.svelte';
import CwKeyerInstrumentHostFixture from '../../semantic/__tests__/fixtures/CwKeyerInstrumentHostFixture.svelte';
import AntennaInstrumentHostFixture from '../../semantic/__tests__/fixtures/AntennaInstrumentHostFixture.svelte';
import desktopControlsCss from '../../skins/desktop-v2/semantic-controls.css?raw';
import RepeaterSurface from '../../semantic/RepeaterSurface.svelte';
import ManagedTotControl from '../../components-v2/controls/ManagedTotControl.svelte';
import TxMicProcessingControl from '../../components-v2/controls/TxMicProcessingControl.svelte';
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

const markerSelector = `[${CAPABILITY_ATTRIBUTE}]`;

// Keep this total over the registry's type, including QA layouts and mobile.
const layouts = {
  'desktop-v2': true, 'sdr-test': true, 'lcd-cockpit': true, 'lcd-scope': true,
  'peer-split': true, 'unified-instrument': true, 'panadapter-first': true,
  'dual-receiver-cockpit': true, 'flagship-probe': true, 'dual-sdr-face': true,
  mobile: true,
} satisfies Record<SkinId, true>;

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

function assertCapability(control: Element | null, capability: { readonly id: string }) {
  expect(control, `harness case for ${capability.id}`).not.toBeNull();
  expect(control!.closest(markerSelector)?.getAttribute(CAPABILITY_ATTRIBUTE)).toBe(capability.id);
  seen.add(capability.id);
}

function assertIdentifiers() {
  const allowed = Object.values(TRANSMIT_CAPABILITIES).map(({ id }) => id);
  for (const control of target.querySelectorAll(markerSelector)) {
    const id = control.getAttribute(CAPABILITY_ATTRIBUTE)!;
    expect(allowed).toContain(id);
  }
  const unkeys = target.querySelectorAll('.rx-tx-unkey, .m-ls-unkey, .ptt-fab-latched, .ptt-latched');
  for (const unkey of unkeys) {
    expect(unkey.closest(markerSelector), NEVER_GUARDED.unkey.reason).toBeNull();
  }
  expect(target.querySelector(markerSelector)).not.toBeNull();
}

describe('transmit capability DOM contract (MOR-4837)', () => {
  it.each(['auto', 'standard'] as const)('renders the %s layout preference', async (layoutPreference) => {
    const skin = resolveSkinId({ capabilities: harness.caps, layoutPreference, isMobile: false, hasAnyScope: true });
    await render(skin);
    assertIdentifiers();
  });
  it.each(Object.keys(layouts) as SkinId[])('%s uses registered IDs in fresh locale renders', async (skin) => {
    setLocale('en-US');
    await render(skin);
    assertIdentifiers();
    const identifiers = () => Array.from(target.querySelectorAll(markerSelector),
      (element) => element.getAttribute(CAPABILITY_ATTRIBUTE)).sort();
    const english = identifiers();
    for (const locale of ['en-US', 'ru-RU', 'ja-JP'] as const) {
      await unmount(component!);
      component = undefined;
      setLocale(locale);
      await render(skin);
      expect(identifiers(), `${skin}/${locale}/fresh`).toEqual(english);
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

describe('operation variants and composite renderers', () => {
  it.each([false, true])('only marks phone setup power-on (radio on=%s)', async (on) => {
    setRadioPowerOn(on);
    await render('mobile');
    target.querySelector<HTMLButtonElement>('.m-settings-btn')!.click();
    flushSync();
    expect(target.querySelector('.m-radio-power')?.getAttribute(CAPABILITY_ATTRIBUTE))
      .toBe(on ? null : TRANSMIT_CAPABILITIES.powerOn.id);
    if (!on) assertCapability(target.querySelector('.m-radio-power'), TRANSMIT_CAPABILITIES.powerOn);
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
      expect(power.closest(markerSelector), 'opening the RF-power editor is not a TX operation').toBeNull();
      power.click();
      flushSync();
      const slider = target.querySelector('[role="slider"]');
      expect(slider).not.toBeNull();
      expect(slider!.closest(markerSelector)?.getAttribute(CAPABILITY_ATTRIBUTE))
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
      assertCapability(buttons[index], capability);
    }
    buttons.at(-1)!.click();
    flushSync();
    expect(target.querySelector('.tx-modal')).not.toBeNull();
    const slots = target.querySelectorAll('.tx-level-slot');
    expect(slots.length).toBeGreaterThan(0);
    for (const [index, slot] of Array.from(slots).entries()) {
      const capability = index === 0 ? TRANSMIT_CAPABILITIES.rfPower : TRANSMIT_CAPABILITIES.txSettings;
      assertCapability(slot, capability);
    }
    assertIdentifiers();
  });

  it('keeps the legacy TX panel lock-release action unmarked', () => {
    harness.tx = { ...IDLE_TX, phase: 'active', intent: 'latched', radioTx: 'on', txRisk: 'confirmed-on' };
    component = mount(TxPanel, { target });
    flushSync();
    const release = target.querySelector('.ptt-button.ptt-latched');
    expect(release).not.toBeNull();
    expect(release!.closest(markerSelector), NEVER_GUARDED.unkey.reason).toBeNull();
    assertIdentifiers();
  });

  it('marks every legacy RIT/XIT control', () => {
    component = mount(RitXitPanel, { target });
    flushSync();
    const controls = target.querySelectorAll('button, [role="slider"]');
    expect(controls.length).toBeGreaterThan(0);
    for (const control of controls) {
      assertCapability(control, TRANSMIT_CAPABILITIES.ritXit);
    }
    assertIdentifiers();
  });

  it('marks VOX enablement and its gain/delay controls', () => {
    component = mount(VoxPanel, { target, props: {
      voxOn: true, voxGain: 50, antiVoxGain: 20, voxDelay: 5,
      onVoxToggle() {}, onVoxGainChange() {}, onAntiVoxGainChange() {}, onVoxDelayChange() {},
    } });
    flushSync();
    expect(target.querySelector('button')?.getAttribute(CAPABILITY_ATTRIBUTE)).toBe(TRANSMIT_CAPABILITIES.vox.id);
    const settings = target.querySelectorAll('[role="slider"]');
    expect(settings.length).toBeGreaterThan(0);
    for (const control of settings) {
      expect(control.closest(markerSelector)?.getAttribute(CAPABILITY_ATTRIBUTE))
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
    assertCapability(target.querySelector('.rx-tx-key'), momentary ? TRANSMIT_CAPABILITIES.txHold : TRANSMIT_CAPABILITIES.txKey);
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
      assertCapability(control, capability);
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
      expect(control!.closest(markerSelector)?.getAttribute(CAPABILITY_ATTRIBUTE))
        .toBe(TRANSMIT_CAPABILITIES.ritXit.id);
    }
  });

  it('marks CW break-in and delay but leaves RX AUTO TUNE unmarked', () => {
    component = mount(CwKeyerInstrumentHostFixture, { target, props: {
      view: withCwKeyer(topologyFixtures['1/single']), autoTuneAvailable: true,
      breakInChoices: [{ value: 0, label: 'OFF' }, { value: 1, label: 'SEMI' }, { value: 2, label: 'FULL' }],
    } });
    flushSync();
    assertIdentifiers();
    assertCapability(target.querySelector('[data-testid="cw-keyer-break-in"] button'), TRANSMIT_CAPABILITIES.cwBreakIn);
    const correction = target.querySelector('[data-testid="cw-keyer-auto-tune"]');
    expect(correction).not.toBeNull();
    expect(correction!.closest(markerSelector), NEVER_GUARDED.cwAutoTune.reason).toBeNull();
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
    assertCapability(target.querySelector('.repeater-surface button'), TRANSMIT_CAPABILITIES.repeater);
  });

  it('keeps the phone TX-lock release unmarked', () => {
    const hold = { attach: () => ({ update() {}, destroy() {} }), abort() {}, destroy() {} };
    component = mount(PttFab, { target, props: { mode: 'latched', hold } });
    flushSync();
    const release = target.querySelector('button')!;
    expect(release.closest(markerSelector), NEVER_GUARDED.unkey.reason).toBeNull();
  });

  it('also marks the phone landscape PTT', async () => {
    window.innerWidth = 844; window.innerHeight = 390;
    await render('mobile');
    assertIdentifiers();
    expect(target.querySelector('.m-ls-ptt')).not.toBeNull();
  });
});


describe('timeout, microphone path and emergency power-off', () => {
  it('marks the timeout Save operation at its shared definition', () => {
    component = mount(ManagedTotControl, { target });
    flushSync();
    assertCapability(target.querySelector('[data-testid="managed-tot-save"]'), TRANSMIT_CAPABILITIES.txTimeoutSave);
    assertIdentifiers();
  });

  it('marks both microphone-path choices at their shared definition', () => {
    component = mount(TxMicProcessingControl, { target });
    flushSync();
    const choices = target.querySelectorAll('input[type="radio"]');
    expect(choices).toHaveLength(2);
    for (const choice of choices) {
      assertCapability(choice, TRANSMIT_CAPABILITIES.txMicPath);
    }
    assertIdentifiers();
  });

  it.each(['desktop', 'phone', 'nested'] as const)('marks the microphone path in its %s host', async (host) => {
    if (host === 'nested') {
      component = mount(TxPanel, { target });
      flushSync();
      target.querySelector<HTMLButtonElement>('.tx-button-grid > button:last-child')!.click();
    } else {
      await render(host === 'phone' ? 'mobile' : 'desktop-v2');
      if (host === 'phone') {
        target.querySelector<HTMLButtonElement>('[aria-controls="m-chip-panel-tx"]')!.click();
        flushSync();
        target.querySelector<HTMLButtonElement>('.m-tx-settings-btn')!.click();
      } else {
        target.querySelector<HTMLButtonElement>('.settings-btn')!.click();
      }
    }
    flushSync();
    const choices = target.querySelectorAll('[data-testid="tx-mic-processing"] input');
    expect(choices).toHaveLength(2);
    for (const choice of choices) assertCapability(choice, TRANSMIT_CAPABILITIES.txMicPath);
  });

  it.each(['status', 'phone'] as const)('marks timeout Save from the %s entry point', async (host) => {
    await render(host === 'phone' ? 'mobile' : 'desktop-v2');
    if (host === 'phone') {
      target.querySelector<HTMLButtonElement>('[aria-controls="m-chip-panel-tx"]')!.click();
      flushSync();
      target.querySelector<HTMLButtonElement>('.m-tx-settings-btn')!.click();
    } else {
      target.querySelector<HTMLButtonElement>('[data-testid="managed-tot-trigger"]')!.click();
    }
    flushSync();
    assertCapability(target.querySelector('[data-testid="managed-tot-save"]'), TRANSMIT_CAPABILITIES.txTimeoutSave);
  });

  it.each([false, true])('a DOM guard leaves the power-off toggle usable (mobile=%s)', async (mobile) => {
    // The actual toggle must have no marker while it means OFF.
    setRadioPowerOn(true);
    await render(mobile ? 'mobile' : 'desktop-v2');
    if (mobile) {
      target.querySelector<HTMLButtonElement>('.m-settings-btn')!.click();
      flushSync();
    }
    const off = target.querySelector<HTMLButtonElement>('.power-toggle-btn')!;
    expect(off).not.toBeNull();
    for (const root of target.querySelectorAll(markerSelector)) {
      if (root instanceof HTMLButtonElement) root.disabled = true;
      for (const button of root.querySelectorAll<HTMLButtonElement>('button')) button.disabled = true;
    }
    expect(off.closest(markerSelector), NEVER_GUARDED.powerOff.reason).toBeNull();
    expect(off.disabled).toBe(false);
  });
});


describe('registry inventory harness cases', () => {
  it('exercises split and VFO transfer in the desktop layout', async () => {
    await render('desktop-v2');
    assertCapability(target.querySelector('[data-vfo-split]'), TRANSMIT_CAPABILITIES.split);
    assertCapability(target.querySelector('[data-vfo-swap]'), TRANSMIT_CAPABILITIES.vfoTransfer);
  });
});


describe('antenna renderer and wrapper styling', () => {
  it.each([false, true])('marks the TX antenna with either renderer (custom=%s)', (custom) => {
    component = mount(AntennaInstrumentHostFixture, { target, props: {
      view: withAntenna(topologyFixtures['1/single']), tx: idleTx,
      ...(custom ? { finiteAppearance } : {}),
    } });
    flushSync();
    const port = target.querySelector(custom ? '.rp-capability-renderer button' : '[data-testid="antenna-ports"] button');
    assertCapability(port, TRANSMIT_CAPABILITIES.txAntenna);
    assertIdentifiers();
  });

  it('preserves the desktop row flex rule for a wrapped renderer button', () => {
    const face = document.createElement('div');
    face.className = 'desktop-control-face';
    target.append(face);
    const panel = document.createElement('div');
    panel.className = 'semantic-control-panel';
    face.append(panel);
    component = mount(TxAuxScalarHostFixture, { target: panel, props: {
      view: withTxAux(topologyFixtures['1/single']), tx: idleTx, presentation: 'grouped',
      finiteAppearance, rendererContext: createFiniteRendererContext(),
    } });
    flushSync();
    const row = target.querySelector('.tx-aux-row')!;
    expect(row).not.toBeNull();
    const rule = desktopControlsCss.match(/([^{}]*\.rp-capability-renderer\s*>\s*button)\s*\{\s*flex:\s*1 1 0;/u);
    expect(rule).not.toBeNull();
    const button = row.querySelector('.rp-capability-renderer button');
    expect(button).not.toBeNull();
    expect(button!.matches(rule![1].trim())).toBe(true);
  });
});

it('every listed capability has a harness case', () => {
  const missing = Object.values(TRANSMIT_CAPABILITIES)
    .map(({ id }) => id).filter((id) => !seen.has(id));
  expect(missing, `Missing capability harness cases: ${missing.join(', ')}`).toEqual([]);
});
