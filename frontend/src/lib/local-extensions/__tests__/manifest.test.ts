import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  LOCAL_EXTENSION_MANIFEST_URL,
  LOCAL_SUPERVISOR_META_NAME,
  loadLocalExtensionManifest,
  localSupervisorAdvertised,
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

    await expect(loadLocalExtensionManifest({ fetch, supervisorAdvertised: true })).resolves.toBeNull();
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

    await expect(loadLocalExtensionManifest({ fetch, supervisorAdvertised: true })).resolves.toBeNull();
  });

  it('treats invalid JSON as no local extensions', async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockRejectedValue(new SyntaxError('bad json')),
    } as unknown as Response);

    await expect(loadLocalExtensionManifest({ fetch, supervisorAdvertised: true })).resolves.toBeNull();
  });

  it('treats unsupported manifest versions as no local extensions', async () => {
    const fetch = vi.fn().mockResolvedValue(mockResponse(200, {
      version: 2,
      extensions: [{ id: 'one', mount: 'floating-overlay', entry: '/local/one.js' }],
    }));

    await expect(loadLocalExtensionManifest({ fetch, supervisorAdvertised: true })).resolves.toBeNull();
  });

  it('treats unsupported host API versions as no local extensions', async () => {
    const fetch = vi.fn().mockResolvedValue(mockResponse(200, {
      version: 1,
      host_api: '3.0',
      extensions: [{ id: 'one', mount: 'floating-overlay', entry: '/local/one.js' }],
    }));

    await expect(loadLocalExtensionManifest({ fetch, supervisorAdvertised: true })).resolves.toBeNull();
  });

  it('treats malformed host API versions as no local extensions', async () => {
    const fetch = vi.fn().mockResolvedValue(mockResponse(200, {
      version: 1,
      host_api: 1,
      extensions: [{ id: 'one', mount: 'floating-overlay', entry: '/local/one.js' }],
    }));

    await expect(loadLocalExtensionManifest({ fetch, supervisorAdvertised: true })).resolves.toBeNull();
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
      supervisorAdvertised: true,
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

/**
 * MOR-2242 — /api/local/v1/* is the Pro supervisor surface and does not
 * exist on a core-only server, and the browser logs a console error for every
 * failed fetch regardless of how page JS handles the response. So the loader
 * must not fire the manifest request at all unless the local supervisor
 * advertises itself through the `<meta name="rigplane-local-supervisor">`
 * marker injected into the served page.
 */
describe('local supervisor advertisement gate (MOR-2242)', () => {
  afterEach(() => {
    document.querySelector(`meta[name="${LOCAL_SUPERVISOR_META_NAME}"]`)?.remove();
  });

  it('localSupervisorAdvertised reads the supervisor meta tag from the document', () => {
    expect(localSupervisorAdvertised()).toBe(false);
    const meta = document.createElement('meta');
    meta.name = LOCAL_SUPERVISOR_META_NAME;
    document.head.appendChild(meta);
    expect(localSupervisorAdvertised()).toBe(true);
  });

  it('does not fetch the manifest when no local supervisor advertises itself', async () => {
    const fetch = vi.fn();

    await expect(loadLocalExtensionManifest({ fetch })).resolves.toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('fetches the manifest when the supervisor meta tag is present', async () => {
    const meta = document.createElement('meta');
    meta.name = LOCAL_SUPERVISOR_META_NAME;
    document.head.appendChild(meta);
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
