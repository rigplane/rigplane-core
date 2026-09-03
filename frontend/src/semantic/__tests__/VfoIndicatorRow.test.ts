import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import VfoIndicatorRow from '../VfoIndicatorRow.svelte';
import type {
  Availability, ReceiverIndicatorField, ReceiverIndicatorViewModel,
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
    receiver: 'MAIN', availability: AVAILABLE, rfState: 'receiving',
    sMeter: known(0), bandwidthHz: known(2400), agcMode: known(0),
    nbActive: known(false), nrActive: known(true), notchMode: known('off'),
    attenuator: known(0), preamp: known(0), rfGain: known(0),
    digiSel: known(false), ipPlus: known(true), ...overrides,
  };
}

let component: ReturnType<typeof mount> | null = null;
let target: HTMLDivElement;

function render(value: ReceiverIndicatorViewModel): HTMLElement {
  component = mount(VfoIndicatorRow, { target, props: { indicator: value } });
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
    const root = render(indicator());
    expect(root.querySelectorAll('[data-testid="vfo-indicator-row"]')).toHaveLength(1);
    expect(root.querySelector('[data-indicator-receiver="MAIN"]')).not.toBeNull();
    expect(root.querySelectorAll('[data-testid="receiver-s-meter"]')).toHaveLength(1);
    expect(root.querySelector('[data-testid="receiver-s-meter"] svg')).not.toBeNull();
    expect(root.querySelector('[data-testid="receiver-s-meter-unknown"]')).toBeNull();
  });

  it('renders a true unknown shell and never passes a fabricated zero to LinearSMeter', () => {
    const root = render(indicator({ sMeter: unknown() }));
    expect(root.querySelector('[data-testid="receiver-s-meter"] svg')).toBeNull();
    expect(root.querySelector('[data-testid="receiver-s-meter-unknown"]')?.textContent).toContain('S —');
  });

  it('preserves known false and zero as observed OFF/0 facts, not unknown', () => {
    const root = render(indicator());
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

  it('keeps an unavailable structural receiver present, disabled, and explicitly unknown', () => {
    const root = render(indicator({
      receiver: 'SUB',
      availability: { structural: true, operational: false },
      rfState: 'unknown',
      sMeter: unknown(), bandwidthHz: unknown(), agcMode: unknown(),
      nbActive: unknown(), nrActive: unknown(), notchMode: unknown(),
      attenuator: unknown(), preamp: unknown(), rfGain: unknown(),
      digiSel: unknown(), ipPlus: unknown(),
    }));
    const row = root.querySelector('[data-testid="vfo-indicator-row"]');
    expect(row?.getAttribute('data-indicator-receiver')).toBe('SUB');
    expect(row?.getAttribute('data-indicator-operational')).toBe('false');
    expect(row?.querySelector('[data-indicator-rf="unknown"]')?.textContent).toBe('RF ?');
    expect(row?.querySelectorAll('[data-state="unknown"]').length).toBeGreaterThan(0);
  });

  it('omits structurally absent facts instead of inventing an unsupported value', () => {
    const root = render(indicator({
      bandwidthHz: unknown(false), agcMode: unknown(false), nbActive: unknown(false),
      nrActive: unknown(false), notchMode: unknown(false), attenuator: unknown(false),
      preamp: unknown(false), rfGain: unknown(false), digiSel: unknown(false),
      ipPlus: unknown(false),
    }));
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
