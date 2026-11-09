import { createReadStream } from 'node:fs';
import { writeFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { parseCurrentScopeFrame, parseScopeGranted } from '../../../src/lib/runtime/scope-frame';
import type { ScopeGranted, ScopeSubscription } from '../../../src/lib/runtime/scope-view';
import type { WsCommand } from '../../../src/lib/types/protocol';
import { startJourneyServer } from './server-process';
import { parseScopeWriteReceipt } from './panorama-receipt';

interface Work { raf: number; rafMs: number; texture: number; draw: number; static: number; labels: string[] }
declare global { interface Window { panoramaWork: Work } }

for (const [index, model] of ['IC-705', 'IC-7300'].entries()) {
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
        const errors: string[] = [];
        const commands: WsCommand[] = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('console', message => { if (message.type() === 'error') console.log('browser', message.text()); });
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
        if (zoom === true) {
          await page.getByRole('button', { name: 'More scope controls' }).first().click();
          await page.getByTestId('scope-zoom-in').first().click();
          await expect.poll(() => frames.at(-1)?.view).toEqual([14_225_000, 14_275_000]);
          await page.keyboard.press('Escape');
        }
        const box = await area.boundingBox(); expect(box).not.toBeNull();
        const before = { subscriptions: sent.length, writes: (await writes()).length, work: await work() };
        if (zoom === 'span') {
          for (const [action, width] of [['Decrease scope span', 50000], ['Increase scope span', 100000], [']', 250000]] as const) {
            const mark = frames.length;
            if (action === ']') await page.keyboard.press(action);
            else await page.getByRole('button', { name: action, exact: true }).first().click();
            const bounds = [14_250_000 - width / 2, 14_250_000 + width / 2];
            await expect.poll(() => grants.at(-1)?.view).toEqual({ startHz: bounds[0], endHz: bounds[1] });
            await expect.poll(() => frames.slice(mark).some(frame => frame.pixels && frame.view.every((hz, i) => hz === bounds[i])
              && frame.coverage.every((hz, i) => hz === bounds[i]))).toBe(true);
            await expect(page.getByTestId('scope-span-value').first()).toHaveText(`±${width / 2000}k`);
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
        const view = zoom === 'span' ? [14_125_000, 14_375_000] : zoom ? [14_225_000, 14_275_000] : [finalHz - 50_000, finalHz + 50_000];
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
        expect.soft(sent.slice(before.subscriptions).every(request => ('follow' in request.view) !== (zoom === true))).toBe(true);
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
        expect(errors).toEqual([]);
      } finally {
        await server.kill();
      }
    });
  }
}
