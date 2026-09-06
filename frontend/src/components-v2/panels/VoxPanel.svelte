<script lang="ts">
  import { ValueControl } from '../controls/value-control';
  import { rawToPercentDisplay } from '../../primitives/scalar/value-control-core';
  import { HardwareButton } from '$lib/Button';

  interface Props {
    voxOn: boolean;
    voxGain: number;
    antiVoxGain: number;
    voxDelay: number;
    onVoxToggle: () => void;
    onVoxGainChange: (v: number) => void;
    onAntiVoxGainChange: (v: number) => void;
    onVoxDelayChange: (v: number) => void;
  }

  let {
    voxOn,
    voxGain,
    antiVoxGain,
    voxDelay,
    onVoxToggle,
    onVoxGainChange,
    onAntiVoxGainChange,
    onVoxDelayChange,
  }: Props = $props();

  function delayDisplay(raw: number): string {
    return `${(raw * 0.1).toFixed(1)}s`;
  }
</script>

<div class="panel-body">
  <div class="toggle-row">
    <HardwareButton indicator="edge-left" active={voxOn} color="amber" onclick={onVoxToggle}>
      VOX {voxOn ? 'ON' : 'OFF'}
    </HardwareButton>
  </div>

  {#if voxOn}
    <ValueControl
      label="VOX Gain"
      value={voxGain}
      min={0}
      max={255}
      step={1}
      renderer="hbar"
      displayFn={rawToPercentDisplay}
      accentColor="var(--v2-accent-orange)"
      onChange={onVoxGainChange}
      variant="hardware-illuminated"
    />

    <ValueControl
      label="Anti-VOX"
      value={antiVoxGain}
      min={0}
      max={255}
      step={1}
      renderer="hbar"
      displayFn={rawToPercentDisplay}
      accentColor="var(--v2-accent-cyan)"
      onChange={onAntiVoxGainChange}
      variant="hardware-illuminated"
    />

    <ValueControl
      label="VOX Delay"
      value={voxDelay}
      min={0}
      max={20}
      step={1}
      renderer="discrete"
      tickStyle="notch"
      displayFn={delayDisplay}
      accentColor="var(--v2-accent-yellow)"
      onChange={onVoxDelayChange}
      variant="hardware-illuminated"
    />
  {/if}
</div>

<style>
  .panel-body {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px;
  }

  .toggle-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }
</style>
