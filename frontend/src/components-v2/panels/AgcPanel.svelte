<script lang="ts">
  import { HardwareButton } from '$lib/Button';
  import { buildAgcOptions } from './agc-utils';
  import { deriveAgcProps, getAgcHandlers } from '$lib/runtime/adapters/panel-adapters';

  const handlers = getAgcHandlers();
  let props = $derived(deriveAgcProps());
  let options = $derived(buildAgcOptions(props.agcModes, props.agcLabels));
  let showAgc = $derived(props.hasAgc ?? true);
</script>

{#if showAgc}
  <div class="panel-body">
    <div class="button-grid">
      {#each options as option}
        <HardwareButton
          active={props.agcMode === option.value}
          indicator="edge-left"
          color="cyan"
          onclick={() => handlers.onAgcModeChange(option.value)}
        >
          {option.label}
        </HardwareButton>
      {/each}
    </div>
  </div>
{/if}

<style>
  .panel-body {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 7px 8px;
  }
</style>
