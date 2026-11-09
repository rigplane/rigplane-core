import { createReadStream } from 'node:fs';
import { writeFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { parseCurrentScopeFrame, parseScopeGranted } from '../../../src/lib/runtime/scope-frame';
import type { ScopeGranted, ScopeSubscription } from '../../../src/lib/runtime/scope-view';
import type { WsCommand } from '../../../src/lib/types/protocol';
import { startJourneyServer } from './server-process';

interface Work { raf: number; rafMs: number; texture: number; draw: number; static: number }
declare global { interface Window { panoramaWork: Work } }

for (const [index, model] of ['IC-705', 'IC-7300'].entries()) {
  test(`${model}: one drag settles subscriptions and WebKit renderer work`, async ({ page }, info) => {
    const evidence = info.outputPath('radio.jsonl');
    const port = Number(process.env.PANORAMA_PORT ?? 18845) + index;
    const server = await startJourneyServer(port, 14_250_000,
      ['--model', model, '--evidence', evidence], 'tests/e2e/panorama_web_server.py');
    const sent: Array<ScopeSubscription & { type: string }> = [];
    const grants: ScopeGranted[] = [];
    const frames: Array<{ view: number[]; coverage: number[] }> = [];
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
            coverage: [parsed.frame.coverageStartHz, parsed.frame.coverageEndHz] });
        }
      });
    });
    await page.addInitScript(() => {
      const work: Work = { raf: 0, rafMs: 0, texture: 0, draw: 0, static: 0 };
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
        work.static++; return Reflect.apply(paint, this, args);
      };
    });
    const work = () => page.evaluate(() => ({ ...window.panoramaWork }));
    const writes = async () => {
      let contents = '';
      for await (const chunk of createReadStream(evidence, { encoding: 'utf8' })) contents += chunk;
      return contents.trim().split('\n').filter(Boolean).map(
        line => JSON.parse(line) as { sub: number; data: number[]; span: number });
    };
    try {
      await page.goto(`http://127.0.0.1:${port}/?ui=v2`);
      await expect.poll(() => frames.length, { timeout: 30_000 }).toBeGreaterThan(10);
      const area = page.locator('.spectrum-area').first();
      const box = await area.boundingBox(); expect(box).not.toBeNull();
      const before = { subscriptions: sent.length, writes: (await writes()).length, work: await work() };
      await page.mouse.move(box!.x + box!.width * 0.6, box!.y + box!.height * 0.3);
      await page.mouse.down();
      await page.mouse.move(box!.x + box!.width * 0.45, box!.y + box!.height * 0.3, { steps: 5 });
      await page.mouse.up();
      // Observation windows, not synchronization delays: 10 s after release,
      // with the final 5 s isolated from gesture/acknowledgement work.
      await page.waitForTimeout(5000);
      const quietStart = { subscriptions: sent.length, writes: (await writes()).length, commands: commands.length, frames: frames.length, work: await work() };
      await page.waitForTimeout(5000);
      const quietEnd = { subscriptions: sent.length, writes: (await writes()).length, commands: commands.length, frames: frames.length, work: await work() };
      await writeFile(evidence.replace('.jsonl', '.pause'), 'pause');
      await page.waitForTimeout(2000);
      const idleStart = await work();
      await page.waitForTimeout(5000);
      const idleEnd = await work();
      const report = { model, before, quietStart, quietEnd, idleStart, idleEnd, sent, grants, frames,
        writes: await writes(), commands, errors };
      await writeFile(info.outputPath('measurement.json'), JSON.stringify(report, null, 2));
      console.log(JSON.stringify({ model, before, quietStart, quietEnd, idleStart, idleEnd, errors }));
      expect.soft(quietEnd.subscriptions - quietStart.subscriptions).toBe(0);
      expect.soft(before.work.draw).toBeGreaterThan(0);
      expect.soft(sent.every(request => 'follow' in request.view)).toBe(true);
      expect.soft(commands.filter(command => command.name === 'set_freq').length).toBeGreaterThan(0);
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
      console.log('receipts', JSON.stringify({ sent: sent.length, grants: grants.length, frames: frames.length, errors }));
      await server.kill();
    }
  });
}
