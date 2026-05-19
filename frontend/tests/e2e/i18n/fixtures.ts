/**
 * Mock backend fixtures for the i18n visual smoke pack (RP-ML-006).
 *
 * The visual pack runs Playwright against the built static frontend with
 * NO live backend. We mock just enough of the `/api/v1/*` HTTP surface
 * and the control WebSocket to let `runtime.bootstrap()` succeed and
 * `RadioLayout` paint a populated, deterministic state. Anything beyond
 * that is YAGNI for screenshots.
 *
 * Keep this fixture small and stable. Random or time-dependent values
 * cause baseline screenshot churn; everything here is frozen.
 */

import type { Capabilities } from '../../../src/lib/types/capabilities';
import type { ServerState, ReceiverState } from '../../../src/lib/types/state';

const baseReceiver: ReceiverState = {
  freqHz: 14_205_000,
  mode: 'USB',
  filter: 1,
  dataMode: 0,
  sMeter: 12,
  att: 0,
  preamp: 0,
  nb: false,
  nr: false,
  afLevel: 128,
  rfGain: 200,
  squelch: 0,
  agc: 1,
  autoNotch: false,
  manualNotch: false,
  agcTimeConstant: 60,
  nrLevel: 5,
  pbtInner: 0,
  pbtOuter: 0,
  filterWidth: 2400,
  ifShift: 0,
  contour: 0,
  nbLevel: 5,
  manualNotchWidth: 0,
};

const subReceiver: ReceiverState = {
  ...baseReceiver,
  freqHz: 7_185_000,
  mode: 'LSB',
};

export const mockState: ServerState = {
  revision: 1,
  updatedAt: '2026-05-19T00:00:00Z',
  active: 'MAIN',
  powerOn: true,
  ptt: false,
  split: false,
  dualWatch: false,
  tunerStatus: 0,
  main: baseReceiver,
  sub: subReceiver,
  connection: {
    rigConnected: true,
    radioReady: true,
    controlConnected: true,
  },
  radioHealth: {
    serverReachable: true,
    radioLink: 'connected',
    readiness: 'ready',
    likelyCause: 'unknown',
    sinceMs: 0,
    lastError: null,
  },
  radioDetail: {
    status: 'connected',
    uptimeSeconds: 600,
  },
  meterSource: 'S',
  cwPitch: 600,
  micGain: 50,
  compressorOn: false,
  compressorLevel: 5,
  monitorOn: false,
  monitorGain: 100,
  voxOn: false,
  ritOn: false,
  ritTx: false,
  ritFreq: 0,
};

export const mockDisconnectedState: ServerState = {
  ...mockState,
  connection: {
    rigConnected: false,
    radioReady: false,
    controlConnected: false,
  },
  radioHealth: {
    serverReachable: true,
    radioLink: 'disconnected',
    readiness: 'stalled',
    likelyCause: 'radio_network_lost',
    sinceMs: 12_000,
    lastError: 'Connection refused',
  },
  radioDetail: {
    status: 'disconnected',
    uptimeSeconds: 0,
  },
};

export const mockPowerOffState: ServerState = {
  ...mockState,
  powerOn: false,
  connection: {
    rigConnected: true,
    radioReady: false,
    controlConnected: true,
  },
  radioHealth: {
    serverReachable: true,
    radioLink: 'unknown',
    readiness: 'stalled',
    likelyCause: 'radio_powered_off_likely',
    sinceMs: 5_000,
    lastError: null,
  },
};

export const mockCapabilities: Capabilities = {
  model: 'IC-7610',
  scope: true,
  audio: true,
  tx: true,
  capabilities: ['scope', 'audio', 'tx'],
  receivers: 2,
  vfoScheme: 'main_sub',
  hasLan: false,
  modes: ['USB', 'LSB', 'CW', 'CW-R', 'AM', 'FM', 'RTTY'],
  filters: ['WIDE', 'MID', 'NARROW'],
  filterWidthMin: 50,
  filterWidthMax: 3600,
  attValues: [0, 20],
  attLabels: { '0': 'OFF', '20': '20dB' },
  preValues: [0, 1, 2],
  preLabels: { '0': 'OFF', '1': 'P1', '2': 'P2' },
  agcModes: [1, 2, 3],
  agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' },
  freqRanges: [
    {
      start: 1_800_000,
      end: 30_000_000,
      label: 'HF',
      bands: [
        { name: '160m', start: 1_800_000, end: 2_000_000, default: 1_840_000, bsrCode: 1 },
        { name: '80m', start: 3_500_000, end: 4_000_000, default: 3_750_000, bsrCode: 3 },
        { name: '40m', start: 7_000_000, end: 7_300_000, default: 7_185_000, bsrCode: 7 },
        { name: '20m', start: 14_000_000, end: 14_350_000, default: 14_205_000, bsrCode: 14 },
        { name: '15m', start: 21_000_000, end: 21_450_000, default: 21_300_000, bsrCode: 21 },
        { name: '10m', start: 28_000_000, end: 29_700_000, default: 28_500_000, bsrCode: 28 },
      ],
    },
  ],
  scopeSource: 'hardware',
  scopeConfig: {
    centerMode: true,
    amplitudeMax: 100,
    defaultSpan: 100_000,
  },
};

export const mockInfo = {
  name: 'rigplane',
  version: '0.0.0-test',
  build: 'i18n-visual-smoke',
};
