/**
 * Diagnostic-report REST client (issue #1397).
 *
 * Typed wrapper around the four diagnose endpoints introduced by #1414:
 *   - POST   /api/v1/diagnose/preview
 *   - POST   /api/v1/diagnose/send
 *   - POST   /api/v1/diagnose/save
 *   - DELETE /api/v1/diagnose/preview/{preview_id}
 *
 * Lives in the `lib/api/` layer (a peer to `lib/transport/`) — components in
 * `dialogs/` and `wiring/` may import this module directly. Layout/panel
 * presentation components must NOT import it; they should pass callbacks
 * down to the dialog wiring instead.
 *
 * Auth header behaviour mirrors `transport/http-client.ts` (Bearer token
 * from localStorage, key `icom-lan-auth-token`). The helper is duplicated
 * here on purpose — keeping `lib/api/` independent of transport internals.
 *
 * See `docs/plans/2026-05-03-diagnostic-data-collection-design.md` §4.9.
 */

const BASE = '/api/v1/diagnose';

// ─── Types ───────────────────────────────────────────────────────────────────

export interface PreviewRequest {
  description?: string;
  issue_ref?: string;
  email?: string;
  callsign?: string;
}

export interface FileEntry {
  path: string;
  size: number;
}

export interface PreviewResponse {
  preview_id: string;
  csrf_token: string;
  manifest: Record<string, unknown>;
  files: FileEntry[];
  total_size_bytes: number;
  redactions_applied: string[];
  endpoint_url: string;
}

export interface ReportSubmitted {
  report_id: string;
  support_url: string;
  received_at_unix: number;
  auth_class: 'anonymous' | 'authenticated';
}

/** Typed error raised on any non-2xx response from the diagnose endpoints. */
export class DiagnosticsApiError extends Error {
  public readonly code: string;
  public readonly detail: string;
  public readonly httpStatus: number;
  public readonly retryAfterSeconds?: number;

  constructor(
    code: string,
    detail: string,
    httpStatus: number,
    retryAfterSeconds?: number,
  ) {
    super(`${code}: ${detail}`);
    this.name = 'DiagnosticsApiError';
    this.code = code;
    this.detail = detail;
    this.httpStatus = httpStatus;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function getAuthHeaders(): Record<string, string> {
  const storage = globalThis.localStorage;
  if (!storage || typeof storage.getItem !== 'function') {
    return {};
  }
  const token = storage.getItem('rigplane-auth-token');
  if (token) return { Authorization: `Bearer ${token}` };
  return {};
}

interface ErrorBody {
  error?: string;
  message?: string;
  retry_after_seconds?: number;
}

async function safeJson(res: Response): Promise<ErrorBody | null> {
  try {
    return (await res.json()) as ErrorBody;
  } catch {
    return null;
  }
}

async function raiseFromResponse(
  res: Response,
  fallbackCode: string,
): Promise<never> {
  const body = await safeJson(res);
  const code = body?.error ?? fallbackCode;
  const detail = body?.message ?? res.statusText ?? '';
  const retryAfter =
    typeof body?.retry_after_seconds === 'number' ? body.retry_after_seconds : undefined;
  throw new DiagnosticsApiError(code, detail, res.status, retryAfter);
}

// ─── API ─────────────────────────────────────────────────────────────────────

/**
 * Build a diagnostic preview bundle on the server.
 *
 * The server returns a `preview_id` + `csrf_token` pair that must be supplied
 * to subsequent send/save/delete calls. The user-facing manifest is included
 * so the dialog can render the file list and redaction summary.
 */
export async function previewBundle(req: PreviewRequest): Promise<PreviewResponse> {
  const res = await fetch(`${BASE}/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(req),
  });
  if (!res.ok) await raiseFromResponse(res, 'preview_failed');
  return (await res.json()) as PreviewResponse;
}

/**
 * Upload a previously-generated bundle to the configured support endpoint.
 *
 * Requires both `preview_id` and the matching `csrf_token` from the preview
 * response. The server enforces consent server-side; we always send `true`
 * because the dialog UI gates the Send button on an explicit checkbox.
 */
export async function sendBundle(
  previewId: string,
  csrfToken: string,
): Promise<ReportSubmitted> {
  const res = await fetch(`${BASE}/send`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Diagnostic-CSRF': csrfToken,
      ...getAuthHeaders(),
    },
    body: JSON.stringify({ preview_id: previewId, consent: true }),
  });
  if (!res.ok) await raiseFromResponse(res, 'send_failed');
  return (await res.json()) as ReportSubmitted;
}

/**
 * Download the bundle locally as a ZIP file. Caller is responsible for
 * triggering the actual download (typically via `URL.createObjectURL`).
 */
export async function saveBundle(
  previewId: string,
  csrfToken: string,
): Promise<Blob> {
  const res = await fetch(`${BASE}/save`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Diagnostic-CSRF': csrfToken,
      ...getAuthHeaders(),
    },
    body: JSON.stringify({ preview_id: previewId }),
  });
  if (!res.ok) await raiseFromResponse(res, 'save_failed');
  return res.blob();
}

/**
 * Discard a server-side preview without sending. Should be called whenever
 * the user cancels the dialog after generating a preview, to free server
 * resources promptly (the server will GC them eventually either way).
 */
export async function deletePreview(
  previewId: string,
  csrfToken: string,
): Promise<void> {
  const res = await fetch(`${BASE}/preview/${previewId}`, {
    method: 'DELETE',
    headers: { 'X-Diagnostic-CSRF': csrfToken, ...getAuthHeaders() },
  });
  if (!res.ok) await raiseFromResponse(res, 'delete_failed');
}
