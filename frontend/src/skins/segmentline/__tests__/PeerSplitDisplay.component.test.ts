import { afterEach, describe, expect, it } from 'vitest';
import { mount, unmount } from 'svelte';
import type { PeerSplitDisplayModel } from '../../../semantic/radio-display-model';
import PeerSplitDisplay from '../PeerSplitDisplay.svelte';

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
    frequency: known(14_195_500), mode: known('CW'), filter: known('FIL2'), band: { state: 'unsupported' },
    sMeter: { state: 'unknown' }, bandwidthHz: known(500), ifShiftHz: { state: 'unsupported' },
    pbtInnerHz: { state: 'unsupported' }, pbtOuterHz: { state: 'unsupported' }, spectrum: 'inactive',
    dsp: { agc: known('MID'), nb: indicator(), nr: indicator('inactive'), notch: known('off') },
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
  activeReceiver: {
    receiver: 'MAIN', label: 'VFO A', activity: 'active', operational: true,
    frequency: known(14_250_000), mode: known('USB'), filter: known('FIL1'), band: known('20m'),
    sMeter: known(-18), bandwidthHz: known(2400), ifShiftHz: known(0),
    pbtInnerHz: known(400), pbtOuterHz: known(-400),
    spectrum: 'waiting',
    dsp: { agc: known('MID'), nb: indicator(), nr: indicator('active'), notch: known('off') },
    front: { preamp: known(1), attenuator: known(0), rfGain: known(0.7), digiSel: { state: 'unsupported' }, ipPlus: indicator() },
  },
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
  normalizedFftBins?: Partial<Record<'MAIN' | 'SUB', readonly number[]>>,
  displayModel: PeerSplitDisplayModel = model,
) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(PeerSplitDisplay, {
    target, props: { model: displayModel, normalizedFftBins },
  });
  return target;
}

describe('PeerSplitDisplay', () => {
  it('is a read-only glass subtree with no interactive affordance', () => {
    const target = render();
    expect(target.querySelectorAll('button,input,select,a[href],[tabindex],[role="button"],[role="switch"]')).toHaveLength(0);
    expect(target.innerHTML).not.toMatch(/onclick|onpointer|onwheel/i);
  });

  it('renders two fixed peer columns and keeps the inactive column ghosted', () => {
    const target = render();
    const columns = target.querySelectorAll('[data-testid="lcd-peer-column"]');
    expect(columns).toHaveLength(2);
    expect(columns[0].getAttribute('data-column-activity')).toBe('active');
    expect(columns[1].getAttribute('data-column-activity')).toBe('inactive');
    expect(target.querySelector('[data-testid="lcd-frequency-MAIN"]')?.textContent).toContain('14.250.000');
    expect(target.querySelector('[data-testid="lcd-frequency-SUB"]')?.textContent).toContain('14.195.500');
  });

  it('keeps the three offset slots and renders no fabricated spectrum bars', () => {
    const target = render();
    expect(target.querySelectorAll('[data-testid^="lcd-offset-"]')).toHaveLength(6);
    expect(target.querySelectorAll('[data-spectrum-bin]')).toHaveLength(0);
    expect(target.querySelectorAll('[data-testid="lcd-filter-envelope"]')).toHaveLength(2);
  });

  it('keeps the safe-empty FFT buffer inside the passive primitive', () => {
    const target = render();
    const fft = target.querySelectorAll('[data-testid="lcd-af-fft"]');
    expect(fft).toHaveLength(2);
    expect(target.querySelector('[data-testid="lcd-scope-label-MAIN"]')?.textContent?.trim()).toBe('AF SCOPE · BANDPASS');
    expect([...fft].map((node) => node.getAttribute('data-fft-mode'))).toEqual(['safe-empty', 'safe-empty']);
    expect(fft[0].querySelector('.fft-trace')?.getAttribute('d')).toMatch(/^M0\.00,92\.00 L/);
  });

  it('renders supplied active-receiver bins but ignores bins for an inactive receiver', () => {
    const target = render({ MAIN: [0, 0.5, 1], SUB: [1, 0.5, 0] });
    const fft = target.querySelectorAll('[data-testid="lcd-af-fft"]');
    expect(fft[0].getAttribute('data-fft-mode')).toBe('live');
    expect(fft[1].getAttribute('data-fft-mode')).toBe('safe-empty');
  });

  it('keeps truthful filter envelopes when AF-FFT is structurally unsupported', () => {
    const receivers: PeerSplitDisplayModel['receivers'] = [
      { ...model.receivers[0], spectrum: 'unsupported' },
      { ...model.receivers[1], spectrum: 'unsupported' },
    ];
    const hardwareOnlyModel: PeerSplitDisplayModel = {
      ...model,
      receivers,
      activeReceiver: receivers[0],
    };
    const target = render(undefined, hardwareOnlyModel);

    expect(target.querySelectorAll('[data-testid="lcd-af-fft"]')).toHaveLength(0);
    expect(target.querySelectorAll('[data-testid="lcd-filter-envelope"]')).toHaveLength(2);
    expect(target.querySelector('[data-scope-state="unsupported"] polyline')).not.toBeNull();
    expect(getComputedStyle(target.querySelector('[data-scope-state="unsupported"]')!).visibility).toBe('visible');
    expect(target.querySelector('[data-testid="lcd-scope-label-MAIN"]')?.textContent?.trim()).toBe('BANDPASS');
    expect(target.textContent).not.toContain('AF SCOPE');
  });

  it('ghosts active-receiver rails when receiver identity is unknown', () => {
    const target = render(undefined, { ...model, activeReceiver: null });
    const factRail = target.querySelector('.fact-rail')!;

    expect(factRail.querySelectorAll('[data-state="active"]')).toHaveLength(0);
    expect(factRail.querySelectorAll('[data-state="unknown"]')).toHaveLength(9);
    expect(factRail.textContent).toContain('PRE ?');
    expect(factRail.textContent).toContain('ATT ?');
  });

  it('keeps split-off geometry without claiming a zero delta', () => {
    const target = render(undefined, {
      ...model, offsets: { ...model.offsets, split: { state: 'inactive' } },
    });
    const splitCells = target.querySelectorAll('[data-testid$="-split"]');

    expect(splitCells).toHaveLength(2);
    expect([...splitCells].map((cell) => cell.textContent)).toEqual([
      'SPLIT —kHz', 'SPLIT —kHz',
    ]);
    expect([...splitCells].some((cell) => cell.textContent?.includes('0.000'))).toBe(false);
  });

  it.each([
    ['manual', 'active', 'inactive'],
    ['auto', 'inactive', 'active'],
    ['off', 'inactive', 'inactive'],
  ] as const)('maps notch mode %s to mutually exclusive NOTCH/ANF flags', (
    notchMode, notchState, anfState,
  ) => {
    const activeReceiver = {
      ...model.activeReceiver!,
      dsp: { ...model.activeReceiver!.dsp, notch: known(notchMode) },
    };
    const target = render(undefined, { ...model, activeReceiver });

    expect(target.querySelector('[data-status-label="NOTCH"]')?.getAttribute('data-state')).toBe(notchState);
    expect(target.querySelector('[data-status-label="ANF"]')?.getAttribute('data-state')).toBe(anfState);
    const anfIcon = target.querySelector('[data-status-label="ANF"] svg');
    expect(anfIcon?.querySelector('path')?.getAttribute('d')).toBe('M19.07 4.93A10 10 0 0 0 6.99 3.34');
    expect(anfIcon?.querySelector('path[d="M3 18 6 6h2l2 8h4l2-8h2l3 12"]')).toBeNull();
  });

  it('ghosts both NOTCH and ANF when notch mode is unknown', () => {
    const activeReceiver = {
      ...model.activeReceiver!,
      dsp: { ...model.activeReceiver!.dsp, notch: { state: 'unknown' as const } },
    };
    const target = render(undefined, { ...model, activeReceiver });

    expect(target.querySelector('[data-status-label="NOTCH"]')?.getAttribute('data-state')).toBe('unknown');
    expect(target.querySelector('[data-status-label="ANF"]')?.getAttribute('data-state')).toBe('unknown');
  });
});
