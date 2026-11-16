import { expect, test, type Page } from '@playwright/test';
import wire from '../../../src/lib/transport/api-channel-wire.json' with { type: 'json' };
import golden from '../../../src/lib/transport/api-channel-golden.json' with { type: 'json' };
import {
  expectControlLink,
  collectFirstTexts,
  expectReadoutHz,
  foreignDestinations,
  openRemoteUi,
  acquireControllerKey,
  parseRelayedAnswer,
  setStationFrequency,
  simState,
  simUnkeyLatencyMs,
  stationState,
  stubMicrophone,
  STATION_ORIGIN,
  trackDestinations,
  type RelayedAnswer,
} from './station';
import type { RecordedChannel } from './boot-entry';

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

/** Expose the Node-side offer relay the P0 probe and the R-A1 boot entry
 * both use: the station's real WebRTC endpoint answers it; the relay (not
 * a faked server) is the one signalling fake the harness allows. */
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

    // Release: the simulator must report unkeyed within 1 s of the mouse
    // release (same-host wall clocks). The helper correlates the simulator's
    // own journal with this press/release and requires the final state OFF.
    const releasedAtMs = Date.now();
    await page.mouse.up();
    const latencyMs = await simUnkeyLatencyMs(pressedAtMs, releasedAtMs);
    expect(latencyMs).toBeLessThanOrEqual(1000);
  });
});

// The four data-channel labels come from the committed golden documents
// (the attach_api/attach_scope/... document set); the ordered/lossy split
// mirrors the production factories the answerer serves
// (src/rigplane/web/transport/webrtc.py: add_control_channel and
// add_api_channel are ordered+reliable, _add_lossy_channel is unordered
// with maxRetransmits=0). The readback in the row pins the table against
// the negotiated channels. maxRetransmits is null where the browser
// default (reliable) applies.
interface ChannelSpec {
  label: string;
  ordered: boolean;
  maxRetransmits: number | null;
}

const CHANNELS: readonly ChannelSpec[] = [
  { label: 'control', ordered: true, maxRetransmits: null },
  { label: 'scope', ordered: false, maxRetransmits: 0 },
  { label: 'audio', ordered: false, maxRetransmits: 0 },
  { label: 'api', ordered: true, maxRetransmits: null },
] as const;

// The golden documents the harness reads are the ones with frame text and a
// decoded form; the file also holds request/response documents of other shapes.
const ATTACH_DOCUMENTS = (
  golden as unknown as {
    documents: Record<string, { text: string; decoded: Record<string, unknown> }>;
  }
).documents;

/** The attach frame texts for the four labels, from the golden documents
 * keyed attach_<label>; their <SESSION-KEY> placeholder takes the live key. */
function attachTexts(): Record<string, string> {
  const texts: Record<string, string> = {};
  for (const { label } of CHANNELS) {
    const document = ATTACH_DOCUMENTS[`attach_${label}`];
    if (!document) throw new Error(`no attach_${label} golden document`);
    texts[label] = document.text;
  }
  return texts;
}

test('P0: a bare page connects four data channels and reaches control_hello', async ({ page }) => {
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
      // Every channel attaches as its FIRST text frame (the golden
      // document), all sent at once right after open — the server closes
      // a channel whose attach lands more than 5 s after dispatch.
      const key = await relayWindow.__controllerKey();
      // Receive listeners for all three acknowledgement channels exist
      // before any attach goes out (scope/audio are unordered and can
      // answer before control_hello); then await them together.
      const collect = new Function(`return ${collectSource}`)() as typeof collectFirstTexts;
      const frames = await collect(
        { control: created.control, scope: created.scope, audio: created.audio },
        () => {
          for (const { label } of channels) {
            created[label].send(attachText[label].replaceAll('<SESSION-KEY>', key));
          }
        },
        { control: 10_000, scope: 5_000, audio: 5_000 },
      );
      const readback = channels.map(({ label }) => ({
        label: created[label].label,
        ordered: created[label].ordered,
        maxRetransmits: created[label].maxRetransmits,
      }));
      const result = {
        hello: frames.control!,
        scopeAck: frames.scope!,
        audioAck: frames.audio!,
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
  // The lossy channels' first frames are their attach acks, exactly the
  // golden documents.
  expect(JSON.parse(outcome.scopeAck)).toEqual(ATTACH_DOCUMENTS.attach_ack_scope.decoded);
  expect(JSON.parse(outcome.audioAck)).toEqual(ATTACH_DOCUMENTS.attach_ack_audio.decoded);
});

/** The R-A1 boot entry's exposed surface (boot-entry.ts), as the Node side
 * reads it back from the page. */
interface EntryWindow {
  __bootRemote?: (channels: readonly ChannelSpec[]) => Promise<RecordedChannel[]>;
  __remoteSnapshot?: () => {
    channels: RecordedChannel[];
    linkLost: string[];
    loads: number;
    pcState: string;
  };
  __remoteHandle?: { shutdown(): void };
  __apiFetch?: (path: string, init?: RequestInit) => Promise<Response>;
}

test('R-A1: remote boot, real seam — the UI runs over bootRemote\'s link', async ({ page }) => {
  // The full row is longer than the matrix default: boot (ICE + kit
  // activation + control attach), a deliberate 6 s wait, two RX-audio
  // cycles, a scope release/reacquire and shutdown.
  test.setTimeout(150_000);
  await setStationFrequency(BASELINE_HZ);
  // The two allowed fakes only: the mic stub and the offer relay. The page
  // itself, the peer connection and every channel are the page's own.
  await stubMicrophone(page);
  await exposeOfferRelay(page);
  await page.goto(`${STATION_ORIGIN}/remote-entry/index.html`, { waitUntil: 'domcontentloaded' });
  // Any reload of this page destroys the injected link (PR-B's must-not);
  // the row counts main-frame navigations from here on.
  let navigations = 0;
  page.on('framenavigated', (frame) => {
    if (frame === page.mainFrame()) navigations += 1;
  });

  const entry = () => page.evaluate(() => (window as typeof window & EntryWindow).__remoteSnapshot!());
  const channelsOf = async (label: string): Promise<RecordedChannel[]> =>
    (await entry()).channels.filter((channel) => channel.label === label);
  const liveChannels = async (label: string): Promise<RecordedChannel[]> =>
    (await channelsOf(label)).filter((channel) => channel.opened && !channel.closed);

  // Boot: the page creates the peer and the api channel, relays the offer,
  // then calls the production bootRemote over them.
  await page.evaluate(
    (channels) => (window as typeof window & EntryWindow).__bootRemote!(channels),
    [...CHANNELS],
  );

  // 1. Bootstrap over the seam: the UI attached control through the link
  // (the server counts a control client) and shows the server's frequency.
  await expectControlLink();
  await expect.poll(async () => (await liveChannels('control')).length).toBeGreaterThan(0);
  const serverHz = (await simState()).freq_hz;
  await expectReadoutHz(page, serverHz);

  // 2. One user command end to end: tune through the frequency entry
  // dialog; the simulator's state (not the UI) is the witness.
  const readout = page.locator('.receiver-instrument [data-vfo-freq]').first();
  await readout.click();
  await page.getByTestId('band-entry-input').fill('7.100');
  await page.getByTestId('band-entry-set').click();
  await expect.poll(async () => (await simState()).freq_hz).toBe(TUNED_HZ);
  await expectReadoutHz(page, TUNED_HZ);

  // 3. The audio channel is created ON DEMAND: after 6 s with RX audio off
  // — longer than the server's 5 s attach deadline counted from a channel
  // reaching it — no audio channel exists yet. Enabling RX LIVE must then
  // still attach a fresh channel and receive audio frames on it.
  await page.waitForTimeout(6_000); // the deliberate wait; not synchronisation
  expect(await channelsOf('audio'), 'no audio channel while RX audio is off').toHaveLength(0);
  await page.getByTestId('rx-audio-monitor-live').click();
  await expect(page.getByTestId('rx-audio-monitor')).toHaveAttribute('data-monitor-mode', 'live');
  await expect
    .poll(async () => ((await channelsOf('audio')).at(0)?.binaryFrames ?? -1), {
      timeout: 15_000,
    })
    .toBeGreaterThan(0);
  // The server's audio broadcaster served that client (wsClients.audio
  // counts the audio subscriptions the attached channel made).
  await expect
    .poll(async () => (await stationState()).wsClients?.audio ?? 0)
    .toBeGreaterThan(0);

  // 4. RX audio off closes the channel; on again is a NEW channel that
  // also receives frames. The page recorded two audio channels, the first
  // closed; the server's audio client count went 1 -> 0 -> 1.
  await page.getByTestId('rx-audio-monitor-mute').click();
  await expect(page.getByTestId('rx-audio-monitor')).toHaveAttribute('data-monitor-mode', 'mute');
  await expect
    .poll(async () => (await channelsOf('audio')).filter((c) => c.closed).length)
    .toBeGreaterThanOrEqual(1);
  await expect.poll(async () => (await stationState()).wsClients?.audio ?? -1).toBe(0);
  await page.getByTestId('rx-audio-monitor-live').click();
  await expect
    .poll(async () => {
      const audio = await channelsOf('audio');
      return audio.length >= 2 && audio[0]!.closed && (audio.at(-1)?.binaryFrames ?? 0) > 0
        ? audio.length
        : -1;
    }, { timeout: 15_000 })
    .toBeGreaterThanOrEqual(2);
  await expect.poll(async () => (await stationState()).wsClients?.audio ?? -1).toBe(1);
  const audioChannels = await channelsOf('audio');
  expect(audioChannels.map((c) => c.closed)).toEqual([true, false]);

  // Same for scope: the boot-opened scope channel serves, the VIEW toggle
  // releases it (channel closed, the server's scope handler gone), and
  // re-enabling opens a second scope channel.
  await expect
    .poll(async () => ((await liveChannels('scope')).length > 0 && ((await stationState()).wsClients?.scope ?? 0) > 0))
    .toBe(true);
  const viewToggle = page.locator('.scope-demand-toggle').first();
  await expect(viewToggle).toHaveAttribute('aria-pressed', 'true');
  await viewToggle.click();
  await expect(viewToggle).toHaveAttribute('aria-pressed', 'false');
  await expect
    .poll(async () => (await channelsOf('scope')).filter((c) => c.closed).length)
    .toBeGreaterThanOrEqual(1);
  await expect.poll(async () => (await stationState()).wsClients?.scope ?? -1).toBe(0);
  await viewToggle.click();
  await expect
    .poll(async () => {
      const scope = await channelsOf('scope');
      return scope.length >= 2 && scope[0]!.closed && scope.at(-1)!.opened && !scope.at(-1)!.closed
        ? scope.length
        : -1;
    })
    .toBeGreaterThanOrEqual(2);
  await expect.poll(async () => (await stationState()).wsClients?.scope ?? -1).toBe(1);
  const scopeChannels = await channelsOf('scope');
  expect(scopeChannels.at(-1)!.closed, 'the reacquired scope channel is open').toBe(false);

  // 5. shutdown(): a pending apiFetch (issued, its HEAD one microtask
  // away) and a new one both reject; every channel the UI opened — api,
  // control, scope, both audio channels — is closed on the page side; the
  // App is unmounted.
  const rejections = await page.evaluate(async () => {
    const w = window as typeof window & EntryWindow;
    const describe = (error: unknown) => ({
      name: error instanceof Error ? error.name : typeof error,
      message: error instanceof Error ? error.message : String(error),
    });
    const outcome = (promise: Promise<Response>) =>
      promise.then(
        (response) => ({ rejected: false as const, status: response.status }),
        (error: unknown) => ({ rejected: true as const, ...describe(error) }),
      );
    const pending = w.__apiFetch!('/api/v1/state'); // still pending: unsettled
    w.__remoteHandle!.shutdown(); // the same task: it is pending AT shutdown
    return { pending: await outcome(pending), fresh: await outcome(w.__apiFetch!('/api/v1/state')) };
  });
  expect(rejections.pending.rejected, 'the pending apiFetch rejects').toBe(true);
  expect(rejections.fresh.rejected, 'a new apiFetch after shutdown rejects').toBe(true);
  await expect
    .poll(async () => {
      const { channels } = await entry();
      return channels.length > 0 && channels.every((c) => c.closed && c.readyState === 'closed')
        ? channels.length
        : -1;
    }, { timeout: 15_000 })
    .toBeGreaterThan(0);
  await expect
    .poll(async () => page.evaluate(() => document.getElementById('app')?.childElementCount ?? -1))
    .toBe(0);

  // 6. Must-not: no link loss was reported and the page never reloaded
  // (the entry counts load events; its own module load is the one).
  const final = await entry();
  expect(final.linkLost, 'onLinkLost was never called').toEqual([]);
  expect(final.loads, 'exactly the initial load event').toBe(1);
  expect(navigations, 'no main-frame navigation after the entry loaded').toBe(0);
});

