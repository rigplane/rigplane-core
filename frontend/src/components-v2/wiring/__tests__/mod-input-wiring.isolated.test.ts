/**
 * MOD-input source wiring (MOR-616).
 *
 * `onModInputChange` must route the new source to the SET command of the
 * active receiver's DATA group (T1/MOR-615 backend contract:
 * `set_data_off_mod_input` / `set_data1_mod_input` / `set_data2_mod_input` /
 * `set_data3_mod_input`, payload `{ source: int }`).
 *
 * Covered on the runtime `panel-commands` copy — the one ModePanel actually
 * uses via panel-adapters, and since A15 the only one that exists.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(),
}));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => null),
  getRadioState: vi.fn(() => null),
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    setAudioConfig: vi.fn(),
    startRx: vi.fn(),
    stopRx: vi.fn(),
    setRxVolume: vi.fn(),
    rxEnabled: false,
  },
}));

import { sendCommand } from '$lib/transport/ws-client';
import { getActiveReceiver, getRadioState } from '$lib/stores/radio.svelte';
import { makeModeHandlers as makeRuntimeModeHandlers } from '$lib/runtime/commands/panel-commands';

const factories: ReadonlyArray<readonly [string, () => { onModInputChange: (source: number) => void }]> = [
  ['panel-commands', makeRuntimeModeHandlers],
];

describe.each(factories)('%s onModInputChange (MOR-616)', (_name, makeHandlers) => {
  beforeEach(() => {
    vi.mocked(sendCommand).mockClear();
    vi.mocked(getActiveReceiver).mockReturnValue(null);
    vi.mocked(getRadioState).mockReturnValue(null);
  });

  it('emits set_data_off_mod_input when DATA is off', () => {
    vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN', main: {} } as never);
    vi.mocked(getActiveReceiver).mockReturnValue({ dataMode: 0 } as never);

    makeHandlers().onModInputChange(5);

    expect(sendCommand).toHaveBeenCalledWith('set_data_off_mod_input', { source: 5 });
  });

  it('emits the per-group command for D1/D2/D3', () => {
    const cases = [
      [1, 'set_data1_mod_input', 'data1ModInput'],
      [2, 'set_data2_mod_input', 'data2ModInput'],
      [3, 'set_data3_mod_input', 'data3ModInput'],
    ] as const;

    for (const [dataMode, command, stateKey] of cases) {
      vi.mocked(getRadioState).mockReturnValue({ active: 'MAIN', main: {} } as never);
      vi.mocked(getActiveReceiver).mockReturnValue({ dataMode } as never);

      makeHandlers().onModInputChange(3);

      expect(sendCommand).toHaveBeenCalledWith(command, { source: 3 });
    }
  });

  it('fails closed when no receiver state exists', () => {
    makeHandlers().onModInputChange(0);

    expect(sendCommand).not.toHaveBeenCalled();
  });
});
