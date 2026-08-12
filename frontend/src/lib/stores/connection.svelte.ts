// Connection health state
import type { ServerState } from '../types/state';

type RadioHealth = NonNullable<ServerState['radioHealth']>;

let wsConnected = $state(false);
let audioConnected = $state(false);
let scopeConnected = $state(false);
let scopeLastFrame = $state(0);
let radioStatus = $state<'connected' | 'connecting' | 'reconnecting' | 'disconnected'>('disconnected');
let radioPowerOn = $state<boolean | null>(null);
let rigConnected = $state(false);
let radioReady = $state(false);
let controlConnected = $state(false);
let radioHealth = $state<RadioHealth | null>(null);
let lastResponseTime = $state<number | null>(null);

// MOR-1526 F2: rigConnected/radioReady/radioHealth are refreshed only by
// state_update and go stale (not reset) on a transport drop. Clearing them
// would be visible to isLiveRadioAvailable()/sendCommand — a command-gate
// change this display fix must not make. Instead, track whether the facts
// have been observed on the CURRENT transport session; the chip refuses to
// go green until they have.
let factsObservedThisSession = $state(false);

let lastStateUpdate = $state(0);
const STALE_THRESHOLD_MS = 5000;
let staleState = $state(false);
let reconnecting = $state(false);

if (typeof window !== 'undefined') {
  setInterval(() => {
    const age = lastStateUpdate > 0 ? Date.now() - lastStateUpdate : 0;
    staleState = lastStateUpdate > 0 && age > STALE_THRESHOLD_MS;
  }, 1000);
}

// MOR-1419: "server link" honesty. The A10 HTTP-polling retirement (#2362)
// deleted the only real producer of the legacy `httpConnected` field (the
// HTTP state poller); the field became an orphan that only ever mirrored
// (imperfectly, and with a cold-start race) the WS transport it now
// duplicates. `wsConnected` — set synchronously from the real transport's
// state-change callback — is the single honest live signal for both the
// control-link and server-link indicators.
let isFullyConnected = $derived(wsConnected);
let overallConnected = $derived(wsConnected && audioConnected);
let audioAliveControlDead = $derived(audioConnected && !wsConnected);
let connectionStatus = $derived<'connected' | 'partial' | 'disconnected'>(
  wsConnected ? 'connected' : 'disconnected',
);

export function setWsConnected(v: boolean): void {
  wsConnected = v;
  if (!v) factsObservedThisSession = false;
}

export function setReconnecting(v: boolean): void {
  reconnecting = v;
}

export function setLastResponseTime(ms: number): void {
  lastResponseTime = ms;
}

export function getConnectionStatus(): 'connected' | 'partial' | 'disconnected' {
  return connectionStatus;
}

export function isConnected(): boolean {
  return isFullyConnected;
}

export function getWsConnected(): boolean {
  return wsConnected;
}

export function setAudioConnected(v: boolean): void {
  audioConnected = v;
}

export function isAudioConnected(): boolean {
  return audioConnected;
}

export function isOverallConnected(): boolean {
  return overallConnected;
}

export function isAudioAliveControlDead(): boolean {
  return audioAliveControlDead;
}

export function getLastResponseTime(): number | null {
  return lastResponseTime;
}

export function markStateUpdated(): void {
  lastStateUpdate = Date.now();
  staleState = false;
}

export function isStale(): boolean {
  return staleState;
}

export function isReconnecting(): boolean {
  return reconnecting;
}

export function setScopeConnected(v: boolean): void {
  scopeConnected = v;
}

export function markScopeFrame(): void {
  scopeLastFrame = Date.now();
}

export function isScopeConnected(): boolean {
  return scopeConnected;
}

export function setRadioStatus(s: string): void {
  const valid = ['connected', 'connecting', 'reconnecting', 'disconnected'] as const;
  if (valid.includes(s as typeof valid[number])) {
    radioStatus = s as typeof radioStatus;
  }
}

export function getRadioStatus(): string {
  return radioStatus;
}

export function setRadioPowerOn(v: boolean | null): void {
  radioPowerOn = v;
}

export function getRadioPowerOn(): boolean | null {
  return radioPowerOn;
}

export function setRigConnected(v: boolean): void {
  rigConnected = v;
  factsObservedThisSession = true;
}

export function getRigConnected(): boolean {
  return rigConnected;
}

export function setRadioReady(v: boolean): void {
  radioReady = v;
}

export function getRadioReady(): boolean {
  return radioReady;
}

export function setControlConnected(v: boolean): void {
  controlConnected = v;
}

export function getControlConnected(): boolean {
  return controlConnected;
}

export function setRadioHealth(v: RadioHealth | null): void {
  radioHealth = v;
}

export function getRadioHealth(): RadioHealth | null {
  return radioHealth;
}

// MOR-1526: "Radio ↔ Server" chip honesty. `radioStatus` above is an
// event-only field — its ONLY writer is the reconnect-edge `connection_status`
// WS event (ws-client.ts), which the backend emits solely on reconnect edges
// (watchdog-timeout / attempt / success / permanent-failure — see
// runtime/radio_reconnect.py). A session that never reconnects never
// receives that event, so `radioStatus` sits at its default 'disconnected'
// forever — red on a perfectly healthy link. This is the same class of bug
// MOR-1419 fixed for the neighboring `httpState` chip by switching it to a
// live, continuously-synced fact instead of an edge-triggered one; applied
// here to the radio-link chip, additively — `radioStatus`/`setRadioStatus`/
// `getRadioStatus` are unchanged and still drive the reconnect overlay.
//
// Precedence: while a reconnect is actively in flight, the event stream is
// the only source that knows it ("still trying", attempt N) — overlay it.
// Otherwise (including first render, before any reconnect has ever fired)
// the steady state comes from live per-field facts synced on every
// state_update (rigConnected/radioReady/radioHealth — radio.svelte.ts),
// never from an event that may simply never arrive. `wsConnected` gates the
// whole thing because rigConnected/radioReady/radioHealth are themselves
// only refreshed by state_update messages and go stale (not reset) the
// instant the transport drops — without this gate a real disconnect would
// keep showing the last-known-good facts, reproducing the same class of lie
// this fix removes.
// F4 (verifier review round 1): `radioState === 'connected'` now implies
// `rigConnected && radioReady` by construction, which silently starved
// MOR-620's "connected but not radio-ready (CI-V link degraded)" signal —
// StatusBar could never observe that combination to downgrade to
// 'degraded' anymore. Owner ruling: keep MOR-620's vocabulary alive by
// having the steady state emit 'degraded' itself, rather than retiring
// the distinction.
//
// F2 (verifier review round 2): the up-edge honesty guard is
// `factsObservedThisSession`, not a reset of rigConnected/radioReady/
// radioHealth themselves (see setWsConnected/setRigConnected above and the
// R2 comment in ws-client.ts) — those three must stay untouched by this
// display fix because isLiveRadioAvailable()/sendCommand() also read them
// for command-gating. Until a fresh state_update lands on the CURRENT
// transport session, the steady state can never claim 'connected' — at
// worst it reads 'degraded' off the pre-drop facts, never green.
let radioLinkSteady = $derived<'connected' | 'degraded' | 'disconnected'>(
  wsConnected
    && factsObservedThisSession
    && rigConnected
    && radioReady
    && radioHealth?.serverReachable !== false
    && (radioHealth == null || radioHealth.radioLink === 'connected')
    ? 'connected'
    : wsConnected && radioHealth?.serverReachable !== false && (!factsObservedThisSession || !rigConnected || !radioReady)
      ? 'degraded'
      : 'disconnected',
);

let radioLinkState = $derived<'connected' | 'connecting' | 'reconnecting' | 'degraded' | 'disconnected'>(
  radioStatus === 'connecting' || radioStatus === 'reconnecting' ? radioStatus : radioLinkSteady,
);

export function getRadioLinkState(): 'connected' | 'connecting' | 'reconnecting' | 'degraded' | 'disconnected' {
  return radioLinkState;
}

export function isLiveRadioAvailable(): boolean {
  if (!radioHealth) {
    return radioReady;
  }
  return (
    radioReady
    && radioHealth.serverReachable
    && radioHealth.radioLink === 'connected'
    && radioHealth.readiness === 'ready'
    && radioHealth.likelyCause === 'unknown'
  );
}
