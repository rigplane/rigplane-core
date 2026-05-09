// Connection health state
import type { ServerState } from '../types/state';

type RadioHealth = NonNullable<ServerState['radioHealth']>;

let httpConnected = $state(false);
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

let isFullyConnected = $derived(httpConnected && wsConnected);
let overallConnected = $derived(wsConnected && audioConnected);
let audioAliveControlDead = $derived(audioConnected && !wsConnected);
let connectionStatus = $derived<'connected' | 'partial' | 'disconnected'>(
  isFullyConnected ? 'connected' : httpConnected || wsConnected ? 'partial' : 'disconnected',
);

export function setHttpConnected(v: boolean): void {
  httpConnected = v;
}

export function setWsConnected(v: boolean): void {
  wsConnected = v;
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

export function getHttpConnected(): boolean {
  return httpConnected;
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
