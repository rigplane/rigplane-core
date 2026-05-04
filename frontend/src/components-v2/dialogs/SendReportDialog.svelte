<script lang="ts">
  /**
   * Send-Report dialog (issue #1397).
   *
   * Three-screen flow:
   *   1. form    — collect description / issue ref / opt-in contact info
   *   2. preview — show file list + redactions, gate Send on explicit consent
   *   3. result  — show success URL or typed error message
   *
   * Backend contract: see `lib/api/diagnostics.ts` and
   * `docs/plans/2026-05-03-diagnostic-data-collection-design.md` §4.9.
   */
  import {
    previewBundle,
    sendBundle,
    saveBundle,
    deletePreview,
    DiagnosticsApiError,
    type PreviewResponse,
    type ReportSubmitted,
  } from '$lib/api/diagnostics';

  type Props = {
    open: boolean;
    onClose: () => void;
  };

  let { open, onClose }: Props = $props();

  type Screen = 'form' | 'preview' | 'result';

  // ── State ──
  let screen = $state<Screen>('form');
  let description = $state('');
  let issueRef = $state('');
  let email = $state('');
  let callsign = $state('');
  let preview = $state<PreviewResponse | null>(null);
  let understandChecked = $state(false);
  let busy = $state(false);
  let result = $state<ReportSubmitted | null>(null);
  let errorMsg = $state<string | null>(null);
  let copied = $state(false);
  let modalRoot = $state<HTMLDivElement | null>(null);

  // Reset everything to a clean form when the dialog re-opens.
  $effect(() => {
    if (open) {
      reset();
    }
  });

  function reset(): void {
    screen = 'form';
    description = '';
    issueRef = '';
    email = '';
    callsign = '';
    preview = null;
    understandChecked = false;
    busy = false;
    result = null;
    errorMsg = null;
    copied = false;
  }

  // Focus trap: keep tab cycle inside the modal.
  function handleKeydown(ev: KeyboardEvent): void {
    if (!open) return;
    if (ev.key === 'Escape') {
      ev.preventDefault();
      void handleCancel();
      return;
    }
    if (ev.key !== 'Tab' || !modalRoot) return;
    const focusables = modalRoot.querySelectorAll<HTMLElement>(
      'button, input, textarea, select, [href], [tabindex]:not([tabindex="-1"])',
    );
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement as HTMLElement | null;
    if (ev.shiftKey && active === first) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && active === last) {
      ev.preventDefault();
      first.focus();
    }
  }

  // ── Handlers ──

  async function handleGeneratePreview(): Promise<void> {
    if (busy) return;
    busy = true;
    errorMsg = null;
    try {
      const req = {
        description: description.trim() || undefined,
        issue_ref: issueRef.trim() || undefined,
        email: email.trim() || undefined,
        callsign: callsign.trim() || undefined,
      };
      preview = await previewBundle(req);
      screen = 'preview';
    } catch (err) {
      errorMsg = formatError(err);
      result = null;
      screen = 'result';
    } finally {
      busy = false;
    }
  }

  async function handleSend(): Promise<void> {
    if (busy || !preview || !understandChecked) return;
    busy = true;
    errorMsg = null;
    try {
      result = await sendBundle(preview.preview_id, preview.csrf_token);
      preview = null; // server-side preview is consumed by send
      screen = 'result';
    } catch (err) {
      errorMsg = formatError(err);
      result = null;
      screen = 'result';
    } finally {
      busy = false;
    }
  }

  async function handleSave(): Promise<void> {
    if (busy || !preview) return;
    busy = true;
    errorMsg = null;
    try {
      const blob = await saveBundle(preview.preview_id, preview.csrf_token);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `icom-lan-diagnostic-${preview.preview_id}.zip`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      errorMsg = formatError(err);
      result = null;
      screen = 'result';
    } finally {
      busy = false;
    }
  }

  async function handleCancel(): Promise<void> {
    // Best-effort cleanup of server-side preview; ignore failures.
    if (preview) {
      try {
        await deletePreview(preview.preview_id, preview.csrf_token);
      } catch {
        // ignored — server will GC eventually
      }
    }
    onClose();
  }

  async function handleResultClose(): Promise<void> {
    // If we have a lingering preview (failed-send path), clean it up so the
    // server doesn't have to wait for its 10-min GC sweep.
    if (preview) {
      try {
        await deletePreview(preview.preview_id, preview.csrf_token);
      } catch {
        // best-effort — server will GC eventually
      }
      preview = null;
    }
    onClose();
  }

  async function handleCopy(): Promise<void> {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result.support_url);
      copied = true;
      setTimeout(() => {
        copied = false;
      }, 1500);
    } catch {
      // clipboard API unavailable — silently no-op
    }
  }

  function handleBackdropClick(ev: MouseEvent): void {
    if (ev.target === ev.currentTarget) {
      void handleCancel();
    }
  }

  function formatError(err: unknown): string {
    if (err instanceof DiagnosticsApiError) {
      switch (err.code) {
        case 'rate_limited':
          return err.retryAfterSeconds !== undefined
            ? `Rate limited. Retry in ${err.retryAfterSeconds} seconds.`
            : 'Rate limited. Please wait a few minutes and try again.';
        case 'bundle_too_large':
          return 'Bundle too large. Try unchecking some categories.';
        case 'forbidden_content':
          return 'Server rejected the bundle (forbidden content detected). Review the manifest before submitting again.';
        case 'origin_mismatch':
          return 'Origin mismatch. Please reload the page and try again.';
        case 'preview_not_found':
          return 'Preview expired. Generate a new one and try again.';
        case 'csrf_missing':
          return 'Session expired. Close and reopen the dialog to try again.';
        default:
          return `Upload failed: ${err.detail || err.code}`;
      }
    }
    if (err instanceof TypeError) {
      // fetch() throws TypeError on network failure
      return 'Network error. Check your connection and try again.';
    }
    return `Unexpected error: ${String(err)}`;
  }

  function formatBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KiB`;
    return `${(n / 1024 / 1024).toFixed(2)} MiB`;
  }
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="modal-backdrop"
    onclick={handleBackdropClick}
    data-testid="send-report-backdrop"
  >
    <div
      class="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="send-report-title"
      bind:this={modalRoot}
    >
      <div class="modal-header">
        <h2 id="send-report-title">Send diagnostic report</h2>
        <button
          type="button"
          class="close-btn"
          aria-label="Close"
          onclick={handleCancel}
          disabled={busy}
        >
          ✕
        </button>
      </div>

      {#if screen === 'form'}
        <div class="modal-body">
          <p class="help-text">
            Generates a redacted bundle of recent logs and configuration. You will see
            a full preview before anything is uploaded.
          </p>

          <label class="field">
            <span class="field-label">Describe the problem</span>
            <textarea
              bind:value={description}
              rows="4"
              placeholder="What were you doing when the issue occurred?"
              data-testid="field-description"
            ></textarea>
          </label>

          <label class="field">
            <span class="field-label">Issue URL (optional)</span>
            <input
              type="url"
              bind:value={issueRef}
              placeholder="https://github.com/.../issues/123"
              data-testid="field-issue"
            />
          </label>

          <div class="field-row">
            <label class="field">
              <span class="field-label">Email (optional)</span>
              <input
                type="email"
                bind:value={email}
                placeholder="you@example.com"
                data-testid="field-email"
              />
            </label>
            <label class="field">
              <span class="field-label">Callsign (optional)</span>
              <input
                type="text"
                bind:value={callsign}
                placeholder="N0CALL"
                data-testid="field-callsign"
              />
            </label>
          </div>
        </div>

        <div class="modal-footer">
          <button
            type="button"
            class="btn btn-secondary"
            onclick={handleCancel}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            type="button"
            class="btn btn-primary"
            onclick={handleGeneratePreview}
            disabled={busy}
            data-testid="btn-generate"
          >
            {busy ? 'Generating…' : 'Generate preview'}
          </button>
        </div>
      {:else if screen === 'preview' && preview}
        <div class="modal-body">
          <p class="help-text">
            Review the bundle below. Nothing leaves this machine until you click
            <strong>Send</strong>.
          </p>

          <dl class="meta-grid">
            <dt>Endpoint</dt>
            <dd class="endpoint" data-testid="meta-endpoint">{preview.endpoint_url}</dd>
            <dt>Total size</dt>
            <dd data-testid="meta-size">{formatBytes(preview.total_size_bytes)}</dd>
            <dt>Files</dt>
            <dd>{preview.files.length}</dd>
            {#if preview.redactions_applied.length > 0}
              <dt>Redactions</dt>
              <dd>{preview.redactions_applied.join(', ')}</dd>
            {/if}
          </dl>

          <div class="file-list" role="list" data-testid="file-list">
            {#each preview.files as file (file.path)}
              <div class="file-row" role="listitem">
                <span class="file-path">{file.path}</span>
                <span class="file-size">{formatBytes(file.size)}</span>
              </div>
            {/each}
          </div>

          <label class="consent">
            <input
              type="checkbox"
              bind:checked={understandChecked}
              data-testid="consent-checkbox"
            />
            <span>
              I understand this bundle will be uploaded to the endpoint above and
              that my redacted logs may be reviewed by the maintainer.
            </span>
          </label>
        </div>

        <div class="modal-footer">
          <button
            type="button"
            class="btn btn-secondary"
            onclick={handleCancel}
            disabled={busy}
            data-testid="btn-cancel"
          >
            Cancel
          </button>
          <button
            type="button"
            class="btn btn-secondary"
            onclick={handleSave}
            disabled={busy}
            data-testid="btn-save"
          >
            Save locally
          </button>
          <button
            type="button"
            class="btn btn-primary"
            onclick={handleSend}
            disabled={busy || !understandChecked}
            data-testid="btn-send"
          >
            {busy ? 'Sending…' : 'Send'}
          </button>
        </div>
      {:else if screen === 'result'}
        <div class="modal-body">
          {#if result}
            <div class="result-success" data-testid="result-success">
              <p class="result-title">Report uploaded successfully.</p>
              <dl class="meta-grid">
                <dt>Report ID</dt>
                <dd><code>{result.report_id}</code></dd>
                <dt>Tracking URL</dt>
                <dd class="url-row">
                  <a href={result.support_url} target="_blank" rel="noopener noreferrer">
                    {result.support_url}
                  </a>
                  <button
                    type="button"
                    class="btn btn-ghost"
                    onclick={handleCopy}
                    data-testid="btn-copy"
                  >
                    {copied ? 'Copied' : 'Copy'}
                  </button>
                </dd>
                <dt>Auth class</dt>
                <dd>{result.auth_class}</dd>
              </dl>
            </div>
          {:else if errorMsg}
            <div class="result-error" data-testid="result-error" role="alert">
              <p class="result-title">Could not send the report.</p>
              <p class="error-detail">{errorMsg}</p>
            </div>
          {/if}
        </div>

        <div class="modal-footer">
          <button
            type="button"
            class="btn btn-primary"
            onclick={handleResultClose}
            data-testid="btn-close"
          >
            Close
          </button>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 9000;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.55);
    backdrop-filter: blur(3px);
    padding: 24px;
  }

  .modal {
    background: var(--v2-bg-primary, #0f0f1a);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 6px;
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.65);
    width: 100%;
    max-width: 560px;
    max-height: calc(100vh - 48px);
    display: flex;
    flex-direction: column;
    color: var(--v2-text-primary, #e0e0e0);
    font-family: 'Roboto Mono', monospace;
  }

  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--v2-border-darker, #1a1a2e);
  }

  .modal-header h2 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--v2-text-primary, #e0e0e0);
  }

  .close-btn {
    background: none;
    border: none;
    color: var(--v2-text-dim, #888);
    font-size: 16px;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1;
  }

  .close-btn:hover {
    color: var(--v2-accent-red, #ef4444);
  }

  .modal-body {
    padding: 16px;
    overflow-y: auto;
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid var(--v2-border-darker, #1a1a2e);
  }

  .help-text {
    margin: 0;
    font-size: 12px;
    color: var(--v2-text-dim, #888);
    line-height: 1.5;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .field-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--v2-text-dim, #888);
    font-weight: 600;
  }

  .field input,
  .field textarea {
    background: var(--v2-bg-input, #1a1a2e);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--v2-text-primary, #e0e0e0);
    padding: 6px 8px;
    font-family: inherit;
    font-size: 12px;
    resize: vertical;
  }

  .field input:focus,
  .field textarea:focus {
    outline: none;
    border-color: var(--v2-accent-cyan, #06b6d4);
  }

  .field-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }

  .meta-grid {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 4px 12px;
    margin: 0;
    font-size: 11px;
  }

  .meta-grid dt {
    color: var(--v2-text-dim, #888);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .meta-grid dd {
    margin: 0;
    color: var(--v2-text-primary, #e0e0e0);
    word-break: break-all;
  }

  .endpoint {
    font-family: inherit;
    color: var(--v2-accent-cyan, #06b6d4);
  }

  .file-list {
    background: var(--v2-bg-input, #1a1a2e);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    max-height: 180px;
    overflow-y: auto;
    font-size: 11px;
  }

  .file-row {
    display: flex;
    justify-content: space-between;
    padding: 4px 8px;
    border-bottom: 1px solid var(--v2-border-darker, #1a1a2e);
  }

  .file-row:last-child {
    border-bottom: none;
  }

  .file-path {
    color: var(--v2-text-primary, #e0e0e0);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-right: 12px;
  }

  .file-size {
    color: var(--v2-text-dim, #888);
    flex-shrink: 0;
  }

  .consent {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    font-size: 11px;
    line-height: 1.5;
    color: var(--v2-text-primary, #e0e0e0);
    cursor: pointer;
  }

  .consent input[type='checkbox'] {
    margin-top: 2px;
    accent-color: var(--v2-accent-cyan, #06b6d4);
  }

  .btn {
    background: var(--v2-bg-input, #1a1a2e);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--v2-text-primary, #e0e0e0);
    padding: 6px 12px;
    font-family: inherit;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .btn:hover:not(:disabled) {
    background: var(--v2-bg-card, #252540);
    border-color: var(--v2-accent-cyan, #06b6d4);
  }

  .btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .btn-primary {
    background: var(--v2-accent-cyan, #06b6d4);
    border-color: var(--v2-accent-cyan, #06b6d4);
    color: #0a0a0f;
  }

  .btn-primary:hover:not(:disabled) {
    background: #22c5e3;
    border-color: #22c5e3;
    color: #0a0a0f;
  }

  .btn-ghost {
    background: transparent;
    border: 1px solid transparent;
    padding: 2px 6px;
    font-size: 10px;
  }

  .btn-ghost:hover:not(:disabled) {
    background: var(--v2-bg-card, #252540);
    border-color: var(--v2-border, #2a2a3e);
  }

  .url-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .url-row a {
    color: var(--v2-accent-cyan, #06b6d4);
    text-decoration: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .url-row a:hover {
    text-decoration: underline;
  }

  .result-title {
    margin: 0 0 8px 0;
    font-size: 13px;
    font-weight: 600;
  }

  .result-success .result-title {
    color: var(--v2-accent-green, #4ade80);
  }

  .result-error .result-title {
    color: var(--v2-accent-red, #ef4444);
  }

  .error-detail {
    margin: 0;
    font-size: 12px;
    color: var(--v2-text-primary, #e0e0e0);
    line-height: 1.5;
  }
</style>
