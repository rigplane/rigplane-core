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
 * `RxAudioPanel.svelte`'s guard already covers for this exact field.
 *
 * This panel is A13a's one granted display-honesty guard owner
 * (correction 5246842617 §3, in the 5246487510 shape): its AF readout is
 * rendered unconditionally, so no guard placed inside `MobileRadioLayout`
 * can reach it without re-fabricating a number.
 *
 * MOR-2668: the guard renders an unobserved level as '' (unlit LCD
 * segment) in HBarRenderer's reserved `.vc-value` box (MOR-2657) — never
 * a dash, never "NaN".
 *
 * Each test names the mutation it kills.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import { readFileSync } from 'node:fs';
import EssentialsPanel from '../EssentialsPanel.svelte';

// MOR-2910: the AF level control reads the shared AF command-feedback lane
// (`getAfLevelControlFeedback`), so this file mocks the accessor with a
// hoisted mutable snapshot — the display tests drive the confirmed reading
// through it, and the wiring tests advance the feedback lifecycle.
const afFeedback = vi.hoisted(() => ({ value: null as Record<string, unknown> | null }));
const afFeedbackOf = (confirmed: number, over: Record<string, unknown> = {}) => ({
  confirmed, target: null, requestedTarget: null, phase: 'idle' as const,
  busy: false, availability: 'available' as const, outcome: null,
  lifecycleId: null, transitionId: null, providerGeneration: 1, sessionEpoch: 1,
  scope: { control: 'af-level', receiver: 0 as const },
  repeatPolicy: 'latest-target-wins' as const, ...over,
});
const afHandler = vi.hoisted(() => ({ onAfLevelChange: (_value: number) => {} }));
vi.mock('$lib/runtime/frontend-runtime', () => ({ runtime: {
  get state() { return null; },
  get caps() { return null; },
  get controlSession() { return { state: 'disconnected' as const, epoch: -1 }; },
} }));
vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  getAfLevelControlFeedback: () => afFeedback.value,
}));

const noop = () => {};

function baseProps(afLevel: number): ComponentProps<typeof EssentialsPanel> {
  return {
    vfoOps: { splitActive: false },
    mode: { currentMode: 'USB', modes: [] },
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
  // The panel reads the confirmed reading through the mocked
  // `getAfLevelControlFeedback`, so the display cases drive it there (the
  // `rxAudio.afLevel` prop still travels for the pre-MOR-2910 contract).
  afFeedback.value = afFeedbackOf(afLevel);
  host = document.createElement('div');
  document.body.appendChild(host);
  instance = mount(EssentialsPanel, { target: host, props: baseProps(afLevel) });
  return host;
}

// Same shape as `RxAudioPanel.isolated.test.ts`'s readout lookup: find the
// ValueControl header by label, then read its value box.
function vcValueEl(t: HTMLElement, label: string): HTMLElement {
  const headers = Array.from(t.querySelectorAll('.vc-header'));
  const header = headers.find(
    (h) => h.querySelector('.vc-label')?.textContent === label,
  );
  if (!header) throw new Error(`ValueControl labeled "${label}" not found`);
  const el = header.querySelector('.vc-value');
  if (!el) throw new Error(`Value box for "${label}" not found`);
  return el as HTMLElement;
}

function vcValueFor(t: HTMLElement, label: string): string {
  return vcValueEl(t, label).textContent ?? '';
}

afterEach(() => {
  if (instance) unmount(instance);
  instance = null;
  if (host) host.remove();
  host = null;
});

describe('EssentialsPanel AF-level display honesty (MOR-1409 A13a, MOR-2668)', () => {
  // Kills: dropping the `Number.isFinite` guard from `formatAfLevelDisplay`
  // (mutation battery #4-equivalent for this consumer).
  it('never renders a "NaN" substring for an unobserved AF level', () => {
    const text = render(Number.NaN).textContent ?? '';
    expect(text).not.toContain('NaN');
  });

  // Kills: substituting a fabricated finite fallback (e.g. `?? 0` / `?? 0.5`)
  // or a dash-family placeholder instead of the empty unread readout.
  it('renders an empty unread AF value in its reserved box, never a dash', () => {
    const t = render(Number.NaN);
    expect(vcValueEl(t, 'AF Level')).not.toBeNull();
    expect(vcValueFor(t, 'AF Level')).toBe('');
  });

  // Kills: a placeholder leaking into the slider's accessible name.
  it('exposes no placeholder in the AF slider accessible name when unread', () => {
    const t = render(Number.NaN);
    const slider = t.querySelector('[role="slider"]');
    expect(slider?.getAttribute('aria-label')).toBe('AF Level');
    expect(slider?.getAttribute('aria-valuetext')).toBeNull();
  });

  // Kills: a guard that swallows real readings too (over-broad placeholder).
  it('still renders a real observed AF level as a percentage', () => {
    expect(vcValueFor(render(0.42), 'AF Level')).toBe('42%');
  });

  // Kills: guarding only the extremes — 0 is a legitimate observed reading and
  // must not be confused with "never observed".
  it('renders an observed zero AF level as 0%, not as unknown', () => {
    expect(vcValueFor(render(0), 'AF Level')).toBe('0%');
  });

  // Kills: a reservation narrower than the widest reading this displayFn
  // can produce ('100%' — normalized percent clamps to 0..100).
  it('renders the widest AF level reading exactly', () => {
    expect(vcValueFor(render(1), 'AF Level')).toBe('100%');
  });

  it('reserves the AF value box for the widest reading (HBarRenderer .vc-value)', () => {
    const widest = '100%'.length;
    const source = readFileSync(
      'src/components-v2/controls/value-control/HBarRenderer.svelte', 'utf8',
    ).replace(/\/\*[\s\S]*?\*\//g, '');
    const rule = source.match(/\.vc-value \{([^}]*)\}/);
    expect(rule).not.toBeNull();
    const minWidth = Number(rule![1].match(/min-width: (\d+)ch/)?.[1]);
    expect(Number.isFinite(minWidth)).toBe(true);
    expect(minWidth).toBeGreaterThanOrEqual(widest);
    expect(rule![1]).toContain('tabular-nums');
    expect(source).toMatch(/\.vc-value:empty::before \{ content: '\\200b'; \}/);
  });
});

// MOR-2910 — the AF level control consumes the shared command-feedback scalar
// (`getAfLevelControlFeedback`): requested/confirmed/error through the
// binding, aria-busy while a request is pending, dispatch through the shared
// policy. The feedback accessor is the hoisted mock above; the request still
// leaves through the panel's existing onAfLevelChange prop handler.
describe('EssentialsPanel AF level command-feedback wiring (MOR-2910)', () => {
  // The mocked accessor is a plain (non-reactive) snapshot, so each case
  // stages its full feedback shape BEFORE mounting — a post-mount
  // reassignment would never re-render. The panel under test reads the
  // snapshot through its binding on mount.
  function renderWiring(over: Record<string, unknown> = {}): HTMLElement {
    afFeedback.value = afFeedbackOf(0.5, over);
    host = document.createElement('div');
    document.body.appendChild(host);
    instance = mount(EssentialsPanel, {
      target: host,
      props: {
        ...baseProps(0.5),
        onAfLevelChange: afHandler.onAfLevelChange,
      },
    });
    flushSync();
    return host;
  }

  function afSlider(): HTMLElement {
    const slider = host?.querySelector<HTMLElement>('[aria-label="AF Level"]');
    if (!slider) throw new Error('AF Level slider not found');
    return slider;
  }

  it('projects the requested target with busy state over confirmed truth', () => {
    const t = renderWiring({
      phase: 'awaiting-confirmation', busy: true, target: 0.75, requestedTarget: 0.75,
    });

    const slider = afSlider();
    expect(slider.dataset.commandPhase).toBe('awaiting-confirmation');
    expect(slider.getAttribute('aria-busy')).toBe('true');
    expect(slider.getAttribute('aria-valuenow')).toBe('0.5');
    expect(vcValueFor(t, 'AF Level')).toBe('50%');
    const descriptionId = slider.getAttribute('aria-describedby')!;
    expect(t.querySelector(`#${descriptionId}`)?.textContent).toContain('75%');
  });

  it('exposes a terminal error without replacing the confirmed reading', () => {
    // `transitionId` carries the terminal transition the status span
    // announces; without it the renderer has nothing to say.
    const t = renderWiring({
      phase: 'failed', transitionId: 'mor-2910-af-failed',
      outcome: { phase: 'failed', error: 'radio refused' },
    });

    const slider = afSlider();
    expect(slider.dataset.commandPhase).toBe('failed');
    expect(slider.getAttribute('aria-busy')).toBe('false');
    expect(slider.getAttribute('aria-valuenow')).toBe('0.5');
    expect(t.querySelector('[data-control-feedback-status]')?.textContent)
      .toContain('radio refused');
  });

  it('dispatches through the shared policy into the prop handler', () => {
    vi.useFakeTimers();
    const calls: number[] = [];
    afHandler.onAfLevelChange = (value: number) => calls.push(value);
    renderWiring();
    afSlider().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    vi.advanceTimersByTime(50);
    expect(calls).toEqual([0.51]);
    vi.useRealTimers();
  });
});
