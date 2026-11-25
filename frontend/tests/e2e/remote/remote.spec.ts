import { existsSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test, type Page } from '@playwright/test';
import controllerWire from '../../../../tests/fixtures/host-api-v3-wire.json' with { type: 'json' };
import wire from '../../../src/lib/transport/api-channel-wire.json' with { type: 'json' };
import golden from '../../../src/lib/transport/api-channel-golden.json' with { type: 'json' };
import { parseCurrentScopeFrame } from '../../../src/lib/runtime/scope-frame';
import type { RemoteBootHarness } from './boot-entry';
import {
  expectControlLink,
  collectFirstTexts,
  expectReadoutHz,
  foreignDestinations,
  installPlaybackWitness,
  openRemoteUi,
  acquireControllerKey,
  countManagedCommandPosts,
  parseRelayedAnswer,
  playbackBuffers,
  setStationFrequency,
  simState,
  simUnkeyLatencyMs,
  STATION_ORIGIN,
  stationManagedTransmit,
  stationState,
  supportBundleLogCount,
  trackDestinations,
  type RelayedAnswer,
} from './station';

/**
 * The remote/browser E2E matrix (C5b PR-0, MOR-4493). Every row runs in
 * both projects (chromium, webkit) and is declared twice (repeatEach: 2 in
 * playwright.remote.config.ts); the no-skips reporter fails the run when a
 * row is skipped or missing. Frames and channel labels come from the
 * committed golden documents (api-channel-golden.json), never re-typed.
 */

const BASELINE_HZ = 14_074_000;
const TUNED_HZ = 7_100_000;
const STATION_HZ = 14_100_000;

async function tuneFromUi(page: Page, hz: number): Promise<void> {
  await page.locator('.receiver-instrument [data-vfo-freq]').first().click();
  await page.getByTestId('band-entry-input').fill((hz / 1_000_000).toFixed(3));
  await page.getByTestId('band-entry-set').click();
  await expect.poll(async () => (await simState()).freq_hz).toBe(hz);
  await expectReadoutHz(page, hz);
}

// The committed wire/golden contract documents are imported from the source
// of truth (src/lib/transport), never re-typed here.

/**
 * Validate control_hello directly against its wire-contract `fields` rows.
 * The harness supports the field kinds control_hello uses and throws on
 * any other kind so a future contract change cannot pass silently.
 */
function payloadMatchesWireContract(
  contract: { fields: unknown[][] },
  value: unknown,
): boolean {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  const kindOk = (kind: string, field: unknown): boolean => {
    if (kind === 'string') return typeof field === 'string';
    if (kind === 'integer') return typeof field === 'number' && Number.isInteger(field);
    if (kind === 'boolean') return typeof field === 'boolean';
    if (kind === 'array of strings')
      return Array.isArray(field) && field.every((item) => typeof item === 'string');
    if (kind.length > 2 && kind.startsWith('"') && kind.endsWith('"'))
      return field === kind.slice(1, -1);
    throw new Error(`wire contract kind not known to the harness: ${kind}`);
  };
  return contract.fields.every((row) => {
    const [name, kind] = row as [string, string];
    if (typeof name !== 'string' || typeof kind !== 'string') return false;
    return Object.hasOwn(record, name) ? kindOk(kind, record[name]) : row[2] !== true;
  });
}

test('L1: the ordinary page talks only to the station', async ({ page }) => {
  // Rows share one station: reset the simulator to the baseline frequency
  // so this row does not depend on what an earlier row tuned.
  await setStationFrequency(BASELINE_HZ);
  const destinations = trackDestinations(page);
  await openRemoteUi(page);

  // The UI is up: the control link connects and the baseline is shown.
  await expectControlLink();
  await expectReadoutHz(page, BASELINE_HZ);

  // HTTP requests and WebSockets (the UI's principal transport) alike.
  const { requests, sockets } = destinations();
  expect(sockets.length, 'the UI opened its WebSocket to the station').toBeGreaterThan(0);
  expect(
    foreignDestinations([...requests, ...sockets]),
    'requests or sockets to non-station origins',
  ).toEqual([]);
});

test('L2: tuning follows both ways between the UI and the simulator', async ({ page }) => {
  await setStationFrequency(BASELINE_HZ);
  await openRemoteUi(page);
  await expectControlLink();
  await expectReadoutHz(page, BASELINE_HZ);

  // UI → station: tune 7.100 MHz through the frequency entry dialog.
  await tuneFromUi(page, TUNED_HZ);

  // Station → UI: change the frequency on the station side; the simulator
  // holds it and the UI follows over the state poll.
  await setStationFrequency(STATION_HZ);
  await expect.poll(async () => (await simState()).freq_hz).toBe(STATION_HZ);
  await expectReadoutHz(page, STATION_HZ, 20_000);
});

test.describe('phone layout', () => {
  test.use({ viewport: { width: 430, height: 932 } });

  test('L3: holding PTT keys the simulator and releasing unkeys within 1 s', async ({ page }) => {
    await openRemoteUi(page);
    await expectControlLink();

    const ptt = page.getByRole('button', { name: 'Push to talk' });
    await expect(ptt).toBeVisible();

    // Press and hold: the page starts the (stubbed) microphone, then keys.
    // The fake radio keys whether or not its MOD input is LAN (the page
    // only shows its advisory "Set LAN" banner), so the row holds PTT
    // without touching the banner: with one mouse the pointer is captured
    // by the held button, so a second click cannot reach another control.
    await ptt.hover();
    const pressedAtMs = Date.now();
    await page.mouse.down();
    await expect.poll(async () => (await simState()).ptt_on).toBe(true);

    // MOR-4312: the page asked for RAW microphone capture (no browser voice
    // processing), and the captured tone reached the simulated radio through
    // the real page -> WebSocket -> Core -> radio path.
    const requested = await page.evaluate(
      () => (window as unknown as { __micConstraints?: Record<string, unknown> }).__micConstraints,
    );
    expect(requested).toMatchObject({
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
    });
    await expect
      .poll(async () => (await simState()).tx_audio_signal_frames, { timeout: 15_000 })
      .toBeGreaterThan(0);

    // Release: the simulator must report unkeyed within 1 s of the mouse
    // release (same-host wall clocks). The helper correlates the simulator's
    // own journal with this press/release and requires the final state OFF.
    const releasedAtMs = Date.now();
    await page.mouse.up();
    const latencyMs = await simUnkeyLatencyMs(pressedAtMs, releasedAtMs);
    expect(latencyMs).toBeLessThanOrEqual(1000);
  });
});

// Desktop layout (the config's 1728 x 1200 viewport). On a local-mode station
// the desktop key is the latching "Key transmitter" button (RxTxSurface with
// momentary = runtime.remoteController = false): a click keys, UNKEY unkeys.
test('L4: desktop PTT keys the simulator, UNKEY unkeys it within 1 s and RX audio resumes', async ({ page }) => {
  await installPlaybackWitness(page);
  const managedPosts = countManagedCommandPosts(page);
  await openRemoteUi(page);
  await expectControlLink();

  // RX audio is live first: decoded receive buffers reach the AudioContext.
  await page.getByTestId('rx-audio-monitor-live').click();
  await expect.poll(() => playbackBuffers(page), { timeout: 15_000 }).toBeGreaterThan(5);

  const key = page.getByTestId('rx-tx-key');
  const unkey = page.getByTestId('rx-tx-unkey');
  await expect(key).toBeEnabled();
  await expect(key).toHaveAttribute('aria-pressed', 'false');
  expect((await simState()).ptt_on).toBe(false);
  const signalFramesBefore = (await simState()).tx_audio_signal_frames;

  // Key: the simulator reports keyed and the page shows the key pressed.
  const keyedAtMs = Date.now();
  await key.click();
  await expect.poll(async () => (await simState()).ptt_on).toBe(true);
  await expect(key).toHaveAttribute('aria-pressed', 'true');
  // The (stubbed) microphone tone reaches the simulated radio.
  await expect
    .poll(async () => (await simState()).tx_audio_signal_frames, { timeout: 15_000 })
    .toBeGreaterThan(signalFramesBefore);
  // The POST counter saw this transmission (its positive control for L5).
  expect(managedPosts()).toBeGreaterThan(0);
  // No receive audio plays while the radio is keyed: across a fresh interval
  // in which more TX frames reach the simulator, no receive buffer is
  // scheduled for playback.
  const heldAtKeyed = await playbackBuffers(page);
  const signalFramesAtKeyed = (await simState()).tx_audio_signal_frames;
  await expect
    .poll(async () => (await simState()).tx_audio_signal_frames, { timeout: 15_000 })
    .toBeGreaterThan(signalFramesAtKeyed + 25);
  const heldAtUnkey = await playbackBuffers(page);
  expect(heldAtUnkey, 'receive playback must stay held while keyed').toBe(heldAtKeyed);

  // Unkey: the simulator's own journal shows OFF within 1 s of the click,
  // and this key/unkey left exactly one ON then one OFF in it.
  const unkeyedAtMs = Date.now();
  await unkey.click();
  const latencyMs = await simUnkeyLatencyMs(keyedAtMs, unkeyedAtMs);
  expect(latencyMs).toBeLessThanOrEqual(1000);
  const journal = (await simState()).ptt_transitions
    .filter(([at]) => at * 1000 >= keyedAtMs)
    .map(([, keyed]) => keyed);
  expect(journal).toEqual([true, false]);
  await expect(key).toHaveAttribute('aria-pressed', 'false');

  // RX resumes: new receive buffers reach the AudioContext after the unkey.
  await expect
    .poll(() => playbackBuffers(page), { timeout: 15_000 })
    .toBeGreaterThan(heldAtUnkey + 5);
});

// The station logs this line (web/handlers/audio.py AudioHandler
// ._handle_audio_stats) when the page's audio_stats report a refused
// microphone; the logs contributor carries it into the support bundle.
const MIC_REFUSAL_LOG = 'browser microphone capture rejected: category=NotAllowedError';

test('L5: a refused microphone never keys, stays on screen and reaches the support bundle', async ({ page }) => {
  await installPlaybackWitness(page);
  const managedPosts = countManagedCommandPosts(page);
  await openRemoteUi(page, 'refuse');
  await expectControlLink();

  // RX audio is live: the page reports to the station on the audio socket.
  await page.getByTestId('rx-audio-monitor-live').click();
  await expect.poll(() => playbackBuffers(page), { timeout: 15_000 }).toBeGreaterThan(0);

  const key = page.getByTestId('rx-tx-key');
  await expect(key).toBeEnabled();
  const bundleBefore = await supportBundleLogCount(MIC_REFUSAL_LOG);
  const journalBefore = (await simState()).ptt_transitions.length;
  const signalFramesBefore = (await simState()).tx_audio_signal_frames;

  await key.click();

  // On screen: an error toast that names the cause, and the status bar's
  // audio indicator reads failed.
  const toast = page.locator('.toast.error').filter({
    hasText: 'Microphone access denied — TX audio cannot start',
  });
  await expect(toast).toHaveCount(1);
  await expect(toast).toBeVisible();
  const audioChip = page.getByTestId('audio-chip');
  await expect(audioChip).toHaveAttribute('title', 'Audio connection failed');

  // Not half-keyed: no transmit request left the page; the simulator was
  // never keyed and received no TX audio; the station's managed TX stays in
  // receive with PTT observed off; the key is not shown pressed.
  expect(managedPosts()).toBe(0);
  const sim = await simState();
  expect(sim.ptt_on).toBe(false);
  expect(sim.ptt_transitions).toHaveLength(journalBefore);
  expect(sim.tx_audio_signal_frames).toBe(signalFramesBefore);
  const managed = await stationManagedTransmit();
  expect(managed.txObservation.observedPtt).toBe('off');
  expect(managed.managedTransmit).toMatchObject({ status: 'available', intent: { kind: 'rx' } });
  expect((await stationState()).ptt).not.toBe(true);
  await expect(key).toHaveAttribute('aria-pressed', 'false');

  // Not only a toast: once the operator dismisses the toast, the status bar
  // still reads failed.
  await toast.click();
  await expect(toast).toHaveCount(0);
  await expect(audioChip).toHaveAttribute('title', 'Audio connection failed');

  // Traced: the support bundle a diagnostics preview builds now carries the
  // refusal (one more occurrence than before this press; earlier rows and
  // repetitions share the station and its log).
  await expect
    .poll(() => supportBundleLogCount(MIC_REFUSAL_LOG), { timeout: 20_000, intervals: [1_000] })
    .toBeGreaterThan(bundleBefore);
});

// The Python API-channel tests replay both wire documents against Core:
// test_controller_route_ptt_style and test_api_tx_start_managed_transmit_refused.
test('L6: a local-mode station advertising momentary PTT shows a hold label and uses control-channel key and release', async ({ page }, testInfo) => {
  await page.route('**/api/v1/controller', async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    await route.fulfill({ json: controllerWire.controllerStatusApiChannel });
  });
  const managedPosts = countManagedCommandPosts(page);
  await openRemoteUi(page);
  await expectControlLink();
  const key = page.getByTestId('rx-tx-key');
  await expect(key.getByText('Hold to talk', { exact: true })).toBeVisible();
  await expect(key).toHaveAccessibleName('Hold to talk');
  await expect(key).toHaveAttribute('title', 'Hold to talk');
  await expect(key).toBeEnabled();
  await key.screenshot({ path: testInfo.outputPath('hold-to-talk.png') });
  await key.focus();
  try {
    await page.keyboard.down('Space');
    await expect.poll(async () => (await simState()).ptt_on).toBe(true);
  } finally {
    await page.keyboard.up('Space');
  }
  await expect.poll(async () => (await simState()).ptt_on).toBe(false);
  expect(managedPosts()).toBe(0);
});

test('L7: a refused latch explains why, logs its code and never shows TX', async ({ page }) => {
  const code = controllerWire.controllerTxRefusal.error;
  const diagnostic: string[] = [];
  page.on('console', (message) => {
    if (message.text().includes(code)) diagnostic.push(message.text());
  });
  await page.route('**/api/v1/managed-transmit/command', async (route) => {
    if (route.request().postDataJSON().operation !== 'transmit_on') return route.continue();
    await route.fulfill({ status: 403, json: controllerWire.controllerTxRefusal });
  });
  await openRemoteUi(page);
  await expectControlLink();
  const key = page.getByTestId('rx-tx-key');
  await expect(key).toHaveAttribute('aria-label', 'Key transmitter');
  const journalBefore = (await simState()).ptt_transitions.length;
  await key.evaluate((button) => {
    // Observe every change, including a transient optimistic pressed state.
    button.dataset.txPressedSeen = 'false';
    new MutationObserver((records) => {
      if (records.some(({ oldValue }) => oldValue === 'true') || button.getAttribute('aria-pressed') === 'true') {
        button.dataset.txPressedSeen = 'true';
      }
    }).observe(button, { attributeFilter: ['aria-pressed'], attributeOldValue: true });
  });
  await key.click();
  await expect(page.locator('.toast.error').filter({
    hasText: 'Hold PTT to transmit on this connection',
  })).toBeVisible();
  expect(diagnostic).not.toHaveLength(0);
  await expect(key).toHaveAttribute('aria-pressed', 'false');
  await expect(key).toHaveAttribute('data-tx-pressed-seen', 'false');
  const sim = await simState();
  expect(sim.ptt_on).toBe(false);
  expect(sim.ptt_transitions).toHaveLength(journalBefore);
});

test('L8: a missing microphone gets its own message and never submits a latch', async ({ page }) => {
  const managedPosts = countManagedCommandPosts(page);
  await openRemoteUi(page, 'missing');
  await expectControlLink();
  const key = page.getByTestId('rx-tx-key');
  await key.click();
  await expect(page.locator('.toast.error')).toContainText('No microphone found');
  await expect(key).toHaveAttribute('aria-pressed', 'false');
  expect(managedPosts()).toBe(0);
});

// The six data-channel labels, their ordering/reliability and which frames
// each carries come from the committed wire contract's K5 channel set
// (`data_channels`, producer: rigplane.web.channel_attach.DATA_CHANNELS):
// control and api are one reliable channel each; scope and audio are a
// lossy binary channel plus a reliable ordered `*-text` companion that
// carries their text frames (the attach included). The readback in the row
// pins the contract against the negotiated channels. maxRetransmits is null
// where the browser default (reliable) applies.
interface ChannelSpec {
  label: string;
  ordered: boolean;
  maxRetransmits: number | null;
}

const CHANNELS: readonly ChannelSpec[] = wire.data_channels.map((spec) => ({
  label: spec.label,
  ordered: spec.ordered,
  maxRetransmits: spec.max_retransmits,
}));

/** Kind -> the label that carries its text frames (and so its attach). */
const ATTACH_LABEL: Record<string, string> = Object.fromEntries(
  wire.data_channels
    .filter((spec) => spec.carries !== 'binary')
    .map((spec) => [spec.kind, spec.label]),
);

// The golden documents the harness reads are the ones with frame text and a
// decoded form; the file also holds request/response documents of other shapes.
const ATTACH_DOCUMENTS = (
  golden as unknown as {
    documents: Record<string, { text: string; decoded: Record<string, unknown> }>;
  }
).documents;

/** The attach frame text per attach-carrying label, from the golden
 * documents keyed attach_<kind>; their <SESSION-KEY> placeholder takes the
 * live key. */
function attachTexts(): Record<string, string> {
  const texts: Record<string, string> = {};
  for (const [kind, label] of Object.entries(ATTACH_LABEL)) {
    const document = ATTACH_DOCUMENTS[`attach_${kind}`];
    if (!document) throw new Error(`no attach_${kind} golden document`);
    texts[label] = document.text;
  }
  return texts;
}

async function exposeOfferRelay(page: Page): Promise<void> {
  await page.exposeFunction('__relayOffer', async (sdp: string, type: string) => {
    const response = await fetch(`${STATION_ORIGIN}/api/v1/transport/webrtc/offer`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ sdp, type }),
    });
    return parseRelayedAnswer(await response.json());
  });
}

test('P0: a bare page connects the K5 data channels and reaches control_hello', async ({ page }) => {
  // A bare page on the station's origin (a secure context; about:blank is
  // not one in WebKit, where WebRTC then gathers nothing). Every page
  // request is intercepted and answered locally, so the page itself never
  // talks to the station over HTTP: the Node test relays the offer and the
  // controller-key acquisition.
  await page.route('**/*', (route) =>
    route.fulfill({
      contentType: 'text/html',
      body: '<!doctype html><html><head><title>bare-page</title></head></html>',
    }),
  );
  const navigation = await page.goto(`${STATION_ORIGIN}/bare`);
  // The fulfilled document, not Core's static-file 404: bypassing the
  // interception fails the row here.
  expect(navigation?.status()).toBe(200);
  expect(await page.title()).toBe('bare-page');
  await exposeOfferRelay(page);
  await page.exposeFunction('__controllerKey', acquireControllerKey);

  const outcome = await page.evaluate(
    async ({ channels, attachText, collectSource }) => {
      const relayWindow = window as typeof window & {
        __relayOffer: (sdp: string, type: string) => Promise<RelayedAnswer>;
        __controllerKey: () => Promise<string>;
      };
      const pc = new RTCPeerConnection();
      const created: Record<string, RTCDataChannel> = {};
      for (const channel of channels) {
        created[channel.label] =
          channel.maxRetransmits === null
            ? pc.createDataChannel(channel.label, { ordered: channel.ordered })
            : pc.createDataChannel(channel.label, {
                ordered: channel.ordered,
                maxRetransmits: channel.maxRetransmits,
              });
      }
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      // Wait for ICE gathering to complete: no trickle.
      let candidateCount = 0;
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {
          reject(
            new Error(
              `ICE gathering did not complete (state=${pc.iceGatheringState}, candidates=${candidateCount})`,
            ),
          );
        }, 10_000);
        const done = () => {
          clearTimeout(timer);
          resolve();
        };
        pc.addEventListener('icecandidate', (event) => {
          if (event.candidate) candidateCount += 1;
        });
        if (pc.iceGatheringState === 'complete') done();
        else
          pc.addEventListener('icegatheringstatechange', () => {
            if (pc.iceGatheringState === 'complete') done();
          });
      });
      const answer = await relayWindow.__relayOffer(
        pc.localDescription!.sdp,
        pc.localDescription!.type,
      );
      if (!answer || answer.status !== 'ok') {
        throw new Error(`offer refused: ${JSON.stringify(answer)}`);
      }
      await pc.setRemoteDescription({ sdp: answer.sdp, type: answer.type });
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(
          () => reject(new Error('data channels did not open')),
          15_000,
        );
        const check = () => {
          if (Object.values(created).every((channel) => channel.readyState === 'open')) {
            clearTimeout(timer);
            resolve();
          }
        };
        Object.values(created).forEach((channel) => {
          channel.onopen = check;
        });
        check();
      });
      // Every kind attaches with its FIRST text frame (the golden
      // document) on its text-carrying channel, all sent once right after
      // open (never resent) — the server closes a channel whose attach
      // lands more than 5 s after dispatch.
      const key = await relayWindow.__controllerKey();
      // Receive listeners for all three acknowledgement channels exist
      // before any attach goes out (the channels keep no order between
      // each other, so scope/audio may answer before control_hello); then
      // await them together.
      const collect = new Function(`return ${collectSource}`)() as typeof collectFirstTexts;
      const frames = await collect(
        { control: created.control, 'scope-text': created['scope-text'], 'audio-text': created['audio-text'] },
        () => {
          for (const [label, text] of Object.entries(attachText)) {
            created[label].send(text.replaceAll('<SESSION-KEY>', key));
          }
        },
        { control: 10_000, 'scope-text': 5_000, 'audio-text': 5_000 },
      );
      const readback = channels.map(({ label }) => ({
        label: created[label].label,
        ordered: created[label].ordered,
        maxRetransmits: created[label].maxRetransmits,
      }));
      const result = {
        hello: frames.control!,
        scopeAck: frames['scope-text']!,
        audioAck: frames['audio-text']!,
        channels: readback,
      };
      pc.close();
      return result;
    },
    { channels: CHANNELS, attachText: attachTexts(), collectSource: collectFirstTexts.toString() },
  );

  // The negotiated channels carry the pinned labels/ordering/reliability.
  expect(outcome.channels).toEqual(CHANNELS.map((channel) => ({ ...channel })));
  // The control channel delivered the server's control_hello, validated
  // against the committed wire contract (payloads.control_hello).
  const hello = JSON.parse(outcome.hello) as Record<string, unknown>;
  expect(hello.type).toBe('hello');
  expect(payloadMatchesWireContract(wire.payloads.control_hello, hello)).toBe(true);
  expect(hello.radio).toBe('IC-7610');
  // The scope/audio text channels' first frames are their attach acks,
  // exactly the golden documents.
  expect(JSON.parse(outcome.scopeAck)).toEqual(ATTACH_DOCUMENTS.attach_ack_scope.decoded);
  expect(JSON.parse(outcome.audioAck)).toEqual(ATTACH_DOCUMENTS.attach_ack_audio.decoded);
});

declare global {
  interface Window {
    __remoteBoot: RemoteBootHarness;
  }
}

async function buildRemoteShell(): Promise<void> {
  // The matrix runs serially. Its run directory is shared by both browsers
  // and repetitions, so build the shell once after the ordinary UI build.
  const logDir = process.env.RIGPLANE_REMOTE_E2E_LOG_DIR;
  if (!logDir) throw new Error('The remote matrix did not allocate a run directory');
  const built = path.join(logDir, 'remote-shell-built');
  if (existsSync(built)) return;
  const { build } = await import('vite');
  const frontend = fileURLToPath(new URL('../../../', import.meta.url));
  await build({
    root: frontend,
    configFile: path.join(frontend, 'vite.config.ts'),
    base: '/remote-entry/',
    publicDir: false,
    build: {
      outDir: 'dist/remote-entry',
      emptyOutDir: true,
      rollupOptions: {
        input: path.join(frontend, 'tests/e2e/remote/boot-entry.ts'),
        output: { entryFileNames: 'boot-entry.js' },
      },
    },
  });
  writeFileSync(built, '');
}

async function showRadioScope(page: Page): Promise<void> {
  // The scope canvas sends maxFps: 0 when it is outside the viewport or its tab is hidden.
  await page.bringToFront();
  const panel = page.locator('[data-waterfall]:not(.audio-fft)');
  const viewer = panel.getByTitle('Request scope viewer data');
  // The desktop-v2 skin hosts the demand toggle inside the toolbar's
  // [MORE ▾] panel (MOR-2545); the unhosted faces render it in the row.
  const more = panel.getByTestId('scope-more');
  await expect.poll(async () => (await viewer.count()) + (await more.count()), {
    message: 'a scope demand toggle renders',
  }).toBeGreaterThan(0);
  if ((await viewer.count()) === 0) await more.click();
  if (await viewer.getAttribute('aria-pressed') === 'false') await viewer.click();
  await panel.locator('.spectrum-area').scrollIntoViewIfNeeded();
  await expect.poll(async () => {
    const encoded = await panel.getAttribute('data-scope-subscription');
    const request = encoded === null ? null : JSON.parse(encoded);
    return request?.sourceId === 'radio' ? request.maxFps : 0;
  }, { message: 'the visible radio scope asks for frames' }).toBeGreaterThan(0);
}

test('R1: the station UI and K5 boot operate the same simulator', async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  await buildRemoteShell();
  const wsPage = await page.context().newPage();
  await installPlaybackWitness(wsPage);
  await setStationFrequency(BASELINE_HZ);

  let wsScopeFrames = 0;
  const wsScopeTraffic = {
    sockets: 0,
    lastSubscribe: null as string | null,
    lastReply: null as string | null,
    lastBinary: null as string | null,
  };
  wsPage.on('websocket', (socket) => {
    if (new URL(socket.url()).pathname !== '/api/v1/scope') return;
    wsScopeTraffic.sockets += 1;
    socket.on('framesent', ({ payload }) => {
      if (typeof payload === 'string' && /"type"\s*:\s*"subscribe"/.test(payload)) {
        wsScopeTraffic.lastSubscribe = payload;
      }
    });
    socket.on('framereceived', ({ payload }) => {
      if (typeof payload === 'string') {
        if (/"type"\s*:\s*"(?:granted|error)"/.test(payload)) wsScopeTraffic.lastReply = payload;
        return;
      }
      const frame = parseCurrentScopeFrame(Uint8Array.from(payload).buffer, { source: 'radio', sourceId: 'radio', epoch: 0 });
      if (!frame.ok) {
        wsScopeTraffic.lastBinary = frame.reason;
        return;
      }
      const { source, pixels } = frame.frame;
      wsScopeTraffic.lastBinary = `${source}, ${pixels?.length ?? 0} pixels`;
      if (source === 'radio' && pixels !== null) wsScopeFrames += 1;
    });
  });
  await Promise.all([
    wsPage.waitForResponse((response) =>
      new URL(response.url()).pathname === '/api/v1/controller' &&
      response.request().method() === 'POST' && response.status() === 201,
    ),
    openRemoteUi(wsPage),
  ]);
  await expectControlLink();
  await expectReadoutHz(wsPage, BASELINE_HZ);
  try {
    await showRadioScope(wsPage);
    await expect.poll(() => wsScopeFrames, {
      message: 'the station WebSocket delivers a radio scope frame with pixels', timeout: 15_000,
    }).toBeGreaterThan(0);
  } catch (error) {
    await testInfo.attach('station scope WebSocket traffic', {
      body: JSON.stringify(wsScopeTraffic, null, 2), contentType: 'application/json',
    });
    throw error;
  }
  const wsInfo = await wsPage.evaluate(async () => {
    const response = await fetch('/api/v1/info');
    const { server, model, proto, version } = await response.json();
    return { status: response.status, server, model, proto, version };
  });
  expect(wsInfo).toMatchObject({
    status: 200, server: 'rigplane', model: expect.any(String), proto: expect.any(Number), version: expect.any(String),
  });
  await wsPage.getByTestId('rx-audio-monitor-live').click();
  await expect.poll(() => playbackBuffers(wsPage), { timeout: 15_000 }).toBeGreaterThan(5);
  await wsPage.getByTestId('rx-audio-monitor-mute').click();
  await tuneFromUi(wsPage, TUNED_HZ);
  await wsPage.close();

  await installPlaybackWitness(page);
  await exposeOfferRelay(page);
  const shell = `${STATION_ORIGIN}/remote-entry/index.html?ui=v2`;
  await page.route(shell, (route) => route.fulfill({
    contentType: 'text/html',
    body: '<!doctype html><html lang="en"><div id="app"></div><script type="module" src="/remote-entry/boot-entry.js"></script></html>',
  }));
  await page.route('**/api/v1/**', (route) => route.abort());
  const destinations = trackDestinations(page);
  expect((await page.goto(shell, { waitUntil: 'domcontentloaded' }))?.status()).toBe(200);
  await expect.poll(async () => (await stationState()).wsClients?.control).toBe(0);
  await setStationFrequency(BASELINE_HZ);

  const snapshot = () => page.evaluate(() => window.__remoteBoot.snapshot());
  const snapshotLink = async (link: number) => (await snapshot()).links[link]!;
  const expectRetired = async (link: number) => {
    await expect.poll(async () => (await snapshotLink(link)).channels
      .filter(({ state, coreHandlers }) => state !== 'closed' || coreHandlers)
      .map(({ label, state, coreHandlers }) => ({ label, state, coreHandlers }))).toEqual([]);
  };
  await page.evaluate(() => window.__remoteBoot.start());
  await expectReadoutHz(page, BASELINE_HZ);
  // A real station ACK proves that Core acquired a grant via the API and attached with it.
  await expect.poll(async () => (await snapshotLink(0)).heartbeats).toBeGreaterThan(0);
  const k5Info = await page.evaluate(() => window.__remoteBoot.info());
  expect(k5Info).toEqual(wsInfo);
  await showRadioScope(page);
  await expect.poll(async () => (await snapshotLink(0)).scopeFrames, {
    message: 'the K5 scope data channel delivers a radio scope frame with pixels', timeout: 15_000,
  }).toBeGreaterThan(0);
  await page.getByTestId('rx-audio-monitor-live').click();
  await expect.poll(() => playbackBuffers(page), { timeout: 15_000 }).toBeGreaterThan(5);
  await expect.poll(async () => {
    const { channels } = await snapshotLink(0);
    return channels.filter(({ state, frames }) => state === 'open' && frames > 0)
      .map(({ label, ordered, maxRetransmits }) => ({ label, ordered, maxRetransmits }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }).toEqual([...CHANNELS].sort((a, b) => a.label.localeCompare(b.label)));
  await tuneFromUi(page, TUNED_HZ);

  // Close a different half of each pair. Core must retire both halves and keep control live.
  await page.evaluate(() => window.__remoteBoot.halfClose());
  await expect.poll(async () => {
    const media = (await snapshotLink(0)).channels.filter(({ label }) => label !== 'api' && label !== 'control');
    return {
      labels: [...new Set(media.map(({ label }) => label))].sort(),
      remaining: media.filter(({ state, coreHandlers }) => state !== 'closed' || coreHandlers),
    };
  }).toEqual({ labels: ['audio', 'audio-text', 'scope', 'scope-text'], remaining: [] });
  await tuneFromUi(page, STATION_HZ);
  expect((await snapshotLink(0)).losses).toEqual([]);

  // Core still sends heartbeats. The harness withholds only their ACKs at the receiver.
  await page.evaluate(() => window.__remoteBoot.withholdHeartbeatAcks());
  await expect.poll(async () => (await snapshotLink(0)).losses, { timeout: 12_000 })
    .toEqual(['session_lost']);
  expect((await snapshotLink(0)).withheldHeartbeats).toBeGreaterThan(0);
  await expectRetired(0);
  const root = page.locator('#app');
  await expect(root).toBeEmpty();

  await setStationFrequency(BASELINE_HZ);
  // The host supplies two fresh offers and invokes boot twice in one browser task.
  await page.evaluate(() => window.__remoteBoot.replaceImmediately());
  await expectRetired(1);
  expect((await snapshotLink(1)).channels.map(({ label }) => label)).toEqual(['api']);
  await expectReadoutHz(page, BASELINE_HZ);
  await expect.poll(async () => (await snapshotLink(2)).heartbeats).toBeGreaterThan(0);
  await expect.poll(async () => (await stationState()).wsClients?.control).toBe(1);
  await expect(root.locator('.radio-layout')).toHaveCount(1);
  await tuneFromUi(page, TUNED_HZ);
  expect((await snapshot()).links.map(({ losses }) => losses)).toEqual([['session_lost'], [], []]);
  expect(destinations().sockets).toEqual([]);
  expect(destinations().requests.filter((url) => new URL(url).pathname.startsWith('/api/v1/'))).toEqual([]);
});
