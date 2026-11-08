import { writeFile } from 'node:fs/promises';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { startJourneyServer } from './server-process';
import { validateCapabilities } from '../../../src/lib/types/capabilities';

/**
 * MOR-4210 makeup gain, end to end: the real WebServer with the in-memory
 * IC-7610 fake (tests/e2e/journey_web_server.py --audio
 * --rx-tone-level-file), whose RX carries a 1 kHz tone at the dBFS level
 * this spec writes to a file. The page selects LIVE and moves the AF slider
 * through 100/150/200% with the keyboard; the production audio graph's
 * output is read by an AnalyserNode tapped beside every connection into an
 * AudioContext destination. No server document is mocked.
 *
 * Below the limiter knee the output rises by the makeup gain (+10 dB at
 * 150%, +20 dB at 200%); a loud tone at 200% stays under the limiter
 * ceiling. A 100% cap on the slider or a volume that never reaches the
 * player both fail the ratios.
 */

const PORT = 18771;
const FREQ_HZ = 7_030_000;
const QUIET_DBFS = -30;
const LOUD_DBFS = -6;
const amplitude = (dbfs: number) => 10 ** (dbfs / 20);

interface OutputWitness {
  peaks: { at: number; peak: number }[];
  rms: { at: number; value: number }[];
  inputRms: number;
}

async function installOutputWitness(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const witness: OutputWitness = { peaks: [], rms: [], inputRms: 0 };
    (window as Window & { __RX_OUTPUT__?: OutputWitness }).__RX_OUTPUT__ = witness;
    const analysers: AnalyserNode[] = [];
    const proto = AudioNode.prototype as unknown as {
      connect: (this: AudioNode, destination: unknown, ...rest: unknown[]) => unknown;
    };
    const nativeConnect = proto.connect;
    proto.connect = function (destination, ...rest) {
      if (this instanceof AudioBufferSourceNode && this.buffer) {
        const input = this.buffer.getChannelData(0);
        witness.inputRms = Math.sqrt(input.reduce((sum, sample) => sum + sample * sample, 0) / input.length);
      }
      if (destination instanceof AudioDestinationNode) {
        // Tap what the page sends to the speakers, unchanged.
        const analyser = this.context.createAnalyser();
        analyser.fftSize = 2048;
        nativeConnect.call(this, analyser);
        analysers.push(analyser);
      }
      return nativeConnect.call(this, destination, ...rest);
    };
    const samples = new Float32Array(2048);
    setInterval(() => {
      let peak = 0;
      let rms = 0;
      let running = false;
      for (const analyser of analysers) {
        if (analyser.context.state !== 'running') continue;
        running = true;
        analyser.getFloatTimeDomainData(samples);
        rms = Math.max(rms, Math.sqrt(samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length));
        for (const sample of samples) peak = Math.max(peak, Math.abs(sample));
      }
      if (!running) return;
      witness.peaks.push({ at: performance.now(), peak });
      witness.rms.push({ at: performance.now(), value: rms });
      if (witness.peaks.length > 2000) witness.peaks.shift();
      if (witness.rms.length > 2000) witness.rms.shift();
    }, 20);
  });
}

/** Highest output peak over the last `windowMs` of page time. */
async function recentPeak(page: Page, windowMs = 400): Promise<number> {
  return page.evaluate((span) => {
    const { peaks } = (window as Window & { __RX_OUTPUT__?: OutputWitness }).__RX_OUTPUT__!;
    const since = performance.now() - span;
    return peaks.filter((entry) => entry.at >= since).reduce((max, entry) => Math.max(max, entry.peak), 0);
  }, windowMs);
}

/** Wait until the output peak settles within `tolerance` of `expected`
 *  (the first frames after LIVE starts arrive louder than the source). */
async function expectSettledPeak(page: Page, expected: number, tolerance: number): Promise<void> {
  await expect.poll(async () => Math.abs((await recentPeak(page, 1000)) / expected - 1), {
    message: `output peak settles at ${expected.toFixed(4)} within ${tolerance * 100}%`,
    timeout: 15_000,
  }).toBeLessThan(tolerance);
}

async function pageNow(page: Page): Promise<number> {
  return page.evaluate(() => performance.now());
}

async function peakSince(page: Page, since: number): Promise<number> {
  return page.evaluate((start) => {
    const { peaks } = (window as Window & { __RX_OUTPUT__?: OutputWitness }).__RX_OUTPUT__!;
    return peaks.filter((entry) => entry.at >= start).reduce((max, entry) => Math.max(max, entry.peak), 0);
  }, since);
}

/** Step the AF slider with the arrow keys until it reads `percent`. */
async function setAfPercent(slider: Locator, percent: number): Promise<void> {
  await slider.focus();
  for (let step = 0; step < 250; step += 1) {
    const now = Math.round(Number(await slider.getAttribute('aria-valuenow')) * 100);
    if (now === percent) return;
    await slider.press(now < percent ? 'ArrowRight' : 'ArrowLeft');
  }
  throw new Error(`AF slider did not reach ${percent}% (reads ${await slider.getAttribute('aria-valuenow')})`);
}

test('MOR-4273 unity settling rejects a startup peak inside the baseline window', async ({ page }) => {
  await page.clock.install();
  await page.clock.runFor(1000);
  const quiet = amplitude(QUIET_DBFS);
  await page.evaluate(level => {
    const win = window as Window & { __RX_OUTPUT__?: OutputWitness; __RX_STARTUP_PEAK_READS__?: number };
    win.__RX_STARTUP_PEAK_READS__ = 0;
    const now = performance.now();
    win.__RX_OUTPUT__ = { rms: [], inputRms: 0, peaks: [
      { at: now - 700, get peak() { win.__RX_STARTUP_PEAK_READS__! += 1; return level * 1.8; } },
      { at: now - 50, peak: level },
    ] };
  }, quiet);
  expect(await recentPeak(page, 400)).toBe(quiet);
  expect(await recentPeak(page, 1000)).toBe(quiet * 1.8);
  const reads = () => page.evaluate(() => (window as Window & { __RX_STARTUP_PEAK_READS__?: number }).__RX_STARTUP_PEAK_READS__!);
  const before = await reads();
  const settling = expectSettledPeak(page, quiet, 0.1);
  await expect.poll(reads, { message: 'Settling must inspect the startup peak in the 1000ms baseline window' }).toBeGreaterThan(before);
  await page.clock.runFor(400);
  await settling;
  expect(await recentPeak(page, 1000)).toBe(quiet);
});

const gainTest = test.extend({
  launchOptions: {
    ...(process.env.RIGPLANE_JOURNEY_CHROMIUM
      ? { executablePath: process.env.RIGPLANE_JOURNEY_CHROMIUM }
      : {}),
    args: ['--mute-audio'],
  },
});

gainTest.describe('LIVE RX makeup gain through the real server and audio graph', () => {
  gainTest.use({ baseURL: `http://127.0.0.1:${PORT}` });

  gainTest('MOR-4296 unsupported routing keeps browser and app-window PCM16 RMS at unity', async ({ browser }, testInfo) => {
    test.setTimeout(90_000);
    const levelFile = testInfo.outputPath('rx-tone-dbfs');
    await writeFile(levelFile, String(QUIET_DBFS));
    const server = await startJourneyServer(PORT + 1, FREQ_HZ, [
      '--audio', '--model', 'IC-705', '--rx-tone-level-file', levelFile,
    ]);
    const readings: { gainDb: number; inputRms: number }[] = [];
    try {
      for (const appWindow of [false, true]) {
        const context = await browser.newContext();
        try {
          const page = await context.newPage();
          await page.addInitScript((app) => {
            Object.defineProperty(globalThis, 'AudioDecoder', { value: undefined, configurable: true });
            if (app) Object.assign(globalThis, { __TAURI_INTERNALS__: {} });
            localStorage.setItem('icom.audio.focus', 'main');
            localStorage.setItem('icom.audio.split_stereo', '0');
            localStorage.setItem('icom.audio.main_gain_db', '-27');
            localStorage.removeItem('icom.audio.sub_gain_db');
          }, appWindow);
          await installOutputWitness(page);
          await page.goto(`http://127.0.0.1:${PORT + 1}/?ui=v2`, { waitUntil: 'domcontentloaded' });
          const caps = validateCapabilities(await (await page.request.get(`http://127.0.0.1:${PORT + 1}/api/v1/capabilities`)).json());
          expect(caps.capabilities).not.toContain('lan_dual_rx_audio_routing');
          await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
          await page.getByTestId('rx-audio-monitor-live').click();
          await setAfPercent(page.getByTestId('rx-audio-af').getByRole('slider'), 100);
          const measureGain = () => page.evaluate(() => {
            const witness = (window as Window & { __RX_OUTPUT__?: OutputWitness }).__RX_OUTPUT__!;
            const samples = witness.rms.filter(({ at }) => at >= performance.now() - 800).map(({ value }) => value).sort((a, b) => a - b);
            if (samples.length < 20 || witness.inputRms <= 0) return null;
            const output = samples[Math.floor(samples.length / 2)]!;
            return output > 0 ? 20 * Math.log10(output / witness.inputRms) : null;
          });
          await expect.poll(async () => {
            const gain = await measureGain();
            return gain === null ? Infinity : Math.abs(gain);
          }, { timeout: 15_000, message: `${appWindow ? 'app-window' : 'browser'} PCM16 output stays within 1.5 dB of input` }).toBeLessThanOrEqual(1.5);
          readings.push({
            gainDb: (await measureGain())!,
            inputRms: await page.evaluate(() => (window as Window & { __RX_OUTPUT__?: OutputWitness }).__RX_OUTPUT__!.inputRms),
          });
          expect(await page.evaluate(() => localStorage.getItem('icom.audio.main_gain_db'))).toBe('-27');
        } finally {
          await context.close();
        }
      }
      expect(readings[0]!.inputRms).toBe(readings[1]!.inputRms);
      expect(Math.abs(readings[0]!.gainDb - readings[1]!.gainDb)).toBeLessThanOrEqual(1.5);
      const resultFile = testInfo.outputPath('pcm16-rms-gain-db.json');
      await writeFile(resultFile, JSON.stringify({ browser: readings[0], appWindow: readings[1] }));
      await testInfo.attach('pcm16-rms-gain-db', { path: resultFile, contentType: 'application/json' });
    } finally {
      await server.kill();
    }
  });

  gainTest('MOR-4210 AF above 100% raises quiet RX and limits loud RX', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const levelFile = testInfo.outputPath('rx-tone-dbfs');
    await writeFile(levelFile, String(QUIET_DBFS));
    const server = await startJourneyServer(PORT, FREQ_HZ, [
      '--audio', '--rx-tone-level-file', levelFile,
    ]);
    try {
      await installOutputWitness(page);
      await page.goto('/?ui=v2', { waitUntil: 'domcontentloaded' });
      await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
      await page.getByTestId('rx-audio-monitor-live').click();
      await expect(page.getByTestId('rx-audio-monitor')).toHaveAttribute('data-monitor-mode', 'live');

      const slider = page.getByTestId('rx-audio-af').getByRole('slider');
      const readout = page.getByTestId('rx-audio-af-value');
      await setAfPercent(slider, 100);
      await expect(readout).toHaveText('100%');
      // Unity: the output carries the source tone at its own level.
      await expectSettledPeak(page, amplitude(QUIET_DBFS), 0.1);
      const unity = await recentPeak(page, 1000);

      // Exercise more than the old 20-second quiet-source detection window.
      const quietStart = await pageNow(page);
      await expect.poll(() => page.evaluate((start) => {
        const { peaks } = (window as Window & { __RX_OUTPUT__?: OutputWitness }).__RX_OUTPUT__!;
        return peaks.filter((entry) => entry.at >= start && entry.peak > 0.005).length;
      }, quietStart), { timeout: 50_000 }).toBeGreaterThan(1300);
      await expect(page.getByTestId('rx-audio-source-low')).toHaveCount(0);

      // 150%: +10 dB; 200%: +20 dB (still below the -6 dBFS limiter knee).
      for (const [percent, ratio] of [[150, 10 ** 0.5], [200, 10]] as const) {
        await setAfPercent(slider, percent);
        await expect(readout).toHaveText(`${percent}%`);
        await expectSettledPeak(page, unity * ratio, 0.1);
      }

      // A loud source at 200% would reach +14 dBFS; the limiter bounds it.
      await writeFile(levelFile, String(LOUD_DBFS));
      const loudFrom = await pageNow(page);
      await expect.poll(() => recentPeak(page)).toBeGreaterThan(0.9);
      await expect.poll(async () => (await pageNow(page)) - loudFrom).toBeGreaterThan(2000);
      expect(await peakSince(page, loudFrom)).toBeLessThanOrEqual(0.99);
    } finally {
      await server.kill();
    }
  });
});
