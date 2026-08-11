/**
 * MOR-1265 — the txAux intent maps agree with the REAL command bus.
 *
 * `SemanticRadioSurfaces` addresses the v2 handlers by name
 * (`txAuxIntents.onAtuTune`, …). Its own component test mocks `../command-bus`,
 * so a renamed or deleted handler there would be invisible to it: the mock
 * would keep answering. This file closes that gap by loading the real module
 * (`dsp-nr-level.isolated.test.ts`'s established mocking recipe) and asserting, for
 * every name the wiring source actually references, that it exists and emits
 * the exact command the radio expects.
 *
 * The safety-critical one is `onAtuTune`: it must send the ATU tune-start
 * command and nothing else. A silent rename would leave the gated TUNE
 * button wired to `undefined` — or, worse, to a neighbouring TX command.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/transport/ws-client', () => ({ sendCommand: vi.fn() }));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return { dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params) };
});
vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => null),
  getRadioState: vi.fn(() => ({
    stateContractVersion: 1, providerGeneration: 1,
    active: 'MAIN', powerLevel: 0.5, micGain: 128, tunerStatus: 0, voxOn: false,
    voxGain: 50, antiVoxGain: 25, voxDelay: 4,
    compressorOn: false, compressorLevel: 20, monitorOn: false, monitorGain: 20,
    driveGain: 50, main: {}, sub: {},
  })),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));
vi.mock('$lib/state/field-status', () => ({ isFieldAvailable: vi.fn(() => true) }));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => ({
    capabilities: ['tx', 'tuner', 'vox', 'compressor', 'monitor', 'drive_gain'],
    receivers: 2, vfoScheme: 'main_sub',
  })),
  capabilitiesMatchGeneration: vi.fn(() => true),
  getControlRange: vi.fn(() => null),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    setAudioConfig: vi.fn(), startRx: vi.fn(), stopRx: vi.fn(),
    setRxVolume: vi.fn(), rxEnabled: false,
  },
}));

import { sendCommand } from '$lib/transport/ws-client';
import { makeTxHandlers, makeVoxHandlers } from '$lib/runtime/commands/panel-commands';

/** The composed object the wiring builds — same spread, same precedence. */
const intents = (): Record<string, (...args: never[]) => void> =>
  ({ ...makeVoxHandlers(), ...makeTxHandlers() }) as unknown as Record<string, (...args: never[]) => void>;

const wiringSource = readFileSync('src/components-v2/wiring/SemanticRadioSurfaces.svelte', 'utf8');
/** Every `txAuxIntents.onXxx` the wiring references, read off the real file. */
const REFERENCED = [...new Set(
  [...wiringSource.matchAll(/txAuxIntents\.(on[A-Za-z]+)/g)].map((m) => m[1]),
)].sort();

beforeEach(() => { vi.mocked(sendCommand).mockClear(); });

describe('the wiring only names handlers the real command bus provides', () => {
  // Kills: a rename/removal in command-bus.ts that the mocked component test
  // cannot see, and a typo in either intent map.
  it('references at least the thirteen txAux intents', () => {
    expect(REFERENCED).toHaveLength(13);
  });

  it.each(REFERENCED)('"%s" exists on the composed handlers and is callable', (name) => {
    expect(typeof intents()[name]).toBe('function');
  });
});

describe('each txAux intent emits the command the radio expects', () => {
  // SAFETY: the transmit-causing one. `value: 2` is "start tuning" — the same
  // encoding `tunerStatus` is read back with (MOR-1244 `atu` mapping).
  it('onAtuTune starts an ATU tune cycle, and sends nothing else', () => {
    intents().onAtuTune();
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith('set_tuner_status', { value: 2 });
  });

  it.each([
    ['onAtuToggle', [], 'set_tuner_status', { value: 1 }],
    ['onVoxToggle', [], 'set_vox', { on: true }],
    ['onCompToggle', [], 'set_compressor', { on: true }],
    ['onMonToggle', [], 'set_monitor', { on: true }],
    ['onRfPowerChange', [0.5], 'set_rf_power', { level: 0.5 }],
    ['onMicGainChange', [200], 'set_mic_gain', { level: 200 }],
    ['onDriveGainChange', [100], 'set_drive_gain', { level: 100 }],
    ['onVoxGainChange', [77], 'set_vox_gain', { level: 77 }],
    ['onAntiVoxGainChange', [12], 'set_anti_vox_gain', { level: 12 }],
    ['onVoxDelayChange', [5], 'set_vox_delay', { level: 5 }],
    ['onCompLevelChange', [33], 'set_compressor_level', { level: 33 }],
    ['onMonLevelChange', [99], 'set_monitor_gain', { level: 99 }],
  ] as const)('%s sends %s', (name, args, command, params) => {
    (intents()[name] as (...a: readonly number[]) => void)(...args);
    expect(sendCommand).toHaveBeenCalledExactlyOnceWith(command, params);
  });
});
