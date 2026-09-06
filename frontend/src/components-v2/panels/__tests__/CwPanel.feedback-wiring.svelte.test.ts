import { readFileSync } from 'node:fs';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { setLocale } from '$lib/i18n';
import type { ControlFeedback } from '$lib/runtime/adapters/panel-adapters';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { CommandLifecycle } from '$lib/stores/commands.svelte';
const props = $state({
  cwPitch: 600, keySpeed: 12, breakIn: 1, breakInDelay: 64, apfMode: 0,
  twinPeak: false, currentMode: 'CW', apfDisabled: false, tpfDisabled: false,
  hasCw: true, hasBreakIn: true, hasApf: false, hasTwinPeak: false, autoTuneAvailable: false,
});
const handlers = { onCwPitchChange: vi.fn(), onKeySpeedChange: vi.fn(),
  onBreakInToggle: vi.fn(), onBreakInModeChange: vi.fn(), onBreakInDelayChange: vi.fn(),
  onApfChange: vi.fn(), onTwinPeakToggle: vi.fn(), onAutoTune: vi.fn() };
const feedback = $state({
  confirmed: 64, target: null, requestedTarget: null, phase: 'idle', busy: false,
  availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
  sessionEpoch: 1, scope: { control: 'break-in-delay', receiver: 0 },
  repeatPolicy: 'latest-target-wins',
} as ControlFeedback<number>);
const fresh = () => ({
  storePath: 'fixture', observed: true, freshness: 'fresh' as const,
  availability: 'available' as const, lastObservedMonotonic: 5,
});
const connectedState = (): ServerState => ({
  stateContractVersion: 1, providerGeneration: 3, active: 'MAIN',
  cwPitch: 600, keySpeed: 12, main: {}, sub: {},
  fieldStatus: { cwPitch: fresh(), keySpeed: fresh() },
} as unknown as ServerState);
const connectedCaps = (): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 3, capabilities: ['cw'],
} as unknown as Capabilities);
const canonical = $state({
  state: connectedState() as ServerState | null,
  caps: connectedCaps() as Capabilities | null,
  session: { state: 'connected' as 'connected' | 'disconnected', epoch: 1 },
});
const lifecycle = $state({ commands: [] as CommandLifecycle[] });
const cwCommand = (
  name: 'set_cw_pitch' | 'set_key_speed', status: CommandLifecycle['status'],
): CommandLifecycle => ({
  id: name, name, params: name === 'set_cw_pitch' ? { value: 650 } : { speed: 28 },
  originalEpoch: 1, eventEpoch: 1, providerGeneration: 3,
  createdAt: 1, updatedAt: 1, timeoutMs: 5_000, status,
  ...(status === 'failed' ? { error: 'radio rejected' } : {}),
});
let CwPanel: typeof import('../CwPanel.svelte').default;
let component: ReturnType<typeof mount> | null = null, target: HTMLDivElement;
beforeAll(async () => {
  vi.doMock('$lib/runtime/frontend-runtime', () => ({ runtime: {
    get state() { return canonical.state; },
    get caps() { return canonical.caps; },
    get controlSession() { return canonical.session; },
  } }));
  vi.doMock('$lib/stores/commands.svelte', async (importOriginal) => ({
    ...await importOriginal<typeof import('$lib/stores/commands.svelte')>(),
    getCommandLifecycles: () => lifecycle.commands,
    isCommandLifecycleSuperseded: () => false,
  }));
  vi.doMock('$lib/runtime/adapters/panel-adapters', async (importOriginal) => ({
    ...await importOriginal<typeof import('$lib/runtime/adapters/panel-adapters')>(),
    deriveCwProps: () => props, getCwHandlers: () => handlers,
    getBreakInDelayControlFeedback: () => feedback,
  }));
  CwPanel = (await import('../CwPanel.svelte')).default;
});
afterAll(() => {
  vi.doUnmock('$lib/runtime/adapters/panel-adapters');
  vi.doUnmock('$lib/runtime/frontend-runtime');
  vi.doUnmock('$lib/stores/commands.svelte');
});
afterEach(() => {
  if (component) unmount(component);
  component = null; target?.remove(); setLocale('en-US');
  Object.assign(feedback, { confirmed: 64, target: null, requestedTarget: null, phase: 'idle',
    busy: false, availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
    sessionEpoch: 1, scope: { control: 'break-in-delay', receiver: 0 } });
  canonical.state = connectedState(); canonical.caps = connectedCaps();
  canonical.session = { state: 'connected', epoch: 1 };
  lifecycle.commands = [];
});
function render() {
  target = document.createElement('div'); document.body.appendChild(target);
  component = mount(CwPanel, { target }); flushSync();
  const input = () => target.querySelector<HTMLInputElement>('[data-testid="cw-break-in-delay"]')!;
  const live = () => target.querySelector<HTMLElement>('[data-testid="cw-break-in-delay-live"]');
  return { input, live };
}
function vcValue(label: string): string {
  const headers = [...target.querySelectorAll('.vc-header')];
  const header = headers.find((entry) => entry.querySelector('.vc-label')?.textContent === label);
  return header?.querySelector('.vc-value')?.textContent ?? '';
}
describe('fallback CwPanel ControlFeedback wiring (MOR-1754)', () => {
  it('projects independent Pitch and Speed phases without replacing canonical truth', () => {
    render();
    const pitch = target.querySelector<HTMLElement>('[aria-label="CW Pitch"]')!;
    const speed = target.querySelector<HTMLElement>('[aria-label="Key Speed"]')!;
    lifecycle.commands = [cwCommand('set_cw_pitch', 'failed'), cwCommand('set_key_speed', 'acknowledged')];
    flushSync();
    expect([pitch.dataset.commandPhase, pitch.getAttribute('aria-busy')]).toEqual(['failed', 'false']);
    expect([speed.dataset.commandPhase, speed.getAttribute('aria-busy')])
      .toEqual(['awaiting-confirmation', 'true']);
    expect([vcValue('CW Pitch'), vcValue('Key Speed')]).toEqual(['600 Hz', '12 WPM']);
    expect(target.querySelectorAll('[data-control-feedback-status]')).toHaveLength(2);
  });

  it('drops pre-request CW work on canonical-store disconnect and recovers from fresh truth', () => {
    vi.useFakeTimers(); handlers.onCwPitchChange.mockClear(); handlers.onKeySpeedChange.mockClear();
    render();
    const pitch = target.querySelector<HTMLElement>('[aria-label="CW Pitch"]')!;
    const speed = target.querySelector<HTMLElement>('[aria-label="Key Speed"]')!;
    pitch.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    speed.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    canonical.state = null; canonical.caps = null;
    canonical.session = { state: 'disconnected', epoch: 1 }; flushSync();
    vi.advanceTimersByTime(50);
    expect([pitch.getAttribute('aria-disabled'), speed.getAttribute('aria-disabled')]).toEqual(['true', 'true']);
    expect([handlers.onCwPitchChange.mock.calls, handlers.onKeySpeedChange.mock.calls]).toEqual([[], []]);

    canonical.session = { state: 'connected', epoch: 2 };
    canonical.caps = { ...connectedCaps(), providerGeneration: 4 };
    canonical.state = { ...connectedState(), providerGeneration: 4 }; flushSync();
    speed.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    vi.advanceTimersByTime(50);
    expect(speed.getAttribute('aria-disabled')).toBe('false');
    expect(handlers.onKeySpeedChange).toHaveBeenCalledExactlyOnceWith(13);
    vi.useRealTimers();
  });

  it('keeps normalized input local until one native change commits it', () => {
    handlers.onBreakInDelayChange.mockClear(); const r = render();
    Object.defineProperty(r.input(), 'valueAsNumber', { configurable: true, value: 111.4 });
    r.input().dispatchEvent(new Event('input', { bubbles: true })); flushSync();
    expect([handlers.onBreakInDelayChange.mock.calls, r.input().value])
      .toEqual([[], '111']);
    r.input().dispatchEvent(new Event('change', { bubbles: true }));
    expect(handlers.onBreakInDelayChange).toHaveBeenCalledExactlyOnceWith(111);
  });
  it.each(['Escape', 'pointercancel'] as const)('%s cancels and suppresses a delayed change', (route) => {
    handlers.onBreakInDelayChange.mockClear(); const r = render();
    r.input().value = '111'; r.input().dispatchEvent(new Event('input', { bubbles: true }));
    const cancel = route === 'Escape'
      ? new KeyboardEvent('keydown', { key: route, bubbles: true })
      : new Event(route, { bubbles: true });
    r.input().dispatchEvent(cancel);
    r.input().dispatchEvent(new Event('change', { bubbles: true }));
    expect([r.input().value, handlers.onBreakInDelayChange.mock.calls]).toEqual(['64', []]);
  });
  it('projects pending and every terminal phase without replacing canonical truth', () => {
    const r = render();
    for (const [phase, busy] of [
      ['submitted', true], ['queued', true], ['dispatched', true],
      ['awaiting-confirmation', true], ['confirmed', false], ['failed', false],
      ['timed-out', false], ['cancelled', false], ['superseded', false],
    ] as const) {
      Object.assign(feedback, { phase, busy, target: busy ? 111 : null, requestedTarget: 111,
        transitionId: `transition-${phase}`, outcome: busy ? null : { phase } });
      flushSync();
      expect([r.input().dataset.commandPhase, r.input().getAttribute('aria-busy'),
        r.input().value, Boolean(r.live()?.textContent)])
        .toEqual([phase, String(busy), busy ? '111' : '64', true]);
    }
  });
  it('announces one localized message per transition and follows out-of-band truth', () => {
    const r = render();
    Object.assign(feedback, { phase: 'failed', requestedTarget: 111,
      transitionId: 'one', outcome: { phase: 'failed' } }); flushSync();
    const first = r.live()?.textContent;
    Object.assign(feedback, { confirmed: 80 }); flushSync();
    expect([r.live()?.textContent, r.input().value]).toEqual([first, '80']);
    setLocale('ru-RU'); Object.assign(feedback, { phase: 'timed-out',
      transitionId: 'two', outcome: { phase: 'timed-out' } }); flushSync();
    expect(r.live()?.textContent).toMatch(/[А-Яа-я]/);
  });
  it('fails closed for stale/unavailable truth and keeps accessibility media seams', () => {
    Object.assign(feedback, { confirmed: null, phase: 'unavailable',
      availability: 'unavailable', transitionId: 'unavailable' });
    const r = render();
    expect([r.input().disabled, r.input().getAttribute('aria-valuetext')])
      .toEqual([true, 'Control unavailable']);
    const source = readFileSync('src/components-v2/panels/CwPanel.svelte', 'utf8');
    for (const seam of ['@media (forced-colors: active)', '@media (prefers-reduced-motion: reduce)',
      'getBreakInDelayControlFeedback']) expect(source).toContain(seam);
    expect(source).not.toMatch(/stores\/(commands|radio)|sendCommand|dispatchRadioIntent/);
  });
  it.each([
    ['provider cancellation', { confirmed: 72, phase: 'cancelled', requestedTarget: 111,
      transitionId: 'provider-replaced', outcome: { phase: 'cancelled' } }, 72],
    ['out-of-band truth', { confirmed: 80, transitionId: 'out-of-band' }, 80],
  ] as const)('%s invalidates a stale draft and suppresses its release', (_case, update, canonical) => {
    handlers.onBreakInDelayChange.mockClear(); const r = render();
    r.input().value = '111'; r.input().dispatchEvent(new Event('input', { bubbles: true }));
    Object.assign(feedback, { ...update, target: null, busy: false }); flushSync();
    const restored = r.input().value; r.input().dispatchEvent(new Event('change', { bubbles: true }));
    expect({ restored, calls: handlers.onBreakInDelayChange.mock.calls })
      .toEqual({ restored: String(canonical), calls: [] });
    r.input().value = '90'; r.input().dispatchEvent(new Event('input', { bubbles: true }));
    r.input().dispatchEvent(new Event('change', { bubbles: true }));
    expect(handlers.onBreakInDelayChange).toHaveBeenCalledExactlyOnceWith(90);
  });
  it.each([
    ['non-finite', { confirmed: Number.NaN }], ['missing', { confirmed: undefined }],
    ['out-of-domain', { confirmed: 256 }], ['off-lattice', { confirmed: 1.5 }],
    ['phase mismatch', { phase: 'failed', busy: true, target: 111,
      requestedTarget: 111, outcome: { phase: 'failed' } }],
  ])('fails closed for %s feedback', (_case, malformed) => {
    handlers.onBreakInDelayChange.mockClear();
    Object.assign(feedback as unknown as Record<string, unknown>, malformed); const r = render();
    expect([r.input().disabled, r.input().getAttribute('aria-valuetext'),
      target.querySelector('[data-testid="cw-break-in-delay-value"]')?.textContent])
      .toEqual([true, 'Control unavailable', '—']);
    r.input().value = '111'; r.input().dispatchEvent(new Event('change', { bubbles: true }));
    expect(handlers.onBreakInDelayChange).not.toHaveBeenCalled();
  });
  it.each([
    ['session epoch', { sessionEpoch: 2 }],
    ['feedback scope', { scope: { control: 'break-in-delay', receiver: 1 } }],
  ] as const)('preserves equal reprojection but invalidates changed %s', (_case, changedContext) => {
    handlers.onBreakInDelayChange.mockClear(); const r = render();
    r.input().value = '111'; r.input().dispatchEvent(new Event('input', { bubbles: true }));
    Object.assign(feedback, { confirmed: 64, sessionEpoch: 1 }); flushSync();
    expect(r.input().value).toBe('111');
    Object.assign(feedback, changedContext); flushSync();
    r.input().dispatchEvent(new Event('change', { bubbles: true }));
    expect([r.input().value, handlers.onBreakInDelayChange.mock.calls]).toEqual(['64', []]);
  });
});
