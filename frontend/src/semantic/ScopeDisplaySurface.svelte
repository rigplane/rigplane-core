<!--
  Semantic scope-display surface (MOR-1312, vocabulary slice 12B — the LAST
  slice of the vocabulary program).

  Presentation only. It renders the MOR-1301/MOR-1312 `scopeDisplay` fact
  group — WHICH scope source is currently live and HOW HEALTHY it is, plus
  the hardware channel's own connectivity — and emits NO intent (v3 ADR
  invariant 11): this is a pure readout, never an action surface.

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
  mount this surface bare in BOTH compositions (the `meters`/`txAux` shape,
  not the control-bearing `rxAudio` single-only shape).

  `hardwareConnected` (MOR-1312 addition, MOR-1352 finding) is genuinely NOT
  redundant with `health` when `source === 'audio_fft'` — see
  `radio-view-model.ts`'s `ScopeDisplayViewModel` doc comment.
-->
<script module lang="ts">
  import type { ScopeDisplayField, ScopeHealthState } from './radio-view-model';

  /** The ONE rendering of "not observed". Never a fabricated default. */
  export const UNKNOWN_TEXT = '—';

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
  const textOf = (f: ScopeDisplayField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : UNKNOWN_TEXT;
  /** `on`/`off`/unknown for a boolean field — narrows `reading.status` inline
   *  so the value read is never a stale/impossible union member. */
  const onOff = (f: ScopeDisplayField<boolean>): string =>
    f.reading.status === 'known' ? (f.reading.value ? 'on' : 'off') : UNKNOWN_TEXT;
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
  <section
    class="scope-display-surface" data-testid="scope-display-surface" role="status"
    aria-label="Scope status"
  >
    <span
      class="scope-display-field" data-testid="scope-display-source"
      data-observed={usable(sd.source)}
    >SRC {textOf(sd.source)}</span>
    <span
      class="scope-display-field" data-testid="scope-display-health"
      data-observed={usable(sd.health)}
      data-tone={sd.health.reading.status === 'known' ? healthTone(sd.health.reading.value) : 'neutral'}
    >{textOf(sd.health)}</span>
    <span
      class="scope-display-field" data-testid="scope-display-hardware"
      data-observed={usable(sd.hardwareConnected)}
    >HW {onOff(sd.hardwareConnected)}</span>
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). Nothing here animates. */
  .scope-display-surface { display: flex; align-items: baseline; gap: 0.5rem; }
  /* Second channel beside `data-observed`, never the only one: the unknown
     text itself is the primary one and survives forced-colors. */
  [data-observed='false'] { font-style: italic; }
</style>
