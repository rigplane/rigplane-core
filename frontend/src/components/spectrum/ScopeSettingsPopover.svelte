<script lang="ts">
  import { runtime } from '$lib/runtime/frontend-runtime';
  import { toSpectrumAuthority } from '$lib/runtime/adapters/scope-adapter';
  import { bindSemanticSurfaceHandlers } from '$lib/runtime/adapters/panel-adapters';

  let { onClose }: { onClose: () => void } = $props();

  const scopeHandlers = bindSemanticSurfaceHandlers().scopeControls;
  let scopeControls = $derived(toSpectrumAuthority(runtime.state, runtime.caps)?.scopeControls ?? null);

  type NumberScopeField = 'centerType' | 'rbw';
  type BooleanScopeField = 'vbwNarrow' | 'duringTx';

  function acceptedNumber(field: NumberScopeField, min: number, max: number): number | null {
    const fact = scopeControls?.[field];
    if (!fact?.availability?.structural || !fact.availability.operational
      || fact.reading?.status !== 'known' || !Number.isSafeInteger(fact.reading.value)
      || fact.reading.value < min || fact.reading.value > max) return null;
    return fact.reading.value;
  }

  function acceptedBoolean(field: BooleanScopeField): boolean | null {
    const fact = scopeControls?.[field];
    if (!fact?.availability?.structural || !fact.availability.operational
      || fact.reading?.status !== 'known' || typeof fact.reading.value !== 'boolean') return null;
    return fact.reading.value;
  }

  let centerType = $derived(acceptedNumber('centerType', 0, 2));
  let vbwNarrow = $derived(acceptedBoolean('vbwNarrow'));
  let rbw = $derived(acceptedNumber('rbw', 0, 2));
  let duringTx = $derived(acceptedBoolean('duringTx'));

  function selectCenterType(value: number): void {
    if (centerType === null || !Number.isSafeInteger(value) || value < 0 || value > 2) return;
    scopeHandlers.onCenterTypeChange(value);
  }

  function selectVbw(value: boolean): void {
    if (vbwNarrow === null) return;
    scopeHandlers.onVbwChange(value);
  }

  function selectRbw(value: number): void {
    if (rbw === null || !Number.isSafeInteger(value) || value < 0 || value > 2) return;
    scopeHandlers.onRbwChange(value);
  }

  function selectDuringTx(value: boolean): void {
    if (duringTx === null) return;
    scopeHandlers.onDuringTxChange(value);
  }

  const CENTER_TYPE_LABELS: [number, string][] = [[0, 'Filter'], [1, 'Carrier'], [2, 'Abs.Freq']];
  const RBW_LABELS: [number, string][] = [[0, 'Wide'], [1, 'Mid'], [2, 'Narrow']];
</script>

<div
  class="popover-backdrop"
  onclick={onClose}
  onkeydown={(e) => { if (e.key === 'Escape' || e.key === 'Enter' || e.key === ' ') onClose(); }}
  role="button"
  tabindex="0"
  aria-label="Close scope settings"
></div>
<div class="scope-settings-popover">
  <div class="popover-header">
    <span>Scope Settings</span>
    <button class="close-btn" onclick={onClose}>x</button>
  </div>

  <div class="setting-group">
    <span class="setting-label">Center Type</span>
    <div class="setting-buttons">
      {#each CENTER_TYPE_LABELS as [val, label]}
        <button
          class="setting-btn"
          class:active={centerType === val}
          disabled={centerType === null}
          onclick={() => selectCenterType(val)}
        >{label}</button>
      {/each}
    </div>
  </div>

  <div class="setting-group">
    <span class="setting-label">VBW</span>
    <div class="setting-buttons">
      <button
        class="setting-btn"
        class:active={vbwNarrow === false}
        disabled={vbwNarrow === null}
        onclick={() => selectVbw(false)}
      >Wide</button>
      <button
        class="setting-btn"
        class:active={vbwNarrow === true}
        disabled={vbwNarrow === null}
        onclick={() => selectVbw(true)}
      >Narrow</button>
    </div>
  </div>

  <div class="setting-group">
    <span class="setting-label">RBW</span>
    <div class="setting-buttons">
      {#each RBW_LABELS as [val, label]}
        <button
          class="setting-btn"
          class:active={rbw === val}
          disabled={rbw === null}
          onclick={() => selectRbw(val)}
        >{label}</button>
      {/each}
    </div>
  </div>

  <div class="setting-group">
    <span class="setting-label">During TX</span>
    <div class="setting-buttons">
      <button
        class="setting-btn"
        class:active={duringTx === false}
        disabled={duringTx === null}
        onclick={() => selectDuringTx(false)}
      >Off</button>
      <button
        class="setting-btn"
        class:active={duringTx === true}
        disabled={duringTx === null}
        onclick={() => selectDuringTx(true)}
      >On</button>
    </div>
  </div>
</div>

<style>
  .popover-backdrop {
    position: fixed;
    inset: 0;
    z-index: 999;
  }

  .scope-settings-popover {
    position: absolute;
    right: 0;
    top: 100%;
    z-index: 1000;
    min-width: 200px;
    background: var(--v2-bg-darkest, #0a0a0f);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 6px;
    padding: 8px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.6);
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
  }

  .popover-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: 6px;
    margin-bottom: 6px;
    border-bottom: 1px solid var(--v2-border, #2a2a3e);
    color: var(--v2-text-primary, #e0e0e0);
    font-size: 11px;
    font-weight: 600;
  }

  .close-btn {
    background: transparent;
    border: none;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 12px;
    padding: 0 2px;
    line-height: 1;
  }

  .close-btn:hover {
    color: var(--text);
  }

  .setting-group {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 0;
    gap: 8px;
  }

  .setting-label {
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    white-space: nowrap;
    min-width: 72px;
  }

  .setting-buttons {
    display: flex;
    gap: 2px;
  }

  .setting-btn {
    padding: 3px 8px;
    background: transparent;
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--text-muted);
    font-family: inherit;
    font-size: inherit;
    cursor: pointer;
    white-space: nowrap;
    line-height: 1;
  }

  .setting-btn:hover {
    background: rgba(255, 255, 255, 0.08);
    color: var(--text);
  }

  .setting-btn.active {
    color: #00d4ff;
    border-color: rgba(0, 212, 255, 0.3);
    background: rgba(0, 212, 255, 0.1);
  }
</style>
