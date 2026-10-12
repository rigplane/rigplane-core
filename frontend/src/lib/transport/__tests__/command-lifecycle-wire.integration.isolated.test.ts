import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '../../types/capabilities';
import { MockWebSocket, instances } from './support/fake-ws-backend';

// Drives one operator command through the real chain: radio-intents ->
// ws-client -> fake socket -> commands store -> radio store -> the readers
// the panels use. Only the WebSocket, the capabilities HTTP fetch and the
// clock (fake timers) are replaced. This file runs in the `isolated`
// project (its mock of `http-client` must not leak into a shared module
// cache).
const fetchCapabilities = vi.hoisted(() => vi.fn());
vi.mock('../http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('../http-client')>(), fetchCapabilities }));

const OLD_HZ = 14_074_000;
const TARGET_HZ = 14_100_000;
const VFO_PARAMS = {
  freq: TARGET_HZ, receiver: 0, slot: 'A', expected_active_slot: 'A', provider_generation: 0,
} as const;

function makeCapabilities(): Capabilities {
  return {
    model: 'TEST', scope: false, audio: false, tx: false, capabilities: [],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false }, txBands: null,
    stateContractVersion: 1, providerGeneration: 0,
  };
}

// Frame shapes follow ControlHandler (src/rigplane/web/handlers/control.py):
// `_dispatch_command` acknowledges an enqueued command with a `response`
// frame (ok:true, a `result` object); `_send_command_failure` answers a
// refused one with ok:false, `error` and `message`.
const accepted = (id: string) => ({ type: 'response', id, ok: true, result: {} });
const refusal = (id: string) => ({
  type: 'response', id, ok: false, error: 'radio_nak', message: 'radio refused the command',
});

/** One server snapshot: `revision` is the semantic revision; `at` is both
 *  the observationSeq and the lastObservedMonotonic of the two frequency
 *  fields. */
function snapshot(freqHz: number, revision: number, at: number) {
  const slot = { freqHz, mode: 'USB', filterNum: 1, dataMode: 0 };
  const observed = {
    storePath: 'x', observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: at,
  };
  return {
    revision, stateRevision: revision, freshnessRevision: 1, healthRevision: 1, observationSeq: at,
    updatedAt: '2026-06-03T00:00:00Z', active: 'MAIN', ptt: false, split: false, dualWatch: false,
    tunerStatus: 0, stateContractVersion: 1, providerGeneration: 0,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    main: {
      freqHz, mode: 'USB', filter: 1, dataMode: 0, sMeter: 0, att: 0, preamp: 0, nb: false, nr: false,
      afLevel: 128, rfGain: 128, squelch: 0, vfoA: slot, activeSlot: 'A',
    },
    fieldStatus: { 'main.freqHz': observed, 'main.vfoA.freqHz': observed },
  };
}
type Snapshot = ReturnType<typeof snapshot>;

function envelope(state: Snapshot, kind: 'full' | 'delta') {
  return {
    type: kind,
    ...(kind === 'full' ? { data: state } : { changed: { main: state.main, fieldStatus: state.fieldStatus }, removed: [] }),
    revision: state.revision, stateRevision: state.stateRevision, freshnessRevision: 1,
    healthRevision: 1, observationSeq: state.observationSeq, stateContractVersion: 1, providerGeneration: 0,
  };
}

const cleanups: Array<() => void> = [];

async function boot() {
  const ws = await import('../ws-client');
  const radio = await import('../../stores/radio.svelte');
  const capabilities = await import('../../stores/capabilities.svelte');
  const store = await import('../../stores/commands.svelte');
  const intents = await import('../../runtime/commands/radio-intents');
  const panel = await import('../../runtime/adapters/panel-adapters');
  const projection = await import('../../runtime/adapters/control-feedback-projection');
  const { flushSync } = await import('svelte');
  const { observeReactive } = await import('./support/observe-reactive.svelte');
  cleanups.push(() => ws.disconnect());
  capabilities.setCapabilities(makeCapabilities());
  ws.connect('ws://test/api/v1/ws');
  const socket = instances[0];
  socket.simulateOpen();
  const send = (frame: object) => socket.simulateMessage(JSON.stringify(frame));
  send({ type: 'state_update', data: envelope(snapshot(OLD_HZ, 1, 10), 'full') });
  expect(radio.getRadioState()?.main.freqHz, 'the seed state was accepted').toBe(OLD_HZ);
  return {
    socket,
    reply: send,
    pushReadback: (state: Snapshot) => send({ type: 'state_update', data: envelope(state, 'delta') }),
    dispatchVfo: () => intents.dispatchRadioIntent({ name: 'set_vfo_freq', params: VFO_PARAMS }),
    dispatchFreq: () => intents.dispatchRadioIntent({ name: 'set_freq', params: { freq: TARGET_HZ, receiver: 0 } }),
    record: (sent: { id: string; originalEpoch: number }) => store.getCommandLifecycle(sent.id, sent.originalEpoch)!,
    // The projection call getBreakInDelayControlFeedback makes in
    // panel-adapters.ts, with this command's descriptor and scope.
    vfoFeedback: () => projection.projectControlFeedback(
      store.DIRECT_VFO_FREQUENCY_COMMAND_DESCRIPTOR, radio.getRadioState(), store.getCommandLifecycles(),
      { control: 'vfo-frequency', receiver: 0, slot: 'A' }, intents.currentControlSessionEpoch(),
      store.isCommandLifecycleSuperseded,
    ),
    pendingHz: () => panel.getPendingFrequencyHz(0),
    watch: <T>(read: () => T): T[] => {
      const watched = observeReactive(read);
      cleanups.push(watched.stop);
      flushSync();
      return watched.values;
    },
    settle: flushSync,
  };
}

describe('command lifecycle through the wire', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    instances.length = 0;
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error install the mock as the global WebSocket constructor
    globalThis.WebSocket = MockWebSocket;
    vi.resetModules();
    vi.useFakeTimers();
    fetchCapabilities.mockResolvedValue(makeCapabilities());
  });

  afterEach(() => {
    for (const cleanup of cleanups.splice(0)) cleanup();
    vi.clearAllTimers();
    vi.useRealTimers();
    globalThis.WebSocket = originalWebSocket;
    vi.resetModules();
  });

  describe('set_vfo_freq, a state-backed control', () => {
    it('ends confirmed when the readback after the ack carries the commanded frequency', async () => {
      const h = await boot();
      const sent = h.dispatchVfo();
      expect(h.socket.sent.map((frame) => JSON.parse(frame))).toEqual([
        { type: 'cmd', name: 'set_vfo_freq', id: sent.id, params: VFO_PARAMS },
      ]);
      expect(h.vfoFeedback()).toMatchObject({ phase: 'dispatched', busy: true, target: TARGET_HZ });
      h.reply(accepted(sent.id));
      expect(h.record(sent).status).toBe('acknowledged');
      expect(h.vfoFeedback()).toMatchObject({ phase: 'awaiting-confirmation', busy: true });
      h.pushReadback(snapshot(TARGET_HZ, 2, 11));
      expect(h.record(sent).status).toBe('confirmed');
      expect(h.vfoFeedback()).toMatchObject({
        phase: 'confirmed', busy: false, confirmed: TARGET_HZ, outcome: { phase: 'confirmed' },
      });
    });

    it('ends failed when the server refuses the command, and a later matching readback does not undo it', async () => {
      const h = await boot();
      const sent = h.dispatchVfo();
      h.reply(refusal(sent.id));
      expect(h.record(sent)).toMatchObject({ status: 'failed', error: 'radio refused the command' });
      expect(h.vfoFeedback()).toMatchObject({
        phase: 'failed', busy: false, target: null,
        outcome: { phase: 'failed', error: 'radio refused the command' },
      });
      h.pushReadback(snapshot(TARGET_HZ, 2, 11));
      expect(h.record(sent).status).toBe('failed');
    });

    it('keeps awaiting when the readback after the ack carries another frequency, then times out', async () => {
      const h = await boot();
      const sent = h.dispatchVfo();
      h.reply(accepted(sent.id));
      h.pushReadback(snapshot(OLD_HZ, 1, 11));
      expect(h.vfoFeedback()).toMatchObject({ phase: 'awaiting-confirmation', busy: true, confirmed: OLD_HZ });
      vi.advanceTimersByTime(sent.timeoutMs - 1);
      expect(h.vfoFeedback().phase).toBe('awaiting-confirmation');
      vi.advanceTimersByTime(1);
      expect(h.record(sent).status).toBe('timed-out');
      expect(h.vfoFeedback()).toMatchObject({ phase: 'timed-out', busy: false, outcome: { phase: 'timed-out' } });
    });

    it('ends timed-out when no readback arrives before the store deadline', async () => {
      const h = await boot();
      const sent = h.dispatchVfo();
      h.reply(accepted(sent.id));
      vi.advanceTimersByTime(sent.timeoutMs - 1);
      expect(h.vfoFeedback().phase).toBe('awaiting-confirmation');
      vi.advanceTimersByTime(1);
      expect(h.record(sent).status).toBe('timed-out');
      expect(h.vfoFeedback()).toMatchObject({ phase: 'timed-out', busy: false, outcome: { phase: 'timed-out' } });
    });
  });

  describe('set_freq, read through the pending marker', () => {
    // The marker is read through an effect, which Svelte re-runs only when a
    // reactive value it read changes. A direct call evaluates the clock
    // afresh and cannot show whether anything makes a reader ask again.
    async function ackedFreq() {
      const h = await boot();
      const seen = h.watch(() => h.pendingHz());
      const sent = h.dispatchFreq();
      h.reply(accepted(sent.id));
      h.settle();
      expect(seen.at(-1)).toBe(TARGET_HZ);
      return { h, seen, sent };
    }

    it('stops showing the target at the first observation of the field after the ack, whatever value it carries', async () => {
      const { h, seen } = await ackedFreq();
      h.pushReadback(snapshot(OLD_HZ, 1, 11));
      h.settle();
      expect(seen.at(-1)).toBeNull();
    });

    it('stops showing the target when the control link closes after the ack, with no further state push', async () => {
      const { h, seen, sent } = await ackedFreq();
      h.socket.simulateClose();
      h.settle();
      expect(seen.at(-1)).toBeNull();
      expect(h.record(sent)).toMatchObject({ status: 'cancelled', error: 'session-disconnected' });
    });

    it('stops showing the target by the store deadline when the link stalls silently after the ack', async () => {
      const { h, seen, sent } = await ackedFreq();
      vi.advanceTimersByTime(sent.timeoutMs - 1);
      h.settle();
      expect(h.record(sent).status).toBe('acknowledged');
      expect(h.pendingHz()).toBeNull();
      vi.advanceTimersByTime(1);
      h.settle();
      expect(h.record(sent).status).toBe('timed-out');
      expect(seen.at(-1)).toBeNull();
    });

    // Expected failure. The marker is meant to retire an acknowledged
    // command a fixed time after its ack (`ACK_CONFIRM_GRACE_MS`,
    // pending-intent-table.ts), but that is judged from the clock only when
    // the accessor is called. After a silent stall nothing calls it again
    // until the store's longer deadline, so a rendered marker outlives the
    // point where a fresh call answers null (asserted in the previous test).
    it.fails('stops showing the target once the accessor itself answers null, without waiting for the store deadline', async () => {
      const { h, seen, sent } = await ackedFreq();
      vi.advanceTimersByTime(sent.timeoutMs - 1);
      h.settle();
      expect(seen.at(-1)).toBeNull();
    });
  });
});
