import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';

// ─── Hoisted mocks: must be defined before the component imports them. ──────
const { previewSpy, sendSpy, saveSpy, deleteSpy } = vi.hoisted(() => ({
  previewSpy: vi.fn(),
  sendSpy: vi.fn(),
  saveSpy: vi.fn(),
  deleteSpy: vi.fn(),
}));

vi.mock('$lib/api/diagnostics', () => {
  class DiagnosticsApiError extends Error {
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
  return {
    previewBundle: previewSpy,
    sendBundle: sendSpy,
    saveBundle: saveSpy,
    deletePreview: deleteSpy,
    DiagnosticsApiError,
  };
});

// Import the mocked module so we can throw the same DiagnosticsApiError class
// the component compares against in `instanceof` checks.
const apiModule = await import('$lib/api/diagnostics');
const { DiagnosticsApiError } = apiModule;

// Import the component AFTER the mock is registered.
import SendReportDialog from '../SendReportDialog.svelte';

// ─── Helpers ────────────────────────────────────────────────────────────────

function makePreview() {
  return {
    preview_id: 'prev-test',
    csrf_token: 'csrf-test',
    manifest: {},
    files: [
      { path: 'manifest.json', size: 256 },
      { path: 'logs/system.log', size: 4096 },
    ],
    total_size_bytes: 4352,
    redactions_applied: ['paths', 'ips'],
    endpoint_url: 'https://support.example.com/intake',
  };
}

function makeReportSubmitted() {
  return {
    report_id: 'report-7777',
    support_url: 'https://support.example.com/r/report-7777',
    received_at_unix: 1714780800,
    auth_class: 'anonymous' as const,
  };
}

function setup(props: { open?: boolean; onClose?: () => void } = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const onClose = props.onClose ?? vi.fn();
  const component = mount(SendReportDialog, {
    target,
    props: { open: props.open ?? true, onClose },
  });
  return { target, onClose, component };
}

// ─── Tests ──────────────────────────────────────────────────────────────────

describe('SendReportDialog', () => {
  beforeEach(() => {
    previewSpy.mockReset();
    sendSpy.mockReset();
    saveSpy.mockReset();
    deleteSpy.mockReset();
  });

  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('does not render when open=false', () => {
    const { target } = setup({ open: false });
    expect(target.querySelector('[data-testid="send-report-backdrop"]')).toBeNull();
  });

  it('renders the form screen by default when open', () => {
    const { target } = setup({ open: true });
    expect(target.querySelector('[data-testid="field-description"]')).not.toBeNull();
    expect(target.querySelector('[data-testid="btn-generate"]')).not.toBeNull();
  });

  it('transitions to preview screen on successful preview', async () => {
    previewSpy.mockResolvedValue(makePreview());
    const { target, component } = setup({ open: true });

    const btn = target.querySelector('[data-testid="btn-generate"]') as HTMLButtonElement;
    btn.click();

    // Allow the awaited fetch to resolve and effects to flush.
    await tick();
    await tick();
    flushSync();

    expect(previewSpy).toHaveBeenCalledTimes(1);
    // With an empty form, all fields should be undefined.
    expect(previewSpy.mock.calls[0][0]).toEqual({
      description: undefined,
      issue_ref: undefined,
      email: undefined,
      callsign: undefined,
    });
    expect(target.querySelector('[data-testid="file-list"]')).not.toBeNull();
    expect(target.querySelector('[data-testid="meta-endpoint"]')?.textContent).toContain(
      'support.example.com',
    );

    unmount(component);
  });

  it('disables Send until consent checkbox is checked', async () => {
    previewSpy.mockResolvedValue(makePreview());
    const { target, component } = setup({ open: true });

    (target.querySelector('[data-testid="btn-generate"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const sendBtn = target.querySelector(
      '[data-testid="btn-send"]',
    ) as HTMLButtonElement;
    expect(sendBtn.disabled).toBe(true);

    const checkbox = target.querySelector(
      '[data-testid="consent-checkbox"]',
    ) as HTMLInputElement;
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync();

    expect(sendBtn.disabled).toBe(false);

    unmount(component);
  });

  it('calls saveBundle when Save locally is clicked', async () => {
    previewSpy.mockResolvedValue(makePreview());
    saveSpy.mockResolvedValue(new Blob(['zip-bytes']));

    // jsdom does not implement createObjectURL by default
    const createUrlSpy = vi
      .spyOn(URL, 'createObjectURL')
      .mockReturnValue('blob://test');
    const revokeUrlSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    const { target, component } = setup({ open: true });

    (target.querySelector('[data-testid="btn-generate"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const saveBtn = target.querySelector(
      '[data-testid="btn-save"]',
    ) as HTMLButtonElement;
    saveBtn.click();
    await tick();
    await tick();
    flushSync();

    expect(saveSpy).toHaveBeenCalledTimes(1);
    expect(saveSpy.mock.calls[0]).toEqual(['prev-test', 'csrf-test']);
    expect(createUrlSpy).toHaveBeenCalled();

    createUrlSpy.mockRestore();
    revokeUrlSpy.mockRestore();
    unmount(component);
  });

  it('calls deletePreview and onClose when Cancel is clicked on preview screen', async () => {
    previewSpy.mockResolvedValue(makePreview());
    deleteSpy.mockResolvedValue(undefined);

    const onClose = vi.fn();
    const { target, component } = setup({ open: true, onClose });

    (target.querySelector('[data-testid="btn-generate"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const cancelBtn = target.querySelector(
      '[data-testid="btn-cancel"]',
    ) as HTMLButtonElement;
    cancelBtn.click();
    await tick();
    await tick();
    flushSync();

    expect(deleteSpy).toHaveBeenCalledWith('prev-test', 'csrf-test');
    expect(onClose).toHaveBeenCalledTimes(1);

    unmount(component);
  });

  it('shows result screen with support URL on send success', async () => {
    previewSpy.mockResolvedValue(makePreview());
    sendSpy.mockResolvedValue(makeReportSubmitted());

    const { target, component } = setup({ open: true });

    (target.querySelector('[data-testid="btn-generate"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const checkbox = target.querySelector(
      '[data-testid="consent-checkbox"]',
    ) as HTMLInputElement;
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync();

    (target.querySelector('[data-testid="btn-send"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    expect(sendSpy).toHaveBeenCalledTimes(1);
    const success = target.querySelector('[data-testid="result-success"]');
    expect(success).not.toBeNull();
    expect(success!.textContent).toContain('support.example.com/r/report-7777');

    unmount(component);
  });

  it('shows rate-limited message when send fails with rate_limited', async () => {
    previewSpy.mockResolvedValue(makePreview());
    sendSpy.mockRejectedValue(
      new DiagnosticsApiError('rate_limited', 'Try again later', 429),
    );

    const { target, component } = setup({ open: true });

    (target.querySelector('[data-testid="btn-generate"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const checkbox = target.querySelector(
      '[data-testid="consent-checkbox"]',
    ) as HTMLInputElement;
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync();

    (target.querySelector('[data-testid="btn-send"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const err = target.querySelector('[data-testid="result-error"]');
    expect(err).not.toBeNull();
    expect(err!.textContent).toContain('Rate limited');

    unmount(component);
  });

  it('shows actionable message when send fails with csrf_missing', async () => {
    previewSpy.mockResolvedValue(makePreview());
    sendSpy.mockRejectedValue(
      new DiagnosticsApiError('csrf_missing', 'Missing CSRF', 403),
    );

    const { target, component } = setup({ open: true });

    (target.querySelector('[data-testid="btn-generate"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const checkbox = target.querySelector(
      '[data-testid="consent-checkbox"]',
    ) as HTMLInputElement;
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync();

    (target.querySelector('[data-testid="btn-send"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const err = target.querySelector('[data-testid="result-error"]');
    expect(err).not.toBeNull();
    expect(err!.textContent).toContain('Session expired');
    // Must not fall through to the generic "Upload failed" message.
    expect(err!.textContent).not.toContain('Upload failed');

    unmount(component);
  });

  it('shows retry_after_seconds in the rate-limited message when provided', async () => {
    previewSpy.mockResolvedValue(makePreview());
    sendSpy.mockRejectedValue(
      new DiagnosticsApiError('rate_limited', 'Try again later', 429, 30),
    );

    const { target, component } = setup({ open: true });

    (target.querySelector('[data-testid="btn-generate"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const checkbox = target.querySelector(
      '[data-testid="consent-checkbox"]',
    ) as HTMLInputElement;
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync();

    (target.querySelector('[data-testid="btn-send"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const err = target.querySelector('[data-testid="result-error"]');
    expect(err).not.toBeNull();
    expect(err!.textContent).toContain('30');
    expect(err!.textContent).toContain('Retry in 30');

    unmount(component);
  });

  it('cleans up the server-side preview when Close is clicked after a send error', async () => {
    previewSpy.mockResolvedValue(makePreview());
    sendSpy.mockRejectedValue(
      new DiagnosticsApiError('rate_limited', 'Try again later', 429),
    );
    deleteSpy.mockResolvedValue(undefined);

    const onClose = vi.fn();
    const { target, component } = setup({ open: true, onClose });

    (target.querySelector('[data-testid="btn-generate"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const checkbox = target.querySelector(
      '[data-testid="consent-checkbox"]',
    ) as HTMLInputElement;
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync();

    (target.querySelector('[data-testid="btn-send"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    // Sanity: we are on the result screen with an error visible.
    expect(target.querySelector('[data-testid="result-error"]')).not.toBeNull();
    expect(deleteSpy).not.toHaveBeenCalled();

    (target.querySelector('[data-testid="btn-close"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    expect(deleteSpy).toHaveBeenCalledWith('prev-test', 'csrf-test');
    expect(onClose).toHaveBeenCalledTimes(1);

    unmount(component);
  });

  it('shows forbidden_content message on that error code', async () => {
    previewSpy.mockResolvedValue(makePreview());
    sendSpy.mockRejectedValue(
      new DiagnosticsApiError('forbidden_content', 'PII detected', 422),
    );

    const { target, component } = setup({ open: true });

    (target.querySelector('[data-testid="btn-generate"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const checkbox = target.querySelector(
      '[data-testid="consent-checkbox"]',
    ) as HTMLInputElement;
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync();

    (target.querySelector('[data-testid="btn-send"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    flushSync();

    const err = target.querySelector('[data-testid="result-error"]');
    expect(err).not.toBeNull();
    expect(err!.textContent).toContain('forbidden content detected');

    unmount(component);
  });
});
