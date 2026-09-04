import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';
import type { PeerSplitDisplayModel } from '../../../semantic/radio-display-model';
import type { LcdSpectrumFrame } from '../lcd-display-contract';
import PanadapterDisplay from '../PanadapterDisplay.svelte';

const known = <T>(value: T) => ({ state: 'known' as const, value });
const indicator = (state: 'active' | 'inactive' = 'inactive') => ({ state });

const receivers: PeerSplitDisplayModel['receivers'] = [
  {
    receiver: 'MAIN', label: 'VFO A', activity: 'active', operational: true,
    frequency: known(14_250_000), mode: known('USB'), filter: known('FIL1'), band: known('20m'),
    sMeter: known(-18), bandwidthHz: known(2400), ifShiftHz: known(0),
    pbtInnerHz: known(400), pbtOuterHz: known(-400), spectrum: 'waiting',
    dsp: { agc: known('MID'), nb: indicator(), nr: indicator('active'), notch: known('off') },
    front: {
      preamp: known(1), attenuator: known(0), rfGain: known(0.7),
      digiSel: { state: 'unsupported' }, ipPlus: indicator(),
    },
  },
  {
    receiver: 'SUB', label: 'VFO B', activity: 'inactive', operational: false,
    frequency: known(14_195_500), mode: known('CW'), filter: known('FIL2'),
    band: { state: 'unsupported' }, sMeter: { state: 'unknown' },
    bandwidthHz: known(500), ifShiftHz: { state: 'unsupported' },
    pbtInnerHz: { state: 'unsupported' }, pbtOuterHz: { state: 'unsupported' },
    spectrum: 'inactive',
    dsp: { agc: known('MID'), nb: indicator(), nr: indicator(), notch: known('off') },
    front: {
      preamp: known(0), attenuator: known(0), rfGain: known(0.7),
      digiSel: { state: 'unsupported' }, ipPlus: indicator(),
    },
  },
];

const model: PeerSplitDisplayModel = {
  kind: 'peer-split',
  rfState: 'receiving',
  receivers,
  activeReceiver: receivers[0],
  top: {
    vox: indicator(), compressor: indicator(), split: indicator(), rit: indicator(),
    tx: indicator(), tune: indicator(), atu: indicator(), antenna: known(1),
  },
  offsets: {
    rit: { state: 'inactive', offsetHz: 0 },
    xit: { state: 'inactive', offsetHz: 0 },
    split: { state: 'inactive' },
  },
  telemetry: {
    drainVoltage: { ...known(13.8), relevant: true },
    drainCurrent: { ...known(0.7), relevant: true },
    power: { state: 'unknown', relevant: false },
    swr: { state: 'unsupported', relevant: false },
    alc: { state: 'unknown', relevant: false },
    compression: { state: 'unknown', relevant: false },
  },
};

function hardwareFrame(overrides: Partial<LcdSpectrumFrame> = {}): LcdSpectrumFrame {
  return {
    source: 'hardware',
    receiver: 'MAIN',
    freshness: 'fresh',
    startHz: 14_200_000,
    endHz: 14_300_000,
    normalizedBins: [0.1, 0.6, 0.25, 0.8],
    ...overrides,
  };
}

let component: ReturnType<typeof mount> | null = null;

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

function render(
  displayModel: PeerSplitDisplayModel = model,
  normalizedFftBins?: Partial<Record<'MAIN' | 'SUB', readonly number[]>>,
  rfFrame?: unknown,
): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(PanadapterDisplay, {
    target, props: { model: displayModel, normalizedFftBins, rfFrame },
  });
  return target;
}

describe('PanadapterDisplay', () => {
  it('declares Direction D native instrument geometry', () => {
    expect(render().querySelector('[data-testid="panadapter-display"]')
      ?.getAttribute('data-native-stage')).toBe('1280x594');
  });

  it('keeps two equal frequency columns truthful', () => {
    const target = render();
    const columns = target.querySelectorAll('[data-testid="panadapter-frequency-column"]');

    expect(columns).toHaveLength(2);
    expect(columns[0].getAttribute('data-receiver')).toBe('MAIN');
    expect(columns[1].getAttribute('data-receiver')).toBe('SUB');
    expect(target.querySelector('[data-testid="lcd-frequency-MAIN"]')?.textContent)
      .toContain('14.250.000');
    expect(target.querySelector('[data-testid="lcd-frequency-SUB"]')?.textContent)
      .toContain('14.195.500');
  });

  it('ghosts unknown frequency/facts and hides unsupported facts', () => {
    const guardedReceivers: PeerSplitDisplayModel['receivers'] = [
      {
        ...model.receivers[0],
        frequency: { state: 'unknown' },
        mode: { state: 'unknown' },
        filter: { state: 'unsupported' },
        band: { state: 'unsupported' },
      },
      model.receivers[1],
    ];
    const target = render({
      ...model,
      receivers: guardedReceivers,
      activeReceiver: guardedReceivers[0],
    });
    const main = target.querySelector('[data-receiver="MAIN"]')!;

    expect(main.querySelector('[data-testid="lcd-frequency-MAIN"]')
      ?.getAttribute('data-state')).toBe('unknown');
    expect([...main.querySelectorAll('.receiver-facts [data-state]')]
      .map((node) => node.getAttribute('data-state')))
      .toEqual(['unknown', 'unsupported', 'unsupported']);

    const frequencySource = readFileSync('src/skins/segmentline/LcdFrequencyReadout.svelte', 'utf8');
    expect(frequencySource).toContain(".frequency[data-state='unknown'] { opacity: 0.34; }");
    expect(frequencySource).toContain(".frequency[data-state='unsupported'] { visibility: hidden; }");
    const source = readFileSync('src/skins/segmentline/PanadapterDisplay.svelte', 'utf8');
    expect(source).toContain(".receiver-facts [data-state='unknown'] { opacity: 0.34; }");
    expect(source).toContain(".receiver-facts [data-state='unsupported'] { visibility: hidden; }");
  });

  it('keeps absent RF data ghosted without fabricating bins or peak annotations', () => {
    const target = render();

    expect(target.querySelector('[data-testid="lcd-rf-panadapter"]')
      ?.getAttribute('data-rf-mode')).toBe('ghost');
    expect(target.querySelectorAll('[data-rf-bin]')).toHaveLength(0);
    expect(target.querySelectorAll('.rf-axis,.rf-carrier,.rf-passband')).toHaveLength(0);
    expect(target.querySelectorAll('[data-rf-peak],.rf-history,.band-edge')).toHaveLength(0);
  });

  it('passes a source-qualified matching hardware frame through without adding samples', () => {
    const frame = hardwareFrame();
    const target = render(model, undefined, frame);

    expect(target.querySelectorAll('[data-rf-bin]')).toHaveLength(frame.normalizedBins.length);
    expect([...target.querySelectorAll('[data-rf-bin]')]
      .map((node) => Number(node.getAttribute('data-rf-sample'))))
      .toEqual(frame.normalizedBins);
    expect(target.querySelector('.rf-carrier')?.getAttribute('data-carrier-hz'))
      .toBe('14250000');
    expect(target.querySelector('.rf-passband')?.getAttribute('data-passband-mode'))
      .toBe('USB');
  });

  it('fails RF receiver mismatch closed at the active-receiver boundary', () => {
    const target = render(model, undefined, hardwareFrame({ receiver: 'SUB' }));

    expect(target.querySelector('[data-testid="lcd-rf-panadapter"]')
      ?.getAttribute('data-frame-reason')).toBe('receiver-mismatch');
    expect(target.querySelectorAll('[data-rf-bin]')).toHaveLength(0);
    expect(target.querySelectorAll('.rf-axis,.rf-carrier,.rf-passband')).toHaveLength(0);
  });

  it('fails a non-hardware frame closed instead of borrowing AF samples', () => {
    const target = render(model, undefined, hardwareFrame({ source: 'audio-fft' }));

    expect(target.querySelector('[data-testid="lcd-rf-panadapter"]')
      ?.getAttribute('data-frame-reason')).toBe('source-mismatch');
    expect(target.querySelectorAll('[data-rf-bin],.rf-axis,.rf-carrier,.rf-passband'))
      .toHaveLength(0);
  });

  it('requires known frequency, mode, width, and shift before showing overlays', () => {
    const guardedReceivers: PeerSplitDisplayModel['receivers'] = [
      {
        ...model.receivers[0],
        frequency: { state: 'unknown' },
        mode: { state: 'unknown' },
        bandwidthHz: { state: 'unknown' },
        ifShiftHz: { state: 'unsupported' },
      },
      model.receivers[1],
    ];
    const target = render({
      ...model,
      receivers: guardedReceivers,
      activeReceiver: guardedReceivers[0],
    }, undefined, hardwareFrame());

    expect(target.querySelectorAll('[data-rf-bin]')).toHaveLength(4);
    expect(target.querySelectorAll('.rf-axis')).toHaveLength(5);
    expect(target.querySelector('.rf-carrier')).toBeNull();
    expect(target.querySelector('.rf-passband')).toBeNull();
  });

  it('preserves LcdAfFft safe-empty semantics in the active-receiver inset', () => {
    const empty = render();
    expect(empty.querySelector('[data-testid="lcd-af-fft"]')
      ?.getAttribute('data-fft-mode')).toBe('safe-empty');

    if (component) unmount(component);
    component = null;
    document.body.innerHTML = '';

    const live = render(model, { MAIN: [0.1, 0.5, 0.9] });
    expect(live.querySelectorAll('[data-testid="lcd-af-fft"]')).toHaveLength(1);
    expect(live.querySelector('[data-testid="lcd-af-fft"]')
      ?.getAttribute('data-fft-mode')).toBe('live');
  });

  it('keeps AF geometry safe-empty when active receiver identity is unknown', () => {
    const target = render({ ...model, activeReceiver: null });

    expect(target.querySelector('[data-testid="lcd-af-fft"]')
      ?.getAttribute('data-fft-mode')).toBe('safe-empty');
    expect(target.querySelector('[data-testid="lcd-rf-panadapter"]')
      ?.getAttribute('data-frame-reason')).toBe('receiver-unknown');
  });

  it('omits demo-only clock, memory, panadapter scales, and band-edge facts', () => {
    const target = render();

    expect(target.textContent).not.toMatch(/\b(?:MEM|SPAN|REF|dBm|dB\/div|band edge)\b/i);
    expect(target.textContent).not.toMatch(/\b\d{1,2}:\d{2}\b/);
    expect(target.textContent).not.toMatch(/\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b/);
  });

  it('is passive and does not cross into resource or command ownership', () => {
    const target = render();
    const source = readFileSync('src/skins/segmentline/PanadapterDisplay.svelte', 'utf8');

    expect(target.querySelectorAll(
      'button,input,select,a[href],[tabindex],[role="button"],[role="switch"]',
    )).toHaveLength(0);
    expect(target.innerHTML).not.toMatch(/onclick|onpointer|onwheel/i);
    expect(source).not.toMatch(/(?:lib\/runtime|stores?\/|controller|transport|socket|demand)/i);
  });
});
