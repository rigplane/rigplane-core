<script lang="ts">
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
  }: { hideScopeControls?: boolean; hideAutoStepToggle?: boolean } = $props();
</script>

<div
  class="spectrum-panel spectrum-panel-stub"
  data-hide-scope-controls={hideScopeControls}
  data-hide-auto-step-toggle={hideAutoStepToggle}
>
  Spectrum Stub
</div>
