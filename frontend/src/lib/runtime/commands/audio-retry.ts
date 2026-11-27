import { makeCommandId } from '$lib/types/protocol';
import { onControlSessionTransition, onMessage, sendCommand } from '$lib/transport/ws-client';

export type AudioRetryResult =
  | { accepted: true }
  | { accepted: false; reason: 'unsupported' | 'not_applicable' };

export function retryAudio(signal: AbortSignal): Promise<AudioRetryResult> {
  const id = makeCommandId();
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timer);
      removeMessage();
      removeTransition();
      signal.removeEventListener('abort', abort);
    };
    const fail = () => {
      cleanup();
      reject(new Error('Audio retry failed'));
    };
    const abort = () => fail();
    const timer = setTimeout(fail, 10_000);
    const removeMessage = onMessage((message) => {
      if ((message.type !== 'response' && message.type !== 'error') || message.id !== id) return;
      if (message.type === 'error' || !message.ok) { fail(); return; }
      const result = message.result;
      if (result?.accepted === true) {
        cleanup();
        resolve({ accepted: true });
      } else if (result?.accepted === false
        && (result.reason === 'unsupported' || result.reason === 'not_applicable')) {
        cleanup();
        resolve({ accepted: false, reason: result.reason });
      } else fail();
    });
    const removeTransition = onControlSessionTransition((transition) => {
      if (transition.state !== 'connected') fail();
    });
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) { abort(); return; }
    try {
      if (!sendCommand('audio_retry', {}, id)) fail();
    } catch {
      fail();
    }
  });
}
