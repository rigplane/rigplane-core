// Audio state — volume, mute, RX/TX bridge
let audioState = $state({
  rxEnabled: false,
  txEnabled: false,
  volume: 50,
  muted: false,
  micEnabled: false,
  bridgeRunning: false,
  // MOR-1791: the server reported it cannot decode Opus, so browser TX runs
  // on the PCM16 capture path instead. A working state, not a fault — it is
  // surfaced as a quiet status hint, never as a modal or a blocking error.
  txCodecFallback: false,
});

export interface RxAudioTargetSnapshot {
  readonly muted: boolean;
  readonly rxEnabled: boolean;
}

export type RxAudioTargetSubscriber = (next: RxAudioTargetSnapshot) => void;
const rxAudioTargetSubscribers = new Set<RxAudioTargetSubscriber>();

export function getRxAudioTargetSnapshot(): RxAudioTargetSnapshot {
  return Object.freeze({
    muted: audioState.muted,
    rxEnabled: audioState.rxEnabled,
  });
}

function notifyRxAudioTargetSubscribers(): void {
  const snapshot = getRxAudioTargetSnapshot();
  for (const subscriber of rxAudioTargetSubscribers) {
    try {
      subscriber(snapshot);
    } catch (error) {
      console.warn('RX audio target subscriber failed', error);
    }
  }
}

export function subscribeRxAudioTarget(subscriber: RxAudioTargetSubscriber): () => void {
  rxAudioTargetSubscribers.add(subscriber);
  try {
    subscriber(getRxAudioTargetSnapshot());
  } catch (error) {
    rxAudioTargetSubscribers.delete(subscriber);
    throw error;
  }
  let active = true;
  return () => {
    if (!active) return;
    active = false;
    rxAudioTargetSubscribers.delete(subscriber);
  };
}

export function getAudioState(): typeof audioState {
  return audioState;
}

export function setVolume(v: number): void {
  audioState.volume = Math.max(0, Math.min(100, Math.round(v)));
}

export function toggleMute(): void {
  audioState.muted = !audioState.muted;
  notifyRxAudioTargetSubscribers();
}

export function setMuted(v: boolean): void {
  if (audioState.muted === v) return;
  audioState.muted = v;
  notifyRxAudioTargetSubscribers();
}

export function setRxEnabled(v: boolean): void {
  if (audioState.rxEnabled === v) return;
  audioState.rxEnabled = v;
  notifyRxAudioTargetSubscribers();
}

export function setTxEnabled(v: boolean): void {
  audioState.txEnabled = v;
}

export function setMicEnabled(v: boolean): void {
  audioState.micEnabled = v;
}

export function setBridgeRunning(v: boolean): void {
  audioState.bridgeRunning = v;
}

export function setTxCodecFallback(v: boolean): void {
  audioState.txCodecFallback = v;
}
