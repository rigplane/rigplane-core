<script lang="ts">
  import { HardwareButton } from '$lib/Button';
  import { ValueControl, normalizedPercentDisplay } from '../controls/value-control';
  import { deriveRxAudioProps, getRxAudioHandlers } from '$lib/runtime/adapters/audio-adapter';
  import { buildMonitorOptions, formatMonitorStatus } from './audio-utils';
  import { getShortcutHint } from '../layout/shortcut-hints';
  import AudioRoutingControl from './AudioRoutingControl.svelte';
  import { t } from '$lib/i18n';

  const handlers = getRxAudioHandlers();
  let props = $derived(deriveRxAudioProps());

  let options = $derived(buildMonitorOptions(props.hasLiveAudio));
  let statusText = $derived(formatMonitorStatus(props.monitorMode));
  const monitorShortcut = getShortcutHint('toggle_monitor');
  const afShortcut = getShortcutHint('adjust_af_level');
  let isMuted = $derived(props.monitorMode === 'mute');
  let showDisconnected = $derived(
    props.hasLiveAudio && props.monitorMode === 'live' && !props.isAudioConnected,
  );
</script>

{#if props.hasAfLevel || props.hasLiveAudio}
    <div class="panel-body">
      <div class="button-group">
        {#each options as option}
          <HardwareButton
            active={props.monitorMode === option.value}
            indicator="edge-left"
            color="cyan"
            title={monitorShortcut}
            onclick={() => handlers.onMonitorModeChange(option.value as string)}
          >
            {option.label}
          </HardwareButton>
        {/each}
      </div>
      <ValueControl
        label="AF Level"
        value={props.afLevel}
        min={0}
        max={1}
        step={0.01}
        renderer="hbar"
        displayFn={normalizedPercentDisplay}
        accentColor="var(--v2-accent-cyan-alt)"
        shortcutHint={afShortcut}
        title={afShortcut}
        disabled={isMuted}
        onChange={handlers.onAfLevelChange}
      variant="hardware-illuminated"
      />
      <div class="output-indicator" class:audio-disconnected={showDisconnected}>
        {#if showDisconnected}{t('core.overlay.audioLinkLost')}{:else}{statusText}{/if}
      </div>
      {#if props.hasDualReceiver}
        <AudioRoutingControl />
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

  .button-group {
    display: flex;
    gap: 4px;
  }

  .button-group > :global(button) {
    flex: 1 1 0;
    min-width: 0;
  }

  .output-indicator {
    color: var(--v2-text-muted);
    font-family: 'Roboto Mono', monospace;
    font-size: 9px;
    letter-spacing: 0.04em;
    text-align: center;
  }

  .output-indicator.audio-disconnected {
    color: var(--v2-accent-yellow, #facc15);
  }
</style>
