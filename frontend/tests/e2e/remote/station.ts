import { expect, type Page } from '@playwright/test';

/**
 * The station both browsers' rows run against: the Core WebServer over
 * the IC-7610 LAN fake (tests/e2e/remote_web_server.py) plus the test
 * sidecar that exposes the simulator's observable state (keyed, frequency)
 * without any test hook inside src/.
 */

export const STATION_PORT = 18780;
export const STATION_ORIGIN = `http://127.0.0.1:${STATION_PORT}`;
const SIM_ORIGIN = `http://127.0.0.1:${STATION_PORT + 1}`;

export interface SimState {
  ptt_on: boolean;
  freq_hz: number;
  /** PTT transitions as [unix seconds, keyed]. */
  ptt_timeline: [number, boolean][];
}

/** The server's own view (``GET /api/v1/state`` from Node, not the page). */
export async function stationState(): Promise<{
  wsClients?: { control?: number };
}> {
  const response = await fetch(`${STATION_ORIGIN}/api/v1/state`);
  if (!response.ok) throw new Error(`/api/v1/state answered ${response.status}`);
  return (await response.json()) as { wsClients?: { control?: number } };
}

/** Wait until the UI's control link is up, read from the server's
 * wsClients count — DOM-free, so it holds in both layouts and browsers. */
export async function expectControlLink(): Promise<void> {
  await expect
    .poll(async () => (await stationState()).wsClients?.control ?? 0)
    .toBeGreaterThan(0);
}

/** The simulator's current observable state, from the sidecar. */
export async function simState(): Promise<SimState> {
  const response = await fetch(`${SIM_ORIGIN}/sim`);
  if (!response.ok) throw new Error(`sidecar /sim answered ${response.status}`);
  return (await response.json()) as SimState;
}

/** Change the frequency on the station side (silent; the radio's ~2 s
 * state-poll cadence carries it to the UI). */
export async function setStationFrequency(hz: number): Promise<void> {
  const response = await fetch(`${SIM_ORIGIN}/sim/freq`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ hz }),
  });
  if (!response.ok) throw new Error(`sidecar /sim/freq answered ${response.status}`);
}

/** The wall-clock time (ms) at which the simulator last keyed OFF at or
 * after `sinceMs` (unix seconds in the timeline are same-host wall clock);
 * rejects if it does not happen within `timeoutMs`. */
export async function simUnkeyedAt(
  sinceMs: number,
  timeoutMs = 10_000,
): Promise<number> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const state = await simState();
    const unkey = state.ptt_timeline
      .filter(([at, keyed]) => !keyed && at * 1000 >= sinceMs)
      .at(0);
    if (unkey) return unkey[0] * 1000;
    if (Date.now() > deadline) {
      throw new Error(`simulator did not unkey within ${timeoutMs} ms`);
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

/** Every request the page issued so far (all origins). */
export function trackRequests(page: Page): () => string[] {
  const urls: string[] = [];
  page.on('request', (request) => urls.push(request.url()));
  return () => urls;
}

/**
 * The ONLY allowed microphone stub (adviser brief §5): replace
 * navigator.mediaDevices.getUserMedia with an oscillator →
 * MediaStreamDestination stream, in page.addInitScript, so it works in
 * Chromium and WebKit alike. Everything after getUserMedia is the
 * production path.
 */
export async function stubMicrophone(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const media = navigator.mediaDevices;
    if (!media) throw new Error('navigator.mediaDevices is missing');
    media.getUserMedia = async () => {
      const context = new AudioContext();
      const oscillator = context.createOscillator();
      const destination = context.createMediaStreamDestination();
      oscillator.frequency.value = 1000;
      oscillator.connect(destination);
      oscillator.start();
      return destination.stream;
    };
  });
}

/** Open the ordinary UI page served by Core, with the microphone stub in
 * place (rows that never transmit are unaffected by it). */
export async function openRemoteUi(page: Page): Promise<void> {
  await stubMicrophone(page);
  await page.goto('/?ui=v2', { waitUntil: 'domcontentloaded' });
}

/** The frequency readout renders MHz.kHz.Hz digit groups; compare digits.
 * `timeoutMs` widens only the station→UI direction, whose freshness the
 * IC-7610 profile serves on a 2 s poll cadence (adaptive decay caps it at
 * 4 s, rigs/ic7610.toml [state_acquisition]). */
export async function expectReadoutHz(
  page: Page,
  hz: number,
  timeoutMs = 10_000,
): Promise<void> {
  const readout = page.locator('.receiver-instrument [data-vfo-freq]').first();
  await expect
    .poll(async () => Number(((await readout.textContent()) ?? '').replace(/\D/g, '')), {
      timeout: timeoutMs,
    })
    .toBe(hz);
}
