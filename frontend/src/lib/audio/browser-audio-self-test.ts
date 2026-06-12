import type { BrowserAudioRouteState } from './browser-audio-route';

export type BrowserAudioSelfTestVerdict = 'pass' | 'fail' | 'blocked';

export type BrowserAudioReceiveBlocker =
  | 'wrong-or-missing-route'
  | 'known-tone-missing'
  | 'silence'
  | 'clipping'
  | 'level-out-of-range'
  | 'level-range-not-available'
  | 'level-not-measured';

export type BrowserAudioReceiveCaveat = 'latency-not-measured';

export type BrowserAudioTxBlocker = 'safe-tx-validation-required';

export interface BrowserAudioKnownToneInput {
  detected: boolean;
  frequencyHz?: number;
  confidence?: number;
}

export interface BrowserAudioLevelInput {
  measured: boolean;
  rmsDbfs?: number;
  peakDbfs?: number;
  silenceDetected?: boolean;
  clippingDetected?: boolean;
  usableRmsDbfs?: {
    min: number;
    max: number;
  };
}

export type BrowserAudioLatencyInput =
  | {
      measured: true;
      milliseconds: number;
      caveats?: readonly string[];
    }
  | {
      measured: false;
      caveats?: readonly string[];
    };

export interface BrowserAudioSelfTestAnalyzerInput {
  knownTone: BrowserAudioKnownToneInput;
  level: BrowserAudioLevelInput;
  latency: BrowserAudioLatencyInput;
}

export interface BrowserAudioSelfTestInput {
  route: BrowserAudioRouteState;
  analyzer: BrowserAudioSelfTestAnalyzerInput;
}

export type BrowserAudioSelfTestRouteResult = BrowserAudioRouteState;

export interface BrowserAudioKnownToneResult {
  status: 'detected' | 'missing';
  frequencyHz?: number;
  confidence?: number;
}

export interface BrowserAudioLevelResult {
  status:
    | 'usable'
    | 'silence'
    | 'clipping'
    | 'out-of-range'
    | 'range-not-available'
    | 'not-measured';
  rmsDbfs?: number;
  peakDbfs?: number;
  usableRmsDbfs?: {
    min: number;
    max: number;
  };
}

export type BrowserAudioLatencyResult =
  | {
      status: 'measured';
      milliseconds: number;
      unit: 'ms';
      blockingReceive: false;
      caveats: string[];
    }
  | {
      status: 'not-measured';
      unit: 'ms';
      blockingReceive: false;
      caveats: string[];
    };

export interface BrowserAudioReadinessResult<TBlocker extends string, TCaveat extends string> {
  ready: boolean;
  verdict: BrowserAudioSelfTestVerdict;
  blockers: TBlocker[];
  caveats: TCaveat[];
}

export interface BrowserAudioSelfTestState {
  route: BrowserAudioSelfTestRouteResult;
  knownTone: BrowserAudioKnownToneResult;
  level: BrowserAudioLevelResult;
  latency: BrowserAudioLatencyResult;
  receive: BrowserAudioReadinessResult<BrowserAudioReceiveBlocker, BrowserAudioReceiveCaveat>;
  tx: BrowserAudioReadinessResult<BrowserAudioTxBlocker, never>;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function evaluateRoute(route: BrowserAudioRouteState): BrowserAudioSelfTestRouteResult {
  switch (route.status) {
    case 'selected':
      return {
        ...route,
        input: { ...route.input },
        output: { ...route.output },
      };
    case 'missing-endpoints':
      return {
        ...route,
        missing: [...route.missing],
        ...(route.input ? { input: { ...route.input } } : {}),
        ...(route.output ? { output: { ...route.output } } : {}),
      };
    case 'permission-denied':
    case 'unsupported-output-selection':
      return { ...route };
  }
}

function evaluateKnownTone(input: BrowserAudioKnownToneInput): BrowserAudioKnownToneResult {
  const result: BrowserAudioKnownToneResult = {
    status: input.detected ? 'detected' : 'missing',
  };
  if (finite(input.frequencyHz)) result.frequencyHz = input.frequencyHz;
  if (finite(input.confidence)) result.confidence = input.confidence;
  return result;
}

function evaluateLevel(input: BrowserAudioLevelInput): BrowserAudioLevelResult {
  const result: BrowserAudioLevelResult = {
    status: 'not-measured',
  };
  if (finite(input.rmsDbfs)) result.rmsDbfs = input.rmsDbfs;
  if (finite(input.peakDbfs)) result.peakDbfs = input.peakDbfs;
  if (input.usableRmsDbfs) {
    result.usableRmsDbfs = {
      min: input.usableRmsDbfs.min,
      max: input.usableRmsDbfs.max,
    };
  }

  if (!input.measured || !finite(input.rmsDbfs)) return result;
  if (input.clippingDetected === true) return { ...result, status: 'clipping' };
  if (input.silenceDetected === true) return { ...result, status: 'silence' };

  const range = input.usableRmsDbfs;
  if (!range || !finite(range.min) || !finite(range.max)) {
    return { ...result, status: 'range-not-available' };
  }

  if (input.rmsDbfs < range.min || input.rmsDbfs > range.max) {
    return { ...result, status: 'out-of-range' };
  }

  return { ...result, status: 'usable' };
}

function evaluateLatency(input: BrowserAudioLatencyInput): BrowserAudioLatencyResult {
  if (input.measured && finite(input.milliseconds)) {
    return {
      status: 'measured',
      milliseconds: input.milliseconds,
      unit: 'ms',
      blockingReceive: false,
      caveats: [...(input.caveats ?? [])],
    };
  }

  return {
    status: 'not-measured',
    unit: 'ms',
    blockingReceive: false,
    caveats: ['latency-not-measured', ...(input.caveats ?? [])],
  };
}

function receiveBlockerForLevel(
  status: BrowserAudioLevelResult['status'],
): BrowserAudioReceiveBlocker | null {
  switch (status) {
    case 'usable':
      return null;
    case 'silence':
      return 'silence';
    case 'clipping':
      return 'clipping';
    case 'out-of-range':
      return 'level-out-of-range';
    case 'range-not-available':
      return 'level-range-not-available';
    case 'not-measured':
      return 'level-not-measured';
  }
}

export function evaluateBrowserAudioSelfTest(
  input: BrowserAudioSelfTestInput,
): BrowserAudioSelfTestState {
  const route = evaluateRoute(input.route);
  const knownTone = evaluateKnownTone(input.analyzer.knownTone);
  const level = evaluateLevel(input.analyzer.level);
  const latency = evaluateLatency(input.analyzer.latency);
  const receiveBlockers: BrowserAudioReceiveBlocker[] = [];

  if (route.status !== 'selected') receiveBlockers.push('wrong-or-missing-route');
  if (knownTone.status !== 'detected') receiveBlockers.push('known-tone-missing');
  const levelBlocker = receiveBlockerForLevel(level.status);
  if (levelBlocker) receiveBlockers.push(levelBlocker);

  const receiveCaveats: BrowserAudioReceiveCaveat[] = latency.status === 'not-measured'
    ? ['latency-not-measured']
    : [];

  return {
    route,
    knownTone,
    level,
    latency,
    receive: {
      ready: receiveBlockers.length === 0,
      verdict: receiveBlockers.length === 0 ? 'pass' : 'fail',
      blockers: receiveBlockers,
      caveats: receiveCaveats,
    },
    tx: {
      ready: false,
      verdict: 'blocked',
      blockers: ['safe-tx-validation-required'],
      caveats: [],
    },
  };
}
