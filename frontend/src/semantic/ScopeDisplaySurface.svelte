<!--
  Semantic scope-display surface (MOR-1312, vocabulary slice 12B — the LAST
  slice of the vocabulary program).

  Presentation only. It renders the MOR-1301/MOR-1312 `scopeDisplay` fact
  group — WHICH scope source is currently live and HOW HEALTHY it is, plus
  the hardware channel's own connectivity — and emits NO intent (v3 ADR
  invariant 11): this is a pure readout, never an action surface.

  MOR-2545 PR2: the facts render as ONE compact in-row indicator — a tone
  dot, the "SRC" chip and a visible text readout beside it (unread parts
  omitted, never `—`/`?` placeholders) whose span only the toolbar host
  hides, with the full text always in `title`/accessible name.

  SCOPE (boundary ruling, 11A verify, carried forward by 12A/12B):
  (1) NEVER scope TUNING. MODE/EDGE/HOLD/REF/etc. are `scopeControls`
      (slice 11A/11A′) and are not duplicated here.
  (2) NEVER the scope's PIXELS. Live hardware/audio-FFT frames stay a wholly
      App-owned resource demand (MOR-1161 + `ScaledStage` territory) — this
      file renders three short facts, not a canvas.
  (3) NEVER band-plan/DX/EiBi overlays. Explicitly out of scope per the
      ticket.

  ZERO FOCUSABLE ELEMENTS, BY CONSTRUCTION (MOR-1069 mounting canon). A
  source/health readout has no action to offer, so this surface renders no
  `button`/`input`/`select`/`a[href]`/`[tabindex]` — pinned in
  `__tests__/ScopeDisplaySurface.test.ts` and re-pinned at the composed-tree
  level in `semantic-scope-display-wiring.component.test.ts`, so a future
  control addition trips both. That property is what lets `SemanticRadioSurfaces`
  mount this surface in BOTH compositions (the `meters`/`txAux` shape, not the
  control-bearing `rxAudio` single-only shape) — bare included, wherever
  `zoneOwning()` finds no zone carrying `scopeDisplay`.

  `hardwareConnected` (MOR-1312 addition, MOR-1352 finding) is genuinely NOT
  redundant with `health` when `source === 'audio_fft'` — see
  `radio-view-model.ts`'s `ScopeDisplayViewModel` doc comment.
-->
<script module lang="ts">
  import type { ScopeDisplayField, ScopeHealthState } from './radio-view-model';

  /** Three-tone classification for `health`, mirroring `indicatorTone`
   *  (`components-v2/layout/StatusBar.svelte`) — reproduced, not imported,
   *  because `semantic/` may not import `components-v2/*` (the same ADR
   *  boundary reason `classifyScopeHealth` reproduces
   *  `deriveScopeIndicatorState` instead of calling it). */
  export type HealthTone = 'green' | 'yellow' | 'red' | 'neutral';
  export function healthTone(state: ScopeHealthState): HealthTone {
    switch (state) {
      case 'connected': return 'green';
      case 'connecting':
      case 'starting':
      case 'waiting':
      case 'reconnecting': return 'yellow';
      case 'disconnected':
      case 'failed': return 'red';
      default: return 'neutral';
    }
  }

  const usable = (f: ScopeDisplayField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  /** The indicator's tooltip/accessible-name text: one part per READ fact,
   *  unread parts omitted — never a `—` placeholder (MOR-2545). */
  export function indicatorText(sd: {
    source: ScopeDisplayField<string>;
    health: ScopeDisplayField<ScopeHealthState>;
    hardwareConnected: ScopeDisplayField<boolean>;
  }): string {
    return readoutParts(sd, true).join(' · ');
  }
  /** The VISIBLE readout beside the chip: the same parts as `indicatorText`
   *  with the source part reduced to its value — the chip already prints
   *  the "SRC" label, so dot + chip + this text composes to exactly the
   *  `indicatorText` string ("● SRC hardware · connected · HW on") without
   *  printing the label twice. Only the toolbar host hides this span
   *  (compact form, `SpectrumToolbar.svelte`'s `.scope-status-host`). */
  export function readoutText(sd: {
    source: ScopeDisplayField<string>;
    health: ScopeDisplayField<ScopeHealthState>;
    hardwareConnected: ScopeDisplayField<boolean>;
  }): string {
    return readoutParts(sd, false).join(' · ');
  }
  function readoutParts(
    sd: {
      source: ScopeDisplayField<string>;
      health: ScopeDisplayField<ScopeHealthState>;
      hardwareConnected: ScopeDisplayField<boolean>;
    },
    withSourceLabel: boolean,
  ): string[] {
    const parts: string[] = [];
    if (sd.source.reading.status === 'known') {
      parts.push(withSourceLabel ? `SRC ${sd.source.reading.value}` : String(sd.source.reading.value));
    }
    if (sd.health.reading.status === 'known') parts.push(String(sd.health.reading.value));
    if (sd.hardwareConnected.reading.status === 'known') {
      parts.push(`HW ${sd.hardwareConnected.reading.value ? 'on' : 'off'}`);
    }
    return parts;
  }
</script>

<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';

  interface Props { view: RadioViewModel }
  let { view }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group
   *  doctrine): a radio the MOR-1301 evidence gate declined gets no empty
   *  indicator and no zone had to learn about it. */
  let sd = $derived(view.scopeDisplay);
</script>

{#if sd}
  {@const text = indicatorText(sd)}
  {@const readout = readoutText(sd)}
  <section
    class="scope-display-surface" data-testid="scope-display-surface" role="status"
    aria-label={text || 'Scope status'}
    title={text || undefined}
  >
    <span
      class="scope-display-indicator" data-testid="scope-display-indicator"
      data-observed={usable(sd.source)}
      data-tone={sd.health.reading.status === 'known' ? healthTone(sd.health.reading.value) : 'neutral'}
    >SRC</span>
    {#if readout}<span class="scope-display-text">{readout}</span>{/if}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). Nothing here animates.
     MOR-2545 PR2: one compact in-row indicator — dot + "SRC" chip + the
     visible readout span (`readoutText`); the toolbar host hides the span. */
  .scope-display-surface { display: inline-flex; align-items: center; gap: 6px; }
  .scope-display-indicator {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 0 4px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.05em;
    white-space: nowrap;
    color: var(--dl-vfo-unlit-text, var(--v2-text-secondary, inherit));
  }
  /* The visible readout beside the chip — the full status text for every
     mount outside the toolbar row. Unread parts are omitted from it, same
     as from `indicatorText`. */
  .scope-display-text {
    font-size: 10px;
    letter-spacing: 0.05em;
    white-space: nowrap;
    color: var(--dl-vfo-unlit-text, var(--v2-text-secondary, inherit));
  }
  /* Tone dot — `data-tone` stays machine-readable for tests. Colour comes
     from the `--v2-accent-*` theme tokens. */
  .scope-display-indicator::before {
    content: '';
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
  }
  .scope-display-indicator[data-tone='green']::before { background: var(--v2-accent-green, currentColor); }
  .scope-display-indicator[data-tone='yellow']::before { background: var(--v2-accent-yellow, currentColor); }
  .scope-display-indicator[data-tone='red']::before { background: var(--v2-accent-red, currentColor); }
</style>
