/**
 * MOR-4250 red-PR fix — the controller wire→toast table lives in this
 * neutral module (not `$lib/transport`) so presentation entry points such
 * as `src/App.svelte` can recognise a coded controller failure without
 * violating the transport-import ban (ADR 2026-04-12,
 * `no-restricted-imports`: `eslint src/` failed on the transport import).
 *
 * These tests pin the boundary conversion: every wire code maps to its
 * camelCase toast code, real `ControllerClientError` instances resolve
 * structurally (no `instanceof` needed by callers), and anything without
 * a known wire code resolves to `null` so the caller keeps its fallback.
 */
import { describe, it, expect } from 'vitest';
import {
  CONTROLLER_TOAST_CODES,
  controllerToastCodeOf,
} from '../controller-toast-codes';
import { ControllerClientError } from '../transport/controller-client';

describe('CONTROLLER_TOAST_CODES', () => {
  it('covers every controller wire code with a camelCase toast code', () => {
    expect(CONTROLLER_TOAST_CODES).toEqual({
      controller_busy: 'controllerBusy',
      controller_invalid: 'controllerInvalid',
      controller_lost: 'controllerLost',
      controller_not_ready: 'controllerNotReady',
      controller_unsupported: 'controllerUnsupported',
      controller_local_required: 'controllerLocalRequired',
      remote_disabled: 'remoteDisabled',
    });
  });

  it('is the same table transport re-exports to runtime consumers', async () => {
    const transport = await import('../transport/controller-client');
    expect(transport.CONTROLLER_TOAST_CODES).toBe(CONTROLLER_TOAST_CODES);
  });
});

describe('controllerToastCodeOf', () => {
  it('resolves a real ControllerClientError structurally', () => {
    expect(controllerToastCodeOf(new ControllerClientError('controller_lost')))
      .toBe('controllerLost');
  });

  it('resolves a plain object carrying a known wire code', () => {
    expect(controllerToastCodeOf({ code: 'remote_disabled' })).toBe('remoteDisabled');
  });

  it('returns null for uncoded failures so the caller keeps its fallback', () => {
    expect(controllerToastCodeOf(new Error('boom'))).toBeNull();
    expect(controllerToastCodeOf({ code: 'no_such_code' })).toBeNull();
    expect(controllerToastCodeOf({ code: 42 })).toBeNull();
    expect(controllerToastCodeOf(null)).toBeNull();
    expect(controllerToastCodeOf(undefined)).toBeNull();
    expect(controllerToastCodeOf('controller_lost')).toBeNull();
  });
});
