<script lang="ts">
  import type { Snippet } from 'svelte';
  // MOR-1369 (S6b-1) — records the `hideScopeControls` prop RadioLayout
  // passes through, so tests here can assert the channel wiring without
  // paying for SpectrumPanel's full canvas/runtime mount (that coverage
  // lives in SpectrumPanel.component.test.ts).
  //
  // MOR-1486 ruling B — same idiom for `hideAutoStepToggle`, so layout
  // tests can assert which layouts gate the AUTO toggle without a full
  // SpectrumPanel mount.
  let {
    hideScopeControls = false,
    hideAutoStepToggle = false,
    scopeControls,
  }: { hideScopeControls?: boolean; hideAutoStepToggle?: boolean; scopeControls?: Snippet } = $props();
</script>

<div
  class="spectrum-panel spectrum-panel-stub"
  data-hide-scope-controls={hideScopeControls}
  data-hide-auto-step-toggle={hideAutoStepToggle}
  data-has-scope-controls={scopeControls !== undefined}
>
  Spectrum Stub
  {#if scopeControls}{@render scopeControls()}{/if}
</div>
