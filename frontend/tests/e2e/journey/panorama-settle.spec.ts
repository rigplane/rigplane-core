// MOR-4392 round 2 — canonical settle resolution against MOR-4385
// (coordinator binding 21:53 EST + SPAN-disabled-in-FIX ruling 2026-11-10).
// Needs BOTH codex/mor-4387-vfo-follow (SPAN disabled in FIX, client crop)
// and codex/mor-4385-span-half-width (real-width ladder, FIX follow grant
// clipped to the streamed fixed window). Do NOT commit on either branch:
// the coordinator lands it with whichever merges second — apply over
// frontend/tests/e2e/journey/panorama-settle.spec.ts (it replaces that file;
// testMatch /panorama-.*\.spec\.ts/ would otherwise run both). For runs,
// place at frontend/tests/e2e/journey/panorama-settle.resolved-for-4385.spec.ts.
// FIX variant (zoomMode 1): SPAN keys disabled, stepping writes nothing,
// grant and frames stay the fixed edges. CENTER keeps per-step writes
// (width/2 preset) and VFO-centred grants. The MOR-4393 row proves each
// click against the viewport visible at click time (no hard-coded 14.242).
import { createReadStream } from 'node:fs';
import { writeFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { parseCurrentScopeFrame, parseScopeGranted } from '../../../src/lib/runtime/scope-frame';
import type { ScopeGranted, ScopeSubscription } from '../../../src/lib/runtime/scope-view';
import type { WsCommand } from '../../../src/lib/types/protocol';
import { startJourneyServer } from './server-process';
import { parseScopeWriteReceipt } from './panorama-receipt';
import { expectPanoramaSettled, watchPanoramaErrors } from './panorama-guards';

interface Work { raf: number; rafMs: number; texture: number; draw: number; static: number; labels: string[] }
declare global { interface Window { panoramaWork: Work } }

for (const [index, [model, zoomMode]] of ([['IC-705', 0], ['IC-7300', 0], ['IC-7610', 1]] as const).entries()) {
  for (const zoom of [false, true, 'span'] as const) {
    test(`${model}: ${zoom === 'span' ? 'span arrows and keyboard' : zoom ? 'zoom then drag' : 'one drag'} settles subscriptions and WebKit renderer work`, async ({ page }, info) => {
      const evidence = info.outputPath('radio.jsonl');
      const port = Number(process.env.PANORAMA_PORT ?? 18845) + index;
      const server = await startJourneyServer(port, 14_250_000,
        ['--model', model, '--evidence', evidence], 'tests/e2e/panorama_web_server.py');
      try {
        const sent: Array<ScopeSubscription & { type: string }> = [];
        const grants: ScopeGranted[] = [];
        const frames: Array<{ view: number[]; coverage: number[]; pixels: boolean; seq: number; receivedAt: number }> = [];
        const watch = watchPanoramaErrors(page);
        const errors = watch.pageErrors;
        const commands: WsCommand[] = [];
        page.on('response', response => { if (response.status() >= 400) console.log('HTTP', response.status(), response.url()); });
        page.on('websocket', socket => {
          if (new URL(socket.url()).pathname === '/api/v1/ws') socket.on('framesent', ({ payload }) => {
            if (typeof payload === 'string') {
              const command = JSON.parse(payload) as WsCommand;
              if (command.type === 'cmd') commands.push(command);
            }
          });
          if (!socket.url().endsWith('/api/v1/scope')) return;
          socket.on('framesent', ({ payload }) => {
            if (typeof payload === 'string') {
              const message = JSON.parse(payload) as ScopeSubscription & { type: string };
              if (message.type === 'subscribe') sent.push(message);
            }
          });
          socket.on('framereceived', ({ payload }) => {
            if (typeof payload === 'string') {
              const grant = parseScopeGranted(JSON.parse(payload));
              if (grant) grants.push(grant);
              else console.log('scope-control', payload);
            } else {
              const buffer = Uint8Array.from(payload).buffer;
              const parsed = parseCurrentScopeFrame(buffer, { source: 'radio', sourceId: 'radio', epoch: 0 });
              if (parsed.ok) frames.push({ view: [parsed.frame.viewStartHz, parsed.frame.viewEndHz],
                coverage: [parsed.frame.coverageStartHz, parsed.frame.coverageEndHz],
                pixels: parsed.frame.pixels?.some(value => value > 0) === true, seq: parsed.frame.seq, receivedAt: performance.now() });
            }
          });
        });
        await page.addInitScript(() => {
          const work: Work = { raf: 0, rafMs: 0, texture: 0, draw: 0, static: 0, labels: [] };
          window.panoramaWork = work;
          const raf = window.requestAnimationFrame.bind(window);
          window.requestAnimationFrame = callback => raf(time => {
            const start = performance.now(); work.raf++;
            try { callback(time); } finally { work.rafMs += performance.now() - start; }
          });
          const gl = WebGL2RenderingContext.prototype;
          const texture = gl.texImage2D, draw = gl.drawArrays;
          gl.texImage2D = function (this: WebGL2RenderingContext, ...args: unknown[]) {
            work.texture++; return Reflect.apply(texture, this, args);
          } as typeof gl.texImage2D;
          gl.drawArrays = function (...args) { work.draw++; return Reflect.apply(draw, this, args); };
          const paint = CanvasRenderingContext2D.prototype.fillText;
          CanvasRenderingContext2D.prototype.fillText = function (...args) {
            if (/^\d+\.\d{3}$/.test(args[0])) {
              if (args[1] === 20) work.labels = [];
              work.labels.push(args[0]);
            }
            work.static++; return Reflect.apply(paint, this, args);
          };
        });
        const work = () => page.evaluate(() => ({ ...window.panoramaWork }));
        const writes = async () => {
          let contents = '';
          for await (const chunk of createReadStream(evidence, { encoding: 'utf8' })) contents += chunk;
          return contents.trim().split('\n').filter(Boolean).map(
            parseScopeWriteReceipt);
        };
        await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
        await expect.poll(() => frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
        const area = page.locator('.spectrum-area').first();
        const marker = page.locator('.waterfall-content .tune-line');
        if (zoom === true) {
          await page.getByRole('button', { name: 'More scope controls' }).first().click();
          await page.getByTestId('scope-zoom-in').first().click();
          await expect.poll(() => frames.at(-1)?.view).toEqual([14_225_000, 14_275_000]);
          await page.keyboard.press('Escape');
          // MOR-4387: zoom keeps follow mode with the zoomed width — the
          // VFO marker survives and later tuning still follows.
          await expect.poll(() => sent.at(-1)?.view).toEqual({ follow: 'vfo', spanHz: 50_000 });
          await expect(marker).toBeVisible();
        }
        // MOR-4387: zoom no longer pins a fixed window, so the FIX-mode span
        // variant enters FIX through the explicit scope-mode control instead;
        // the fixed edges hold the same 100 kHz the follow default granted.
        if (zoom === 'span' && zoomMode === 1) {
          await page.getByRole('radio', { name: 'FIX' }).first().click();
          await expect.poll(async () => (await writes()).filter(receipt => receipt.sub === 0x14).at(-1)?.data[1]).toBe(1);
          await expect.poll(() => frames.at(-1)?.view).toEqual([14_200_000, 14_300_000]);
        }
        const box = await area.boundingBox(); expect(box).not.toBeNull();
        const before = { subscriptions: sent.length, writes: (await writes()).length, work: await work() };
        if (zoom === 'span') {
          if (zoomMode === 1) {
            // MOR-4392 round 2 (coordinator ruling 2026-11-10): in FIX the
            // window is the radio's fixed window and SPAN is a CENTER-mode
            // setting — the keys render DISABLED and stepping writes nothing
            // to the radio (no mode flip, no span write); the grant and
            // frames stay the fixed window (4385 re-resolves the follow grant
            // per frame, clipped to the streamed fixed edges).
            const fixed = [14_200_000, 14_300_000];
            const spanValue = page.getByTestId('scope-span-value').first();
            await expect(spanValue).toHaveText('±50k');
            expect((await writes()).filter(receipt => receipt.sub === 0x14).at(-1)?.data[1]).toBe(1);
            for (const name of ['Decrease scope span', 'Increase scope span']) {
              await expect(page.getByRole('button', { name, exact: true }).first()).toBeDisabled();
            }
            const receiptsBefore = await writes();
            await page.keyboard.press('[');
            await page.keyboard.press(']');
            await page.waitForTimeout(1000);
            const receiptsAfter = await writes();
            expect(receiptsAfter.length).toBe(receiptsBefore.length);
            const scopeWrites = (list: typeof receiptsAfter) =>
              list.filter(receipt => receipt.sub === 0x14 || receipt.sub === 0x15);
            expect(scopeWrites(receiptsAfter)).toEqual(scopeWrites(receiptsBefore));
            await expect(spanValue).toHaveText('±50k');
            await expect.poll(() => grants.at(-1)?.view).toEqual({ startHz: fixed[0], endHz: fixed[1] });
            await expect.poll(() => frames.at(-1)?.view).toEqual(fixed);
          } else {
            // MOR-4385: span choices are real view widths (twice the Icom
            // ±half-width preset). MOR-4387: the CENTER variant steps
            // 50k → 100k → 200k real widths with per-step writes.
            const widths = [50000, 100000, 200000];
            await expect(page.getByTestId('scope-span-value').first()).toHaveText('±50k');
            for (const [step, action] of ['Decrease scope span', 'Increase scope span', ']'].entries()) {
              const width = widths[step]!;
              const mark = frames.length;
              if (action === ']') await page.keyboard.press(action);
              else await page.getByRole('button', { name: action, exact: true }).first().click();
              const bounds = [14_250_000 - width / 2, 14_250_000 + width / 2];
              await expect.poll(() => grants.at(-1)?.view).toEqual({ startHz: bounds[0], endHz: bounds[1] });
              await expect.poll(() => frames.slice(mark).some(frame => frame.pixels && frame.view.every((hz, i) => hz === bounds[i])
                && frame.coverage.every((hz, i) => hz === bounds[i]))).toBe(true);
              await expect(page.getByTestId('scope-span-value').first()).toHaveText(`±${width / 2000}k`);
              expect((await writes()).filter(receipt => receipt.sub === 0x15).at(-1)?.span).toBe(width / 2);
            }
          }
        } else {
        await page.mouse.move(box!.x + box!.width * (zoom ? 0.9 : 0.6), box!.y + box!.height * 0.3);
        await page.mouse.down();
        await page.mouse.move(box!.x + box!.width * (zoom ? 0.1 : 0.45), box!.y + box!.height * 0.3, { steps: 5 });
        await page.mouse.up();
        }
        // Observation windows, not synchronization delays: 10 s after release,
        // with the final 5 s isolated from gesture/acknowledgement work.
        await page.waitForTimeout(5000);
        const quietStart = { atMs: performance.now(), subscriptions: sent.length, writes: (await writes()).length, commands: commands.length, frames: frames.length, work: await work() };
        await page.waitForTimeout(5000);
        const quietEnd = { subscriptions: sent.length, writes: (await writes()).length, commands: commands.length, frames: frames.length,
          lastFrameAgeMs: performance.now() - (frames.at(-1)?.receivedAt ?? 0), work: await work() };
        const finalHz = zoom === 'span' ? 14_250_000 : zoom ? 14_290_000 : 14_265_000;
        await expect.poll(async () => (await page.request.get(`http://127.0.0.1:${port}/api/v1/state`)).json()).toMatchObject({ main: { freqHz: finalHz } });
        await expect.poll(async () => Number((await page.locator('.receiver-instrument [data-vfo-freq]').first().innerText()).replace(/\D/g, ''))).toBe(finalHz);
        // MOR-4385 real widths; MOR-4387: a zoomed view follows the VFO (span
        // preserved); the FIX variant is granted clipped to the fixed edges.
        const view = zoom === 'span' ? (zoomMode === 1 ? [14_200_000, 14_300_000] : [14_150_000, 14_350_000]) : zoom ? [finalHz - 25_000, finalHz + 25_000] : [finalHz - 50_000, finalHz + 50_000];
        if (zoom === true) await expect(marker).toBeVisible();
        const live = frames.slice(quietStart.frames, quietEnd.frames);
        expect.soft(live.length).toBeGreaterThan(10);
        expect.soft((live[0]?.receivedAt ?? Infinity) - quietStart.atMs).toBeLessThan(1000);
        expect.soft(quietEnd.lastFrameAgeMs).toBeLessThan(1000);
        expect.soft(live.every((frame, i) => i === 0 || frame.receivedAt - live[i - 1].receivedAt < 1000)).toBe(true);
        expect.soft(live.at(-1)?.seq ?? 0).toBeGreaterThan(live[0]?.seq ?? 0);
        expect.soft(live.every(frame => frame.pixels && frame.view.every((hz, i) => hz === view[i])
          && frame.coverage.every((hz, i) => hz === view[i]))).toBe(true);
        await expect.soft(area.locator('.scope-hatch')).toHaveCount(0);
        expect.soft(quietEnd.work.labels[0]).toBe((view[0] / 1e6).toFixed(3));
        expect.soft(quietEnd.work.labels.at(-1)).toBe((view[1] / 1e6).toFixed(3));
        expect.soft(quietEnd.work.draw - quietStart.work.draw).toBeGreaterThan(0);
        // MOR-4387: zoom stays in follow (CENTER) — it must not write FIX
        // mode to the radio in either variant.
        if (zoom === true) expect.soft((await writes()).filter(receipt => receipt.sub === 0x14 && receipt.data.length === 2).at(-1)?.data[1] ?? 0).toBe(0);
        await writeFile(evidence.replace('.jsonl', '.pause'), 'pause');
        await page.waitForTimeout(2000);
        const idleStart = await work();
        const idleFrames = frames.length;
        await page.waitForTimeout(5000);
        const idleEnd = await work();
        expect.soft(frames.length).toBe(idleFrames);
        const report = { model, before, quietStart, quietEnd, idleStart, idleEnd, sent, grants, frames,
          writes: await writes(), commands, errors };
        await writeFile(info.outputPath('measurement.json'), JSON.stringify(report, null, 2));
        console.log(JSON.stringify({ model, before, quietStart, quietEnd, idleStart, idleEnd, errors }));
        expect.soft(quietEnd.subscriptions - quietStart.subscriptions).toBe(0);
        expect.soft(before.work.draw).toBeGreaterThan(0);
        expect.soft(sent.every(request => 'follow' in request.view)).toBe(true);
        if (zoom !== 'span') expect.soft(commands.filter(command => command.name === 'set_freq').length).toBeGreaterThan(0);
        expect.soft(commands.filter(command => command.name === 'set_scope_span')).toHaveLength(0);
        expect.soft(quietEnd.writes - quietStart.writes).toBe(0);
        expect.soft(commands.slice(quietStart.commands).filter(command => command.name === 'set_freq')).toHaveLength(0);
        expect.soft(quietEnd.subscriptions - before.subscriptions).toBeLessThanOrEqual(8);
        expect.soft(quietEnd.work.texture - quietStart.work.texture).toBe(0);
        expect.soft(quietEnd.work.static - quietStart.work.static).toBe(0);
        expect.soft(quietEnd.work.raf - quietStart.work.raf).toBeLessThanOrEqual(3 * (quietEnd.frames - quietStart.frames) + 8);
        expect.soft(idleEnd.raf - idleStart.raf).toBeLessThanOrEqual(4);
        expect.soft(idleEnd.draw - idleStart.draw).toBe(0);
        expect.soft(idleEnd.static - idleStart.static).toBe(0);
        expect.soft(idleEnd.rafMs - idleStart.rafMs).toBeLessThan(20);
        // MOR-4392: no page error and no Svelte effect-depth loop anywhere
        // in the scenario (shared panorama guard).
        expectPanoramaSettled(watch);
      } finally {
        await server.kill();
      }
    });
  }
}

// ── MOR-4387: the view follows the VFO unless explicitly pinned ──
// (a) click-tune burst + arrow tuning keeps the marker in view and the
//     follow subscription quiet; (b) zoom + arrow tuning keeps the marker,
//     the zoomed width, and emits no scope re-negotiation per step;
// (c) a band preset stays pinned while the VFO is inside and returns to
//     follow once the confirmed VFO leaves the pinned window.
for (const [index, model] of ['IC-705', 'IC-7300'].entries()) {
  test(`${model}: MOR-4387 click-tune burst then arrow tuning keeps the VFO marker in view`, async ({ page }, info) => {
    const port = 18860 + index;
    const server = await startJourneyServer(port, 14_250_000,
      ['--model', model, '--evidence', info.outputPath('radio.jsonl')], 'tests/e2e/panorama_web_server.py');
    const wsCommands: WsCommand[] = [];
    const watch = watchPanoramaErrors(page);
    try {
      const state = { sent: 0, frames: 0 };
      page.on('websocket', socket => {
        if (new URL(socket.url()).pathname === '/api/v1/ws') socket.on('framesent', ({ payload }) => {
          if (typeof payload === 'string') {
            const command = JSON.parse(payload) as WsCommand;
            if (command.type === 'cmd') wsCommands.push(command);
          }
        });
        if (!socket.url().endsWith('/api/v1/scope')) return;
        socket.on('framesent', ({ payload }) => {
          if (typeof payload === 'string' && (JSON.parse(payload) as { type: string }).type === 'subscribe') state.sent += 1;
        });
        socket.on('framereceived', ({ payload }) => { if (typeof payload !== 'string') state.frames += 1; });
      });
      await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
      const marker = page.locator('.waterfall-content .tune-line');
      await expect(marker).toBeVisible({ timeout: 30_000 });
      await expect.poll(() => state.frames, { timeout: 30_000 }).toBeGreaterThan(10);
      const area = page.locator('.spectrum-area').first();
      const box = await area.boundingBox();
      expect(box).not.toBeNull();
      for (const fraction of [0.62, 0.72, 0.42]) {
        await page.mouse.click(box!.x + box!.width * fraction, box!.y + box!.height * 0.3);
        await expect(marker).toBeVisible();
      }
      const radioState = async () => (await (await page.request.get(`http://127.0.0.1:${port}/api/v1/state`)).json()) as { main?: { freqHz?: number } };
      await expect.poll(async () => (await radioState()).main?.freqHz, { timeout: 10_000 }).not.toBe(14_250_000);
      const sentBefore = state.sent;
      const commandsBeforeArrows = wsCommands.length;
      // Realistic key-repeat pacing (the MOR-4387 repro's cadence): the
      // tuning accumulator (MOR-1864) re-reads the confirmed base at every
      // paced flush, so a zero-gap burst right after absolute click retunes
      // could key off a readback-lagged base. What this scenario pins is the
      // repro's own definition of correct: a clean sequential step series —
      // plus a live marker and zero scope re-negotiation.
      for (let i = 0; i < 12; i++) {
        await page.keyboard.press('ArrowRight');
        await page.waitForTimeout(150);
      }
      const arrowSteps = () => wsCommands.slice(commandsBeforeArrows)
        .filter(command => command.name === 'set_freq')
        .map(command => (command.params as { freq?: number }).freq ?? 0);
      await expect.poll(() => arrowSteps().length, { timeout: 15_000 }).toBe(12);
      const steps = arrowSteps();
      for (let i = 1; i < steps.length; i++) expect(steps[i]! - steps[i - 1]!).toBe(1_000);
      await expect.poll(radioState).toMatchObject({ main: { freqHz: steps.at(-1) } });
      await expect(marker).toBeVisible();
      expect(state.sent - sentBefore).toBe(0);
      expectPanoramaSettled(watch);
    } finally {
      await writeFile(info.outputPath('commands.json'), JSON.stringify(wsCommands)).catch(() => {});
      await server.kill();
    }
  });

  // Owner rule (no weakened tests — tests check what the user sees): 12
  // paced ArrowRight presses land the VFO exactly +12 kHz from the settled
  // frequency the last click tuned to. Each click is proved against the
  // viewport visible at click time (the rendered subscription bounds), so a
  // later click mapping against the moved viewport cannot hide behind a
  // hard-coded frequency. (MOR-4393: fixed on codex/mor-4393-tuning-steps —
  // the MOR-1864 accumulator continues a burst on echoes of its own sends,
  // so readback latency no longer loses steps.)
  test(`${model}: MOR-4393 click-tune burst then 12 arrows lands +12 kHz from the clicked frequency`, async ({ page }, info) => {
    const port = 18890 + index;
    const server = await startJourneyServer(port, 14_250_000,
      ['--model', model, '--evidence', info.outputPath('radio.jsonl')], 'tests/e2e/panorama_web_server.py');
    try {
      const frames = { count: 0 };
      page.on('websocket', socket => {
        if (!socket.url().endsWith('/api/v1/scope')) return;
        socket.on('framereceived', ({ payload }) => { if (typeof payload !== 'string') frames.count += 1; });
      });
      await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
      await expect.poll(() => frames.count, { timeout: 30_000 }).toBeGreaterThan(10);
      const radioState = async () => (await (await page.request.get(`http://127.0.0.1:${port}/api/v1/state`)).json()) as { main?: { freqHz?: number } };
      const panel = page.locator('.spectrum-panel').first();
      await expect.poll(async () => panel.getAttribute('data-scope-subscription')).not.toBeNull();
      const area = page.locator('.spectrum-area').first();
      const box = await area.boundingBox();
      expect(box).not.toBeNull();
      // Click-to-tune snaps to the 1 kHz tuning step (SpectrumPanel
      // snapFrequency over getTuningStep); the follow viewport is the
      // confirmed VFO ± span/2 (MOR-4387: the view follows the VFO).
      for (const fraction of [0.62, 0.72, 0.42]) {
        const subscription = JSON.parse((await panel.getAttribute('data-scope-subscription'))!) as
          { view: { follow: 'vfo'; spanHz: number } | { startHz: number; endHz: number } };
        const centerHz = (await radioState()).main!.freqHz!;
        const bounds = 'follow' in subscription.view
          ? [centerHz - subscription.view.spanHz / 2, centerHz + subscription.view.spanHz / 2]
          : [subscription.view.startHz, subscription.view.endHz];
        const expectedHz = Math.round((bounds[0]! + fraction * (bounds[1]! - bounds[0]!)) / 1000) * 1000;
        await page.mouse.click(box!.x + box!.width * fraction, box!.y + box!.height * 0.3);
        await expect.poll(async () => (await radioState()).main?.freqHz, { timeout: 10_000 }).toBe(expectedHz);
      }
      const clickedHz = (await radioState()).main!.freqHz!;
      for (let i = 0; i < 12; i++) {
        await page.keyboard.press('ArrowRight');
        await page.waitForTimeout(150);
      }
      await expect.poll(async () => (await radioState()).main?.freqHz, { timeout: 10_000 }).toBe(clickedHz + 12_000);
    } finally {
      await server.kill();
    }
  });

  test(`${model}: MOR-4387 zoom keeps follow and survives arrow tuning`, async ({ page }, info) => {
    const port = 18870 + index;
    const server = await startJourneyServer(port, 14_250_000,
      ['--model', model, '--evidence', info.outputPath('radio.jsonl')], 'tests/e2e/panorama_web_server.py');
    const watch = watchPanoramaErrors(page);
    try {
      const sent: Array<ScopeSubscription & { type: string }> = [];
      const frames = { count: 0 };
      page.on('websocket', socket => {
        if (!socket.url().endsWith('/api/v1/scope')) return;
        socket.on('framesent', ({ payload }) => {
          if (typeof payload === 'string' && (JSON.parse(payload) as { type: string }).type === 'subscribe') {
            sent.push(JSON.parse(payload));
          }
        });
        socket.on('framereceived', ({ payload }) => { if (typeof payload !== 'string') frames.count += 1; });
      });
      await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
      const marker = page.locator('.waterfall-content .tune-line');
      await expect(marker).toBeVisible({ timeout: 30_000 });
      await expect.poll(() => frames.count, { timeout: 30_000 }).toBeGreaterThan(10);
      await page.getByRole('button', { name: 'More scope controls' }).first().click();
      await page.getByTestId('scope-zoom-in').first().click();
      await page.keyboard.press('Escape');
      await expect.poll(() => sent.at(-1)?.view).toEqual({ follow: 'vfo', spanHz: 50_000 });
      await expect(marker).toBeVisible();
      const sentBefore = sent.length;
      for (let i = 0; i < 40; i++) await page.keyboard.press('ArrowRight');
      await expect.poll(async () => (await page.request.get(`http://127.0.0.1:${port}/api/v1/state`)).json())
        .toMatchObject({ main: { freqHz: 14_290_000 } });
      await expect.poll(async () => Number((await page.locator('.receiver-instrument [data-vfo-freq]').first().innerText()).replace(/\D/g, ''))).toBe(14_290_000);
      await expect(marker).toBeVisible();
      expect(sent.filter(request => 'follow' in request.view)).toHaveLength(sent.length);
      expect(sent.length - sentBefore).toBe(0);
      expectPanoramaSettled(watch);
    } finally {
      await server.kill();
    }
  });

  test(`${model}: MOR-4387 band preset stays pinned inside and returns to follow beyond`, async ({ page }, info) => {
    const port = 18880 + index;
    const server = await startJourneyServer(port, 14_250_000,
      ['--model', model, '--evidence', info.outputPath('radio.jsonl')], 'tests/e2e/panorama_web_server.py');
    const watch = watchPanoramaErrors(page);
    try {
      const sent: Array<ScopeSubscription & { type: string }> = [];
      const frames = { count: 0 };
      page.on('websocket', socket => {
        if (!socket.url().endsWith('/api/v1/scope')) return;
        socket.on('framesent', ({ payload }) => {
          if (typeof payload === 'string' && (JSON.parse(payload) as { type: string }).type === 'subscribe') {
            sent.push(JSON.parse(payload));
          }
        });
        socket.on('framereceived', ({ payload }) => { if (typeof payload !== 'string') frames.count += 1; });
      });
      await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
      const marker = page.locator('.waterfall-content .tune-line');
      await expect(marker).toBeVisible({ timeout: 30_000 });
      await expect.poll(() => frames.count, { timeout: 30_000 }).toBeGreaterThan(10);
      await page.getByRole('button', { name: 'More scope controls' }).first().click();
      const select = page.locator('[data-testid="scope-more-panel"] [data-testid="scope-preset-select"]');
      await select.selectOption({ index: 1 });
      await page.keyboard.press('Escape');
      await expect.poll(() => sent.at(-1)?.view).toEqual({ startHz: 14_000_000, endHz: 14_350_000 });
      for (let i = 0; i < 4; i++) await page.getByRole('button', { name: 'Increase tuning step' }).first().click();
      await expect.poll(() => sent.at(-1)?.view).toEqual({ startHz: 14_000_000, endHz: 14_350_000 });
      for (let i = 0; i < 4; i++) await page.keyboard.press('ArrowRight');
      await expect.poll(async () => (await page.request.get(`http://127.0.0.1:${port}/api/v1/state`)).json())
        .toMatchObject({ main: { freqHz: 14_650_000 } });
      await expect.poll(() => sent.at(-1)?.view).toEqual({ follow: 'vfo', spanHz: 100_000 });
      await expect(marker).toBeVisible();
      expectPanoramaSettled(watch);
    } finally {
      await server.kill();
    }
  });
}
