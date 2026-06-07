import { describe, expect, it } from 'vitest';

import config from '../../../../playwright.i18n.config';

describe('playwright i18n config', () => {
  it('starts its own preview server instead of reusing a stale local port', () => {
    expect(config.webServer).toMatchObject({
      reuseExistingServer: false,
    });
  });

  it('serves an immutable dist snapshot for the full i18n run', () => {
    const webServer =
      Array.isArray(config.webServer) ? config.webServer[0] : config.webServer;

    expect(webServer?.command).toContain('scripts/i18n-preview-server.mjs');
    expect(webServer?.command).not.toContain('vite preview');
  });
});
