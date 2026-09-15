<!--
  Semantic RX-audio surface (MOR-1279, vocabulary slice 3B; grown by MOR-2425
  RX-B/RX-C to place host-owned finite-control handles alongside the AF-level
  scalar this file has always placed itself).

  Presentation only. It renders the MOR-1274 `rxAudio` fact group by placing
  host-owned instrument handles; it holds no state, consults no controller
  and owns no audio lifetime (v3 ADR invariant 11). Monitor mode, dual-RX
  routing focus/split, MOD-input source/readiness and the "Set LAN" remedy
  are all owned by `RxAudioInstrumentHost.svelte` (see its own doc comment)
  — this file only decides WHERE each handle is placed and, for AF level,
  whether it renders at all.

  NO AUDIO LIFETIME HERE. This surface must never open, start, probe or even
  import the audio path: audio lifetime is App-owned (MOR-1058) and "a view
  opened the transport on mount" is the MOR-972 P0 shape. It has no
  `onMount`, no effect, and imports neither `$lib/audio/audio-manager` nor
  `$lib/transport/*` — the eslint semantic boundary forbids both, and
  `__tests__/RxAudioSurface.test.ts` pins it a second time by source scan and
  by mounting with the seams spied.

  AF LEVEL IS 0..1 AND IS NOT RESCALED HERE. `RxAudioSnapshot.volume` is
  0..100 and the adapter divides by 100 exactly once
  (`radio-view-model-adapter.ts::deriveRxAudio`); the command handler
  (`makeRxAudioHandlers().onAfLevelChange`) takes 0..1 back. This file only
  displays the reading and places the host-owned handle — no arithmetic of
  its own touches the value.

  Two-level availability (MOR-977/1256), same as every sibling surface:
  `structural: false` renders NOTHING for that field — "this radio has no
  dual-RX routing" is a different claim from "the routing prefs were never
  restored", which renders present-and-unobserved. AF level is gated here
  directly (`rx.afLevel.availability.structural`), and its unread reading
  renders `UNKNOWN_TEXT` with `data-observed="false"`; the other five
  handles self-gate inside their own host-owned handle.
-->
<script module lang="ts">
  import type { RxAudioField } from './radio-view-model';
  import { formatKnownLevel } from './format-level';
  import { UNKNOWN_TEXT } from './rx-audio-instruments';

  export {
    FOCUS_CHOICES, LINK_LOST_TEXT, MONITOR_MODES, READINESS_LABEL, SPLIT_CHOICES, UNKNOWN_TEXT,
  } from './rx-audio-instruments';

  /** Usable ⇔ the radio HAS it, it is readable NOW, and it was actually read. */
  export const usable = (f: RxAudioField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  /** Honest text: an unread fact reads as unknown, never as a default. */
  export const textOf = (f: RxAudioField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : UNKNOWN_TEXT;
  const afText = (f: RxAudioField<number>): string =>
    f.reading.status === 'known' ? formatKnownLevel(f.reading.value, 0, 1) : UNKNOWN_TEXT;
</script>

<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';
  import type { RxAudioFiniteLayout, RxAudioInstrumentHandles } from './rx-audio-instruments';

  interface Props {
    view: RadioViewModel;
    handles: RxAudioInstrumentHandles;
    finiteLayout?: RxAudioFiniteLayout;
  }
  let { view, handles, finiteLayout }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine):
   *  a radio with no audio chain gets no empty panel and no zone had to learn
   *  about it. */
  let rx = $derived(view.rxAudio);
</script>

{#if rx}
  <section class="rx-audio-surface" data-testid="rx-audio-surface" aria-label="Receive audio">
    <!-- Monitor-mode radiogroup and the paired link-lost annotation
         (MOR-1384) are one `RxAudioInstrumentHost` handle: the annotation
         must never separate from the control it explains. When `finiteLayout`
         is supplied it places the finite five itself (MOR-2425 RX-B/RX-C) —
         mirrors `RfFrontEndSurface.svelte`'s own `{#if finiteLayout}` shape:
         the monitor-mode handle sits BEFORE the AF scalar in the default
         grouped order, so it is the one rendered directly (not suppressed)
         in the same position `finiteLayout(handles)` takes over. -->
    {#if finiteLayout}
      {@render finiteLayout(handles)}
    {:else}
      {@render handles.monitorMode()}
      {#if rx.afLevel.availability.structural}
      <label
        class="rx-audio-level" data-testid="rx-audio-af" data-observed={usable(rx.afLevel)}
      >
        <span class="rx-audio-name">AF</span>
        <!-- 0..1 — the contract's OWN unit. No rescale in either
             direction: the value in is the fact, the value out is the intent. -->
        {@render handles.afLevel()}
        <output data-testid="rx-audio-af-value">{afText(rx.afLevel)}</output>
      </label>
      {/if}
      {@render handles.routingFocus()}
      {@render handles.routingSplit()}
      <!-- MOD-input readiness/source readouts and the one-click LAN remedy
           (MOR-2366) are two adjacent handles, not one: `ModInputTxWarning`
           must be able to render the SAME remedy independently later without
           a second owner of the command (rx-tx-surface.ts::keyBlockedReasons
           doctrine). -->
      {@render handles.modInputSource()}
      {@render handles.setModInputLan()}
    {/if}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). Nothing here animates. */
  .rx-audio-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .rx-audio-level { display: flex; align-items: baseline; gap: 0.5rem; }
  .rx-audio-name { min-width: 4ch; }
  /* Second channel beside `data-observed`, never the only one: the unknown
     text itself is the primary one and survives forced-colors. */
  [data-observed='false'] { font-style: italic; }
</style>
