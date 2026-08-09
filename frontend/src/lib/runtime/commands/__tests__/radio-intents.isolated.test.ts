import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CommandDeliveryEvent, ControlSessionTransition } from '$lib/transport/ws-client';
import type { RadioIntent } from '../radio-intents';

const harness = vi.hoisted(() => ({
  delivery: undefined as ((event: CommandDeliveryEvent) => void) | undefined,
  transition: undefined as ((event: ControlSessionTransition) => void) | undefined,
  session: { state: 'connected' as const, epoch: 7 },
  sendCommand: vi.fn(() => true),
}));

vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: vi.fn(() => harness.session),
  onCommandDelivery: vi.fn((handler: (event: CommandDeliveryEvent) => void) => {
    harness.delivery = handler;
    return () => {
      if (harness.delivery === handler) harness.delivery = undefined;
    };
  }),
  onControlSessionTransition: vi.fn((handler: (event: ControlSessionTransition) => void) => {
    harness.transition = handler;
    return () => {
      if (harness.transition === handler) harness.transition = undefined;
    };
  }),
  sendCommand: harness.sendCommand,
}));

describe('typed non-PTT radio intents', () => {
  let intents: typeof import('../radio-intents');
  let lifecycle: typeof import('$lib/stores/commands.svelte');

  beforeEach(async () => {
    vi.useFakeTimers();
    vi.resetModules();
    harness.delivery = undefined;
    harness.transition = undefined;
    harness.session = { state: 'connected', epoch: 7 };
    harness.sendCommand.mockReset().mockReturnValue(true);
    intents = await import('../radio-intents');
    lifecycle = await import('$lib/stores/commands.svelte');
  });

  afterEach(() => {
    lifecycle.resetCommandLifecycle();
    vi.useRealTimers();
  });

  it('sends one exact envelope and explicitly bypasses legacy optimistic mutation', () => {
    const record = intents.dispatchRadioIntent({
      id: 'freq-1',
      name: 'set_freq',
      params: { freq: 14_074_000, receiver: 0 },
    });

    expect(harness.sendCommand).toHaveBeenCalledTimes(1);
    expect(harness.sendCommand).toHaveBeenCalledWith(
      'set_freq',
      { freq: 14_074_000, receiver: 0 },
      'freq-1',
      { optimistic: false },
    );
    expect(record).toMatchObject({ id: 'freq-1', originalEpoch: 7, status: 'pending' });
    expect(lifecycle.getCommandLifecycle('freq-1', 7)?.status).toBe('pending');
  });

  it('correlates delivery without turning acknowledgement into radio truth', () => {
    intents.dispatchRadioIntent({ id: 'mode-1', name: 'set_mode', params: { mode: 'CW', receiver: 1 } });
    harness.delivery?.({
      commandId: 'mode-1',
      kind: 'transport-sent',
      originalEpoch: 7,
      eventEpoch: 7,
    });
    harness.delivery?.({ commandId: 'mode-1', kind: 'ack', originalEpoch: 7, eventEpoch: 7 });

    expect(lifecycle.getCommandLifecycle('mode-1', 7)).toMatchObject({
      status: 'acknowledged',
      eventEpoch: 7,
    });
    expect(lifecycle.getCommandLifecycle('mode-1', 7)).not.toHaveProperty('confirmedValue');
  });

  it('isolates error, timeout, cancellation, and stale-session results', () => {
    intents.dispatchRadioIntent({ id: 'error', name: 'set_vfo', params: { vfo: 'B' } });
    harness.delivery?.({
      commandId: 'error',
      kind: 'response-error',
      originalEpoch: 7,
      eventEpoch: 7,
      error: 'denied',
    });
    expect(lifecycle.getCommandLifecycle('error', 7)?.status).toBe('failed');

    intents.dispatchRadioIntent({ id: 'timeout', name: 'set_freq', params: { freq: 1 } });
    vi.advanceTimersByTime(5_000);
    expect(lifecycle.getCommandLifecycle('timeout', 7)?.status).toBe('timed-out');

    intents.dispatchRadioIntent({ id: 'provider', name: 'set_filter', params: { filter: 1 } });
    harness.delivery?.({
      commandId: 'provider', kind: 'error', originalEpoch: 7, eventEpoch: 7,
      error: 'provider session replaced', cancelled: true,
    });
    expect(lifecycle.getCommandLifecycle('provider', 7)?.status).toBe('cancelled');

    intents.dispatchRadioIntent({ id: 'cancel', name: 'set_filter', params: { filter: 2 } });
    harness.transition?.({ state: 'disconnected', epoch: 7 });
    expect(lifecycle.getCommandLifecycle('cancel', 7)?.status).toBe('cancelled');
    harness.delivery?.({ commandId: 'cancel', kind: 'response-ok', originalEpoch: 7, eventEpoch: 8 });
    expect(lifecycle.getCommandLifecycle('cancel', 7)?.status).toBe('cancelled');
  });

  it('makes every PTT spelling unavailable in types and rejects forged runtime input', () => {
    if (false) {
      // @ts-expect-error PTT is exclusively owned by the TX controller.
      intents.dispatchRadioIntent({ name: 'ptt_on', params: {} });
      // @ts-expect-error PTT is exclusively owned by the TX controller.
      intents.dispatchRadioIntent({ name: 'ptt_off', params: {} });
      // @ts-expect-error PTT is exclusively owned by the TX controller.
      intents.dispatchRadioIntent({ name: 'ptt', params: { state: false } });
    }

    for (const name of ['ptt', 'ptt_on', 'ptt_off']) {
      expect(() => intents.dispatchRadioIntent({ name, params: {} } as never)).toThrow(/non-PTT/i);
    }
    expect(harness.sendCommand).not.toHaveBeenCalled();
    expect(lifecycle.getCommandLifecycles()).toHaveLength(0);
  });

  it('publishes the complete current non-PTT command-name set without a second authority', () => {
    const rit: RadioIntent = { name: 'set_rit_frequency', params: { freq: 300 } };
    expect(rit.params).toEqual({ freq: 300 });
    if (false) {
      // @ts-expect-error The shipped RIT command uses `freq`, not `value`.
      const invalidRit: RadioIntent = { name: 'set_rit_frequency', params: { value: 300 } };
      expect(invalidRit).toBeDefined();
    }
    expect(intents.RADIO_INTENT_NAMES).toHaveLength(92);
    expect(new Set(intents.RADIO_INTENT_NAMES).size).toBe(92);
    expect(intents.RADIO_INTENT_NAMES).toContain('set_data3_mod_input');
    expect(intents.RADIO_INTENT_NAMES).not.toContain('ptt');
    expect(intents.RADIO_INTENT_NAMES).not.toContain('ptt_on');
    expect(intents.RADIO_INTENT_NAMES).not.toContain('ptt_off');
  });
});
