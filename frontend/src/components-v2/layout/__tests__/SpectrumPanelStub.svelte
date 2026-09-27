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
  //
  // MOR-2545 PR2 — renders the `scopeStatusIndicator` snippet RadioLayout
  // now passes in (the compact scope-display indicator the real toolbar
  // mounts in its row), so the composed tree keeps proving where the
  // status surface lives.
  let {
    hideScopeControls = false,
    hideAutoStepToggle = false,
    scopeControls,
    scopeStatusIndicator,
    scopeProjection,
    scopeDemanded,
    onScopeDemandChange,
  }: {
    hideScopeControls?: boolean; hideAutoStepToggle?: boolean; scopeControls?: Snippet;
    scopeStatusIndicator?: Snippet;
    // MOR-2442 — the managed projection/demand props, recorded the way the
    // contract fields above are, so layout tests prove who gets the
    // SemanticRadioSurfaces region without mounting the real panel.
    scopeProjection?: unknown; scopeDemanded?: boolean;
    onScopeDemandChange?: unknown;
  } = $props();
</script>

<div
  class="spectrum-panel spectrum-panel-stub"
  data-hide-scope-controls={hideScopeControls}
  data-hide-auto-step-toggle={hideAutoStepToggle}
  data-has-scope-controls={scopeControls !== undefined}
  data-managed-scope={scopeProjection !== undefined}
  data-scope-demanded={scopeDemanded ?? 'undefined'}
  data-has-scope-demand-handler={onScopeDemandChange !== undefined}
>
  Spectrum Stub
  {#if scopeControls}{@render scopeControls()}{/if}
  {#if scopeStatusIndicator}{@render scopeStatusIndicator()}{/if}
</div>
