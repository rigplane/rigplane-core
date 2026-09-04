<script lang="ts">
  import { onDestroy } from 'svelte';
  import { getManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';

  const tx = getManagedAppTxController();
  const initial = tx.snapshot();
  let txState = $state.raw(initial);
  let draft = $state(initial.fresh && initial.configuredSeconds !== null
    ? String(initial.configuredSeconds)
    : '');
  let dirty = $state(false);
  let saving = $state(false);
  let error = $state<string | null>(null);

  const stopWatching = tx.subscribe((next) => {
    txState = next;
    if (!dirty && next.fresh) {
      draft = next.configuredSeconds === null ? '' : String(next.configuredSeconds);
    }
  });
  onDestroy(stopWatching);

  function updateDraft(value: string): void {
    draft = value;
    dirty = true;
    error = null;
  }

  async function save(): Promise<void> {
    const trimmed = draft.trim();
    const configuredSeconds = trimmed === '' ? null : Number(trimmed);
    if (configuredSeconds !== null
      && (!Number.isFinite(configuredSeconds) || configuredSeconds <= 0)) {
      error = 'Enter a positive number, or leave blank to disable';
      return;
    }
    saving = true;
    error = null;
    try {
      await tx.setTot(configuredSeconds);
      dirty = false;
      const canonical = tx.snapshot();
      if (canonical.fresh) {
        draft = canonical.configuredSeconds === null ? '' : String(canonical.configuredSeconds);
      }
    } catch {
      error = 'TOT update failed';
    } finally {
      saving = false;
    }
  }
</script>

<div class="managed-tot-control" data-testid="managed-tot-control">
  <div class="managed-tot-readout">
    <span data-testid="managed-tot-current">
      LIMIT {txState.fresh ? (txState.configuredSeconds === null ? 'OFF' : `${txState.configuredSeconds}s`) : '---'}
    </span>
    {#if txState.remainingMs !== null}
      <span data-testid="managed-tot-countdown">REMAINING {Math.ceil(txState.remainingMs / 1000)}s</span>
    {/if}
  </div>
  <div class="managed-tot-editor">
    <label for="managed-tot-draft">TOT seconds</label>
    <input
      id="managed-tot-draft"
      data-testid="managed-tot-draft"
      type="number"
      inputmode="decimal"
      min="0"
      step="any"
      placeholder="OFF"
      value={draft}
      disabled={!txState.fresh || saving}
      oninput={(event) => updateDraft(event.currentTarget.value)}
    />
    <button
      type="button"
      data-testid="managed-tot-save"
      disabled={!txState.fresh || saving || !dirty}
      onclick={() => { void save(); }}
    >
      {saving ? 'SAVING…' : 'APPLY'}
    </button>
  </div>
  <div class="managed-tot-help">Blank disables the software limit.</div>
  {#if error}
    <div class="managed-tot-error" data-testid="managed-tot-error">{error}</div>
  {/if}
</div>

<style>
  .managed-tot-control {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding-top: 8px;
    border-top: 1px solid var(--v2-border);
    font-family: 'Roboto Mono', monospace;
  }

  .managed-tot-readout,
  .managed-tot-editor {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .managed-tot-readout {
    justify-content: space-between;
    color: var(--v2-text-primary, #e5e7eb);
    font-size: 10px;
    font-weight: 700;
  }

  .managed-tot-editor label {
    flex: 1;
    color: var(--v2-text-subdued, #aaa);
    font-size: 10px;
  }

  .managed-tot-editor input {
    box-sizing: border-box;
    width: 70px;
    padding: 5px 6px;
    border: 1px solid var(--v2-border);
    border-radius: 3px;
    background: var(--v2-bg-darkest, #111);
    color: var(--v2-text-primary, #e5e7eb);
    font: inherit;
  }

  .managed-tot-editor button {
    padding: 5px 7px;
    border: 1px solid var(--v2-border);
    border-radius: 3px;
    background: transparent;
    color: var(--v2-accent-orange, #f59e0b);
    font: inherit;
    font-size: 9px;
    font-weight: 700;
  }

  .managed-tot-editor :disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  .managed-tot-help,
  .managed-tot-error {
    font-size: 9px;
    line-height: 1.3;
  }

  .managed-tot-help {
    color: var(--v2-text-dim, #888);
  }

  .managed-tot-error {
    color: var(--v2-accent-red, #ef4444);
  }
</style>
