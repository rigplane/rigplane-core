<!--
  Accept Probe Meter — a bespoke S-meter built for the `accept-probe` skin
  (MOR-2035 / MOR-2034 acceptance experiment: "build a skin with its own
  S-meters and indicators without touching anything below the presentation
  layer"). Domain: 'calibrated-db-rel-s9' (see
  `components-v2/meters/__tests__/meter-contract.ts`) — `value` is the
  S-meter's calibrated dB-relative-to-S9 reading, matching `toMeterProps`'s
  `sValue`/`LinearSMeter`'s own `value` prop. S-unit/dBm text is derived
  only through `smeter-scale.ts`'s own functions, never a local formula, per
  that contract's rule for this directory.
-->
<script lang="ts">
  import {
    calibratedToSegments, calibratedToSUnit, calibratedToDbm, formatDbm,
  } from './smeter-scale';

  interface Props {
    value: number;
    active?: boolean;
  }
  let { value, active = false }: Props = $props();

  const segments = $derived(calibratedToSegments(value));
  const sUnit = $derived(calibratedToSUnit(value));
  const dbmText = $derived(formatDbm(calibratedToDbm(value)));
</script>

<div class="accept-probe-meter" data-testid="accept-probe-meter" data-active={active}>
  <div class="accept-probe-meter-bar">
    {#each Array.from({ length: 20 }) as _, i (i)}
      <span class="accept-probe-meter-seg" class:lit={i < segments}></span>
    {/each}
  </div>
  <div class="accept-probe-meter-text">{sUnit} {dbmText}</div>
</div>

<style>
  .accept-probe-meter {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-family: monospace;
  }
  .accept-probe-meter-bar {
    display: flex;
    gap: 1px;
  }
  .accept-probe-meter-seg {
    width: 6px;
    height: 14px;
    background: #333;
  }
  .accept-probe-meter-seg.lit {
    background: #4caf50;
  }
  .accept-probe-meter[data-active='true'] .accept-probe-meter-seg.lit {
    background: #ff9800;
  }
  .accept-probe-meter-text {
    font-size: 0.85rem;
  }
</style>
