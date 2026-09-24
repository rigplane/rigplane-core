import { assertSteadyFreshFixture, caps, frames, info, smallEvent, state } from './fixture';

const fixtureCheck = assertSteadyFreshFixture();
const errors: string[] = [];
const sent: unknown[] = [];
const api: string[] = [];
const startupWrites: { method: string; path: string; bodySha256: string; status: number }[] = [];
const sockets: Socket[] = [];
const accepted: { sequence: number; time: number }[] = [];
const arrivals: { kind: string; time: number }[] = [];
let running = false;
let sequence = 1;
let started = 0;
let timer: ReturnType<typeof setTimeout>;
let eventTimer: ReturnType<typeof setTimeout>;
const mode = new URLSearchParams(location.search).get('mode') ?? 'mounted';
localStorage.setItem('rigplane:workspace', JSON.stringify({ version: 1, layout: 'standard', designLanguage: 'studioline', theme: 'nord' }));
localStorage.setItem('rigplane.i18n.locale', 'en-US');

function fail(message: string) { errors.push(message); throw new Error(message); }
class Socket extends EventTarget {
  static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
  readyState = 0; binaryType = 'arraybuffer'; bufferedAmount = 0;
  onopen: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: ((e: Event) => void) | null = null;
  constructor(public url: string) {
    super();
    const parsed = new URL(url, location.href);
    if (parsed.host !== location.host || !['/api/v1/ws', '/api/v1/scope'].includes(parsed.pathname)) fail(`Unexpected socket ${parsed.pathname}`);
    sockets.push(this);
    setTimeout(() => {
      this.readyState = 1;
      const open = new Event('open'); this.dispatchEvent(open); this.onopen?.(open);
      if (parsed.pathname === '/api/v1/ws') setTimeout(() => this.emit(frames[0]), 0);
    }, 0);
  }
  emit(data: string) {
    if (this.readyState !== 1) return;
    const event = new MessageEvent('message', { data });
    this.dispatchEvent(event); this.onmessage?.(event);
  }
  send(data: string) {
    const message = JSON.parse(data); sent.push(message);
    if (message.type !== 'ping' && !(message.type === 'subscribe' && message.streams?.every((v: string) => v === 'events'))) fail(`Unexpected outgoing message ${message.type}`);
  }
  close() { this.readyState = 3; const event = new Event('close'); this.dispatchEvent(event); this.onclose?.(event); }
}
Object.assign(window, { WebSocket: Socket });
window.fetch = async (input, init) => {
  const url = new URL(input instanceof Request ? input.url : String(input), location.href);
  const method = init?.method ?? (input instanceof Request ? input.method : 'GET');
  api.push(`${method} ${url.pathname}`);
  if (url.origin !== location.origin || url.search) fail('Unexpected fetch origin/query');
  if (method === 'PUT' && url.pathname === '/api/local/v1/rc28/tuning-step' && !running) {
    if (typeof init?.body !== 'string') fail('Unexpected startup body type');
    const body = JSON.parse(init!.body as string);
    if (Object.keys(body).length !== 1 || !Number.isFinite(body.tuning_step_hz)) fail('Unexpected startup body');
    const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(init!.body as string));
    startupWrites.push({ method, path: url.pathname, bodySha256: [...new Uint8Array(hash)].map(b => b.toString(16).padStart(2, '0')).join(''), status: 404 });
    return new Response('{}', { status: 404 });
  }
  if (method !== 'GET') fail(`Unexpected fetch ${method} ${url.origin}${url.pathname}`);
  const fixtures: Record<string, unknown> = {
    '/api/local/v1/ui/manifest': { version: 1, host_api: '2.0', extensions: [] },
    '/api/v1/band-plan/layers': { layers: [] },
    '/api/v1/band-plan/config': { region: 'US', availableRegions: ['US'] },
    '/api/v1/info': info, '/api/v1/capabilities': caps, '/api/v1/state': state,
    '/api/v1/managed-transmit': { schemaVersion: 1, sampledAt: '2026-01-01T00:00:00Z',
      managedTransmit: { status: 'available', intent: { kind: 'rx' }, releaseRequired: false,
        lastError: null, lastActuation: null, abortErrors: [],
        tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null } },
      txObservation: { observedPtt: 'off' } },
  };
  if (!(url.pathname in fixtures)) fail(`Unexpected API ${url.pathname}`);
  return new Response(JSON.stringify(fixtures[url.pathname]), { headers: { 'Content-Type': 'application/json' } });
};
const radio = await import('../../src/lib/stores/radio.svelte');
radio.subscribeRadioState(next => {
  if (next) accepted.push({ sequence: next.observationSeq, time: performance.now() });
});
const snapshot = () => ({ mode, fixtureCheck, errors, sent, api, startupWrites, accepted, arrivals, running,
  fieldCount: Object.keys(state.fieldStatus!).length,
  observedFields: Object.values(state.fieldStatus!).filter(s => s.observed).length,
  deltaUnits: frames[1].length, fullUnits: frames[0].length,
  stateSequence: radio.getRadioState()?.observationSeq,
  ptt: radio.getRadioState()?.ptt,
  sockets: sockets.map(s => ({ path: new URL(s.url, location.href).pathname, readyState: s.readyState })),
});
function control() { return sockets.find(s => new URL(s.url, location.href).pathname === '/api/v1/ws')!; }
function emitState() {
  if (sequence >= frames.length) fail('Bounded stream exhausted');
  arrivals.push({ kind: 'state', time: performance.now() });
  control().emit(frames[sequence++]);
}
function stop() { running = false; clearTimeout(timer); clearTimeout(eventTimer); }
Object.assign(window, { replay: {
  snapshot, emitState, stop,
  start() {
    if (running) fail('Replay already running');
    running = true; started = performance.now();
    let states = 0; let events = 0;
    function stateTask() {
      if (!running) return;
      emitState(); states++;
      timer = setTimeout(stateTask, Math.max(0, started + (states + 1) * 1000 / 11 - performance.now()));
    }
    function eventTask() {
      if (!running) return;
      arrivals.push({ kind: 'event', time: performance.now() }); control().emit(smallEvent); events++;
      eventTimer = setTimeout(eventTask, Math.max(0, started + (events + 1) * 1000 / 14 - performance.now()));
    }
    timer = setTimeout(stateTask, 1000 / 11); eventTimer = setTimeout(eventTask, 1000 / 14);
  },
} });
if (mode === 'mounted') await import('../../src/main');
else if (mode === 'store-only') (await import('../../src/lib/transport/ws-client')).connect('/api/v1/ws');
else fail(`Unknown mode ${mode}`);
