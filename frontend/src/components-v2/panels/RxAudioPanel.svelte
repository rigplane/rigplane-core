<script lang="ts">
  import { onDestroy } from 'svelte';
  import { HardwareButton } from '$lib/Button';
  import { ValueControl } from '../controls/value-control';
  import { normalizedPercentDisplay } from '../../primitives/scalar/value-control-core';
  import { finiteValue, valueText } from '../../primitives/reading-text';
  import { deriveRxAudioProps, getRxAudioHandlers } from '$lib/runtime/adapters/audio-adapter';
  import { getAfLevelControlFeedback } from '$lib/runtime/adapters/panel-adapters';
  import {
    createContinuousScalar,
    createHBarContinuousScalarPolicy,
  } from '../../primitives/scalar/continuous-scalar.svelte';
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

  // MOR-1409 A12 (coordinator adjudication, Core #2317, comment 5246487510):
  // `hasAfLevel`/`hasLiveAudio` gate on capability presence only — a
  // connected receiver that has never reported `afLevel` (optional field,
  // local-monitor path) still passes the gate with `props.afLevel === NaN`
  // (panel-props.ts no longer fabricates `?? 0.5`). `normalizedPercentDisplay`
  // (primitives/scalar/value-control-core.ts, not an A12 owner) has no NaN guard —
  // `Math.round(Math.max(0, Math.min(1, NaN)) * 100)` is `NaN`, rendering
  // the literal "NaN%". MOR-2668: an unread level renders '' (unlit LCD
  // segment) in HBarRenderer's reserved `.vc-value` box (MOR-2657), the
  // `TxAuxScalarHost.formatValue` shape — never a dash, never "NaN".
  function formatAfLevelDisplay(v: number): string {
    return valueText(finiteValue(v), normalizedPercentDisplay);
  }

  // MOR-2910: the AF level rides the shared command-feedback scalar —
  // requested/confirmed/error through the binding, dispatch through the
  // shared hbar policy. The mute gate stays the panel's own `enabled`.
  let afLevelFeedback = $derived(getAfLevelControlFeedback());
  const afLevelBinding = createContinuousScalar(
    () => ({
      evidence: 'command-feedback', feedback: afLevelFeedback, command: 'set_af_level',
      domain: { min: 0, max: 1, step: 0.01, defaultValue: null, fineStepDivisor: 10 },
      enabled: !isMuted && afLevelFeedback.availability === 'available',
      request: handlers.onAfLevelChange,
    }),
    createHBarContinuousScalarPolicy({
      preview: 'optimistic', debounceMs: 50, describeTarget: normalizedPercentDisplay,
    }),
  );
  const feedbackIntegratedControl = { 'feedback-policy': 'feedback-integrated' } as const;

  onDestroy(() => {
    afLevelBinding.destroy();
  });
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
        {...feedbackIntegratedControl}
        label="AF Level"
        binding={afLevelBinding}
        renderer="hbar"
        displayFn={formatAfLevelDisplay}
        accentColor="var(--v2-accent-cyan-alt)"
        shortcutHint={afShortcut}
        title={afShortcut}
        variant="hardware-illuminated"
      />
      <div class="output-indicator" class:audio-disconnected={showDisconnected}>
        {#if showDisconnected}{t('core.overlay.audioLinkLost')}{:else}{statusText}{/if}
      </div>
      {#if props.hasAudioRouting}
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
