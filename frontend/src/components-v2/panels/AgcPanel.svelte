<script lang="ts">
  import { HardwareButton } from '$lib/Button';
  import { buildAgcOptions } from './agc-utils';
  import { deriveAgcProps, getAgcHandlers, getAgcArmed } from '$lib/runtime/adapters/panel-adapters';
  import { t } from '$lib/i18n';

  const handlers = getAgcHandlers();
  let p = $derived(deriveAgcProps());
  let options = $derived(buildAgcOptions(p.agcModes, p.agcLabels));
  let showAgc = $derived(p.hasAgc ?? true);
  // MOR-1536: the freshest in-flight `set_agc` target, DISPLAY ONLY (see
  // `panel-adapters.ts`'s ARMED-SIGNAL CONTRACT). `agcMode` above stays the
  // sole selection source — armed only marks the button the pending command
  // is racing toward, never a substitute for the confirmed reading.
  let armed = $derived(getAgcArmed());
  const armedIdBase = $props.id();
</script>

{#if showAgc}
  <div class="panel-body">
    <div class="button-grid">
      {#each options as option}
        {@const isArmed = armed.armed && armed.value === option.value}
        {@const armedId = `${armedIdBase}-${option.value}`}
        <HardwareButton
          active={p.agcMode === option.value}
          indicator="edge-left"
          color="cyan"
          armed={isArmed}
          describedBy={isArmed ? armedId : undefined}
          onclick={() => handlers.onAgcModeChange(option.value)}
        >
          {option.label}
        </HardwareButton>
        {#if isArmed}
          <span id={armedId} class="sr-only">{t('core.agcPanel.pendingAnnouncement')}</span>
        {/if}
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

  /* MOR-1536 — same convention as `ModePanel.svelte`'s `.sr-only`: visually
     hidden, `position: absolute` removes it from `.button-grid`'s flow so
     it never consumes a grid cell. */
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
