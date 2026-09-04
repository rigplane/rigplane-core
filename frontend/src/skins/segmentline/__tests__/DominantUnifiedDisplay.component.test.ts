import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';
import type { PeerSplitDisplayModel } from '../../../semantic/radio-display-model';
import type { LcdSpectrumFrame } from '../lcd-display-contract';
import DominantUnifiedDisplay from '../DominantUnifiedDisplay.svelte';

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
    dsp: { agc: known('FAST'), nb: indicator(), nr: indicator(), notch: known('manual') },
    front: {
      preamp: known(0), attenuator: known(6), rfGain: known(0.5),
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
    vox: indicator(), compressor: indicator('active'), split: indicator('active'),
    rit: indicator('active'), tx: indicator(), tune: indicator(), atu: indicator(),
    antenna: known(1),
  },
  offsets: {
    rit: { state: 'active', offsetHz: 250 },
    xit: { state: 'inactive', offsetHz: 250 },
    split: { state: 'active', offsetHz: -54_500 },
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

let component: ReturnType<typeof mount> | null = null;

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

function render(
  displayModel: PeerSplitDisplayModel = model,
  spectrumFrame?: unknown,
) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(DominantUnifiedDisplay, {
    target, props: { model: displayModel, spectrumFrame },
  });
  return target;
}

function audioFrame(overrides: Partial<LcdSpectrumFrame> = {}): LcdSpectrumFrame {
  return {
    source: 'audio-fft',
    receiver: 'MAIN',
    freshness: 'fresh',
    startHz: 0,
    endHz: 24_000,
    normalizedBins: [0, 0.5, 1],
    ...overrides,
  };
}

describe('DominantUnifiedDisplay', () => {
  it('is a passive display subtree', () => {
    const target = render();

    expect(target.querySelectorAll(
      'button,input,select,a[href],[tabindex],[role="button"],[role="switch"]',
    )).toHaveLength(0);
    expect(target.innerHTML).not.toMatch(/onclick|onpointer|onwheel/i);
  });

  it('keeps MAIN as the dominant readout and SUB as a quiet truthful strip', () => {
    const target = render();
    const hero = target.querySelector('[data-testid="lcd-dominant-main"]')!;
    const sub = target.querySelector('[data-testid="lcd-dominant-sub"]')!;

    expect(hero.getAttribute('data-receiver-activity')).toBe('active');
    expect(sub.getAttribute('data-receiver-activity')).toBe('inactive');
    expect(hero.querySelector('[data-testid="lcd-frequency-MAIN"]')?.textContent).toContain('14.250.000');
    expect(sub.querySelector('[data-testid="lcd-frequency-SUB"]')?.textContent).toContain('14.195.500');
    expect(target.querySelectorAll('[data-testid="lcd-dominant-meter"]')).toHaveLength(2);
  });

  it('uses exactly one unified AF FFT/filter scope for the active receiver', () => {
    const target = render(model, audioFrame());

    expect(target.querySelectorAll('[data-testid="lcd-af-fft"]')).toHaveLength(1);
    expect(target.querySelector('[data-testid="lcd-af-fft"]')?.getAttribute('data-fft-mode')).toBe('live');
    expect(target.querySelectorAll('[data-testid="lcd-filter-envelope"]')).toHaveLength(1);
    expect(target.querySelector('[data-testid="lcd-filter-envelope"]')?.getAttribute('aria-label')).toContain('MAIN');
  });

  it('selects SUB scope facts only when SUB is explicitly active', () => {
    const subActiveReceivers: PeerSplitDisplayModel['receivers'] = [
      { ...receivers[0], activity: 'inactive' },
      { ...receivers[1], activity: 'active', operational: true, spectrum: 'waiting' },
    ];
    const target = render({
      ...model, receivers: subActiveReceivers, activeReceiver: subActiveReceivers[1],
    }, audioFrame({ receiver: 'SUB', normalizedBins: [0, 0.5, 1] }));

    expect(target.querySelector('[data-testid="lcd-dominant-main"]')?.getAttribute('data-receiver-activity')).toBe('inactive');
    expect(target.querySelector('[data-testid="lcd-dominant-sub"]')?.getAttribute('data-receiver-activity')).toBe('active');
    expect(getComputedStyle(target.querySelector('[data-testid="lcd-dominant-main"]')!).opacity).toBe('0.62');
    expect(getComputedStyle(target.querySelector('[data-testid="lcd-dominant-sub"]')!).opacity).toBe('1');
    expect(target.querySelector('[data-testid="lcd-filter-envelope"]')?.getAttribute('aria-label')).toContain('SUB');
    expect(target.querySelector('[data-testid="lcd-af-fft"]')?.getAttribute('data-fft-mode')).toBe('live');
  });

  it('fails closed instead of choosing a receiver when activity is unknown', () => {
    const unknownReceivers: PeerSplitDisplayModel['receivers'] = [
      { ...receivers[0], activity: 'unknown' },
      { ...receivers[1], activity: 'unknown' },
    ];
    const target = render({ ...model, receivers: unknownReceivers, activeReceiver: null }, {
      source: 'audio-fft', receiver: 'MAIN', freshness: 'fresh',
      startHz: 0, endHz: 24_000, normalizedBins: [1, 0.5, 0],
    });

    expect(target.querySelector('[data-testid="lcd-dominant-scope-unknown"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-testid="lcd-af-fft"]')).toHaveLength(0);
    expect(target.querySelectorAll('[data-testid="lcd-filter-envelope"]')).toHaveLength(0);
  });

  it('does not invent reference-only clock, memory, temperature, or band-edge facts', () => {
    const target = render();
    const visibleText = [...target.querySelectorAll('*')]
      .filter((node) => getComputedStyle(node).visibility !== 'hidden')
      .map((node) => node.childNodes.length === 1 ? node.textContent ?? '' : '')
      .join(' ');

    expect(visibleText).not.toMatch(/UTC|LOC|DATE|TEMP|QSY|M-0\d|BAND\s*EDGE|14\.248|14\.252/i);
  });

  it('ghosts unknown facts and structurally hides unsupported facts', () => {
    const unknownMain = {
      ...receivers[0],
      frequency: { state: 'unknown' as const },
      mode: { state: 'unknown' as const },
    };
    const unknownReceivers = [unknownMain, receivers[1]] as const;
    const target = render({
      ...model, receivers: unknownReceivers, activeReceiver: unknownMain,
    });

    expect(target.querySelector('[data-testid="lcd-frequency-MAIN"]')?.getAttribute('data-state')).toBe('unknown');
    expect(target.querySelector('[data-testid="lcd-main-mode"]')?.getAttribute('data-state')).toBe('unknown');
    expect(target.querySelector('[data-testid="lcd-main-mode"]')?.textContent).toContain('?');
    const unsupportedBand = target.querySelector('[data-testid="lcd-sub-band"]')!;
    expect(unsupportedBand.getAttribute('data-state')).toBe('unsupported');
    expect(getComputedStyle(unsupportedBand).visibility).toBe('hidden');
  });

  it('keeps stage geometry and state treatment styles in the owning component', () => {
    const source = readFileSync('src/skins/segmentline/DominantUnifiedDisplay.svelte', 'utf8');
    const style = source.match(/<style>([\s\S]*?)<\/style>/)?.[1] ?? '';

    expect(style).toContain('.dominant-display {');
    expect(style).toContain('grid-template-rows: 132px 56px 52px minmax(0, 1fr);');
    expect(style).toContain(".fact[data-state='unsupported'] { visibility: hidden; }");
    expect(style).toContain(".fact[data-state='unknown'] { color: var(--ink-soft); }");
  });

  it.each([
    ['missing', undefined],
    ['stale', audioFrame({ freshness: 'stale' })],
    ['source mismatch', audioFrame({ source: 'hardware' })],
    ['receiver mismatch', audioFrame({ receiver: 'SUB' })],
    ['malformed', { source: 'audio-fft', receiver: 'MAIN', freshness: 'fresh', startHz: 0, endHz: 0, normalizedBins: [0] }],
  ])('fails closed to an empty AF trace for %s input', (_label, spectrumFrame) => {
    const target = render(model, spectrumFrame);

    expect(target.querySelector('[data-testid="lcd-af-fft"]')?.getAttribute('data-fft-mode'))
      .toBe('safe-empty');
  });

  it('uses the frozen source-qualified spectrum resolver', () => {
    const source = readFileSync('src/skins/segmentline/DominantUnifiedDisplay.svelte', 'utf8');

    expect(source).toContain("import { resolveLcdSpectrumFrame } from './lcd-display-contract';");
    expect(source).toContain("source: 'audio-fft'");
    expect(source).toContain("spectrumResolution.state === 'live'");
  });
});
