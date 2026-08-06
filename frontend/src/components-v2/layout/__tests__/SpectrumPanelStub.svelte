<script lang="ts">
  // MOR-1369 (S6b-1) — records the `hideScopeControls` prop RadioLayout
  // passes through, so tests here can assert the channel wiring without
  // paying for SpectrumPanel's full canvas/runtime mount (that coverage
  // lives in SpectrumPanel.component.test.ts).
  let { hideScopeControls = false }: { hideScopeControls?: boolean } = $props();
</script>

<div class="spectrum-panel spectrum-panel-stub" data-hide-scope-controls={hideScopeControls}>
  Spectrum Stub
</div>
