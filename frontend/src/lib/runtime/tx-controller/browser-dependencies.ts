import { getCapabilities } from '$lib/stores/capabilities.svelte';
import { getRadioState } from '$lib/stores/radio.svelte';
import { getTxAudioControl } from '$lib/runtime/adapters/tx-adapter';
import { makeCommandId } from '$lib/types/protocol';
import { onCommandDelivery, onControlSessionTransition, sendCommand, type CommandDeliveryEvent, type ControlSessionTransition } from '$lib/transport/ws-client';
import { createAppAuthorityProjector, type AppAuthorityProjection } from './app-authority';
import type { TxControllerDependencies } from './controller';
import type { PttMarker, PttObservation } from './model';
type Unsubscribe = () => void;
type LifecycleSource = (release: () => void) => Unsubscribe;
const noop = () => {};
const commandName = { on: 'ptt_on', off: 'ptt_off' } as const;
const outcome = { 'transport-sent': 'sent', ack: 'ack', 'response-ok': 'response-ok',
  'response-error': 'response-error', error: 'transport-error' } as const;
export function createBrowserTxControllerDependencies() {
  const project = createAppAuthorityProjector();
  const audio = getTxAudioControl();
  const cleanups = new Set<Unsubscribe>();
  const timers = new Set<ReturnType<typeof globalThis.setTimeout>>();
  let disposed = false;
  const track = (unsubscribe: Unsubscribe): Unsubscribe => {
    if (disposed) { unsubscribe(); return noop; }
    let active = true;
    const remove = () => {
      if (!active) return;
      active = false; cleanups.delete(remove); unsubscribe();
    };
    cleanups.add(remove); return remove;
  };
  const projectAuthority = (session: ControlSessionTransition): AppAuthorityProjection => project(getRadioState(), getCapabilities(), session);
  const dependencies: TxControllerDependencies = {
    startAudio: () => disposed ? Promise.resolve('TX browser dependencies disposed') : audio.startTx(),
    stopLocalAudio: () => { if (!disposed) audio.stopLocalAudio(); },
    restoreMod: (barrier: PttMarker, observation: PttObservation) => {
      if (!disposed) audio.restoreModAfterConfirmedOff({ barrier, observation: {
        ptt: observation.value, pttObserved: observation.observed,
        pttFreshness: observation.fresh ? 'fresh' : 'unknown', pttSource: observation.source,
        ...observation.marker,
      } });
    },
    commandId: () => makeCommandId(),
    schedule: (callback, delayMs) => {
      if (disposed) return null;
      let handle: ReturnType<typeof globalThis.setTimeout>;
      handle = globalThis.setTimeout(() => {
        timers.delete(handle); if (!disposed) callback();
      }, delayMs);
      timers.add(handle); return handle;
    },
    cancel: (handle) => {
      if (handle == null) return;
      const timer = handle as ReturnType<typeof globalThis.setTimeout>;
      timers.delete(timer); globalThis.clearTimeout(timer);
    },
    timeoutMs: { 'audio-start': 5_000, 'on-confirmation': 5_000, 'off-confirmation': 5_000 },
    sendPtt: (command, commandId, correlation, report) => {
      if (disposed) return;
      let terminal = false; let remove = noop;
      const delivery = (event: CommandDeliveryEvent) => {
        if (event.commandId !== commandId || event.originalEpoch !== correlation.originalEpoch) return;
        const isSent = event.kind === 'transport-sent';
        terminal = event.kind === 'response-ok' || event.kind === 'response-error' || event.kind === 'error';
        if (terminal) remove();
        report({
          outcome: outcome[event.kind],
          eventEpoch: event.eventEpoch,
          barrier: isSent ? projectAuthority({ state: 'connected', epoch: event.eventEpoch }).ptt.marker : null,
        });
      };
      remove = track(onCommandDelivery(delivery));
      try {
        const accepted = sendCommand(commandName[command], {
          target: { ...correlation.target }, originalEpoch: correlation.originalEpoch,
        }, commandId);
        if (!accepted && command === 'on' && !terminal) {
          remove(); report({ outcome: 'transport-error', eventEpoch: correlation.originalEpoch, barrier: null });
        }
      } catch {
        remove();
        if (!terminal) report({ outcome: 'transport-error', eventEpoch: correlation.originalEpoch, barrier: null });
      }
    },
  };
  return {
    dependencies, projectAuthority,
    subscribeSession: (handler: (projection: AppAuthorityProjection, session: ControlSessionTransition) => void) => disposed
      ? noop : track(onControlSessionTransition((session) => { if (!disposed) handler(projectAuthority(session), session); })),
    bindLifecycleRelease: (source: LifecycleSource, release: () => void) =>
      disposed ? noop : track(source(() => { if (!disposed) release(); })),
    dispose: () => {
      if (disposed) return;
      disposed = true; for (const remove of [...cleanups]) try { remove(); } catch { /* disposal remains idempotent */ }
      for (const timer of timers) globalThis.clearTimeout(timer); timers.clear();
    },
  };
}
