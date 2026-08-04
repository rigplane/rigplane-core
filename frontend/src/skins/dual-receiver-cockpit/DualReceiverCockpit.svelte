<!--
  Dual Receiver Cockpit (MOR-1067) — the `dual-receiver-cockpit` manifest's
  compiled shell. Static composition only: places the primary/secondary VFO
  channel strips and the shared RX/TX status+action surface via
  SemanticRadioSurfaces' `strips="dual"` composition (MOR-1065 wiring,
  reused unmodified — its lease-safe TX internals live in exactly one file,
  components-v2/wiring/SemanticRadioSurfaces.svelte). No manufacturer
  conditionals, no runtime/store/command import here.

  Scope, controls, and global are placed as inert structural zones. MOR-1062/
  1065 ship only the vfo/rxTx semantic surfaces — nothing here may claim
  those regions are live. Same two-level gating as the surfaces themselves
  (MOR-977): present so the shell composes every named region, disabled
  because no real surface backs them yet — never falsely active.
-->
<script lang="ts">
  import SemanticRadioSurfaces from '../../components-v2/wiring/SemanticRadioSurfaces.svelte';
</script>

<div class="dual-receiver-cockpit" data-testid="dual-receiver-cockpit">
  <div class="cockpit-receivers" data-testid="cockpit-zone-receivers">
    <SemanticRadioSurfaces strips="dual" />
  </div>

  {#each ['scope', 'controls', 'global'] as zone (zone)}
    <div
      class="cockpit-inert-zone"
      data-testid={`cockpit-zone-${zone}`}
      data-zone-active="false"
      aria-disabled="true"
    ></div>
  {/each}
</div>

<style>
  .dual-receiver-cockpit {
    display: grid;
    grid-template-rows: 1fr auto auto auto;
    gap: 8px;
    height: 100%;
  }
  .cockpit-inert-zone {
    min-height: 0;
  }
</style>
