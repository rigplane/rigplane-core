import { chromium, webkit } from '@playwright/test';
import { createServer } from 'node:http';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const output = process.env.PROFILE_OUTPUT;
assert(output && path.isAbsolute(output), 'PROFILE_OUTPUT must be absolute');
const dist = path.join(output, 'dist');
const smoke = process.argv.includes('--preflight');
const browserType = process.argv.includes('--webkit') ? webkit : chromium;
const serverRequests = [];
const blocked = [];
const server = createServer(async (req, res) => {
  const pathname = new URL(req.url, 'http://127.0.0.1').pathname;
  serverRequests.push(pathname);
  const file = path.resolve(dist, `.${pathname}`);
  if (!file.startsWith(dist + path.sep) || pathname.includes('/api/')) {
    blocked.push(pathname); res.writeHead(403); res.end(); return;
  }
  try {
    const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' }[path.extname(file)] ?? 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': mime }); res.end(await readFile(file));
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
let browser;
const summaries = [];
let failed = false;
try {
  browser = await browserType.launch({ headless: true });
  for (const [index, mode] of (smoke ? ['mounted', 'store-only'] : ['mounted', 'store-only', 'store-only', 'mounted']).entries()) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
    const forbidden = []; const errors = [];
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin || url.pathname.startsWith('/api/') || route.request().method() !== 'GET') {
        forbidden.push(route.request().url()); await route.abort();
      } else await route.continue();
    });
    await context.routeWebSocket('**/*', socket => { forbidden.push(socket.url()); socket.close(); });
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    const name = `${browserType.name()}-${index + 1}-${mode}`;
    await page.goto(`${origin}/profiling/mor2551/index.html?mode=${mode}`);
    await page.waitForFunction(() => window.replay?.snapshot().stateSequence === 1, { timeout: 10000 });
    if (mode === 'mounted') {
      await page.locator('.desktop-control-face').waitFor({ state: 'visible' });
      await page.getByRole('button', { name: 'VIEW ON', exact: true }).click();
      await page.getByRole('button', { name: 'VIEW OFF', exact: true }).waitFor({ state: 'visible' });
    }
    for (const expected of [2, 3]) {
      await page.evaluate(() => window.replay.emitState());
      await page.waitForFunction(sequence => window.replay.snapshot().stateSequence === sequence, expected);
    }
    const before = await page.evaluate(() => window.replay.snapshot());
    await writeFile(path.join(output, `${name}-preflight.json`), JSON.stringify({ ...before, pageErrors: errors, forbidden, serverBlocked: blocked }, null, 2));
    assert.equal(before.accepted.length, 3, 'full plus two deltas must commit once each');
    assert.equal(before.ptt, false);
    assert.deepEqual(before.errors, []); assert.deepEqual(errors, []); assert.deepEqual(forbidden, []); assert.deepEqual(blocked, []);
    assert.equal(before.sent.filter(m => m.type !== 'subscribe' && m.type !== 'ping').length, 0);
    await writeFile(path.join(output, `${name}-preflight.json`), JSON.stringify(before, null, 2));
    console.log(JSON.stringify({ preflight: name, accepted: before.accepted.length, fields: before.fieldCount, observed: before.observedFields, deltaUnits: before.deltaUnits, commands: 0, blocked: 0 }));
    if (!smoke) {
      await page.evaluate(() => window.replay.start());
      await page.waitForTimeout(5000);
      const captureStart = await page.evaluate(() => performance.now());
      let cdp;
      if (browserType === chromium) {
        cdp = await context.newCDPSession(page);
        await cdp.send('Profiler.enable'); await cdp.send('Profiler.setSamplingInterval', { interval: 1000 });
        await cdp.send('Profiler.start');
      }
      await page.waitForTimeout(20000);
      if (cdp) {
        const { profile } = await cdp.send('Profiler.stop');
        await writeFile(path.join(output, `${name}.cpuprofile`), JSON.stringify(profile));
      }
      await page.evaluate(() => window.replay.stop());
      const after = await page.evaluate(() => window.replay.snapshot());
      assert.deepEqual(after.errors, []); assert.deepEqual(errors, []); assert.deepEqual(forbidden, []); assert.deepEqual(blocked, []);
      assert.equal(after.accepted.length, after.arrivals.filter(a => a.kind === 'state').length + 1);
      assert.equal(after.ptt, false);
      const captured = after.arrivals.filter(a => a.time >= captureStart);
      const summary = { name, browser: browser.version(), accepted: after.accepted.length,
        captureStateFrames: captured.filter(a => a.kind === 'state').length,
        captureEvents: captured.filter(a => a.kind === 'event').length,
        fieldCount: after.fieldCount, observedFields: after.observedFields, startupWrites: after.startupWrites,
        deltaUnits: after.deltaUnits, fullUnits: after.fullUnits,
        outgoingCommands: after.sent.filter(m => m.type !== 'subscribe' && m.type !== 'ping').length,
        externalRequests: forbidden.length, serverApiRequests: blocked.length, captureStart, after };
      summaries.push(summary);
      await writeFile(path.join(output, `${name}-result.json`), JSON.stringify(summary, null, 2));
      console.log(JSON.stringify({ completed: name, accepted: summary.accepted, stateFrames: summary.captureStateFrames, events: summary.captureEvents, commands: summary.outgoingCommands }));
    }
    await context.close();
  }
} catch (error) {
  failed = true;
  await mkdir(output, { recursive: true });
  await writeFile(path.join(output, `${browserType.name()}-failure.json`), JSON.stringify({ error: error.stack, serverRequests, blocked, summaries }, null, 2));
  throw error;
} finally {
  await browser?.close(); await new Promise(resolve => server.close(resolve));
  await writeFile(path.join(output, `${browserType.name()}-${smoke ? 'preflight' : 'runs'}.json`), JSON.stringify({ failed, summaries, serverRequests, blocked }, null, 2));
}
