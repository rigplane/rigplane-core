import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CommandDeliveryEvent, CommandLifecycleDeliveryEvent, ControlSessionTransition } from '$lib/transport/ws-client';
import type { RadioIntent } from '../radio-intents';

const harness = vi.hoisted(() => ({
  delivery: undefined as ((event: CommandDeliveryEvent) => void) | undefined,
  lifecycle: undefined as ((event: CommandLifecycleDeliveryEvent) => void) | undefined,
  transition: undefined as ((event: ControlSessionTransition) => void) | undefined,
  session: { state: 'connected' as const, epoch: 7 },
  sendCommand: vi.fn((..._args: unknown[]) => true),
}));

vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: vi.fn(() => harness.session),
  onCommandDelivery: vi.fn((handler: (event: CommandDeliveryEvent) => void) => {
    harness.delivery = handler;
    return () => {
      if (harness.delivery === handler) harness.delivery = undefined;
    };
  }),
  onCommandLifecycleDelivery: vi.fn((handler: (event: CommandLifecycleDeliveryEvent) => void) => {
    harness.lifecycle = handler;
    return () => { if (harness.lifecycle === handler) harness.lifecycle = undefined; };
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
    harness.lifecycle = undefined;
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

  it('sends one exact three-argument envelope with no optimistic side channel', () => {
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
    );
    expect(harness.sendCommand.mock.calls[0]).toHaveLength(3);
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

  it('projects held truth without changing pending or acknowledged authority', () => {
    const beforeAck = intents.dispatchRadioIntent({ id: 'held-first', name: 'set_freq', params: { freq: 1 } });
    const held = { commandId: 'held-first', kind: 'held', originalEpoch: 7, eventEpoch: 7,
      reason: 'tx_active', expiresAt: 12.5 } as const;
    harness.lifecycle?.(held);
    const annotation = lifecycle.getCommandLifecycleHold(beforeAck);
    expect(annotation).toEqual({ ...held });
    expect(Object.isFrozen(annotation)).toBe(true);
    expect(lifecycle.getCommandLifecycle(beforeAck.id, 7)?.status).toBe('pending');
    harness.lifecycle?.(held);
    expect(lifecycle.getCommandLifecycleHold(beforeAck)).toBe(annotation);
    harness.delivery?.({ commandId: beforeAck.id, kind: 'ack', originalEpoch: 7, eventEpoch: 7 });
    expect(lifecycle.getCommandLifecycle(beforeAck.id, 7)?.status).toBe('acknowledged');
    expect(lifecycle.getCommandLifecycleHold(beforeAck)).toBe(annotation);

    const afterAck = intents.dispatchRadioIntent({ id: 'ack-first', name: 'set_mode', params: { mode: 'CW' } });
    harness.delivery?.({ commandId: afterAck.id, kind: 'ack', originalEpoch: 7, eventEpoch: 7 });
    harness.lifecycle?.({ ...held, commandId: afterAck.id });
    expect(lifecycle.getCommandLifecycle(afterAck.id, 7)?.status).toBe('acknowledged');
    expect(lifecycle.getCommandLifecycleHold(afterAck)?.commandId).toBe(afterAck.id);

    const ignored = intents.dispatchRadioIntent({ id: 'ignored', name: 'set_freq', params: { freq: 2 } });
    for (const event of [
      { ...held, commandId: 'missing' }, { ...held, commandId: ignored.id, originalEpoch: 6 },
      { ...held, commandId: ignored.id, eventEpoch: 8 },
    ]) harness.lifecycle?.(event);
    expect(lifecycle.getCommandLifecycleHold(ignored)).toBeUndefined();
  });

  it('makes lifecycle terminals authoritative for one resettable five-second window', () => {
    const record = intents.dispatchRadioIntent({ id: 'terminal', name: 'set_freq', params: { freq: 3 } });
    harness.lifecycle?.({ commandId: record.id, kind: 'held', originalEpoch: 7, eventEpoch: 7,
      reason: 'tx_active', expiresAt: 12.5 });
    harness.delivery?.({ commandId: record.id, kind: 'ack', originalEpoch: 7, eventEpoch: 7 });
    harness.lifecycle?.({ commandId: record.id, kind: 'failed', originalEpoch: 7, eventEpoch: 7, error: 'blocked' });
    expect(lifecycle.getCommandLifecycle(record.id, 7)).toMatchObject({ status: 'failed', error: 'blocked' });
    expect(lifecycle.getCommandLifecycleHold(record)).toBeUndefined();
    vi.advanceTimersByTime(4_999);
    expect(lifecycle.getCommandLifecycle(record.id, 7)).toBeDefined();
    harness.lifecycle?.({ commandId: record.id, kind: 'timed-out', originalEpoch: 7, eventEpoch: 7, error: 'expired' });
    vi.advanceTimersByTime(1);
    expect(lifecycle.getCommandLifecycle(record.id, 7)?.status).toBe('timed-out');
    harness.delivery?.({ commandId: record.id, kind: 'ack', originalEpoch: 7, eventEpoch: 7 });
    lifecycle.cancelPendingCommands(7); lifecycle.confirmCommand(record.id, 7, 7);
    expect(lifecycle.getCommandLifecycle(record.id, 7)?.status).toBe('timed-out');
    vi.advanceTimersByTime(4_998);
    expect(lifecycle.getCommandLifecycle(record.id, 7)).toBeDefined();
    vi.advanceTimersByTime(1);
    expect(lifecycle.getCommandLifecycle(record.id, 7)).toBeUndefined();

    const superseded = intents.dispatchRadioIntent({ id: 'server-superseded', name: 'set_filter', params: { filter: 2 } });
    harness.lifecycle?.({ commandId: superseded.id, kind: 'superseded', originalEpoch: 7, eventEpoch: 7 });
    expect(lifecycle.getCommandLifecycle(superseded.id, 7)?.status).toBe('cancelled');
    expect(lifecycle.isCommandLifecycleSuperseded(superseded)).toBe(true);
    vi.advanceTimersByTime(5_000);
    expect(lifecycle.getCommandLifecycle(superseded.id, 7)).toBeUndefined();
  });

  it('clears held annotations on cancellation, confirmation, timeout, retirement, and reset', () => {
    const cancel = intents.dispatchRadioIntent({ id: 'held-cancel', name: 'set_filter', params: { filter: 1 } });
    const emitHeld = (commandId: string) => harness.lifecycle?.({ commandId, kind: 'held',
      originalEpoch: 7, eventEpoch: 7, reason: 'tx_active', expiresAt: 12.5 });
    emitHeld(cancel.id); harness.transition?.({ state: 'disconnected', epoch: 7 });
    expect(lifecycle.getCommandLifecycleHold(cancel)).toBeUndefined();

    const confirmed = intents.dispatchRadioIntent({ id: 'held-confirm', name: 'set_filter', params: { filter: 2 } });
    harness.delivery?.({ commandId: confirmed.id, kind: 'ack', originalEpoch: 7, eventEpoch: 7 });
    emitHeld(confirmed.id); lifecycle.confirmCommand(confirmed.id, 7, 7);
    expect(lifecycle.getCommandLifecycle(confirmed.id, 7)?.status).toBe('confirmed');
    expect(lifecycle.getCommandLifecycleHold(confirmed)).toBeUndefined();

    const retired = intents.dispatchRadioIntent({ id: 'held-retire', name: 'set_freq', params: { freq: 4 } });
    emitHeld(retired.id); vi.advanceTimersByTime(5_000);
    expect(lifecycle.getCommandLifecycle(retired.id, 7)?.status).toBe('timed-out');
    expect(lifecycle.getCommandLifecycleHold(retired)).toBeUndefined();
    vi.advanceTimersByTime(5_000);
    expect(lifecycle.getCommandLifecycle(retired.id, 7)).toBeUndefined();
    expect(lifecycle.getCommandLifecycleHold(intents.dispatchRadioIntent({ id: retired.id,
      name: 'set_freq', params: { freq: 5 } }))).toBeUndefined();
    emitHeld(retired.id); lifecycle.resetCommandLifecycle();
    expect(lifecycle.getCommandLifecycleHold(retired)).toBeUndefined();
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
    expect(source).not.toMatch(/\bconfirmCommand\s*\(/);
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
      index + 1, 'set_af_level', { level, receiver: 0 }, `af-normalized-${index}`,
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
      'set_rf_power', { level: 0.42 }, 'rf-fraction',
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
    expect(harness.sendCommand.mock.calls.every((call) => call.length === 3)).toBe(true);
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
