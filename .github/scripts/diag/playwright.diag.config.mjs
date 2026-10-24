import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(new URL('../../../frontend/package.json', import.meta.url));
const { defineConfig, devices } = require('@playwright/test');

export default defineConfig({
  testDir: fileURLToPath(new URL('.', import.meta.url)),
  testMatch: 'launch.spec.mjs',
  workers: 1,
  retries: 0,
  reporter: 'line',
  outputDir: path.join(fileURLToPath(new URL('../../../frontend/out/', import.meta.url)), 'pw'),
  use: { ...devices['Desktop Chrome'], headless: true },
  projects: [{ name: 'chromium' }],
});
