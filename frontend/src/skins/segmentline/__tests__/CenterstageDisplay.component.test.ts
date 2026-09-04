import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';
import type {
  PeerSplitDisplayModel,
  PeerSplitReceiverDisplay,
} from '../../../semantic/radio-display-model';
import CenterstageDisplay from '../CenterstageDisplay.svelte';

const known = <T>(value: T) => ({ state: 'known' as const, value });
const indicator = (state: 'active' | 'inactive' = 'inactive') => ({ state });

function receiver(
  id: PeerSplitReceiverDisplay['receiver'],
  activity: PeerSplitReceiverDisplay['activity'],
): PeerSplitReceiverDisplay {
  const main = id === 'MAIN';
  return {
    receiver: id,
    label: main ? 'VFO A' : 'VFO B',
    activity,
    operational: activity === 'active',
    frequency: known(main ? 14_250_000 : 14_195_500),
    mode: known(main ? 'USB' : 'CW'),
    filter: known(main ? 'FIL1' : 'FIL2'),
    band: activity === 'active' ? known('20m') : { state: 'unsupported' },
    sMeter: activity === 'active' ? known(-18) : { state: 'unknown' },
    bandwidthHz: known(main ? 2400 : 500),
    ifShiftHz: activity === 'active' ? known(0) : { state: 'unsupported' },
    pbtInnerHz: activity === 'active' ? known(400) : { state: 'unsupported' },
    pbtOuterHz: activity === 'active' ? known(-400) : { state: 'unsupported' },
    spectrum: activity === 'active' ? 'waiting' : 'inactive',
    dsp: {
      agc: known('MID'), nb: indicator(), nr: indicator('active'), notch: known('off'),
    },
    front: {
      preamp: known(1), attenuator: known(0), rfGain: known(0.7),
      digiSel: { state: 'unsupported' }, ipPlus: indicator(),
    },
  };
}

const main = receiver('MAIN', 'active');
const sub = receiver('SUB', 'inactive');

const model: PeerSplitDisplayModel = {
  kind: 'peer-split',
  rfState: 'receiving',
  receivers: [main, sub],
  activeReceiver: main,
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
  normalizedFftBins?: Partial<Record<'MAIN' | 'SUB', readonly number[]>>,
) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(CenterstageDisplay, {
    target,
    props: { model: displayModel, normalizedFftBins },
  });
  return target;
}

function withActive(id: 'MAIN' | 'SUB'): PeerSplitDisplayModel {
  const receivers: PeerSplitDisplayModel['receivers'] = id === 'MAIN'
    ? [receiver('MAIN', 'active'), receiver('SUB', 'inactive')]
    : [receiver('MAIN', 'inactive'), receiver('SUB', 'active')];
  return {
    ...model,
    receivers,
    activeReceiver: receivers.find((item) => item.receiver === id) ?? null,
  };
}

describe('CenterstageDisplay', () => {
  it('is passive and renders exactly one active-receiver meter and scope', () => {
    const target = render(model, { MAIN: [0, 0.5, 1], SUB: [1, 0.5, 0] });

    expect(target.querySelectorAll('button,input,select,a[href],[tabindex],[role="button"],[role="switch"]')).toHaveLength(0);
    expect(target.innerHTML).not.toMatch(/onclick|onpointer|onwheel/i);
    expect(target.querySelectorAll('.s-meter')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="lcd-filter-envelope"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="lcd-af-fft"]')).toHaveLength(1);
    expect(target.querySelector('[data-testid="lcd-af-fft"]')?.getAttribute('data-fft-mode')).toBe('live');
  });

  it('promotes the active receiver to the hero and keeps the other frequency truthful', () => {
    let target = render(withActive('MAIN'));
    expect(target.querySelector('[data-testid="centerstage-hero"]')?.textContent).toContain('14.250.000');
    expect(target.querySelector('[data-testid="centerstage-secondary"]')?.textContent).toContain('14.195.500');
    unmount(component!);
    component = null;
    document.body.innerHTML = '';

    target = render(withActive('SUB'));
    expect(target.querySelector('[data-testid="centerstage-hero"]')?.textContent).toContain('14.195.500');
    expect(target.querySelector('[data-testid="centerstage-secondary"]')?.textContent).toContain('14.250.000');
  });

  it('fails the hero closed when active identity is unknown without borrowing MAIN', () => {
    const target = render({ ...model, activeReceiver: null });
    const hero = target.querySelector('[data-testid="centerstage-hero"]')!;

    expect(hero.getAttribute('data-receiver')).toBe('unknown');
    expect(hero.getAttribute('data-state')).toBe('unknown');
    expect(hero.textContent).toContain('—.———.———');
    expect(hero.textContent).not.toContain('14.250.000');
    expect(target.querySelector('[data-testid="centerstage-secondary"]')?.textContent).toContain('14.195.500');
    expect(target.querySelector('[data-testid="centerstage-meter"]')?.getAttribute('data-state')).toBe('unknown');
    expect(target.querySelector('[data-testid="lcd-af-fft"]')?.getAttribute('data-fft-mode')).toBe('safe-empty');
  });

  it('limits the quiet orbit and footer to backed model facts', () => {
    const target = render();

    expect([...target.querySelectorAll('[data-orbit-field]')]
      .map((node) => node.getAttribute('data-orbit-field')))
      .toEqual(['MODE', 'FILT', 'BAND', 'AGC']);
    expect(target.querySelector('[data-orbit-field="MODE"]')?.textContent).toContain('USB');
    expect(target.querySelector('[data-orbit-field="FILT"]')?.textContent).toContain('FIL1');
    expect(target.querySelector('[data-orbit-field="BAND"]')?.textContent).toContain('20m');
    expect(target.querySelector('[data-orbit-field="AGC"]')?.textContent).toContain('MID');
    expect(target.querySelector('[data-testid="centerstage-telemetry"]')?.textContent).toContain('VD 13.8');
    expect(target.querySelector('[data-testid="centerstage-telemetry"]')?.textContent).toContain('ID 0.7');
    expect(target.textContent).not.toMatch(/QSY|M-0\d|TEMP|UTC|LOC|DATE|BAND EDGE/i);
    expect(target.querySelector('.memory-seam')).toBeNull();
  });

  it('ghosts unknown facts and hides unsupported facts without inventing values', () => {
    const unsupportedActive: PeerSplitReceiverDisplay = {
      ...main,
      frequency: { state: 'unsupported' },
      mode: { state: 'unknown' },
      filter: { state: 'unsupported' },
      band: { state: 'unknown' },
      sMeter: { state: 'unsupported' },
      bandwidthHz: { state: 'unknown' },
      spectrum: 'unsupported',
      dsp: { ...main.dsp, agc: { state: 'unsupported' } },
    };
    const target = render({
      ...model,
      receivers: [unsupportedActive, sub],
      activeReceiver: unsupportedActive,
    });

    expect(target.querySelector('[data-orbit-field="MODE"]')?.getAttribute('data-state')).toBe('unknown');
    expect(target.querySelector('[data-orbit-field="MODE"]')?.textContent).toContain('?');
    expect(getComputedStyle(target.querySelector('[data-orbit-field="FILT"]')!).visibility).toBe('hidden');
    expect(getComputedStyle(target.querySelector('[data-orbit-field="AGC"]')!).visibility).toBe('hidden');
    expect(getComputedStyle(target.querySelector('[data-testid="centerstage-hero"]')!).visibility).toBe('hidden');
    expect(getComputedStyle(target.querySelector('[data-testid="centerstage-meter"]')!).visibility).toBe('hidden');
    expect(target.querySelectorAll('[data-testid="lcd-af-fft"]')).toHaveLength(0);
    expect(target.textContent).not.toContain('14.250.000');
  });
});
