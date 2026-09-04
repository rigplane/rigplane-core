#!/usr/bin/env node
import { existsSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';
import { createServer } from 'vite';

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, '../..');
const outIndex = process.argv.indexOf('--out');
const output = outIndex >= 0
  ? path.resolve(process.cwd(), process.argv[outIndex + 1])
  : path.resolve(frontend, '../docs/plans/discovery-artifacts/touchscreen-needle-meter/current-render.png');

async function launch() {
  try {
    return await chromium.launch();
  } catch (bundledError) {
    try {
      return await chromium.launch({ channel: 'chrome' });
    } catch {
      // Continue to the explicit cache scan used on hosts with no channel.
    }
    const cache = path.join(homedir(), 'Library/Caches/ms-playwright');
    const candidates = existsSync(cache)
      ? readdirSync(cache)
        .filter((entry) => entry.startsWith('chromium-'))
        .sort((a, b) => Number(b.split('-').pop()) - Number(a.split('-').pop()))
        .flatMap((entry) => [
          path.join(cache, entry, 'chrome-mac-arm64', 'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'),
          path.join(cache, entry, 'chrome-mac-arm64', 'Chromium.app/Contents/MacOS/Chromium'),
        ])
      : [];
    const executablePath = candidates.find((candidate) => existsSync(candidate));
    if (!executablePath) throw bundledError;
    return chromium.launch({ executablePath });
  }
}

const server = await createServer({
  configFile: path.join(frontend, 'vite.fixtures.config.ts'),
  server: { host: '127.0.0.1', port: 0, strictPort: false },
  logLevel: 'error',
});

let browser;
try {
  await server.listen();
  const address = server.httpServer?.address();
  if (!address || typeof address === 'string') throw new Error('fixture server did not expose a TCP port');
  browser = await launch();
  const page = await browser.newPage({
    viewport: { width: 640, height: 240 },
    deviceScaleFactor: 1,
    colorScheme: 'dark',
    reducedMotion: 'reduce',
  });
  const errors = [];
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${address.port}/fixtures/design-a-face-v2/meter.html`, { waitUntil: 'load' });
  await page.waitForSelector('body[data-fixture-ready="true"]');
  await page.screenshot({ path: output, animations: 'disabled', caret: 'hide' });
  if (errors.length > 0) throw new Error(`fixture console errors: ${errors.join(' | ')}`);
  console.log(`capture: PASS ${output} 640x240 reduced-motion`);
} finally {
  await browser?.close();
  await server.close();
}
