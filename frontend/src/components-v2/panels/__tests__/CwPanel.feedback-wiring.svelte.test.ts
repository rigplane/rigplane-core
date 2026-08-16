import { readFileSync } from 'node:fs';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { setLocale } from '$lib/i18n';
import type { ControlFeedback } from '$lib/runtime/adapters/panel-adapters';
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
let CwPanel: typeof import('../CwPanel.svelte').default;
let component: ReturnType<typeof mount> | null = null, target: HTMLDivElement;
beforeAll(async () => {
  vi.doMock('$lib/runtime/adapters/panel-adapters', () => ({
    deriveCwProps: () => props, getCwHandlers: () => handlers,
    getBreakInDelayControlFeedback: () => feedback,
  }));
  CwPanel = (await import('../CwPanel.svelte')).default;
});
afterAll(() => vi.doUnmock('$lib/runtime/adapters/panel-adapters'));
afterEach(() => {
  if (component) unmount(component);
  component = null; target?.remove(); setLocale('en-US');
  Object.assign(feedback, { confirmed: 64, target: null, requestedTarget: null, phase: 'idle',
    busy: false, availability: 'available', outcome: null, lifecycleId: null, transitionId: null });
});
function render() {
  target = document.createElement('div'); document.body.appendChild(target);
  component = mount(CwPanel, { target }); flushSync();
  const input = () => target.querySelector<HTMLInputElement>('[data-testid="cw-break-in-delay"]')!;
  const live = () => target.querySelector<HTMLElement>('[data-testid="cw-break-in-delay-live"]');
  return { input, live };
}
describe('fallback CwPanel ControlFeedback wiring (MOR-1754)', () => {
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
});
