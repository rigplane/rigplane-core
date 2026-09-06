<script lang="ts">
  import { ValueControl } from './value-control';
  import { rawToPercentDisplay } from '../../primitives/scalar/value-control-core';

  // ── HBar demos — real radio parameters (0-255 raw) ──────────────────────
  let nrLevel  = $state(128);
  let nbLevel  = $state(80);
  let afGain   = $state(200);
  let rfGain   = $state(255);

  // ── Bipolar demo — RIT offset ───────────────────────────────────────────
  let ritOffset = $state(0);

  // ── Knob demo — Squelch ─────────────────────────────────────────────────
  let squelch = $state(0);

  function ritDisplay(v: number): string {
    return (v >= 0 ? `+${v}` : `${v}`) + '\u00a0Hz';
  }
</script>

<section class="lab-card">
  <h2>Real Radio HBar Controls <span class="hint">(0–255 raw → percent display)</span></h2>
  <p class="lab-note">
    Production-style horizontal bar controls using <code>rawToPercentDisplay</code>.
    Drag, scroll, or shift-scroll for fine adjustment.
  </p>
  <div class="lab-row">
    <ValueControl
      label="NR Level" value={nrLevel} min={0} max={255} step={1}
      renderer="hbar" displayFn={rawToPercentDisplay}
      accentColor="var(--v2-accent-cyan)"
      onChange={(v) => { nrLevel = v; }}
    />
    <ValueControl
      label="NB Level" value={nbLevel} min={0} max={255} step={1}
      renderer="hbar" displayFn={rawToPercentDisplay}
      accentColor="var(--v2-accent-yellow)"
      onChange={(v) => { nbLevel = v; }}
    />
    <ValueControl
      label="AF Gain" value={afGain} min={0} max={255} step={1}
      renderer="hbar" displayFn={rawToPercentDisplay}
      accentColor="var(--v2-accent-green)"
      onChange={(v) => { afGain = v; }}
    />
    <ValueControl
      label="RF Gain" value={rfGain} min={0} max={255} step={1}
      renderer="hbar" displayFn={rawToPercentDisplay}
      accentColor="var(--v2-accent-orange)"
      onChange={(v) => { rfGain = v; }}
    />
  </div>
</section>

<section class="lab-card">
  <h2>Bipolar — RIT Offset <span class="hint">(±9999 Hz, center-origin)</span></h2>
  <p class="lab-note">
    Center-origin bar for signed values. Double-click resets to zero.
  </p>
  <div class="lab-row lab-row--narrow">
    <ValueControl
      label="RIT" value={ritOffset} min={-9999} max={9999} step={1}
      defaultValue={0}
      renderer="bipolar" displayFn={ritDisplay}
      accentColor="var(--v2-accent-cyan)"
      onChange={(v) => { ritOffset = v; }}
    />
  </div>
</section>

<section class="lab-card">
  <h2>Knob — Squelch <span class="hint">(rotary, 0–255)</span></h2>
  <p class="lab-note">
    SVG rotary knob renderer. Drag arc or scroll to adjust.
  </p>
  <div class="lab-row lab-row--narrow">
    <ValueControl
      label="SQL" value={squelch} min={0} max={255} step={1}
      renderer="knob" displayFn={rawToPercentDisplay}
      accentColor="var(--v2-accent-amber)"
      onChange={(v) => { squelch = v; }}
    />
  </div>
</section>

<style>
  .lab-card {
    padding: 16px;
    border: 1px solid var(--v2-border-panel);
    border-radius: 8px;
    background: linear-gradient(180deg, var(--v2-panel-bg-gradient-top) 0%, var(--v2-panel-bg-gradient-bottom) 100%);
    box-shadow: var(--v2-shadow-sm);
  }

  h2 {
    margin: 0 0 12px;
    color: var(--v2-text-bright);
    font-family: var(--v2-font-mono);
    font-size: 12px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .hint {
    font-weight: 400;
    color: var(--v2-text-secondary);
  }

  .lab-note {
    margin: 0 0 12px;
    font-size: 11px;
    color: var(--v2-text-secondary);
    line-height: 1.5;
  }

  .lab-row {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 12px;
  }

  .lab-row--narrow {
    max-width: 320px;
  }
</style>
