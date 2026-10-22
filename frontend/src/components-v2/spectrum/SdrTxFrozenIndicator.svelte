<!--
  SDR TX-frozen indicator (MOR-3158) — while the SDR source is TX-frozen
  the waterfall holds its last pre-transmit row and this indicator covers
  it: a subtle dim veil plus a "TX" marker, instead of the repeated dark
  rows ("black band") a frozen producer stream would otherwise scroll.

  Mounted by the spectrum region inside the waterfall history wrapper, so
  the veil dims the painted rows without hiding the tune-line/passband
  overlays stacked above the canvas. Pointer-transparent by construction —
  tuning gestures must keep reaching the waterfall.

  Zero focusable elements; `active` is display-only state passed by the
  region, which reads `sdr.txFrozen` from the public state.
-->
<script lang="ts">
  interface Props {
    active: boolean;
  }
  let { active }: Props = $props();
</script>

{#if active}
  <div
    class="sdr-tx-frozen"
    data-testid="sdr-tx-frozen"
    aria-label="Waterfall held during transmit"
    title="Waterfall held during transmit (SDR source TX-frozen)"
  >
    <span class="sdr-tx-frozen-marker" aria-hidden="true">TX</span>
  </div>
{/if}

<style>
  .sdr-tx-frozen {
    position: absolute;
    inset: 0;
    z-index: 7;
    display: flex;
    align-items: flex-start;
    justify-content: flex-end;
    padding: 6px;
    pointer-events: none;
    /* Subtle dim — the marker text is the non-colour state channel. */
    background: rgba(0, 0, 0, 0.25);
  }

  .sdr-tx-frozen-marker {
    padding: 1px 6px;
    border: 1px solid var(--v2-accent-yellow, #eab308);
    border-radius: 3px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.1em;
    color: var(--v2-accent-yellow, #eab308);
    background: var(--v2-bg-darkest, #0a0a0f);
    opacity: 0.9;
  }
</style>
