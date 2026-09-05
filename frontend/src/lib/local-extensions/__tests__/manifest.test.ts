import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  LOCAL_EXTENSION_MANIFEST_URL,
  loadLocalExtensionManifest,
  parseLocalExtensionManifest,
} from '../manifest';

function mockResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

describe('loadLocalExtensionManifest', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('treats 404 as no local extensions', async () => {
    const fetch = vi.fn().mockResolvedValue(mockResponse(404, null));

    await expect(loadLocalExtensionManifest({ fetch })).resolves.toBeNull();
    expect(fetch).toHaveBeenCalledWith(
      LOCAL_EXTENSION_MANIFEST_URL,
      expect.objectContaining({
        credentials: 'same-origin',
        cache: 'no-store',
      }),
    );
  });

  it('treats network failures as no local extensions', async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError('failed'));

    await expect(loadLocalExtensionManifest({ fetch })).resolves.toBeNull();
  });

  it('treats invalid JSON as no local extensions', async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockRejectedValue(new SyntaxError('bad json')),
    } as unknown as Response);

    await expect(loadLocalExtensionManifest({ fetch })).resolves.toBeNull();
  });

  it('treats unsupported manifest versions as no local extensions', async () => {
    const fetch = vi.fn().mockResolvedValue(mockResponse(200, {
      version: 2,
      extensions: [{ id: 'one', mount: 'floating-overlay', entry: '/local/one.js' }],
    }));

    await expect(loadLocalExtensionManifest({ fetch })).resolves.toBeNull();
  });

  it('treats unsupported host API versions as no local extensions', async () => {
    const fetch = vi.fn().mockResolvedValue(mockResponse(200, {
      version: 1,
      host_api: '3.0',
      extensions: [{ id: 'one', mount: 'floating-overlay', entry: '/local/one.js' }],
    }));

    await expect(loadLocalExtensionManifest({ fetch })).resolves.toBeNull();
  });

  it('treats malformed host API versions as no local extensions', async () => {
    const fetch = vi.fn().mockResolvedValue(mockResponse(200, {
      version: 1,
      host_api: 1,
      extensions: [{ id: 'one', mount: 'floating-overlay', entry: '/local/one.js' }],
    }));

    await expect(loadLocalExtensionManifest({ fetch })).resolves.toBeNull();
  });

  it('loads valid same-origin floating overlay extensions', async () => {
    const fetch = vi.fn().mockResolvedValue(mockResponse(200, {
      version: 1,
      host_api: '2.0',
      extensions: [
        {
          id: 'meter',
          title: 'Meter',
          mount: 'floating-overlay',
          entry: '/local/meter.js?x=1#top',
          style: '/local/meter.css',
          requires: ['meter'],
        },
      ],
    }));

    const manifest = await loadLocalExtensionManifest({
      fetch,
      baseUrl: 'http://radio.local/ui/',
    });

    expect(manifest).toEqual({
      version: 1,
      host_api: '2.0',
      extensions: [
        {
          id: 'meter',
          title: 'Meter',
          mount: 'floating-overlay',
          entry: '/local/meter.js?x=1#top',
          style: '/local/meter.css',
          requires: ['meter'],
        },
      ],
    });
  });
});

describe('parseLocalExtensionManifest', () => {
  it.each(['1.0', undefined, '3.0', 2, null])('rejects incompatible host API %s', (host_api) => {
    expect(parseLocalExtensionManifest({
      version: 1,
      ...(host_api === undefined ? {} : { host_api }),
      extensions: [{ id: 'meter', mount: 'floating-overlay', entry: '/local/meter.js' }],
    })).toBeNull();
  });

  it('drops invalid extension descriptors', () => {
    const manifest = parseLocalExtensionManifest({
      version: 1,
      host_api: '2.0',
      extensions: [
        { id: 'ok', mount: 'floating-overlay', entry: '/local/ok.js' },
        { id: '', mount: 'floating-overlay', entry: '/local/no-id.js' },
        { id: 'bad-mount', mount: 'sidecar', entry: '/local/bad-mount.js' },
        { id: 'remote', mount: 'floating-overlay', entry: 'https://example.com/remote.js' },
      ],
    }, 'http://radio.local/');

    expect(manifest?.extensions).toEqual([
      {
        id: 'ok',
        mount: 'floating-overlay',
        entry: '/local/ok.js',
        title: undefined,
        requires: undefined,
        style: undefined,
      },
    ]);
  });

  it('returns null when no valid extensions remain', () => {
    expect(parseLocalExtensionManifest({
      version: 1,
      host_api: '2.0',
      extensions: [
        { id: 'remote', mount: 'floating-overlay', entry: 'https://example.com/remote.js' },
      ],
    }, 'http://radio.local/')).toBeNull();
  });
});
