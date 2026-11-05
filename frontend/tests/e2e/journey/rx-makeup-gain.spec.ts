import { writeFile } from 'node:fs/promises';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { startJourneyServer } from './server-process';

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
}

async function installOutputWitness(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const witness: OutputWitness = { peaks: [] };
    (window as Window & { __RX_OUTPUT__?: OutputWitness }).__RX_OUTPUT__ = witness;
    const analysers: AnalyserNode[] = [];
    const proto = AudioNode.prototype as unknown as {
      connect: (this: AudioNode, destination: unknown, ...rest: unknown[]) => unknown;
    };
    const nativeConnect = proto.connect;
    proto.connect = function (destination, ...rest) {
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
      let running = false;
      for (const analyser of analysers) {
        if (analyser.context.state !== 'running') continue;
        running = true;
        analyser.getFloatTimeDomainData(samples);
        for (const sample of samples) peak = Math.max(peak, Math.abs(sample));
      }
      if (!running) return;
      witness.peaks.push({ at: performance.now(), peak });
      if (witness.peaks.length > 800) witness.peaks.shift();
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
  await expect.poll(async () => Math.abs((await recentPeak(page)) / expected - 1), {
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
