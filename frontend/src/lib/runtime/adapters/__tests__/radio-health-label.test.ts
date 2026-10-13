import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { setLocale } from '$lib/i18n';
import { _resetLocale } from '$lib/i18n/store.svelte';
import type { RadioHealthPublic } from '$lib/types/state';
import { deriveRadioHealthLabel } from '../radio-health-label';

type Cause = RadioHealthPublic['likelyCause'];
type Readiness = RadioHealthPublic['readiness'];

const health = (likelyCause: Cause, readiness: Readiness = 'ready') =>
  ({ likelyCause, readiness });

// Healthy rig and ready facts: with these, only the cause-keyed branches can
// produce a label.
const healthy = { rigConnected: true, radioReady: true, radioState: 'connected' } as const;

beforeEach(() => {
  localStorage.clear();
  _resetLocale();
  setLocale('en-US');
});

afterEach(() => {
  localStorage.clear();
  _resetLocale();
});

describe('deriveRadioHealthLabel — cause-keyed branches', () => {
  it.each<[Cause, Readiness, string]>([
    ['radio_network_lost', 'stalled', 'radio link lost'],
    ['radio_not_responding', 'delayed', 'radio response delayed'],
    ['radio_not_responding', 'ready', 'radio not responding'],
    ['radio_not_responding', 'stalled', 'radio not responding'],
    ['radio_not_responding', 'recovering', 'radio not responding'],
    ['radio_powered_off_likely', 'stalled', 'radio appears powered off or unreachable'],
    ['server_unreachable', 'ready', 'server unreachable'],
    ['radio_session_busy', 'recovering', 'radio in use by another connection, retrying'],
    ['operator_disconnected', 'idle', 'disconnected by the operator'],
  ])('%s with readiness %s reads "%s"', (cause, readiness, expected) => {
    expect(deriveRadioHealthLabel({ health: health(cause, readiness), ...healthy })).toBe(expected);
  });

  it('ignores the rig and ready facts when the cause names the fault', () => {
    expect(deriveRadioHealthLabel({
      health: health('radio_network_lost'),
      rigConnected: false, radioReady: false, radioState: 'degraded',
    })).toBe('radio link lost');
  });
});

describe('deriveRadioHealthLabel — default branches', () => {
  const unnamed: Cause[] = ['unknown', 'radio_remote_control_unreachable', 'radio_not_connected'];

  it.each(unnamed)('reads "rig offline" for cause %s when the rig is down and the link is degraded', (cause) => {
    expect(deriveRadioHealthLabel({
      health: health(cause), rigConnected: false, radioReady: true, radioState: 'degraded',
    })).toBe('rig offline');
  });

  it.each(unnamed)('reads "radio link degraded — radio not ready" for cause %s when only readiness is missing', (cause) => {
    expect(deriveRadioHealthLabel({
      health: health(cause), rigConnected: true, radioReady: false, radioState: 'degraded',
    })).toBe('radio link degraded — radio not ready');
  });

  it('prefers "rig offline" when both the rig and readiness are missing', () => {
    expect(deriveRadioHealthLabel({
      health: health('unknown'), rigConnected: false, radioReady: false, radioState: 'degraded',
    })).toBe('rig offline');
  });

  it('serves the same default labels without any health payload', () => {
    expect(deriveRadioHealthLabel({
      health: null, rigConnected: false, radioReady: false, radioState: 'degraded',
    })).toBe('rig offline');
    expect(deriveRadioHealthLabel({
      health: null, rigConnected: true, radioReady: false, radioState: 'degraded',
    })).toBe('radio link degraded — radio not ready');
  });

  it.each(['connected', 'connecting', 'reconnecting', 'disconnected'])(
    'is empty when the link state is %s, whatever the rig and ready facts',
    (radioState) => {
      expect(deriveRadioHealthLabel({
        health: health('unknown'), rigConnected: false, radioReady: false, radioState,
      })).toBe('');
    },
  );

  it('is empty when no fact is missing, with or without a health payload', () => {
    expect(deriveRadioHealthLabel({ health: null, ...healthy })).toBe('');
    expect(deriveRadioHealthLabel({
      health: health('unknown'), rigConnected: true, radioReady: true, radioState: 'degraded',
    })).toBe('');
  });
});

describe('deriveRadioHealthLabel — locale', () => {
  it('follows the active locale', () => {
    setLocale('ja-JP');
    expect(deriveRadioHealthLabel({ health: health('radio_network_lost'), ...healthy }))
      .toBe('トランシーバーとの通信が切断されました');
  });

  it.each([
    ['ru-RU', 'отключено оператором'],
    ['ja-JP', 'オペレーターが切断しました'],
  ] as const)('reads the operator disconnect copy in %s', (locale, expected) => {
    setLocale(locale);
    expect(deriveRadioHealthLabel({ health: health('operator_disconnected', 'idle'), ...healthy }))
      .toBe(expected);
  });

  it('reads the busy copy in Russian', () => {
    setLocale('ru-RU');
    expect(deriveRadioHealthLabel({ health: health('radio_session_busy', 'recovering'), ...healthy }))
      .toBe('трансивер занят другим подключением, повторяем попытку');
  });
});
