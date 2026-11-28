import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { createManagedBrowserDependencies } from '../browser-dependencies';
import { ManagedTxController } from '../managed-controller';
import { invalidateManagedTransmit, receiveManagedTransmitSnapshot } from '$lib/stores/managed-transmit.svelte';
import { setLocale, messageFromReasonCode } from '$lib/i18n';
import RefusalSurface from './support/RefusalSurface.svelte';

const h = vi.hoisted(() => ({ notify: vi.fn(), start: vi.fn(async () => null) }));
vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: () => ({ state: 'connected', epoch: 4 }),
  emitLocalNotification: h.notify, onMessage: () => () => {},
  onCommandDelivery: () => () => {}, onControlSessionTransition: () => () => {},
  sendCommand: vi.fn(),
}));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({ getTxAudioControl: () => ({
  startManagedTx: h.start, stopLocalAudio: () => {}, onTxAudioDied: () => () => {},
}) }));

afterEach(() => { vi.restoreAllMocks(); });
describe('PTT refusal presentation (MOR-4934)', () => {
  it.each([false, true])('reports an enabled-click race and visibly explains a stale disabled button (standard=%s)', async (standard) => {
    setLocale('en-US');
    invalidateManagedTransmit();
    receiveManagedTransmitSnapshot({
      schemaVersion: 1, sampledAt: '2026-11-28T00:00:00Z',
      managedTransmit: {
        status: 'available', intent: { kind: 'rx' }, releaseRequired: false, lastError: null,
        lastActuation: null, abortErrors: [],
        tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null },
      }, txObservation: { observedPtt: 'off' },
    });
    const browser = createManagedBrowserDependencies();
    const refresh = vi.spyOn(browser.dependencies, 'refresh').mockImplementation(() => new Promise<void>(() => {}));
    const submit = vi.spyOn(browser.dependencies, 'submit');
    vi.spyOn(console, 'info').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    h.notify.mockClear(); h.start.mockClear();
    const controller = new ManagedTxController(browser.dependencies);
    const target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(RefusalSurface, { target, props: { controller, standard } });
    flushSync();
    const key = target.querySelector('[data-testid="rx-tx-key"]') as HTMLButtonElement;
    const unkey = target.querySelector('[data-testid="rx-tx-unkey"]') as HTMLButtonElement;
    try {
      expect(key.disabled).toBe(false);
      invalidateManagedTransmit();
      expect(key.disabled).toBe(false);
      key.click();
      expect(h.notify).toHaveBeenCalledExactlyOnceWith(
        'error', messageFromReasonCode('txAuthorityUnavailable'), 'txAuthorityUnavailable',
      );
      expect(refresh).toHaveBeenCalledTimes(1);
      expect(submit).not.toHaveBeenCalled();
      expect(h.start).not.toHaveBeenCalled();
      flushSync();
      expect(key.disabled).toBe(true);
      expect(unkey.disabled).toBe(false);
      const reason = target.querySelector('[data-reason="authority_unavailable"]') as HTMLElement;
      expect(reason).not.toBeNull();
      expect(reason.textContent).toBe(messageFromReasonCode('txAuthorityUnavailable'));
      expect(reason.closest('.sr-only, [hidden]')).toBeNull();
      expect(getComputedStyle(reason).display).not.toBe('none');
      const description = document.getElementById(key.getAttribute('aria-describedby')!);
      expect(description?.textContent).toContain(messageFromReasonCode('txAuthorityUnavailable'));
    } finally {
      await unmount(component); controller.dispose(); browser.dispose(); target.remove();
    }
  });
});
