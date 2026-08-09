import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
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

  it('removes dormant keyer type from both the type and runtime vocabulary', () => {
    if (false) {
      // @ts-expect-error There is no observed keyer-type fact or executable command.
      intents.dispatchRadioIntent({ name: 'set_keyer_type', params: { type: 1 } });
    }
    expect(() => intents.dispatchRadioIntent({
      name: 'set_keyer_type', params: { type: 1 },
    } as never)).toThrow(/non-PTT/i);
    expect(intents.RADIO_INTENT_NAMES).not.toContain('set_keyer_type');
    expect(harness.sendCommand).not.toHaveBeenCalled();
    expect(lifecycle.getCommandLifecycles()).toHaveLength(0);
  });

  it('contains lifecycle only and cannot write radio truth from ACK or result', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/lib/runtime/commands/radio-intents.ts'), 'utf8');
    expect(source).not.toMatch(/from ['"]\$lib\/stores\/radio/);
    expect(source).not.toMatch(/\b(?:patchActiveReceiver|patchRadioState|patchReceiver)\s*\(/);
    expect(source).not.toContain('set_keyer_type');
    expect(source).not.toMatch(/['"]ptt(?:_on|_off)?['"]/);
  });

  it('rejects forged malformed known intents before lifecycle or transport', () => {
    const malformed = [
      null,
      [],
      { name: 'set_freq', params: {} },
      { name: 'set_freq', params: null },
      { name: 'set_freq', params: [] },
      { name: 'set_freq', params: { freq: '14074000' } },
      { name: 'set_freq', params: { freq: Number.NaN } },
      { name: 'set_freq', params: { freq: Number.MAX_SAFE_INTEGER + 1 } },
      { name: 'set_freq', params: { freq: 1.5 } },
      { name: 'set_freq', params: { freq: 1 }, unexpected: true },
      { name: 'set_compressor', params: { on: 1 } },
      { name: 'set_af_level', params: { level: -0.01, receiver: 0 } },
      { name: 'set_af_level', params: { level: 1.01, receiver: 0 } },
      { name: 'set_af_level', params: { level: 10, receiver: 0 } },
      { name: 'set_af_level', params: { level: '0.5', receiver: 0 } },
      { name: 'set_af_level', params: { level: 10, receiver: 7 } },
      { name: 'set_af_level', params: { level: 10, receiver: '0' } },
      { name: 'set_af_level', params: { level: Number.NaN, receiver: 0 } },
      { name: 'set_af_level', params: { level: Number.POSITIVE_INFINITY, receiver: 0 } },
      { name: 'set_af_level', params: { level: 0.5, receiver: 0, unexpected: true } },
      { name: 'set_af_level', params: { level: 0.5, receiver: 0 }, unexpected: true },
      { name: 'set_vfo', params: { vfo: 'VFOA' } },
      { name: 'set_mode', params: { mode: 'USB', receiver: 0, unexpected: true } },
      { name: 'vfo_swap', params: { unexpected: true } },
      { name: 'set_filter', params: { filter: 2 }, id: '' },
      { name: 'set_filter', params: { filter: 2 }, id: 7 },
    ];

    for (const intent of malformed) {
      expect(() => intents.dispatchRadioIntent(intent as never)).toThrow(/invalid radio intent/i);
    }
    expect(harness.sendCommand).not.toHaveBeenCalled();
    expect(lifecycle.getCommandLifecycles()).toHaveLength(0);
  });

  it('accepts exact normalized AF boundaries and fractions without weakening integer fields', () => {
    const levels = [0, 1, 50 / 255] as const;
    levels.forEach((level, index) => intents.dispatchRadioIntent({
      id: `af-normalized-${index}`, name: 'set_af_level', params: { level, receiver: 0 },
    }));

    levels.forEach((level, index) => expect(harness.sendCommand).toHaveBeenNthCalledWith(
      index + 1, 'set_af_level', { level, receiver: 0 }, `af-normalized-${index}`, { optimistic: false },
    ));
    expect(lifecycle.getCommandLifecycles()).toHaveLength(levels.length);
    expect(lifecycle.getCommandLifecycles()).toEqual(expect.arrayContaining(levels.map((_, index) =>
      expect.objectContaining({ id: `af-normalized-${index}`, name: 'set_af_level', status: 'pending' }))));
    expect(() => intents.dispatchRadioIntent({
      name: 'set_nr_level', params: { level: 0.42, receiver: 0 },
    } as never)).toThrow(TypeError);
  });

  it('accepts the shipped fractional RF-power scale without weakening integer TX fields', () => {
    intents.dispatchRadioIntent({
      id: 'rf-fraction', name: 'set_rf_power', params: { level: 0.42 },
    });

    expect(harness.sendCommand).toHaveBeenCalledExactlyOnceWith(
      'set_rf_power', { level: 0.42 }, 'rf-fraction', { optimistic: false },
    );
    expect(() => intents.dispatchRadioIntent({
      name: 'set_mic_gain', params: { level: 0.42 },
    } as never)).toThrow(TypeError);
  });

  it('accepts representative exact envelopes derived from every descriptor family', () => {
    const representatives: RadioIntent[] = [
      { name: 'vfo_swap', params: {} },
      { name: 'memory_clear', params: { channel: 1 } },
      { name: 'set_compressor', params: { on: true } },
      { name: 'set_nb', params: { on: false, receiver: 0 } },
      { name: 'set_mic_gain', params: { level: 10 } },
      { name: 'set_af_level', params: { level: 50 / 255, receiver: 1 } },
      { name: 'set_cw_pitch', params: { value: 10 } },
      { name: 'set_pbt_inner', params: { value: 10, receiver: 0 } },
      { name: 'set_data_mode', params: { mode: 1, receiver: 1 } },
      { name: 'set_data3_mod_input', params: { source: 2 } },
      { name: 'set_scope_mode', params: { mode: 1 } },
      { name: 'set_scope_span', params: { span: 25_000 } },
      { name: 'set_scope_speed', params: { speed: 2 } },
      { name: 'scan_start', params: { type: 1 } },
      { name: 'set_mode', params: { mode: 'USB', filter: 2, receiver: 0 } },
      { name: 'set_filter', params: { filter: 2 } },
      { name: 'set_vfo', params: { vfo: 'A' } },
      { name: 'set_vfo', params: { vfo: 'B' } },
      { name: 'set_vfo', params: { vfo: 'MAIN' } },
      { name: 'set_vfo', params: { vfo: 'SUB' } },
    ];

    representatives.forEach((intent) => intents.dispatchRadioIntent(intent));
    expect(harness.sendCommand).toHaveBeenCalledTimes(representatives.length);
    expect(lifecycle.getCommandLifecycles()).toHaveLength(representatives.length);
  });

  it('rejects the 101st pending facade intent before transport', () => {
    for (let i = 0; i < 100; i += 1) {
      intents.dispatchRadioIntent({ id: `pending-${i}`, name: 'set_freq', params: { freq: i } });
    }
    expect(() => intents.dispatchRadioIntent({
      id: 'overflow', name: 'set_freq', params: { freq: 101 },
    })).toThrow(/capacity/i);

    expect(harness.sendCommand).toHaveBeenCalledTimes(100);
    expect(lifecycle.getCommandLifecycles()).toHaveLength(100);
    expect(lifecycle.getCommandLifecycle('pending-0', 7)?.status).toBe('pending');
    expect(lifecycle.getCommandLifecycle('overflow', 7)).toBeUndefined();
  });

  it('publishes the complete current non-PTT command-name set without a second authority', () => {
    const rit: RadioIntent = { name: 'set_rit_frequency', params: { freq: 300 } };
    expect(rit.params).toEqual({ freq: 300 });
    if (false) {
      // @ts-expect-error The shipped RIT command uses `freq`, not `value`.
      const invalidRit: RadioIntent = { name: 'set_rit_frequency', params: { value: 300 } };
      expect(invalidRit).toBeDefined();
    }
    expect(intents.RADIO_INTENT_NAMES).toHaveLength(91);
    expect(new Set(intents.RADIO_INTENT_NAMES).size).toBe(91);
    expect(intents.RADIO_INTENT_NAMES).toContain('set_data3_mod_input');
    expect(intents.RADIO_INTENT_NAMES).not.toContain('ptt');
    expect(intents.RADIO_INTENT_NAMES).not.toContain('ptt_on');
    expect(intents.RADIO_INTENT_NAMES).not.toContain('ptt_off');
    expect(intents.RADIO_INTENT_NAMES).not.toContain('set_keyer_type');
  });
});
