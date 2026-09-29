<script lang="ts">
  import { HardwareButton } from '$lib/Button';
  import { deriveAntennaProps, getAntennaHandlers } from '$lib/runtime/adapters/panel-adapters';

  const handlers = getAntennaHandlers();
  let p = $derived(deriveAntennaProps());

  let txAntenna = $derived(p.txAntenna);
  let rxAnt = $derived(p.rxAnt);
  // MOR-2978: `null` is the unread sentinel for both (`toAntennaProps`) —
  // the keys expose the confirmed selection and stay disabled until observed.
  let txKnown = $derived(txAntenna !== null);
  let rxKnown = $derived(rxAnt !== null);
  let antennaCount = $derived(p.antennaCount);
  let hasRxAntenna = $derived(p.hasRxAntenna);
  const onSelectAnt1 = handlers.onSelectAnt1;
  const onSelectAnt2 = handlers.onSelectAnt2;
  const onToggleRxAnt = handlers.onToggleRxAnt;
</script>

{#if antennaCount > 1}
  <div class="controls">
    <div class="control-row">
      <span class="control-label">TX</span>
      <div class="button-group">
        <HardwareButton
          active={txAntenna === 1}
          pressed={txAntenna === null ? undefined : txAntenna === 1}
          disabled={!txKnown}
          indicator="edge-left"
          color="cyan"
          onclick={onSelectAnt1}
        >
          ANT1
        </HardwareButton>
        <HardwareButton
          active={txAntenna === 2}
          pressed={txAntenna === null ? undefined : txAntenna === 2}
          disabled={!txKnown}
          indicator="edge-left"
          color="cyan"
          onclick={onSelectAnt2}
        >
          ANT2
        </HardwareButton>
      </div>
    </div>

    {#if hasRxAntenna}
      <div class="control-row">
        <span class="control-label">RX</span>
        <div class="button-group">
          <HardwareButton
            active={rxAnt ?? false}
            pressed={rxAnt ?? undefined}
            disabled={!rxKnown}
            indicator="edge-left"
            color="green"
            onclick={onToggleRxAnt}
          >
            RX ANT
          </HardwareButton>
        </div>
      </div>
    {/if}
  </div>
{/if}

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

  .button-group {
    display: flex;
    gap: 4px;
    flex: 1 1 auto;
    min-width: 0;
  }

  .button-group > :global(button) {
    flex: 1 1 0;
    min-width: 0;
  }
</style>
