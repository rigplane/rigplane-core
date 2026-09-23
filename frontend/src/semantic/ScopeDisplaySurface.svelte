<!--
  Semantic scope-display surface (MOR-1312, vocabulary slice 12B — the LAST
  slice of the vocabulary program).

  Presentation only. It renders the MOR-1301/MOR-1312 `scopeDisplay` fact
  group — WHICH scope source is currently live and HOW HEALTHY it is, plus
  the hardware channel's own connectivity — and emits NO intent (v3 ADR
  invariant 11): this is a pure readout, never an action surface.

  MOR-2545 PR2 SHAPE: the pre-MOR-2545 three-span status line row is GONE —
  the same facts render as ONE compact in-row indicator (a tone dot plus the
  "SRC" chip) whose tooltip and accessible name carry the full text
  ("SRC hardware · connected · HW on"). UNREAD PARTS ARE OMITTED from that
  text, never shown as `—`/`?` placeholders; when every part is unread the
  accessible name falls back to "Scope status" and the tooltip is omitted
  entirely. The tone dot is the second channel beside the text (never the
  only one), so it survives forced-colors as a shape with a colour-var
  fallback.

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
    const parts: string[] = [];
    if (sd.source.reading.status === 'known') parts.push(`SRC ${sd.source.reading.value}`);
    if (sd.health.reading.status === 'known') parts.push(String(sd.health.reading.value));
    if (sd.hardwareConnected.reading.status === 'known') {
      parts.push(`HW ${sd.hardwareConnected.reading.value ? 'on' : 'off'}`);
    }
    return parts.join(' · ');
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
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). Nothing here animates.
     MOR-2545 PR2: one compact in-row indicator; the text lives in the
     section's `title`/`aria-label`, unread parts omitted (see indicatorText). */
  .scope-display-surface { display: inline-flex; align-items: center; }
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
  /* Tone dot — the second channel beside the tooltip text, never the only
     one; `data-tone` stays machine-readable for tests and forced-colors.
     Colour comes from the design language's accent vars only — this
     surface's own stylesheet stays free of literal colours (MOR-977),
     degrading to currentColor when no skin defines them. */
  .scope-display-indicator::before {
    content: '';
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: currentColor;
  }
  .scope-display-indicator[data-tone='green']::before { background: var(--dl-scope-tone-green, var(--v2-accent-green, currentColor)); }
  .scope-display-indicator[data-tone='yellow']::before { background: var(--dl-scope-tone-yellow, var(--v2-accent-yellow, currentColor)); }
  .scope-display-indicator[data-tone='red']::before { background: var(--dl-scope-tone-red, var(--v2-accent-red, currentColor)); }
</style>
