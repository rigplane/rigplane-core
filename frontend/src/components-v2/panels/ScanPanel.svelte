<script lang="ts">
  import { HardwareButton } from '$lib/Button';

  import { deriveScanProps, getScanHandlers } from '$lib/runtime/adapters/panel-adapters';

  const handlers = getScanHandlers();
  let p = $derived(deriveScanProps());

  let scanning = $derived(p.scanning);
  let scanType = $derived(p.scanType);
  let scanResumeMode = $derived(p.scanResumeMode);
  // MOR-2979: STOP exposes the confirmed scanning state and stays disabled
  // until observed (`toScanProps`), same seam as #3927's choice keys.
  let scanningKnown = $derived(p.scanningKnown ?? true);
  const onScanStart = handlers.onScanStart;
  const onScanStop = handlers.onScanStop;
  const onDfSpanChange = handlers.onDfSpanChange;
  const onResumeChange = handlers.onResumeChange;

  const scanTypes = [
    { value: 0x01, label: 'PROG' },
    { value: 0x02, label: 'P2' },
    { value: 0x03, label: '\u0394F' },
    { value: 0x12, label: 'FINE' },
    { value: 0x22, label: 'MEM' },
    { value: 0x23, label: 'SEL' },
  ] as const;

  const dfSpans = [
    { value: 0xA1, label: '\u00b15k' },
    { value: 0xA2, label: '\u00b110k' },
    { value: 0xA3, label: '\u00b120k' },
    { value: 0xA4, label: '\u00b150k' },
    { value: 0xA5, label: '\u00b1100k' },
    { value: 0xA6, label: '\u00b1500k' },
    { value: 0xA7, label: '\u00b11M' },
  ] as const;

  const resumeModes = [
    { value: 0xD0, label: 'OFF' },
    { value: 0xD1, label: '5s' },
    { value: 0xD2, label: '10s' },
    { value: 0xD3, label: '15s' },
  ] as const;

  /** Selected scan type for the next start (remembers last choice). */
  let selectedType = $state(0x01);

  /** Whether ΔF type is currently selected. */
  let isDfSelected = $derived(selectedType === 0x03);

  function handleTypeClick(type: number): void {
    selectedType = type;
    onScanStart(type);
  }
</script>

<div class="controls">
  <!-- Scan status indicator + stop button -->
  <div class="control-row">
    <span class="control-label">
      {#if scanning}
        <span class="scan-active">SCAN</span>
      {:else}
        SCAN
      {/if}
    </span>
    <div class="button-group">
      <HardwareButton
        active={scanning}
        pressed={scanningKnown ? scanning : undefined}
        disabled={!scanningKnown}
        indicator="edge-left"
        color="red"
        onclick={onScanStop}
      >
        STOP
      </HardwareButton>
    </div>
  </div>

  <!-- Scan type selection -->
  <div class="control-row">
    <span class="control-label">TYPE</span>
    <div class="button-group type-grid">
      {#each scanTypes as st}
        <HardwareButton
          active={scanning && scanType === st.value}
          indicator="edge-left"
          color="cyan"
          onclick={() => handleTypeClick(st.value)}
        >
          {st.label}
        </HardwareButton>
      {/each}
    </div>
  </div>

  <!-- ΔF Span (only visible when ΔF type is selected or active) -->
  {#if isDfSelected || (scanning && scanType === 0x03)}
    <div class="control-row">
      <span class="control-label">SPAN</span>
      <div class="button-group span-grid">
        {#each dfSpans as span}
          <HardwareButton
            indicator="edge-left"
            color="amber"
            onclick={() => onDfSpanChange(span.value)}
          >
            {span.label}
          </HardwareButton>
        {/each}
      </div>
    </div>
  {/if}

  <!-- Resume mode -->
  <div class="control-row">
    <span class="control-label">RESUME</span>
    <div class="button-group">
      {#each resumeModes as rm}
        <HardwareButton
          active={scanResumeMode === (rm.value & 0x0F)}
          indicator="edge-left"
          color="green"
          onclick={() => onResumeChange(rm.value)}
        >
          {rm.label}
        </HardwareButton>
      {/each}
    </div>
  </div>
</div>

<style>
  .controls {
    padding: 8px 10px 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .control-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    min-width: 0;
  }

  .control-label {
    color: var(--v2-text-dim);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.06em;
    flex-shrink: 0;
    min-width: 34px;
  }

  .scan-active {
    color: var(--v2-color-red, #ff4444);
    animation: pulse 1.2s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  .button-group {
    display: flex;
    gap: 4px;
    flex: 1 1 auto;
    min-width: 0;
    flex-wrap: wrap;
  }

  .button-group > :global(button) {
    flex: 1 1 0;
    min-width: 0;
  }

  .type-grid > :global(button) {
    min-width: 36px;
    flex: 0 1 auto;
  }

  .span-grid > :global(button) {
    min-width: 38px;
    flex: 0 1 auto;
  }
</style>
