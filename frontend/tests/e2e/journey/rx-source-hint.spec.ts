import { writeFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { startJourneyServer } from './server-process';

/**
 * MOR-4210 quiet-source hint, end to end: the real WebServer with the
 * in-memory fake (tests/e2e/journey_web_server.py --audio --model
 * --rx-tone-level-file), whose RX carries a 1 kHz tone at the dBFS level
 * this spec writes to a file. Capabilities come from the shipped profile
 * through /api/v1/capabilities and the audio through /api/v1/audio; the
 * page's own player detects the quiet source and the runtime publishes it
 * to the RX audio surface. No server document is mocked.
 *
 * IC-7300 declares `[audio].rx_audio_level_menu`, so the hint names that
 * menu item; IC-7610 declares none and gets the generic text. A loud frame
 * clears the hint, and it leaves with LIVE.
 */

const FREQ_HZ = 7_030_000;
const QUIET_DBFS = -30;  // between the -60 dBFS silence floor and the -12 dBFS bar
const LOUD_DBFS = -3;
// 20 s of audible audio, plus the 1.5 s publication tick and start-up.
const DETECTION_TIMEOUT_MS = 40_000;

const hintTest = test.extend({
  launchOptions: {
    ...(process.env.RIGPLANE_JOURNEY_CHROMIUM
      ? { executablePath: process.env.RIGPLANE_JOURNEY_CHROMIUM }
      : {}),
    args: ['--mute-audio'],
  },
});

hintTest.describe('LIVE quiet-source hint through the real server', () => {
  hintTest('MOR-4210 IC-7300 names its menu item, clears on loud audio and leaves with LIVE', async ({ page }, testInfo) => {
    test.setTimeout(150_000);
    const port = 18772;
    const levelFile = testInfo.outputPath('rx-tone-dbfs');
    await writeFile(levelFile, String(QUIET_DBFS));
    const server = await startJourneyServer(port, FREQ_HZ, [
      '--audio', '--model', 'IC-7300', '--rx-tone-level-file', levelFile,
    ]);
    try {
      await page.goto(`http://127.0.0.1:${port}/?ui=v2`, { waitUntil: 'domcontentloaded' });
      await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
      const hint = page.getByTestId('rx-audio-source-low');
      await expect(hint).toHaveCount(0);

      await page.getByTestId('rx-audio-monitor-live').click();
      await expect(page.getByTestId('rx-audio-monitor')).toHaveAttribute('data-monitor-mode', 'live');
      await expect(hint).toHaveText(
        "Radio audio is quiet. Raise the radio's audio output level: SET > Connectors > ACC/USB AF Output Level.",
        { timeout: DETECTION_TIMEOUT_MS },
      );

      await writeFile(levelFile, String(LOUD_DBFS));
      await expect(hint).toHaveCount(0, { timeout: 10_000 });

      await writeFile(levelFile, String(QUIET_DBFS));
      await expect(hint).toBeVisible({ timeout: DETECTION_TIMEOUT_MS });
      await page.getByTestId('rx-audio-monitor-local').click();
      await expect(page.getByTestId('rx-audio-monitor')).toHaveAttribute('data-monitor-mode', 'local');
      await expect(hint).toHaveCount(0);
    } finally {
      await server.kill();
    }
  });

  hintTest('MOR-4210 IC-7610 without a declared menu item gets the generic hint', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const port = 18773;
    const levelFile = testInfo.outputPath('rx-tone-dbfs');
    await writeFile(levelFile, String(QUIET_DBFS));
    const server = await startJourneyServer(port, FREQ_HZ, [
      '--audio', '--model', 'IC-7610', '--rx-tone-level-file', levelFile,
    ]);
    try {
      await page.goto(`http://127.0.0.1:${port}/?ui=v2`, { waitUntil: 'domcontentloaded' });
      await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
      await page.getByTestId('rx-audio-monitor-live').click();
      await expect(page.getByTestId('rx-audio-source-low')).toHaveText(
        "Radio audio is quiet. Raise the radio's audio output level.",
        { timeout: DETECTION_TIMEOUT_MS },
      );
    } finally {
      await server.kill();
    }
  });
});
