<script lang="ts">
  /**
   * ESSENTIALS panel — mobile IA chip-scroll default-active content (#839).
   * 90%-of-the-time controls: VFO ops, MODE quick, FILTER quick, AUDIO, DSP toggles.
   */
  import { onDestroy } from 'svelte';
  import { HardwareButton } from '$lib/Button';
  import { ValueControl } from '../controls/value-control';
  import { normalizedPercentDisplay } from '../../primitives/scalar/value-control-core';
  import { finiteValue, valueText } from '../../primitives/reading-text';
  import { getAfLevelControlFeedback } from '$lib/runtime/adapters/panel-adapters';
  import {
    createContinuousScalar,
    createHBarContinuousScalarPolicy,
  } from '../../primitives/scalar/continuous-scalar.svelte';

  interface Props {
    // MOR-2979: `splitKnown`/`nbKnown`/`nrKnown`/`manualNotchKnown`/
    // `autoNotchKnown` ride `toVfoOpsProps`/`toDspProps` — the SPLIT/NB/NR/
    // NOTCH keys expose the confirmed on/off and stay disabled until
    // observed, same seam as #3927's choice keys.
    vfoOps: { splitActive?: boolean; splitKnown?: boolean };
    mode: { currentMode: string; modes: string[] };
    // MOR-2683: `currentFilter` is `null` while the filter reading has not
    // arrived (`toFilterProps`' unread sentinel) — `null` never equals a
    // real 1-based index, so no filter choice lights for an unread filter.
    filter: { currentFilter: number | null; filterLabels?: string[] };
    rxAudio: { monitorMode: string; afLevel: number };
    dsp: {
      nbActive: boolean; nrMode: number; notchMode: string;
      nbKnown?: boolean; nrKnown?: boolean;
      manualNotchKnown?: boolean; autoNotchKnown?: boolean;
    };
    quickModes: string[];
    onSplitToggle: () => void;
    onSwap: () => void;
    onEqual: () => void;
    onModeChange: (m: string) => void;
    onModeMore: () => void;
    onFilterChange: (n: number) => void;
    onFilterMore: () => void;
    onMonitorModeChange: (m: string) => void;
    onAfLevelChange: (v: number) => void;
    onNbToggle: (v: boolean) => void;
    onNrModeChange: (v: number) => void;
    onNotchModeChange: (v: string) => void;
  }

  let {
    vfoOps,
    mode,
    filter,
    rxAudio,
    dsp,
    quickModes,
    onSplitToggle,
    onSwap,
    onEqual,
    onModeChange,
    onModeMore,
    onFilterChange,
    onFilterMore,
    onMonitorModeChange,
    onAfLevelChange,
    onNbToggle,
    onNrModeChange,
    onNotchModeChange,
  }: Props = $props();

  // MOR-1409 A13a display-honesty guard (grant 5246842617 §3, in the
  // 5246487510 shape — guards only, no other change to this file).
  // `MobileRadioLayout` now feeds this panel the canonical RX-audio
  // projection, which reports `Number.NaN` for an AF level the radio has
  // never sent. `normalizedPercentDisplay` has no non-finite branch —
  // `Math.round(Math.max(0, Math.min(1, NaN)) * 100)` is `NaN` — so the
  // readout would render the literal "NaN%" on the default-active mobile
  // chip. MOR-2668: an unread level renders '' (unlit LCD segment) in
  // HBarRenderer's reserved `.vc-value` box (MOR-2657). Same shape as
  // `RxAudioPanel.svelte`'s guard for this exact field. MOR-2910: the
  // confirmed reading now arrives through the shared AF command-feedback
  // lane; this guard still owns the non-finite → '' mapping.
  function formatAfLevelDisplay(v: number): string {
    return valueText(finiteValue(v), normalizedPercentDisplay);
  }

  // MOR-2910: the AF level rides the shared command-feedback scalar —
  // requested/confirmed/error through the binding, dispatch through the
  // shared hbar policy. The request still leaves through the panel's
  // existing onAfLevelChange prop handler.
  let afLevelFeedback = $derived(getAfLevelControlFeedback());
  const afLevelBinding = createContinuousScalar(
    () => ({
      evidence: 'command-feedback', feedback: afLevelFeedback, command: 'set_af_level',
      domain: { min: 0, max: 1, step: 0.01, defaultValue: null, fineStepDivisor: 10 },
      enabled: afLevelFeedback.availability === 'available',
      request: onAfLevelChange,
    }),
    createHBarContinuousScalarPolicy({
      preview: 'optimistic', debounceMs: 50, describeTarget: normalizedPercentDisplay,
    }),
  );
  const feedbackIntegratedControl = { 'feedback-policy': 'feedback-integrated' } as const;

  // MOR-2979: NOTCH reflects the manual/auto pair — known only when both
  // strands are observed, never from a half-observed pair.
  let splitKnown = $derived(vfoOps.splitKnown ?? true);
  let nbKnown = $derived(dsp.nbKnown ?? true);
  let nrKnown = $derived(dsp.nrKnown ?? true);
  let notchKnown = $derived((dsp.manualNotchKnown ?? true) && (dsp.autoNotchKnown ?? true));

  onDestroy(() => {
    afLevelBinding.destroy();
  });
</script>

<div class="m-essentials">
  <!-- VFO ops -->
  <div class="m-row m-vfo-ops">
    <HardwareButton
      active={vfoOps.splitActive ?? false}
      pressed={splitKnown ? (vfoOps.splitActive ?? false) : undefined}
      disabled={!splitKnown}
      indicator="edge-left"
      color={vfoOps.splitActive ? 'yellow' : 'muted'}
      onclick={onSplitToggle}
    >
      SPLIT
    </HardwareButton>
    <HardwareButton indicator="edge-left" color="cyan" onclick={onSwap}>
      A↔B
    </HardwareButton>
    <HardwareButton indicator="edge-left" color="cyan" onclick={onEqual}>
      A=B
    </HardwareButton>
  </div>

  <!-- MODE quick -->
  <div class="m-row">
    {#each quickModes as m}
      <HardwareButton
        active={mode.currentMode === m}
        indicator="edge-left"
        color="cyan"
        onclick={() => onModeChange(m)}
      >
        {m}
      </HardwareButton>
    {/each}
    <HardwareButton indicator="edge-left" color="muted" onclick={onModeMore}>
      More…
    </HardwareButton>
  </div>

  <!-- FILTER quick -->
  <div class="m-row">
    {#each (filter.filterLabels ?? ['FIL1', 'FIL2', 'FIL3']) as label, idx}
      <HardwareButton
        active={filter.currentFilter === idx + 1}
        indicator="edge-left"
        color="cyan"
        onclick={() => onFilterChange(idx + 1)}
      >
        {label}
      </HardwareButton>
    {/each}
    <HardwareButton indicator="edge-left" color="muted" onclick={onFilterMore}>
      More…
    </HardwareButton>
  </div>

  <!-- AUDIO monitor mode -->
  <div class="m-row">
    {#each ['local', 'live', 'mute'] as opt}
      <HardwareButton
        active={rxAudio.monitorMode === opt}
        indicator="edge-left"
        color={opt === 'mute' ? 'red' : 'cyan'}
        onclick={() => onMonitorModeChange(opt)}
      >
        {opt === 'local' ? 'LOCAL' : opt === 'live' ? 'LIVE' : 'MUTE'}
      </HardwareButton>
    {/each}
  </div>

  <!-- AF level -->
  <ValueControl
    {...feedbackIntegratedControl}
    label="AF Level"
    binding={afLevelBinding}
    renderer="hbar"
    displayFn={formatAfLevelDisplay}
    accentColor="var(--v2-accent-cyan-alt)"
    variant="hardware-illuminated"
  />

  <!-- DSP toggles -->
  <div class="m-row">
    <HardwareButton
      active={dsp.nbActive}
      pressed={nbKnown ? dsp.nbActive : undefined}
      disabled={!nbKnown}
      indicator="edge-left"
      color={dsp.nbActive ? 'green' : 'muted'}
      onclick={() => onNbToggle(!dsp.nbActive)}
    >
      NB
    </HardwareButton>
    <HardwareButton
      active={dsp.nrMode > 0}
      pressed={nrKnown ? dsp.nrMode > 0 : undefined}
      disabled={!nrKnown}
      indicator="edge-left"
      color={dsp.nrMode > 0 ? 'green' : 'muted'}
      onclick={() => onNrModeChange(dsp.nrMode > 0 ? 0 : 1)}
    >
      NR
    </HardwareButton>
    <HardwareButton
      active={dsp.notchMode !== 'off'}
      pressed={notchKnown ? dsp.notchMode !== 'off' : undefined}
      disabled={!notchKnown}
      indicator="edge-left"
      color={dsp.notchMode !== 'off' ? 'green' : 'muted'}
      onclick={() => onNotchModeChange(dsp.notchMode !== 'off' ? 'off' : 'auto')}
    >
      NOTCH
    </HardwareButton>
  </div>
</div>

<style>
  .m-essentials {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px;
  }

  .m-row {
    display: flex;
    gap: 4px;
  }

  .m-row > :global(button) {
    flex: 1 1 0;
    min-width: 0;
    min-height: 44px;
  }

  .m-vfo-ops > :global(button) {
    min-height: 36px;
  }
</style>
