import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  previewBundle,
  sendBundle,
  saveBundle,
  deletePreview,
  DiagnosticsApiError,
  type PreviewResponse,
  type ReportSubmitted,
} from '../diagnostics';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makePreviewResponse(): PreviewResponse {
  return {
    preview_id: 'prev-abc123',
    csrf_token: 'csrf-xyz',
    manifest: { schema_version: 1, format: 'icom-lan-diag/1' },
    files: [
      { path: 'manifest.json', size: 256 },
      { path: 'logs/system.log', size: 4096 },
    ],
    total_size_bytes: 4352,
    redactions_applied: ['paths', 'ips'],
    endpoint_url: 'https://support.example.com/intake',
  };
}

function makeReportSubmitted(): ReportSubmitted {
  return {
    report_id: 'report-7777',
    support_url: 'https://support.example.com/r/report-7777',
    received_at_unix: 1714780800,
    auth_class: 'anonymous',
  };
}

/** Minimal Response-like object good enough for these tests. */
function fakeOkResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    json: () => Promise.resolve(body),
    blob: () => Promise.resolve(new Blob([JSON.stringify(body)])),
  } as unknown as Response;
}

function fakeErrorResponse(status: number, body: unknown, statusText = 'Error'): Response {
  return {
    ok: false,
    status,
    statusText,
    json: () => Promise.resolve(body),
    blob: () => Promise.resolve(new Blob()),
  } as unknown as Response;
}

// ─── previewBundle ───────────────────────────────────────────────────────────

describe('previewBundle', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    try {
      globalThis.localStorage?.clear?.();
    } catch {
      // some other test may have replaced localStorage with a stub
    }
  });

  it('POSTs to /api/v1/diagnose/preview with JSON body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(fakeOkResponse(makePreviewResponse()));
    globalThis.fetch = fetchMock;

    const result = await previewBundle({
      description: 'Audio dropouts',
      issue_ref: 'https://github.com/example/icom-lan/issues/42',
      email: 'user@example.com',
      callsign: 'N0CALL',
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/diagnose/preview');
    expect(init.method).toBe('POST');
    expect(init.headers['Content-Type']).toBe('application/json');
    const body = JSON.parse(init.body);
    expect(body.description).toBe('Audio dropouts');
    expect(body.callsign).toBe('N0CALL');
    expect(result.preview_id).toBe('prev-abc123');
    expect(result.csrf_token).toBe('csrf-xyz');
  });

  it('includes Authorization header when auth token is stored', async () => {
    // Stub a minimal localStorage — robust against earlier tests that may
    // have replaced globalThis.localStorage (vitest fast-pool isolate:false).
    const store: Record<string, string> = { 'icom-lan-auth-token': 'tok-secret' };
    const stub = {
      getItem: (k: string) => (k in store ? store[k] : null),
      setItem: (k: string, v: string) => {
        store[k] = v;
      },
      removeItem: (k: string) => {
        delete store[k];
      },
      clear: () => {
        for (const k of Object.keys(store)) delete store[k];
      },
    };
    Object.defineProperty(globalThis, 'localStorage', {
      value: stub,
      configurable: true,
      writable: true,
    });

    const fetchMock = vi.fn().mockResolvedValue(fakeOkResponse(makePreviewResponse()));
    globalThis.fetch = fetchMock;

    await previewBundle({});

    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBe('Bearer tok-secret');
  });

  it('throws DiagnosticsApiError with server-supplied code on 4xx', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      fakeErrorResponse(403, { error: 'origin_mismatch', message: 'Bad origin' }, 'Forbidden'),
    );
    globalThis.fetch = fetchMock;

    await expect(previewBundle({})).rejects.toBeInstanceOf(DiagnosticsApiError);
    try {
      await previewBundle({});
    } catch (err) {
      const e = err as DiagnosticsApiError;
      expect(e.code).toBe('origin_mismatch');
      expect(e.detail).toBe('Bad origin');
      expect(e.httpStatus).toBe(403);
    }
  });

  it('falls back to "preview_failed" when server response has no error code', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      fakeErrorResponse(500, null, 'Internal Server Error'),
    );
    globalThis.fetch = fetchMock;

    try {
      await previewBundle({});
      throw new Error('expected throw');
    } catch (err) {
      const e = err as DiagnosticsApiError;
      expect(e.code).toBe('preview_failed');
      expect(e.httpStatus).toBe(500);
    }
  });
});

// ─── sendBundle ──────────────────────────────────────────────────────────────

describe('sendBundle', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    try {
      globalThis.localStorage?.clear?.();
    } catch {
      // some other test may have replaced localStorage with a stub
    }
  });

  it('POSTs CSRF token + consent flag', async () => {
    const fetchMock = vi.fn().mockResolvedValue(fakeOkResponse(makeReportSubmitted()));
    globalThis.fetch = fetchMock;

    const result = await sendBundle('prev-abc', 'csrf-xyz');

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/diagnose/send');
    expect(init.method).toBe('POST');
    expect(init.headers['X-Diagnostic-CSRF']).toBe('csrf-xyz');
    const body = JSON.parse(init.body);
    expect(body).toEqual({ preview_id: 'prev-abc', consent: true });
    expect(result.report_id).toBe('report-7777');
  });

  it('surfaces rate_limited error code', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      fakeErrorResponse(429, { error: 'rate_limited', message: 'Try again later' }),
    );

    try {
      await sendBundle('prev-abc', 'csrf-xyz');
      throw new Error('expected throw');
    } catch (err) {
      const e = err as DiagnosticsApiError;
      expect(e.code).toBe('rate_limited');
      expect(e.httpStatus).toBe(429);
    }
  });

  it('carries retry_after_seconds from the server response body', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      fakeErrorResponse(429, {
        error: 'rate_limited',
        message: 'Try again later',
        retry_after_seconds: 30,
      }),
    );

    try {
      await sendBundle('prev-abc', 'csrf-xyz');
      throw new Error('expected throw');
    } catch (err) {
      const e = err as DiagnosticsApiError;
      expect(e.code).toBe('rate_limited');
      expect(e.retryAfterSeconds).toBe(30);
    }
  });
});

// ─── saveBundle ──────────────────────────────────────────────────────────────

describe('saveBundle', () => {
  afterEach(() => vi.restoreAllMocks());

  it('returns a Blob when server responds 200', async () => {
    const fakeBlob = new Blob(['zip-bytes']);
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      blob: () => Promise.resolve(fakeBlob),
      json: () => Promise.resolve({}),
    } as unknown as Response);

    const blob = await saveBundle('prev-abc', 'csrf-xyz');
    expect(blob).toBeInstanceOf(Blob);
  });

  it('sends CSRF header on save', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      blob: () => Promise.resolve(new Blob()),
      json: () => Promise.resolve({}),
    } as unknown as Response);
    globalThis.fetch = fetchMock;

    await saveBundle('prev-abc', 'csrf-xyz');

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/diagnose/save');
    expect(init.headers['X-Diagnostic-CSRF']).toBe('csrf-xyz');
  });

  it('throws DiagnosticsApiError on 4xx', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      fakeErrorResponse(404, { error: 'preview_not_found', message: 'gone' }),
    );
    await expect(saveBundle('prev-abc', 'csrf-xyz')).rejects.toBeInstanceOf(
      DiagnosticsApiError,
    );
  });
});

// ─── deletePreview ───────────────────────────────────────────────────────────

describe('deletePreview', () => {
  afterEach(() => vi.restoreAllMocks());

  it('DELETEs /api/v1/diagnose/preview/{id} with CSRF header', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      statusText: 'No Content',
      json: () => Promise.resolve({}),
    } as unknown as Response);
    globalThis.fetch = fetchMock;

    await deletePreview('prev-abc', 'csrf-xyz');

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/v1/diagnose/preview/prev-abc');
    expect(init.method).toBe('DELETE');
    expect(init.headers['X-Diagnostic-CSRF']).toBe('csrf-xyz');
  });

  it('throws DiagnosticsApiError on 4xx', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      fakeErrorResponse(403, { error: 'forbidden', message: 'csrf bad' }),
    );
    await expect(deletePreview('prev-abc', 'csrf-xyz')).rejects.toBeInstanceOf(
      DiagnosticsApiError,
    );
  });
});
