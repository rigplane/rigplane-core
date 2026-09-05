import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { DisplayTelemetry } from '../../../semantic/radio-display-model';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import LcdTxMeterScales from '../LcdTxMeterScales.svelte';

const field = (txDisplay: DisplayTelemetry['txDisplay']): DisplayTelemetry => ({
  state: 'known', value: 207, relevant: true, txDisplay,
});
const current = (value: number): DisplayTelemetry => field({ supported: true, relevance: 'relevant', observation: { state: 'current', value } });
function render(value: DisplayTelemetry) {
  const props = proxy({ power: value, swr: value, alc: value });
  const root = document.createElement('div'); document.body.append(root);
  const component = mount(LcdTxMeterScales, { target: root, props }); flushSync();
  return { root, props, close: () => { unmount(component); root.remove(); } };
}
afterEach(clearCapabilities);

for (const supported of [false, true]) for (const relevance of ['idle', 'relevant', 'indeterminate'] as const)
  for (const observation of [{ state: 'current', value: 128 }, { state: 'stale', value: 128 },
    { state: 'unknown', reason: 'not-observed' }] as const) {
    it(`${supported}/${relevance}/${observation.state}`, () => {
      const { root, close } = render(field(supported ? { supported, relevance, observation } : { supported }));
      try {
        expect(root.querySelectorAll('[data-tx-scale]')).toHaveLength(supported ? 3 : 0);
        expect(root.querySelectorAll('[data-tx-segment]')).toHaveLength(supported ? 60 : 0);
        const measured = supported && relevance !== 'idle' && observation.state === 'current';
        expect(root.querySelectorAll('[data-tx-fill]')).toHaveLength(measured ? 33 : 0);
        for (const cell of root.querySelectorAll('[data-tx-scale]')) {
          const text = cell.textContent ?? '';
          expect(text).not.toContain('207');
          expect(cell.getAttribute('aria-label')).not.toContain('207');
          expect(text).toContain(relevance === 'idle' ? 'IDLE' : observation.state === 'stale' ? 'STALE' : observation.state === 'unknown' ? '?' : '128 raw');
          if (!measured) expect(cell.getAttribute('aria-label')).not.toContain('128');
          if (relevance === 'indeterminate') expect(cell.getAttribute('aria-label')).toContain('RF relevance indeterminate');
        }
        expect(root.querySelectorAll('button,input,select,[tabindex]')).toHaveLength(0);
      } finally { close(); }
    });
  }

it('retains tracks and segments when only the additive facet changes', () => {
  const { root, props, close } = render(current(200));
  try {
    const nodes = [...root.querySelectorAll('[data-tx-scale],svg,[data-tx-segment]')];
    props.power = props.swr = props.alc = field({ supported: true, relevance: 'idle', observation: { state: 'current', value: 200 } });
    flushSync();
    expect(nodes.every((node) => node.isConnected)).toBe(true);
    expect(root.querySelectorAll('[data-tx-fill]')).toHaveLength(0);
    expect(root.textContent).not.toMatch(/200|207/);
    props.power = props.swr = props.alc = current(10); flushSync();
    expect(nodes.every((node) => node.isConnected)).toBe(true);
    expect(root.querySelectorAll('[data-tx-fill]')).toHaveLength(3);
  } finally { close(); }
});

it('does not recover a legacy numeric reading when the facet is absent', () => {
  const { root, close } = render(field(undefined));
  try { expect(root.querySelectorAll('[data-tx-scale]')).toHaveLength(0); } finally { close(); }
});

function caps(): Capabilities {
  return { model: 'test', scope: false, audio: false, tx: true, capabilities: ['tx'], receivers: 1,
    vfoScheme: 'ab', freqRanges: [], modes: [], filters: [], txBands: null,
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] }, webrtc: { available: false, enabled: false },
    stateContractVersion: 1, providerGeneration: 0,
    meterCalibrations: {
      power: [{ raw: 0, actual: 0, label: '0' }, { raw: 255, actual: 200, label: '200' }],
      swr: [{ raw: 0, actual: 1, label: '1' }, { raw: 255, actual: 4, label: '4+' }],
      alc: [{ raw: 0, actual: 0, label: '0' }, { raw: 255, actual: 1, label: '1' }],
    },
  };
}

describe('canonical meter calibration', () => {
  it('uses a 200W maximum and SWR ratio without a second interpolation', () => {
    setCapabilities(caps());
    const { root, props, close } = render(current(100));
    try {
      expect(root.querySelector('[data-tx-scale="PWR"]')?.textContent).toContain('100W');
      expect(root.querySelectorAll('[data-tx-scale="PWR"] [data-tx-fill]')).toHaveLength(10);
      props.swr = current(2); props.alc = current(0.5); flushSync();
      expect(root.querySelector('[data-tx-scale="SWR"]')?.textContent).toContain('2.0');
      expect(root.querySelectorAll('[data-tx-scale="SWR"] [data-tx-fill]')).toHaveLength(10);
      expect(root.querySelector('[data-tx-scale="ALC"]')?.textContent).toContain('50%');
      expect(root.querySelectorAll('[data-tx-scale="ALC"] [data-tx-fill]')).toHaveLength(10);
      props.power = props.swr = props.alc = current(0); flushSync();
      expect(root.textContent).toContain('0W'); expect(root.textContent).toContain('1.0'); expect(root.textContent).toContain('0%');
      expect(root.textContent).not.toContain('IDLE');
      expect(root.querySelectorAll('[data-tx-fill]')).toHaveLength(0);
    } finally { close(); }
  });
  it('uses an ALC redline without inventing a calibration table', () => {
    const capabilities = caps(); capabilities.meterCalibrations = {}; capabilities.meterRedlines = { alc: 100 };
    setCapabilities(capabilities);
    const { root, close } = render(current(50));
    try {
      expect(root.querySelector('[data-tx-scale="ALC"]')?.textContent).toContain('50%');
      expect(root.querySelectorAll('[data-tx-scale="ALC"] [data-tx-fill]')).toHaveLength(10);
    } finally { close(); }
  });
});

for (const calibrated of [false, true]) it(`keeps honest long ${calibrated ? 'calibrated' : 'raw'} uncertainty readouts`, () => {
  if (calibrated) {
    const capabilities = caps(); capabilities.meterCalibrations!.power![1].actual = 20000;
    setCapabilities(capabilities);
  }
  const { root, props, close } = render(field({ supported: true, relevance: 'indeterminate',
    observation: { state: 'current', value: calibrated ? 10000 : 255 } }));
  try {
    const cell = root.querySelector('[data-tx-scale="PWR"]')!, readout = cell.querySelector('.readout')!;
    expect(readout.textContent).toBe(calibrated ? '10000W ?' : '255 raw ?');
    expect(readout.classList.contains('long-readout')).toBe(!calibrated);
    expect(cell.getAttribute('aria-label')).toContain(calibrated ? '10000W' : '255 raw');
    expect(cell.getAttribute('aria-label')).toContain('RF relevance indeterminate');
    expect(cell.querySelectorAll('[data-tx-fill]')).toHaveLength(calibrated ? 10 : 20);
    props.power = current(0); flushSync();
    expect(readout.isConnected).toBe(true); expect(readout.classList.contains('long-readout')).toBe(false);
  } finally { close(); }
});
