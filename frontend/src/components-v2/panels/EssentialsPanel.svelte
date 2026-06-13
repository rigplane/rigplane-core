<script lang="ts">
  /**
   * ESSENTIALS panel — mobile IA chip-scroll default-active content (#839).
   * 90%-of-the-time controls: VFO ops, MODE quick, FILTER quick, AUDIO, DSP toggles.
   */
  import { HardwareButton } from '$lib/Button';
  import { ValueControl, normalizedPercentDisplay } from '../controls/value-control';

  interface Props {
    vfoOps: { splitActive?: boolean };
    mode: { currentMode: string; modes: string[] };
    filter: { currentFilter: number; filterLabels?: string[] };
    rxAudio: { monitorMode: string; afLevel: number };
    dsp: { nbActive: boolean; nrMode: number; notchMode: string };
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
</script>

<div class="m-essentials">
  <!-- VFO ops -->
  <div class="m-row m-vfo-ops">
    <HardwareButton
      active={vfoOps.splitActive ?? false}
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
    label="AF Level"
    value={rxAudio.afLevel}
    min={0}
    max={1}
    step={0.01}
    renderer="hbar"
    displayFn={normalizedPercentDisplay}
    accentColor="var(--v2-accent-cyan-alt)"
    onChange={onAfLevelChange}
    variant="hardware-illuminated"
  />

  <!-- DSP toggles -->
  <div class="m-row">
    <HardwareButton
      active={dsp.nbActive}
      indicator="edge-left"
      color={dsp.nbActive ? 'green' : 'muted'}
      onclick={() => onNbToggle(!dsp.nbActive)}
    >
      NB
    </HardwareButton>
    <HardwareButton
      active={dsp.nrMode > 0}
      indicator="edge-left"
      color={dsp.nrMode > 0 ? 'green' : 'muted'}
      onclick={() => onNrModeChange(dsp.nrMode > 0 ? 0 : 1)}
    >
      NR
    </HardwareButton>
    <HardwareButton
      active={dsp.notchMode !== 'off'}
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
