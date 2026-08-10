<script module lang="ts">
  /** Subset of ScopeControls consumed by the bridge SCOPE-status block (issue #832). */
  export interface ScopeStatusProps {
    dual: boolean;
    receiver: number;
    span: number;
    speed: number;
  }
</script>

<script lang="ts">
  import VfoPanel from '../vfo/VfoPanel.svelte';
  import VfoOps from '../vfo/VfoOps.svelte';
  import DualVfoDisplay from '../panels/vfo/DualVfoDisplay.svelte';
  import { hasCapability, hasDualReceiver } from '$lib/stores/capabilities.svelte';
  import { runtime } from '$lib/runtime/frontend-runtime';
  import { toSpectrumAuthority } from '$lib/runtime/adapters/scope-adapter';
  import { bindSemanticSurfaceHandlers, getVfoHandlers } from '$lib/runtime/adapters/panel-adapters';
  import { formatFrequency } from '../display/frequency-format';
  import type { VfoLayoutProfile } from './vfo-layout-tokens';
  import type { VfoStateProps } from './layout-utils';

  // Labels duplicated from spectrum-toolbar-logic.ts to avoid a reverse
  // dependency from layout/ → components/spectrum/. These are small and
  // stable (Icom IC-7610 scope constants).
  const SPAN_LABELS: Record<number, string> = {
    0: '\u00b12.5k', 1: '\u00b15k', 2: '\u00b110k', 3: '\u00b125k',
    4: '\u00b150k', 5: '\u00b1100k', 6: '\u00b1250k', 7: '\u00b1500k',
  };
  const SPEED_LABELS: Record<number, string> = { 0: 'FST', 1: 'MID', 2: 'SLO' };

  interface Props {
    mainVfo: VfoStateProps;
    subVfo: VfoStateProps;
    layoutProfile?: VfoLayoutProfile;
    splitActive: boolean;
    dualWatchActive: boolean;
    /** Read-only indicator: which receiver currently transmits. */
    txVfo: 'main' | 'sub';
    /** Scope digest shown in the bridge (issue #832). Omit to hide. */
    scopeStatus?: ScopeStatusProps | null;
    onSwap?: () => void;
    onEqual?: () => void;
    onSplitToggle?: () => void;
    onQuickSplit?: () => void;
    onDualWatchToggle?: (on: boolean) => void;
    onQuickDw?: () => void;
    onMainVfoClick?: () => void;
    onSubVfoClick?: () => void;
    onMainModeClick?: () => void;
    onSubModeClick?: () => void;
    onMainFreqChange?: (freq: number) => void;
    onSubFreqChange?: (freq: number) => void;
    onSpeak?: () => void;
    onScopeDualToggle?: () => void;
    onScopeReceiverChange?: (receiver: 0 | 1) => void;
  }

  let {
    mainVfo,
    subVfo,
    layoutProfile = 'baseline',
    splitActive,
    dualWatchActive,
    txVfo,
    onSwap = () => {},
    onEqual = () => {},
    onSplitToggle = () => {},
    onQuickSplit = () => {},
    onDualWatchToggle = (_on: boolean) => {},
    onQuickDw = () => {},
    onMainModeClick,
    onSubModeClick,
    onMainFreqChange,
    onSubFreqChange,
    onSpeak,
  }: Props = $props();

  let dualReceiver = $derived(hasDualReceiver());
  let scopeCapable = $derived(hasCapability('scope'));
  const vfoHandlers = getVfoHandlers();
  const scopeHandlers = bindSemanticSurfaceHandlers().scopeControls;
  let scopeControls = $derived(toSpectrumAuthority(runtime.state, runtime.caps)?.scopeControls ?? null);

  type NumberScopeField = 'span' | 'speed' | 'receiver';
  type BooleanScopeField = 'dual';

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

  let scopeSpan = $derived(acceptedNumber('span', 0, 7));
  let scopeSpeed = $derived(acceptedNumber('speed', 0, 2));
  let scopeReceiver = $derived(acceptedNumber('receiver', 0, 1));
  let scopeDual = $derived(acceptedBoolean('dual'));

  function formatBridgeFrequency(freq: number): string {
    const { mhz, khz } = formatFrequency(freq);
    return `${mhz}.${khz}`;
  }

  let rxFrequency = $derived(formatBridgeFrequency(txVfo === 'main' ? subVfo.freq : mainVfo.freq));
  let txFrequency = $derived(formatBridgeFrequency(txVfo === 'main' ? mainVfo.freq : subVfo.freq));

  // Derived active-receiver identity for the segmented toggle in VfoOps.
  let activeReceiver = $derived<'MAIN' | 'SUB'>(mainVfo.isActive ? 'MAIN' : 'SUB');

  function handleActiveReceiverChange(next: 'MAIN' | 'SUB'): void {
    if (next === 'MAIN') {
      vfoHandlers.onMainVfoClick();
    } else {
      vfoHandlers.onSubVfoClick();
    }
  }

  // Debounced frequency announcement for screen readers
  let announcedFrequency = $state('');
  let announceTimer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => {
    const freq = mainVfo.freq;
    if (!freq) return;
    if (announceTimer) clearTimeout(announceTimer);
    announceTimer = setTimeout(() => {
      announcedFrequency = formatBridgeFrequency(freq) + ' MHz';
    }, 800);
    return () => { if (announceTimer) clearTimeout(announceTimer); };
  });
</script>

<span class="sr-only" aria-live="polite" aria-atomic="true">{announcedFrequency}</span>
<div class="vfo-header" class:dual={dualReceiver}>
  {#if dualReceiver}
    <DualVfoDisplay
      main={mainVfo}
      sub={subVfo}
      active={mainVfo.isActive ? 'MAIN' : 'SUB'}
      {layoutProfile}
      onActivate={(receiver) => {
        if (receiver === 'MAIN') {
          vfoHandlers.onMainVfoClick();
        } else {
          vfoHandlers.onSubVfoClick();
        }
      }}
      onMainModeClick={onMainModeClick}
      onSubModeClick={onSubModeClick}
      onMainFreqChange={onMainFreqChange}
      onSubFreqChange={onSubFreqChange}
    />
  {:else}
    <div class="vfo-main-panel">
      <VfoPanel
        {...mainVfo}
        {layoutProfile}
        onVfoClick={vfoHandlers.onMainVfoClick}
        onModeClick={onMainModeClick}
        onFreqChange={onMainFreqChange}
      />
    </div>
  {/if}

  <div class="vfo-bridge-panel">
    <div class="vfo-bridge-stack">
      <VfoOps
        {splitActive}
        {dualWatchActive}
        {txVfo}
        activeVfo={activeReceiver}
        onActiveVfoChange={handleActiveReceiverChange}
        {onSwap}
        {onEqual}
        {onSplitToggle}
        {onQuickSplit}
        onDualWatchToggle={() => onDualWatchToggle(!dualWatchActive)}
        {onQuickDw}
      />

      {#if scopeCapable}
        <div class="scope-status" data-testid="scope-status">
          <span class="scope-status-title">SCOPE</span>
          {#if dualReceiver}
            <div class="scope-status-row scope-pills" role="group" aria-label="Scope source">
              <button
                type="button"
                class="scope-pill"
                class:active={scopeReceiver === 0}
                disabled={scopeReceiver === null}
                onclick={() => { if (scopeReceiver !== null) scopeHandlers.onReceiverChange(0); }}
                title="Set scope source to MAIN"
              >MAIN</button>
              <button
                type="button"
                class="scope-pill"
                class:active={scopeReceiver === 1}
                disabled={scopeReceiver === null}
                onclick={() => { if (scopeReceiver !== null) scopeHandlers.onReceiverChange(1); }}
                title="Set scope source to SUB"
              >SUB</button>
            </div>
            <button
              type="button"
              class="scope-dual"
              class:active={scopeDual === true}
              disabled={scopeDual === null}
              onclick={() => { if (scopeDual !== null) scopeHandlers.onDualChange(!scopeDual); }}
              title="Toggle dual scope"
            >DUAL</button>
          {/if}
          <span class="scope-status-row scope-digest">
            {scopeSpan === null ? '\u2014' : SPAN_LABELS[scopeSpan]} {scopeSpeed === null ? '\u2014' : SPEED_LABELS[scopeSpeed]}
          </span>
        </div>
      {/if}

      {#if dualReceiver}
        <div class:inactive={!splitActive} class="split-status">
          <span class="split-status-title">SPLIT</span>
          <span class="split-status-row">RX {rxFrequency} TX {txFrequency}</span>
        </div>
      {/if}

      {#if onSpeak}
        <button type="button" class="speak-btn" title="Speak current frequency aloud" onclick={onSpeak}>
          SPEAK
        </button>
      {/if}
    </div>
  </div>
</div>

<style>
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  .vfo-header {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    grid-template-areas:
      'main'
      'bridge';
    gap: 4px;
    min-height: 0;
  }

  .vfo-header.dual {
    grid-template-columns: minmax(0, 1fr) var(--vfo-bridge-width, 132px) minmax(0, 1fr);
    grid-template-areas: 'main bridge sub';
    align-items: stretch;
  }

  .vfo-header :global(.vfo-main-panel) {
    grid-area: main;
    min-width: 0;
  }

  .vfo-header :global(.vfo-sub-panel) {
    grid-area: sub;
    min-width: 0;
  }

  .vfo-bridge-panel {
    grid-area: bridge;
    min-width: var(--vfo-bridge-width, 132px);
    display: flex;
    align-items: stretch;
    justify-content: center;
    border: 1px solid var(--v2-border-panel);
    border-radius: 4px;
    background: linear-gradient(180deg, var(--v2-vfo-header-gradient-top) 0%, var(--v2-vfo-header-gradient-bottom) 100%);
    padding: 0 var(--vfo-bridge-pad-x, 4px);
    box-sizing: border-box;
  }

  .vfo-bridge-stack {
    display: flex;
    flex: 1;
    min-height: 0;
    flex-direction: column;
    justify-content: space-between;
    align-items: stretch;
    padding:
      var(--vfo-badge-inset-y, 3px)
      0;
    box-sizing: border-box;
  }

  .vfo-bridge-panel :global(.vfo-ops) {
    flex-direction: column;
    justify-content: center;
    width: 100%;
  }

  .split-status {
    display: flex;
    flex-direction: column;
    gap: 3px;
    margin-top: auto;
    padding:
      var(--vfo-ops-secondary-padding-top, 4px)
      0
      0;
    border-top: 1px solid var(--v2-vfo-header-border);
    font-family: 'Roboto Mono', monospace;
    text-transform: uppercase;
    align-items: center;
  }

  .split-status.inactive {
    opacity: 0.64;
  }

  .split-status-title {
    color: var(--v2-text-muted);
    font-size: 7px;
    font-weight: 700;
    letter-spacing: 0.1em;
  }

  .split-status-row {
    color: var(--v2-text-secondary);
    font-size: 7px;
    font-weight: 700;
    letter-spacing: 0.08em;
  }

  .scope-status {
    display: flex;
    flex-direction: column;
    gap: 3px;
    margin-top: auto;
    padding:
      var(--vfo-ops-secondary-padding-top, 4px)
      0
      0;
    border-top: 1px solid var(--v2-vfo-header-border);
    font-family: 'Roboto Mono', monospace;
    text-transform: uppercase;
    align-items: center;
  }

  .scope-status-title {
    color: var(--v2-text-muted);
    font-size: 7px;
    font-weight: 700;
    letter-spacing: 0.1em;
  }

  .scope-status-row {
    display: flex;
    gap: 4px;
    align-items: center;
    color: var(--v2-text-secondary);
    font-size: 7px;
    font-weight: 700;
    letter-spacing: 0.08em;
  }

  .scope-pill,
  .scope-dual {
    padding: 1px 6px;
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    background: transparent;
    color: var(--v2-text-muted);
    font-family: inherit;
    font-size: 7px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
    line-height: 1.2;
  }

  .scope-pill:hover,
  .scope-dual:hover {
    color: var(--v2-accent-cyan, #00d4ff);
    border-color: var(--v2-accent-cyan, #00d4ff);
  }

  .scope-pill.active,
  .scope-dual.active {
    color: var(--v2-accent-cyan, #00d4ff);
    border-color: rgba(0, 212, 255, 0.4);
    background: rgba(0, 212, 255, 0.1);
  }

  .scope-digest {
    font-variant-numeric: tabular-nums;
  }

  .speak-btn {
    margin-top: 4px;
    padding: 3px 6px;
    border: 1px solid var(--v2-border);
    border-radius: 3px;
    background: transparent;
    color: var(--v2-text-subdued);
    font-family: 'Roboto Mono', monospace;
    font-size: 8px;
    font-weight: 700;
    letter-spacing: 0.08em;
    cursor: pointer;
    transition: all 0.15s;
  }

  .speak-btn:hover {
    color: var(--v2-accent-cyan);
    border-color: var(--v2-accent-cyan);
  }

  .speak-btn:active {
    background: rgba(0, 200, 220, 0.1);
  }

  @media (max-width: 1024px) {
    .vfo-header.dual {
      grid-template-columns: minmax(0, 1fr);
      grid-template-areas:
        'main'
        'bridge'
        'sub';
    }

    .vfo-bridge-panel :global(.vfo-ops) {
      flex-direction: row;
      flex-wrap: wrap;
    }

    .split-status,
    .scope-status {
      align-items: flex-start;
    }

    .scope-status {
      flex-direction: row;
      flex-wrap: wrap;
      gap: 6px;
    }
  }
</style>
