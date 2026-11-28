import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import type { WsIncoming } from '$lib/types/protocol';
import type { ControlSessionTransition } from '$lib/transport/ws-client';
import type { AudioReceiveFailurePublic } from '$lib/types/state';
import type { LocaleCode } from '$lib/i18n/types';

const transport = vi.hoisted(() => ({
  messages: new Set<(message: WsIncoming) => void>(),
  transitions: new Set<(transition: ControlSessionTransition) => void>(),
  sendCommand: vi.fn<(typeof import('$lib/transport/ws-client'))['sendCommand']>(() => true),
}));
vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: transport.sendCommand,
  onMessage: (handler: (message: WsIncoming) => void) => {
    transport.messages.add(handler);
    return () => transport.messages.delete(handler);
  },
  onControlSessionTransition: (handler: (transition: ControlSessionTransition) => void) => {
    transport.transitions.add(handler);
    return () => transport.transitions.delete(handler);
  },
}));

import RxAudioRestoreStatus from '../RxAudioRestoreStatus.svelte';
import { setLocale } from '$lib/i18n';
import { retryAudio } from '$lib/runtime/commands/audio-retry';

const failure: AudioReceiveFailurePublic = {
  stage: 'rx_recovery', code: 'core_rx_restore_failed', reason: 'retries_exhausted',
};
const locales: [LocaleCode, string, string, string, string, string][] = [
  ['en-US', 'Retry', 'Audio retry is not supported.', 'Audio retry is not available in the current state.',
    'Audio retry is not available while transmitting.', 'Audio retry was refused.'],
  ['ru-RU', 'Повторить', 'Повторное восстановление звука не поддерживается.', 'Повторное восстановление звука сейчас недоступно.',
    'Повторное восстановление звука недоступно во время передачи.', 'В повторном восстановлении звука отказано.'],
  ['ja-JP', '再試行', '音声の再試行はサポートされていません。', '現在の状態では音声を再試行できません。',
    '送信中は音声を再試行できません。', '音声の再試行が拒否されました。'],
];
let component: ReturnType<typeof mount> | null = null;

function render(currentFailure: AudioReceiveFailurePublic | null = failure): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(RxAudioRestoreStatus, { target, props: { failure: currentFailure } });
  flushSync();
  return target;
}
function retryButton(target: HTMLElement): HTMLButtonElement {
  const button = target.querySelector<HTMLButtonElement>('button');
  expect(button).not.toBeNull();
  return button!;
}
function reply(result: Record<string, unknown>, id = transport.sendCommand.mock.calls[0][2]!) {
  for (const handler of transport.messages) handler({ type: 'response', id, ok: true, result });
}

beforeEach(() => {
  transport.sendCommand.mockReset().mockReturnValue(true);
  setLocale('en-US');
});
afterEach(async () => {
  if (component) await unmount(component);
  component = null;
  expect(transport.messages.size).toBe(0);
  expect(transport.transitions.size).toBe(0);
  vi.useRealTimers();
  document.body.innerHTML = '';
  setLocale('en-US');
});

describe('RX restoration retry', () => {
  it.each([null, { ...failure, code: 'another_failure' } as AudioReceiveFailurePublic])(
    'offers no retry outside the restoration failure state: %j', (currentFailure) => {
      expect(render(currentFailure).querySelector('button')).toBeNull();
      expect(transport.sendCommand).not.toHaveBeenCalled();
    },
  );

  it('sends once, disables while pending, ignores unrelated replies and retains the failure after acceptance', async () => {
    const target = render();
    const button = retryButton(target);
    button.click();
    button.click();
    flushSync();
    expect(transport.sendCommand).toHaveBeenCalledExactlyOnceWith('audio_retry', {}, expect.any(String));
    expect(button.disabled).toBe(true);
    reply({ accepted: false, reason: 'unsupported' }, 'unrelated');
    await tick();
    expect(button.disabled).toBe(true);
    reply({ accepted: true });
    await tick();
    expect(button.disabled).toBe(false);
    expect(target.textContent).toContain('RX audio could not be restored');
    expect(target.textContent).not.toContain('not supported');
  });

  describe.each(locales)('%s', (locale, label, unsupported, notApplicable, transmitting, refused) => {
    it.each([
      ['unsupported', unsupported], ['not_applicable', notApplicable],
      ['transmitting', transmitting], ['future_reason', refused], [undefined, refused],
    ])('shows the %s refusal plainly and permits another attempt', async (reason, message) => {
      setLocale(locale);
      const target = render();
      const button = retryButton(target);
      expect(button.textContent?.trim()).toBe(label);
      button.click();
      reply({ accepted: false, reason });
      await tick();
      expect(target.textContent).toContain(message);
      expect(target.querySelector('[data-testid="rx-audio-restore-message"]')).not.toBeNull();
      expect(target.textContent).not.toContain('future_reason');
      expect(button.disabled).toBe(false);
      button.click();
      flushSync();
      expect(transport.sendCommand).toHaveBeenCalledTimes(2);
      expect(target.textContent).not.toContain(message);
      reply({ accepted: true }, transport.sendCommand.mock.calls[1][2]!);
      await tick();
    });
  });

  it.each(['not-sent', 'error', 'invalid-result', 'disconnected', 'timeout'])('re-enables after %s and shows a failure', async (outcome) => {
    vi.useFakeTimers();
    if (outcome === 'not-sent') transport.sendCommand.mockReturnValue(false);
    const target = render();
    const button = retryButton(target);
    button.click();
    const id = transport.sendCommand.mock.calls[0][2]!;
    if (outcome === 'error') {
      for (const handler of transport.messages) handler({ type: 'response', id, ok: false, error: 'no_radio' });
    } else if (outcome === 'invalid-result') reply({});
    else if (outcome === 'disconnected') {
      for (const handler of transport.transitions) handler({ state: 'disconnected', epoch: 2 });
    } else if (outcome === 'timeout') await vi.runAllTimersAsync();
    await tick();
    expect(button.disabled).toBe(false);
    expect(target.textContent).toContain('Audio retry failed. Please try again.');
  });

  it('removes pending listeners when unmounted', async () => {
    const target = render();
    retryButton(target).click();
    expect(transport.messages.size).toBe(1);
    await unmount(component!);
    component = null;
    await tick();
    expect(transport.messages.size).toBe(0);
  });

  it('renders the retry action in the generated pseudo-locale', () => {
    setLocale('qps-ploc');
    const button = retryButton(render());
    expect(button.textContent).not.toContain('[missing:');
    expect(button.textContent?.trim()).not.toBe('Retry');
  });
});

describe('audio retry command refusals', () => {
  it.each(['unsupported', 'not_applicable', 'transmitting', 'future_reason', undefined])(
    'preserves a structured refusal with reason %s', async (reason) => {
      const result = retryAudio(new AbortController().signal);
      reply({ accepted: false, ...(reason === undefined ? {} : { reason }) });
      await expect(result).resolves.toEqual({ accepted: false, ...(reason === undefined ? {} : { reason }) });
      expect(transport.messages.size).toBe(0);
      expect(transport.transitions.size).toBe(0);
    },
  );

  it.each([null, 1, {}, true])('rejects a malformed reason %j', async (reason) => {
    const result = retryAudio(new AbortController().signal);
    reply({ accepted: false, reason });
    await expect(result).rejects.toThrow('Audio retry failed');
  });
});
