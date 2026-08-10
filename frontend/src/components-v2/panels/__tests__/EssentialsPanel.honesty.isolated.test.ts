/**
 * MOR-1409 A13a — ESSENTIALS display-honesty boundary.
 *
 * `MobileRadioLayout.svelte` migrates its RX-audio projection from
 * `components-v2/wiring/state-adapter.ts` (which fabricates `afLevel` as
 * `rx?.afLevel ?? 0.5`) to the A11/A12-hardened
 * `lib/runtime/props/panel-props.ts`, which returns `Number.NaN` for an
 * unobserved AF level in local-monitor mode.
 *
 * `EssentialsPanel` renders that number through `normalizedPercentDisplay`,
 * whose `Math.round(Math.max(0, Math.min(1, NaN)) * 100)` is `NaN` — the
 * rendered string becomes the literal "NaN%". That is the same
 * formatted-display defect class that BLOCKED PR #2363 ("NaNkHz") and that
 * `RxAudioPanel.svelte:31-33` already guards for this exact field.
 *
 * This panel is A13a's one granted display-honesty guard owner
 * (correction 5246842617 §3, in the 5246487510 shape): its AF readout is
 * rendered unconditionally, so no guard placed inside `MobileRadioLayout`
 * can reach it without re-fabricating a number.
 *
 * Each test names the mutation it kills.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount } from 'svelte';
import type { ComponentProps } from 'svelte';
import EssentialsPanel from '../EssentialsPanel.svelte';

const noop = () => {};

function baseProps(afLevel: number): ComponentProps<typeof EssentialsPanel> {
  return {
    vfoOps: { splitActive: false },
    mode: { currentMode: '---', modes: [] },
    filter: { currentFilter: 1, filterLabels: [] },
    rxAudio: { monitorMode: 'local', afLevel },
    dsp: { nbActive: false, nrMode: 0, notchMode: 'off' },
    quickModes: [],
    onSplitToggle: noop,
    onSwap: noop,
    onEqual: noop,
    onModeChange: noop,
    onModeMore: noop,
    onFilterChange: noop,
    onFilterMore: noop,
    onMonitorModeChange: noop,
    onAfLevelChange: noop,
    onNbToggle: noop,
    onNrModeChange: noop,
    onNotchModeChange: noop,
  };
}

let host: HTMLElement | null = null;
let instance: Record<string, unknown> | null = null;

function render(afLevel: number): HTMLElement {
  host = document.createElement('div');
  document.body.appendChild(host);
  instance = mount(EssentialsPanel, { target: host, props: baseProps(afLevel) });
  return host;
}

afterEach(() => {
  if (instance) unmount(instance);
  instance = null;
  if (host) host.remove();
  host = null;
});

describe('EssentialsPanel AF-level display honesty (MOR-1409 A13a)', () => {
  // Kills: dropping the `Number.isFinite` guard from `formatAfLevelDisplay`
  // (mutation battery #4-equivalent for this consumer).
  it('never renders a "NaN" substring for an unobserved AF level', () => {
    const text = render(Number.NaN).textContent ?? '';
    expect(text).not.toContain('NaN');
  });

  // Kills: substituting a fabricated finite fallback (e.g. `?? 0` / `?? 0.5`)
  // instead of the established '---' placeholder convention.
  it('renders the established placeholder for an unobserved AF level', () => {
    const text = render(Number.NaN).textContent ?? '';
    expect(text).toContain('---');
    expect(text).not.toContain('0%');
    expect(text).not.toContain('50%');
  });

  // Kills: a guard that swallows real readings too (over-broad placeholder).
  it('still renders a real observed AF level as a percentage', () => {
    const text = render(0.42).textContent ?? '';
    expect(text).toContain('42%');
    expect(text).not.toContain('---');
  });

  // Kills: guarding only the extremes — 0 is a legitimate observed reading and
  // must not be confused with "never observed".
  it('renders an observed zero AF level as 0%, not as unknown', () => {
    const text = render(0).textContent ?? '';
    expect(text).toContain('0%');
    expect(text).not.toContain('---');
  });
});
