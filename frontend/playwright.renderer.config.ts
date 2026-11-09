import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e/renderers', workers: 1, retries: 0, timeout: 30_000,
  outputDir: process.env.RP_RENDERER_OUTPUT ?? '../.playwright-output/renderers',
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:5598', viewport: { width: 1280, height: 800 } },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium', launchOptions: { args: ['--use-angle=metal', '--enable-gpu'] } } },
    { name: 'webkit', use: { browserName: 'webkit', launchOptions: process.env.RP_WEBKIT_EXECUTABLE
      ? { executablePath: process.env.RP_WEBKIT_EXECUTABLE } : {} } },
  ],
  webServer: {
    command: 'npx vite --config vite.fixtures.config.ts --port 5598 --strictPort --host 127.0.0.1',
    url: 'http://127.0.0.1:5598/fixtures/spectrum-witness.html', timeout: 30_000,
  },
});
