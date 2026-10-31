/**
 * MOR-3634 seam — the served IC-9700 capability document reaches the RF power
 * slider through the production path.
 *
 * Python twin: `tests/test_web_server.py::test_ic9700_power_slider_seam_document_matches_served_capabilities`
 * serves `fixtures/power-slider-scale.ic9700.seam.json` from the real
 * WebServer and pins its capability payload (per-band max power, powerUnit).
 * This file replays the SAME document through the real HTTP client (fetch is
 * the only boundary stub — the bytes still cross `fetchCapabilities` and
 * `validateCapabilities`), the real ws-client over a fake socket, and the
 * real radio/capabilities/command stores into the real `TxAuxScalarHost`
 * power slider. Nothing assigns caps or state directly: the MOR-3634
 * component tests that do (`h.caps = ...`, `h.state = ...`) prove the scale
 * math, this file proves the wiring that feeds it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import { fetchCapabilities } from '$lib/transport/http-client';
import { connect, disconnect } from '$lib/transport/ws-client';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import { clearCapabilities, getCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { getRadioState, resetRadioState } from '$lib/stores/radio.svelte';
import { getCommandLifecycles, resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { makeTxHandlers } from '$lib/runtime/commands/panel-commands';
import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
import { rfPowerScale } from '$lib/runtime/props/panel-props';
import type { RadioViewModel } from '../../../semantic/radio-view-model';
import type { TxAuthoritySnapshot } from '../../../semantic/rx-tx-surface';
import type { TxAuxLevelField, TxAuxScalarPresentation } from '../../../semantic/tx-aux-scalar';
import type { RfPowerScale } from '../../../primitives/scalar/value-control-core';
import TxAuxScalarHostFixture from '../../../semantic/__tests__/fixtures/TxAuxScalarHostFixture.svelte';
import seam from './fixtures/power-slider-scale.ic9700.seam.json';

interface SeamBand {
  name: string;
  freqHz: number;
  powerLevel: number;
  stateRevision: number;
  observationSeq: number;
  expect: { ariaValueMax: string; canonical: string };
}

const bands = seam.bands as unknown as SeamBand[];
const documentCaps = seam.capabilities as unknown as Record<string, unknown>;

const RX_IDLE: TxAuthoritySnapshot = {
  phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fault: null,
};
const HBAR: TxAuxScalarPresentation = { form: 'hbar' };

/** A minimal-but-valid full state snapshot for one seam band. */
function envelopeFor(band: SeamBand): Record<string, unknown> {
  const { stateRevision, observationSeq, freqHz, powerLevel } = band;
  const data = {
    stateContractVersion: 1,
    providerGeneration: 0,
    revision: stateRevision,
    stateRevision,
    freshnessRevision: 0,
    observationSeq,
    updatedAt: '2026-10-30T00:00:00.000Z',
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    main: {
      freqHz, mode: 'FM', filter: null, dataMode: null, sMeter: null, att: null,
      preamp: null, nb: null, nr: null, afLevel: null, rfGain: null, squelch: null,
    },
    sub: null,
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: freqHz },
    powerLevel,
  };
  return {
    type: 'full',
    stateContractVersion: 1,
    providerGeneration: 0,
    revision: stateRevision,
    stateRevision,
    freshnessRevision: 0,
    observationSeq,
    data,
  };
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let originalWebSocket: typeof WebSocket;

const row = () => target.querySelector<HTMLElement>('[data-testid="tx-aux-rfPower"]')!;
const slider = () => row().querySelector<HTMLElement>('[role="slider"]')!;
const canonical = () => row().querySelector<HTMLElement>('[data-canonical-value]')!.textContent;

async function bootstrapCapabilities(): Promise<void> {
  const caps = await fetchCapabilities();
  expect(setCapabilities(caps)).toBe(true);
}

function mountSlider(view: RadioViewModel, scale: RfPowerScale) {
  const txHandlers = makeTxHandlers();
  const onLevelChange = (field: TxAuxLevelField, value: number) => {
    if (field === 'rfPower') txHandlers.onRfPowerChange(value);
  };
  const sliderProps = proxy({
    view, tx: RX_IDLE, presentation: 'independent' as const,
    scalarPresentation: HBAR, rfPowerScale: scale, onLevelChange,
  });
  component = mount(TxAuxScalarHostFixture, { target, props: sliderProps });
  flushSync();
  return sliderProps;
}

/** Push one band envelope through the real ws-client and re-derive the slider props. */
function feed(band: SeamBand, sliderProps: { view: RadioViewModel; rfPowerScale: RfPowerScale }): void {
  instances[0].simulateMessage(JSON.stringify({ type: 'state_update', data: envelopeFor(band) }));
  flushSync();
  const state = getRadioState();
  const caps = getCapabilities();
  expect(state?.main.freqHz).toBe(band.freqHz);
  const view = toRadioViewModel(state, caps);
  expect(view?.txAux?.rfPower.availability.structural).toBe(true);
  sliderProps.view = view!;
  sliderProps.rfPowerScale = rfPowerScale(state, caps);
  flushSync();
}

beforeEach(() => {
  instances.length = 0;
  originalWebSocket = globalThis.WebSocket;
  // @ts-expect-error install the mock as the global WebSocket constructor
  globalThis.WebSocket = MockWebSocket;
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    if (String(input).endsWith('/api/v1/capabilities')) {
      return new Response(JSON.stringify(documentCaps), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    }
    throw new Error(`unexpected fetch in the power-slider seam test: ${String(input)}`);
  }));
  resetCommandLifecycle();
  resetRadioState();
  clearCapabilities();
  target = document.createElement('div');
  document.body.appendChild(target);
});

afterEach(() => {
  if (component) {
    unmount(component);
    component = null;
  }
  disconnect();
  target.remove();
  globalThis.WebSocket = originalWebSocket;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  resetCommandLifecycle();
  resetRadioState();
  clearCapabilities();
});

describe('MOR-3634 — the served IC-9700 seam document drives the power slider', () => {
  it('takes the fixture capabilities through the real HTTP client and store', async () => {
    await bootstrapCapabilities();
    const caps = getCapabilities();
    expect(caps?.model).toBe('IC-9700');
    expect(caps?.maxPowerW).toBe(100);
    expect(caps?.powerUnit).toBe('raw_255');
    expect(Object.fromEntries((caps?.txBands ?? []).map((band) => [band.name, band.maxPowerW])))
      .toEqual({ '2m': 100, '70cm': 75, '23cm': 10 });
  });

  it('scales the rendered slider per band and sends the gestured value unchanged', async () => {
    await bootstrapCapabilities();
    connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    const assertBand = (band: SeamBand) => {
      expect(slider().getAttribute('aria-valuemax'), band.name).toBe(band.expect.ariaValueMax);
      expect(row().dataset.max, band.name).toBe(band.expect.ariaValueMax);
      expect(canonical(), band.name).toBe(band.expect.canonical);
    };
    const gestureSendsLevelUnchanged = () => {
      // A real slider gesture on the 60 W band: the dispatched
      // `set_rf_power` level is the chosen slider value (0.8 + one step),
      // never rescaled into watts by the band maximum.
      slider().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      flushSync();
      const lifecycles = getCommandLifecycles();
      expect(lifecycles).toHaveLength(1);
      expect(lifecycles[0]?.name).toBe('set_rf_power');
      expect(lifecycles[0]?.params.level as number).toBeCloseTo(0.81, 10);
      expect(lifecycles[0]?.params.level_unit as string).toBe('normalized');
      const sent = instances[0].sent.map((raw) => JSON.parse(raw) as {
        name?: string; params?: { level?: number };
      });
      const command = sent.find((message) => message?.name === 'set_rf_power');
      expect(command?.params?.level).toBeCloseTo(0.81, 10);
    };

    const holder = {
      view: null as unknown as RadioViewModel,
      rfPowerScale: null as unknown as RfPowerScale,
    };
    feed(bands[0], holder);
    const sliderProps = mountSlider(holder.view, holder.rfPowerScale);
    assertBand(bands[0]);

    for (const band of bands.slice(1)) {
      feed(band, sliderProps);
      assertBand(band);
      if (band.name === '70cm') gestureSendsLevelUnchanged();
    }
  });
});
