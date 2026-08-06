import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import BarGauge from '../BarGauge.svelte';

// MOR-1345: BarGauge's optional fault-highlight channel. `MetersSurface`
// (the semantic meters surface) computes the fault FACT from the shared
// `isSwrFault`/`isAlcFault` predicates and hands this component a boolean —
// BarGauge is the one place the semantic vertical already owns colour
// (zone segments, peak marker), so the actual highlight is drawn here, not
// in the colour-free surface file. Defaults to `false`: every OTHER BarGauge
// consumer (Po/Id/Vd/COMP tiles, and every pre-existing call site) never
// passes the prop and must render exactly as it did before this ticket.

let target: HTMLDivElement;
afterEach(() => { target?.remove(); });

function render(props: Record<string, unknown>) {
  target = document.createElement('div');
  document.body.appendChild(target);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component = mount(BarGauge as any, { target, props });
  flushSync();
  const svg = target.querySelector('svg')!;
  return {
    dispose: () => unmount(component),
    svg,
    containerRect: svg.querySelector('rect')!,
  };
}

const BASE = { value: 0.5, label: 'SWR', displayValue: '2.3' };

describe('BarGauge fault highlight (MOR-1345)', () => {
  it('defaults to no fault when the prop is omitted', () => {
    const g = render(BASE);
    expect(g.svg.getAttribute('data-fault')).toBe('false');
    g.dispose();
  });

  // MUTATION KILLED: the fault prop not reaching the container stroke.
  it('marks data-fault and swaps the container stroke to the accent-red token when fault=true', () => {
    const g = render({ ...BASE, fault: true });
    expect(g.svg.getAttribute('data-fault')).toBe('true');
    expect(g.containerRect.getAttribute('stroke')).toBe('var(--v2-accent-red, #ff4040)');
    expect(g.containerRect.getAttribute('stroke-width')).toBe('2');
    g.dispose();
  });

  it('keeps the ordinary panel-border stroke when fault=false', () => {
    const g = render({ ...BASE, fault: false });
    expect(g.containerRect.getAttribute('stroke')).toBe('var(--v2-bg-panel)');
    expect(g.containerRect.getAttribute('stroke-width')).toBe('1');
    g.dispose();
  });
});
