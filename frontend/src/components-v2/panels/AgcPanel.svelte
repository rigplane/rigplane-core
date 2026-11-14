<script lang="ts">
  import { HardwareButton } from '$lib/Button';
  import { buildAgcOptions } from './agc-utils';
  import { deriveAgcProps, getAgcHandlers, getAgcArmed } from '$lib/runtime/adapters/panel-adapters';
  import { t } from '$lib/i18n';

  const handlers = getAgcHandlers();
  let p = $derived(deriveAgcProps());
  // MOR-2978: `NaN` is the unread sentinel (`toAgcProps`) — the keys expose
  // the confirmed selection and stay disabled until an AGC mode is observed.
  // MOR-4436: a readback-only label (panel-selected mode the protocol
  // cannot command) is a KNOWN state too — buttons stay usable, none is
  // selected, and the label renders as non-clickable text below.
  let agcKnown = $derived(!Number.isNaN(p.agcMode) || p.agcReadbackLabel !== undefined);
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
          pressed={agcKnown ? p.agcMode === option.value : undefined}
          disabled={!agcKnown}
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
    {#if p.agcReadbackLabel !== undefined}
      <p class="readback-label">{t('core.agcPanel.setOnRadio', { label: p.agcReadbackLabel })}</p>
    {/if}
  </div>
{/if}

<style>
  .panel-body {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 7px 8px;
  }

  /* MOR-4436 — readback-only state: plain non-clickable text under the
     buttons, reusing the panel's own typography (no new control). */
  .readback-label {
    margin: 0;
    opacity: 0.75;
  }

  /* MOR-1536 — same convention as `ModePanel.svelte`'s `.sr-only`: visually
     hidden, `position: absolute` removes it from `.button-grid`'s flow so
     it never consumes a grid cell. */
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
