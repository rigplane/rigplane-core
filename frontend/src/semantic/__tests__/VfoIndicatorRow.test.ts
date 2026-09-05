import { SvelteMap } from 'svelte/reactivity';
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount, type ComponentProps } from 'svelte';
import VfoIndicatorRow from '../VfoIndicatorRow.svelte';
import type {
  Availability, RadioWideIndicatorsViewModel,
  ReceiverIndicatorField, ReceiverIndicatorViewModel,
} from '../radio-view-model';

const AVAILABLE: Availability = { structural: true, operational: true };
const known = <T>(value: T): ReceiverIndicatorField<T> => ({
  reading: { status: 'known', value }, availability: AVAILABLE,
});
const unknown = <T = never>(structural = true): ReceiverIndicatorField<T> => ({
  reading: { status: 'unknown' }, availability: { structural, operational: false },
});

function indicator(overrides: Partial<ReceiverIndicatorViewModel> = {}): ReceiverIndicatorViewModel {
  return {
    receiver: 'MAIN', availability: AVAILABLE,
    sMeter: known(0), bandwidthHz: known(2400), agcMode: known(0),
    nbActive: known(false), nrActive: known(true), notchMode: known('off'),
    attenuator: known(0), preamp: known(0), rfGain: known(0),
    digiSel: known(false), ipPlus: known(true), ...overrides,
  };
}

let component: ReturnType<typeof mount> | null = null;
let target: HTMLDivElement;

function render(props: ComponentProps<typeof VfoIndicatorRow>): HTMLElement {
  component = mount(VfoIndicatorRow, { target, props });
  flushSync();
  return target;
}

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  target.remove();
});

describe('VfoIndicatorRow', () => {
  it('renders one addressed row and one real S-meter for a known finite zero', () => {
    const root = render({ indicator: indicator() });
    expect(root.querySelectorAll('[data-testid="vfo-indicator-row"]')).toHaveLength(1);
    expect(root.querySelector('[data-indicator-receiver="MAIN"]')).not.toBeNull();
    expect(root.querySelectorAll('[data-testid="receiver-s-meter"]')).toHaveLength(1);
    expect(root.querySelector('[data-testid="receiver-s-meter"] svg')).not.toBeNull();
    expect(root.querySelector('[data-testid="receiver-s-meter-unknown"]')).toBeNull();
  });

  it('renders a true unknown shell and never passes a fabricated zero to LinearSMeter', () => {
    const root = render({ indicator: indicator({ sMeter: unknown() }) });
    expect(root.querySelector('[data-testid="receiver-s-meter"] svg')).toBeNull();
    expect(root.querySelector('[data-testid="receiver-s-meter-unknown"]')?.textContent).toContain('S —');
  });

  it('preserves known false and zero as observed OFF/0 facts, not unknown', () => {
    const root = render({ indicator: indicator() });
    for (const fact of ['nb', 'digi-sel']) {
      const node = root.querySelector(`[data-indicator-fact="${fact}"]`);
      expect(node?.getAttribute('data-state')).toBe('off');
      expect(node?.textContent).toContain('OFF');
    }
    for (const fact of ['agc', 'attenuator', 'preamp', 'rf-gain']) {
      const node = root.querySelector(`[data-indicator-fact="${fact}"]`);
      expect(node?.getAttribute('data-state')).toBe('known');
      expect(node?.textContent).toContain('0');
    }
  });

  it('renders a capability-provided AGC label verbatim', () => {
    const root = render({ indicator: indicator({ agcMode: known('SLOW') }) });
    expect(root.querySelector('[data-indicator-fact="agc"]')?.textContent).toContain('AGC SLOW');
  });

  it('keeps an unavailable structural receiver present, disabled, and explicitly unknown', () => {
    const root = render({ indicator: indicator({
      receiver: 'SUB',
      availability: { structural: true, operational: false },
      sMeter: unknown(), bandwidthHz: unknown(), agcMode: unknown(),
      nbActive: unknown(), nrActive: unknown(), notchMode: unknown(),
      attenuator: unknown(), preamp: unknown(), rfGain: unknown(),
      digiSel: unknown(), ipPlus: unknown(),
    }) });
    const row = root.querySelector('[data-testid="vfo-indicator-row"]');
    expect(row?.getAttribute('data-indicator-receiver')).toBe('SUB');
    expect(row?.getAttribute('data-indicator-operational')).toBe('false');
    expect(row?.querySelector('[data-indicator-rf]')).toBeNull();
    expect(row?.querySelectorAll('[data-state="unknown"]').length).toBeGreaterThan(0);
  });

  it('omits structurally absent facts instead of inventing an unsupported value', () => {
    const root = render({ indicator: indicator({
      bandwidthHz: unknown(false), agcMode: unknown(false), nbActive: unknown(false),
      nrActive: unknown(false), notchMode: unknown(false), attenuator: unknown(false),
      preamp: unknown(false), rfGain: unknown(false), digiSel: unknown(false),
      ipPlus: unknown(false),
    }) });
    expect(root.querySelectorAll('[data-indicator-fact]')).toHaveLength(0);
    expect(root.querySelector('[data-testid="receiver-s-meter"]')).not.toBeNull();
  });

  it('has no raw runtime-state, transport, command, or TX-controller input', () => {
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8')
      .replace(/<!--[\s\S]*?-->/g, '')
      .replace(/\/\*[\s\S]*?\*\//g, '');
    expect(source).not.toMatch(/radioState|ServerState|fieldStatus|\$lib\/transport|tx-controller|panel-commands/);
  });
});

function shared(): RadioWideIndicatorsViewModel {
  return {
    rfState: 'receiving', antenna: known(1), atu: known('off'),
    ritActive: known(false), ritOffset: known(0),
    xitActive: known(true), xitOffset: known(0),
    actions: {
      main: AVAILABLE, sub: AVAILABLE, equalize: AVAILABLE, swap: AVAILABLE,
      quickSplit: AVAILABLE, quickDualWatch: AVAILABLE, speak: AVAILABLE,
    },
  };
}

describe('radio-wide singleton indicators (MOR-2309)', () => {
  it('renders ANT/ATU/RIT/XIT and authority facts once while preserving false and zero', () => {
    const root = render({
      radioWide: shared(),
    });
    expect(root.querySelectorAll('[data-testid="vfo-shared-indicators"]')).toHaveLength(1);
    expect(root.querySelector('[data-indicator-fact="antenna"]')?.textContent).toContain('ANT 1');
    expect(root.querySelector('[data-indicator-fact="atu"]')?.textContent).toContain('TUNE OFF');
    expect(root.querySelector('[data-indicator-fact="rit"]')?.textContent).toContain('RIT OFF 0 Hz');
    expect(root.querySelector('[data-indicator-fact="xit"]')?.textContent).toContain('XIT ON 0 Hz');
    expect(root.querySelector('[data-indicator-fact="rf-authority"]')?.textContent).toContain('RX');
  });

  it('keeps missing/unobserved shared leaves visibly unknown and omits unsupported facts', () => {
    const root = render({
      radioWide: {
        ...shared(), rfState: 'unknown', antenna: unknown(false), atu: unknown(),
        ritActive: unknown(), ritOffset: unknown(), xitActive: unknown(), xitOffset: unknown(),
      },
    });
    expect(root.querySelector('[data-indicator-fact="antenna"]')).toBeNull();
    expect(root.querySelector('[data-indicator-fact="rf-authority"]')?.textContent).toContain('RF ?');
    for (const fact of ['atu', 'rit', 'xit']) {
      expect(root.querySelector(`[data-indicator-fact="${fact}"]`)?.textContent).toContain('—');
    }
  });

  it('marks RIT/XIT aggregate state unknown when either constituent is unknown', () => {
    const root = render({
      radioWide: {
        ...shared(), ritActive: known(false), ritOffset: unknown(),
        xitActive: unknown(), xitOffset: known(0),
      },
    });
    const rit = root.querySelector('[data-indicator-fact="rit"]');
    const xit = root.querySelector('[data-indicator-fact="xit"]');
    expect(rit?.getAttribute('data-state')).toBe('unknown');
    expect(rit?.textContent).toContain('RIT OFF — Hz');
    expect(xit?.getAttribute('data-state')).toBe('unknown');
    expect(xit?.textContent).toContain('XIT — 0 Hz');
  });
});


describe('MOR-2342 addressed meter appearance', () => {
  it.each(['sdr', 'standard'] as const)('never draws unknown as zero in %s', (appearance) => {
    const root = render({ indicator: indicator({ sMeter: unknown() }), appearance });
    expect(root.querySelector('[data-testid="receiver-s-meter"] svg')).toBeNull();
    expect(root.querySelector('[data-testid="receiver-s-meter-unknown"]')?.textContent).toContain('S —');
  });
  it('selects the SDR meter without changing a confirmed zero or receiver identity', () => {
    const root = render({ indicator: indicator(), appearance: 'sdr' });
    expect(root.querySelector('[data-indicator-receiver="MAIN"]')).not.toBeNull();
    expect(root.querySelector('svg[data-variant="sdr-screen"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="receiver-s-meter-unknown"]')).toBeNull();
  });
  it('renders only the supplied Standard slot label, never MAIN as A', () => {
    const root = render({ indicator: indicator(), appearance: 'standard' });
    expect(root.querySelector('.header-badges')?.textContent).toContain('BAR');
    expect(root.querySelector('.header-badges')?.textContent).toContain('—');
  });
  it('bounds the rendered Standard S-meter at the historical row height', () => {
    const root = render({ indicator: indicator(), appearance: 'standard' });
    const shell = root.querySelector<HTMLElement>('[data-testid="receiver-s-meter"]')!;
    const meter = shell.querySelector<SVGElement>('svg[data-variant="vfo-wide"]')!;
    expect(shell).not.toBeNull();
    expect(meter).not.toBeNull();
    const source = readFileSync('src/semantic/VfoIndicatorRow.svelte', 'utf8');
    expect(source).toMatch(/\[data-indicator-appearance='standard'\] \.s-meter \{\s*width: 100%; max-width: 600px; height: 58px;/);
    expect(source).toContain("svg[data-variant='vfo-wide']");
  });
});


describe('RF gain display observation', () => {
  it.each(['semantic', 'standard', 'sdr'] as const)('keeps the same text and DOM footprint through current/stale/current for %s', (appearance) => {
    const current = indicator({ rfGain: { ...known(0.75), display: { state: 'current', value: 0.75 } } });
    const state = new SvelteMap([['indicator', current]]);
    render({ appearance, get indicator() { return state.get('indicator'); } });
    const node = target.querySelector('[data-indicator-fact="rf-gain"]')!;
    const marker = node.querySelector('.stale-cue')!;
    const text = node.textContent;
    expect(text).toContain('RFG 0.75');
    expect(getComputedStyle(marker).visibility).toBe('hidden');
    expect(getComputedStyle(marker).width).toBe('1ch');
    flushSync(() => state.set('indicator', indicator({ rfGain: {
      ...unknown<number>(), display: { state: 'stale', value: 0.75 },
    } })));
    expect(target.querySelector('[data-indicator-fact="rf-gain"]')).toBe(node);
    expect(node.querySelector('.stale-cue')).toBe(marker);
    expect(node.textContent).toBe(text);
    expect(node.getAttribute('data-state')).toBe('unknown');
    expect(node.getAttribute('data-display-state')).toBe('stale');
    expect(node.getAttribute('aria-label')).toContain('stale');
    expect(getComputedStyle(marker).visibility).toBe('visible');
    expect(marker.textContent?.trim()).not.toBe('');
    expect(target.querySelector('[aria-live], button, input')).toBeNull();
    flushSync(() => state.set('indicator', current));
    expect(node.textContent).toBe(text);
    expect(node.getAttribute('data-display-state')).toBe('current');
    expect(getComputedStyle(marker).visibility).toBe('hidden');
  });
  it('does not display a strict fallback default when explicit observation is unknown', () => {
    render({ indicator: indicator({ rfGain: {
      ...known(0), display: { state: 'unknown', reason: 'not-observed' },
    } }) });
    const node = target.querySelector('[data-indicator-fact="rf-gain"]')!;
    expect(node.textContent).toContain('RFG —');
    expect(node.textContent).not.toContain('RFG 0');
  });
  it('keeps a display-unsupported RFgain absent', () => {
    render({ indicator: indicator({ rfGain: { ...known(0), display: { state: 'unsupported' } } }) });
    expect(target.querySelector('[data-indicator-fact="rf-gain"]')).toBeNull();
  });
});
