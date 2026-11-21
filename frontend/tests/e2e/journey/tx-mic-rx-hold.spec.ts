import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { validateManagedTransmitDocument } from '../../../src/lib/types/managed-transmit';
import { startJourneyServer, type JourneyServerHandle } from './server-process';
import { expectControlLink, simState, simUnkeyLatencyMs, STATION_ORIGIN, STATION_PORT } from '../remote/station';

/**
 * MOR-4594 / MOR-4591, end to end against the Core simulator: the real UI
 * PTT, the real page audio stack and the real server, over the IC-7610 LAN
 * simulator of tests/e2e/remote_web_server.py (the station of the remote
 * rows). That station is started here through startJourneyServer on the
 * remote rows' own ports, 18780 for the server and 18781 for its sidecar,
 * which report the simulator's keyed state and the TX audio frames it
 * received; the remote matrix is not part of the journey leg, so the ports
 * are free there. No radio, no transmission.
 *
 *  1. TX with a microphone that is not 48 kHz mono: Chromium's own fake
 *     capture device fed a 44.1 kHz stereo WAV (the device delivers the
 *     file's format to the page, which the row reads back from the first
 *     microphone frame), and a 16 kHz mono microphone (the fake device only
 *     delivers 44.1 kHz stereo here, so the page's getUserMedia returns a
 *     16 kHz mono stream). PTT must key, TX audio must reach the radio, the
 *     page must show no "TX audio stopped" toast, and the release or UNKEY
 *     must unkey. Both layouts: the desktop KEY/UNKEY pair and the phone
 *     hold button.
 *  2. Receive playback is held while transmitting. While the simulator's
 *     receive audio plays, the press empties the play queue and nothing new
 *     is scheduled (receive frames keep arriving on the wire) until the
 *     release, after which playback resumes. The same holds when the audio
 *     socket drops and reconnects during the transmission. A microphone
 *     that is still opening already holds playback, and one that then
 *     cannot start gives playback back.
 *
 * The rows fail on the 3.2.2 build: its encoder rejects the 44.1 kHz stereo
 * and 16 kHz microphones (PTT never keys), and it keeps playing receive
 * audio while transmitting.
 */

const SIGNAL_HZ = 1000;
const TX_STOPPED_TOAST = 'TX audio stopped';

/** A 16-bit PCM WAV holding a 1 kHz tone. */
function toneWav(sampleRate: number, channels: number): Buffer {
  const frames = sampleRate; // one second; the fake device loops the file
  const data = Buffer.alloc(frames * channels * 2);
  for (let frame = 0; frame < frames; frame += 1) {
    const sample = Math.round(8000 * Math.sin((2 * Math.PI * SIGNAL_HZ * frame) / sampleRate));
    for (let channel = 0; channel < channels; channel += 1) {
      data.writeInt16LE(sample, (frame * channels + channel) * 2);
    }
  }
  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write('WAVEfmt ', 8);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(channels, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * channels * 2, 28);
  header.writeUInt16LE(channels * 2, 32);
  header.writeUInt16LE(16, 34);
  header.write('data', 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

const wavDir = mkdtempSync(path.join(tmpdir(), 'tx-mic-rx-hold-'));
function wavFile(sampleRate: number, channels: number): string {
  const file = path.join(wavDir, `tone-${sampleRate}-${channels}.wav`);
  writeFileSync(file, toneWav(sampleRate, channels));
  return file;
}

const CHROMIUM_ARGS = ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--mute-audio'];

/** What the page's audio stack did, read back from the page. */
interface PageWitness {
  /** AudioBufferSource.start() calls: receive audio scheduled to play. */
  starts: number;
  /** Sources started and not yet ended or stopped. */
  active: number;
  /** Binary frames received on /api/v1/audio sockets. */
  rxFrames: number;
  /** /api/v1/audio sockets opened. */
  audioSockets: number;
  /** Format of the first frame the microphone stream delivered. */
  mic: { sampleRate: number; channels: number } | null;
}

type WitnessHost = { __W__: PageWitness & { sockets: WebSocket[] } };
type MicOverride = { __MIC__?: (constraints?: MediaStreamConstraints) => Promise<MediaStream> };

async function installWitness(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const witness: PageWitness & { sockets: WebSocket[] } = {
      starts: 0, active: 0, rxFrames: 0, audioSockets: 0, mic: null, sockets: [],
    };
    (window as unknown as WitnessHost).__W__ = witness;
    const nativeStart = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (...args: [number?, number?, number?]) {
      witness.starts += 1;
      witness.active += 1;
      this.addEventListener('ended', () => { witness.active -= 1; }, { once: true });
      return nativeStart.apply(this, args);
    };
    const NativeWebSocket = window.WebSocket;
    window.WebSocket = new Proxy(NativeWebSocket, {
      construct(target, args: ConstructorParameters<typeof WebSocket>) {
        const socket = new target(...args);
        if (String(args[0]).includes('/api/v1/audio')) {
          witness.audioSockets += 1;
          witness.sockets.push(socket);
          socket.addEventListener('message', (event) => {
            if (typeof event.data !== 'string') witness.rxFrames += 1;
          });
        }
        return socket;
      },
    });
    // Record the format the microphone stream really delivers.
    const proto = Object.getPrototypeOf(navigator.mediaDevices);
    const nativeGetUserMedia = proto.getUserMedia;
    Object.defineProperty(proto, 'getUserMedia', {
      configurable: true,
      writable: true,
      value: async function (this: MediaDevices, constraints?: MediaStreamConstraints) {
        const override = (window as unknown as MicOverride).__MIC__;
        const stream: MediaStream = await (override ? override(constraints) : nativeGetUserMedia.call(this, constraints));
        const probe = stream.getAudioTracks()[0].clone();
        const processor = new (window as unknown as {
          MediaStreamTrackProcessor: new (init: { track: MediaStreamTrack }) => { readable: ReadableStream<AudioData> };
        }).MediaStreamTrackProcessor({ track: probe });
        const reader = processor.readable.getReader();
        void reader.read().then(({ value }) => {
          if (value) witness.mic = { sampleRate: value.sampleRate, channels: value.numberOfChannels };
          value?.close();
          void reader.cancel();
          probe.stop();
        });
        return stream;
      },
    });
  });
}

const witnessOf = (page: Page): Promise<PageWitness> =>
  page.evaluate(() => {
    const { sockets: _sockets, ...rest } = (window as unknown as WitnessHost).__W__;
    return rest;
  });

/** A microphone whose start the test settles: `__MIC_FAIL__()` rejects the pending start. */
async function pendingMicrophone(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const host = window as unknown as MicOverride & { __MIC_FAIL__?: () => void };
    host.__MIC__ = () => new Promise<MediaStream>((_resolve, reject) => {
      host.__MIC_FAIL__ = () => reject(new DOMException('no microphone', 'NotFoundError'));
    });
  });
}

/** A microphone stream at a fixed rate and channel count, whatever the page asks for. */
async function fixedFormatMicrophone(page: Page, sampleRate: number, channels: number): Promise<void> {
  await page.addInitScript(([rate, count]) => {
    (window as unknown as MicOverride).__MIC__ = async () => {
      const context = new AudioContext({ sampleRate: rate });
      const oscillator = context.createOscillator();
      const destination = context.createMediaStreamDestination();
      destination.channelCount = count;
      destination.channelCountMode = 'explicit';
      oscillator.frequency.value = 1000;
      oscillator.connect(destination);
      oscillator.start();
      await context.resume();
      return destination.stream;
    };
  }, [sampleRate, channels] as const);
}

async function managedTransmit() {
  const response = await fetch(`${STATION_ORIGIN}/api/v1/managed-transmit`);
  return validateManagedTransmitDocument(await response.json());
}

async function expectKeyedOnServer(): Promise<void> {
  await expect.poll(async () => (await simState()).ptt_on).toBe(true);
  await expect.poll(async () => {
    const document = await managedTransmit();
    // The phone's hold button asks for a 'ptt' intent, the desktop key for 'transmit'.
    return document.managedTransmit.status === 'available' && document.managedTransmit.intent.kind !== 'rx'
      && document.txObservation.observedPtt === 'on';
  }).toBe(true);
}

async function openUi(page: Page): Promise<void> {
  await page.goto(`${STATION_ORIGIN}/?ui=v2`, { waitUntil: 'domcontentloaded' });
  await expectControlLink();
}

let station: JourneyServerHandle;
test.beforeAll(async () => {
  station = await startJourneyServer(STATION_PORT, null, [], 'tests/e2e/remote_web_server.py');
});
test.afterAll(async () => {
  await station?.kill();
});

const toast = (page: Page) => page.getByText(TX_STOPPED_TOAST);

/** The two PTT controls of the UI: the desktop key/unkey pair and the phone hold button. */
interface Layout {
  name: string;
  viewport: { width: number; height: number };
  /** Press the PTT; resolves with the press time (ms). */
  key(page: Page): Promise<number>;
  /** Release / unkey. */
  unkey(page: Page): Promise<void>;
  /** Choose the LIVE receive monitor, so receive audio plays in the page. */
  listen(page: Page): Promise<void>;
}

const LAYOUTS: readonly Layout[] = [
  {
    name: 'desktop',
    viewport: { width: 1728, height: 1200 },
    async key(page) {
      const key = page.getByTestId('rx-tx-key');
      await expect(key).toBeVisible();
      const pressedAtMs = Date.now();
      await key.click();
      return pressedAtMs;
    },
    async unkey(page) {
      await page.getByTestId('rx-tx-unkey').click();
    },
    async listen(page) {
      await page.getByTestId('rx-audio-monitor-live').click();
    },
  },
  {
    name: 'phone',
    viewport: { width: 430, height: 932 },
    async key(page) {
      const ptt = page.getByRole('button', { name: 'Push to talk' });
      await expect(ptt).toBeVisible();
      await ptt.hover();
      const pressedAtMs = Date.now();
      await page.mouse.down();
      return pressedAtMs;
    },
    async unkey(page) {
      await page.mouse.up();
    },
    async listen(page) {
      await page.getByRole('button', { name: 'LIVE', exact: true }).click();
    },
  },
];

const MICROPHONES = [
  { name: '44.1 kHz stereo (Chromium fake capture device fed a WAV)', rate: 44_100, channels: 2, wav: true },
  { name: '16 kHz mono', rate: 16_000, channels: 1, wav: false },
] as const;

for (const mic of MICROPHONES) for (const layout of LAYOUTS) {
  const micTest = test.extend({
    launchOptions: {
      args: mic.wav
        ? [...CHROMIUM_ARGS, `--use-file-for-fake-audio-capture=${wavFile(mic.rate, mic.channels)}`]
        : CHROMIUM_ARGS,
    },
  });
  micTest.describe(`TX with a ${mic.name} microphone, ${layout.name} layout`, () => {
    micTest.use({ permissions: ['microphone'], viewport: layout.viewport });
    micTest('PTT keys, TX audio reaches the radio, release unkeys', async ({ page }) => {
      await installWitness(page);
      if (!mic.wav) await fixedFormatMicrophone(page, mic.rate, mic.channels);
      await openUi(page);
      const framesBefore = (await simState()).tx_audio_signal_frames;

      const pressedAtMs = await layout.key(page);
      await expectKeyedOnServer();
      await expect.poll(async () => (await simState()).tx_audio_signal_frames).toBeGreaterThan(framesBefore);
      // The microphone really delivered the format under test.
      await expect.poll(async () => (await witnessOf(page)).mic).toEqual({ sampleRate: mic.rate, channels: mic.channels });
      await expect(toast(page)).toHaveCount(0);

      const releasedAtMs = Date.now();
      await layout.unkey(page);
      expect(await simUnkeyLatencyMs(pressedAtMs, releasedAtMs)).toBeLessThanOrEqual(1000);
      await expect.poll(async () => (await managedTransmit()).txObservation.observedPtt).toBe('off');
      await expect(toast(page)).toHaveCount(0);
    });
  });
}

// ---------------------------------------------------------------------------
// 2. Receive playback is held while transmitting.

/** The page is playing receive audio from the simulator. */
async function listening(page: Page, layout: Layout): Promise<void> {
  await installWitness(page);
  await openUi(page);
  await layout.listen(page);
  await expect.poll(async () => (await witnessOf(page)).active).toBeGreaterThan(0);
}

/** Receive frames keep arriving on the wire: wait for `count` more of them. */
async function receiveMore(page: Page, count: number): Promise<void> {
  const from = (await witnessOf(page)).rxFrames;
  await expect.poll(async () => (await witnessOf(page)).rxFrames - from).toBeGreaterThanOrEqual(count);
}

/** Playback is held: everything queued has ended and nothing new is scheduled
 * although receive frames keep arriving. */
async function expectHeld(page: Page): Promise<void> {
  await expect.poll(async () => (await witnessOf(page)).active).toBe(0);
  const held = (await witnessOf(page)).starts;
  await receiveMore(page, 10);
  const after = await witnessOf(page);
  expect(after.starts).toBe(held);
  expect(after.active).toBe(0);
}

async function expectPlaying(page: Page): Promise<void> {
  const from = (await witnessOf(page)).starts;
  await expect.poll(async () => (await witnessOf(page)).starts - from).toBeGreaterThanOrEqual(5);
}

const rxTest = test.extend({ launchOptions: { args: CHROMIUM_ARGS } });

for (const layout of LAYOUTS) {
  rxTest.describe(`receive playback is held while transmitting, ${layout.name} layout`, () => {
    rxTest.use({ permissions: ['microphone'], viewport: layout.viewport });

    rxTest('held from the press to the release, playing again after', async ({ page }) => {
      await fixedFormatMicrophone(page, 48_000, 1);
      await listening(page, layout);

      const pressedAtMs = await layout.key(page);
      await expectKeyedOnServer();
      await expectHeld(page);

      const releasedAtMs = Date.now();
      await layout.unkey(page);
      await simUnkeyLatencyMs(pressedAtMs, releasedAtMs);
      await expectPlaying(page);
    });

    rxTest('an audio reconnect during the transmission stays held', async ({ page }) => {
      await fixedFormatMicrophone(page, 48_000, 1);
      await listening(page, layout);

      const pressedAtMs = await layout.key(page);
      await expectKeyedOnServer();
      await expectHeld(page);

      const socketsBefore = (await witnessOf(page)).audioSockets;
      await page.evaluate(() => {
        const { sockets } = (window as unknown as WitnessHost).__W__;
        for (const socket of sockets) if (socket.readyState === WebSocket.OPEN) socket.close();
      });
      await expect.poll(async () => (await witnessOf(page)).audioSockets).toBeGreaterThan(socketsBefore);
      await expectHeld(page);

      const releasedAtMs = Date.now();
      await layout.unkey(page);
      await simUnkeyLatencyMs(pressedAtMs, releasedAtMs);
      await expectPlaying(page);
    });

    rxTest('a microphone that cannot start gives playback back', async ({ page }) => {
      await pendingMicrophone(page);
      await listening(page, layout);

      await layout.key(page);
      // The microphone is still opening: playback is already held.
      await expectHeld(page);
      await page.evaluate(() => (window as unknown as { __MIC_FAIL__: () => void }).__MIC_FAIL__());
      await expectPlaying(page);
      expect((await simState()).ptt_on).toBe(false);
      await layout.unkey(page).catch(() => undefined);
    });
  });
}
