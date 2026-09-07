<!--
  Semantic RX-audio surface (MOR-1279, vocabulary slice 3B).

  Presentation only. It renders the MOR-1274 `rxAudio` fact group — monitor
  mode, AF level, dual-RX routing focus/split, MOD-input source + readiness —
  and emits control intents as callbacks. It holds no state, consults no
  controller and owns no audio lifetime (v3 ADR invariant 11).

  SAFETY. Four rules govern this file and nothing may relax them:

  (1) NO AUDIO LIFETIME HERE. This surface must never open, start, probe or
      even import the audio path: audio lifetime is App-owned (MOR-1058) and
      "a view opened the transport on mount" is the MOR-972 P0 shape. It has
      no `onMount`, no effect, and imports neither `$lib/audio/audio-manager`
      nor `$lib/transport/*` — the eslint semantic boundary forbids both, and
      `__tests__/RxAudioSurface.test.ts` pins it a second time by source scan
      and by mounting with the seams spied.

  (2) UNKNOWN IS RENDERED AS UNKNOWN. Three facts here are exactly the ones
      the shipped v2 path fabricates: AF level (`toRxAudioProps` → 0.5),
      routing focus (`AudioRoutingControl` → 'both') and stereo split
      (→ false). Slice 3A degraded all three to `unknown`; re-substituting a
      default at THIS layer would erase that honesty gain one layer up. Every
      unread fact renders `UNKNOWN_TEXT` and carries `data-observed="false"`.

  (3) `live` IS OFFERED BY THE `liveAudio` FACT, never re-derived. Reading
      `caps.capabilities.includes('audio')` back out of a lower layer is what
      the contract exists to stop (and the semantic boundary forbids the store
      import anyway). `structural` decides whether the mode is OFFERED;
      `operational` (audio-WS health) is reported as an annotation and
      deliberately does NOT disable the button — the audio WS opens BECAUSE
      the operator picks `live`, so gating on it would make `live` unreachable.

  (4) AF LEVEL IS 0..1 AND IS NOT RESCALED. `RxAudioSnapshot.volume` is
      0..100 and the adapter divides by 100 exactly once
      (`radio-view-model-adapter.ts::deriveRxAudio`); the command handler
      (`makeRxAudioHandlers().onAfLevelChange`) takes 0..1 back. A second
      divide here would move the operator's AF by two orders of magnitude.

  MOD-input `mismatch` is the recorded "web voice TX = noise/squeal" failure
  (DATA OFF MOD = MIC while the browser streams over LAN). It keeps a
  one-click remedy, exactly like `ModInputTxWarning` — which is NOT moved or
  duplicated here (it stays in the rx-tx zone where MOR-1258 put it); this is
  a standing readiness readout that routes its fix through the SAME command.

  Two-level availability (MOR-977/1256): `structural: false` renders NOTHING —
  "this radio has no dual-RX routing" is a different claim from "the routing
  prefs were never restored", which renders present-and-unobserved.
-->
<script module lang="ts">
  import type { RxAudioField } from './radio-view-model';
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
    {/if}

    {#if rx.afLevel.availability.structural}
      <label
        class="rx-audio-level" data-testid="rx-audio-af" data-observed={usable(rx.afLevel)}
      >
        <span class="rx-audio-name">AF</span>
        <!-- 0..1 — the contract's OWN unit (rule 4). No rescale in either
             direction: the value in is the fact, the value out is the intent. -->
        {@render handles.afLevel()}
        <output data-testid="rx-audio-af-value">{textOf(rx.afLevel)}</output>
      </label>
    {/if}

    {#if !finiteLayout}
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
