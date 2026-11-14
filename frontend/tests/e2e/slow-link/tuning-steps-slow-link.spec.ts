import { expect, test } from '@playwright/test';
import { startJourneyServer } from '../journey/server-process';

/**
 * MOR-4393 — step tuning under CI-V readback latency, real WebServer +
 * WebKit, loopback PanoramaSimulator with `--ack-delay` (the harness knob
 * in tests/e2e/panorama_web_server.py): a frequency SET applies instantly
 * and a transceive-style unsolicited report carries it back at once, but
 * the FB/01 ACK lands 500 ms late — the owner's IC-705-over-Wi-Fi
 * readback-lag shape.
 *
 * Twelve ArrowRight presses (tuning step 1 kHz, USB default) must move the
 * VFO exactly +12 kHz, and the emitted set_freq targets must never go
 * backward (a mid-burst cold reset onto a stale echo is the defect's
 * signature). The accumulator attributes late echoes of its own sends
 * through the MOR-4393 send ledger instead of cold-resetting; the
 * failing-first proof of the loss mechanism lives at the accumulator layer
 * (tuning-accumulator.test.ts, RED commit), because on this branch the web
 * server acknowledges WS-queued commands at enqueue (MOR-1445), so
 * radio-side lag alone never leaves a frontend lifecycle pending.
 *
 * Kept out of tests/e2e/journey/ on purpose: the journey playwright config
 * sweeps every `*.spec.ts` in that directory.
 */
test('IC-705 slow link: 12 ArrowRight presses move the VFO exactly +12 kHz', async ({ page }, info) => {
  const port = Number(process.env.MOR4393_PORT ?? 18849);
  const base = 14_250_000;
  const server = await startJourneyServer(port, base,
    ['--model', 'IC-705', '--evidence', info.outputPath('radio.jsonl'),
      '--ack-delay', '0.5'], 'tests/e2e/panorama_web_server.py');
  try {
    const sentFreqs: number[] = [];
    page.on('websocket', socket => {
      if (new URL(socket.url()).pathname !== '/api/v1/ws') return;
      socket.on('framesent', ({ payload }) => {
        if (typeof payload !== 'string') return;
        const command = JSON.parse(payload);
        if (command.type === 'cmd' && command.name === 'set_freq') sentFreqs.push(command.params.freq);
      });
    });
    const stateFreq = async () => {
      const state = await (await page.request.get(`http://127.0.0.1:${port}/api/v1/state`)).json();
      return state.main.freqHz as number;
    };
    await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
    // The keyboard tune path reads the observed freqHz from radio state —
    // wait until the browser itself shows the base before pressing.
    await expect.poll(stateFreq, { timeout: 30_000 }).toBe(base);
    await expect(page.locator('.receiver-instrument [data-vfo-freq]').first()).toHaveText(/14\s*\.\s*250/, { timeout: 30_000 });
    for (let press = 0; press < 12; press++) {
      await page.keyboard.press('ArrowRight');
      await page.waitForTimeout(150);
    }
    await expect.poll(stateFreq, { timeout: 30_000, intervals: [500] }).toBe(base + 12_000);
    expect(sentFreqs.length).toBeGreaterThan(1);
    for (let index = 1; index < sentFreqs.length; index++) {
      expect(sentFreqs[index]!).toBeGreaterThanOrEqual(sentFreqs[index - 1]!);
    }
  } finally {
    await server.kill();
  }
});
