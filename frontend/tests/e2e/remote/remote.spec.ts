import { expect, test } from '@playwright/test';
import wire from '../../../src/lib/transport/api-channel-wire.json' with { type: 'json' };
import golden from '../../../src/lib/transport/api-channel-golden.json' with { type: 'json' };
import {
  expectControlLink,
  collectFirstTexts,
  expectReadoutHz,
  foreignDestinations,
  installPlaybackWitness,
  openRemoteUi,
  acquireControllerKey,
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
  trackSentTraffic,
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

// The committed wire/golden contract documents are imported from the source
// of truth (src/lib/transport), never re-typed here.

/**
 * Validate a payload against a wire-contract payload's `fields` rows, the
 * same semantics as the production `payloadMatchesContract`
 * (src/lib/transport/datachannel-transport.ts), which Playwright's Node
 * runtime cannot import and which is not re-exported without a src/ change (its JSON import needs an ESM import attribute
 * that would mean changing src/). Restricted on purpose to the kinds the
 * control_hello contract uses; any other kind row is an error, so a future
 * contract change cannot pass silently.
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
  const readout = page.locator('.receiver-instrument [data-vfo-freq]').first();
  await readout.click();
  await page.getByTestId('band-entry-input').fill('7.100');
  await page.getByTestId('band-entry-set').click();
  await expect.poll(async () => (await simState()).freq_hz).toBe(TUNED_HZ);
  await expectReadoutHz(page, TUNED_HZ);

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
  // No receive audio plays while the radio is keyed: no receive buffer is
  // scheduled for playback between these two reads.
  const heldAtKeyed = await playbackBuffers(page);
  await expect.poll(async () => (await simState()).tx_audio_signal_frames).toBeGreaterThan(signalFramesBefore + 5);
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
  await openRemoteUi(page, 'refuse');
  await expectControlLink();
  const sent = trackSentTraffic(page);

  // RX audio is live: the page reports to the station on the audio socket.
  await page.getByTestId('rx-audio-monitor-live').click();
  await expect.poll(() => playbackBuffers(page), { timeout: 15_000 }).toBeGreaterThan(0);

  const key = page.getByTestId('rx-tx-key');
  await expect(key).toBeEnabled();
  const bundleBefore = await supportBundleLogCount(MIC_REFUSAL_LOG);
  const journalBefore = (await simState()).ptt_transitions.length;

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

  // Not half-keyed: no transmit request, no ptt_on and no TX audio start left
  // the page; the simulator was never keyed; the station's managed TX stays in
  // receive with PTT observed off; the key is not shown pressed.
  expect(sent().managedCommands).toEqual([]);
  expect(sent().commands.filter((name) => name === 'ptt_on')).toEqual([]);
  expect(sent().audioStarts.filter((direction) => direction === 'tx')).toEqual([]);
  const sim = await simState();
  expect(sim.ptt_on).toBe(false);
  expect(sim.ptt_transitions).toHaveLength(journalBefore);
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
  await page.exposeFunction('__relayOffer', async (sdp: string, type: string) => {
    const response = await fetch(`${STATION_ORIGIN}/api/v1/transport/webrtc/offer`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ sdp, type }),
    });
    return parseRelayedAnswer(await response.json());
  });
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
