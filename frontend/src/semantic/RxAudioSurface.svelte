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
  import { modInputSourceLabel } from '$lib/radio/mod-input';
  import type {
    AudioFocus, ModInputReadiness, MonitorMode, RxAudioField,
  } from './radio-view-model';

  /** The three monitor modes, in the shipped `RxAudioPanel` order. */
  export const MONITOR_MODES: readonly MonitorMode[] = ['local', 'live', 'mute'];
  /** Dual-RX focus choices, verbatim `AudioRoutingControl`'s own order. */
  export const FOCUS_CHOICES: readonly AudioFocus[] = ['main', 'sub', 'both'];
  /** Stereo split as two ABSOLUTE choices rather than one relative toggle: a
   *  toggle computed from an unknown reading would arm a guess (the 1B rule),
   *  and it would leave the control permanently dead while routing is
   *  unobserved. `[value, label]`. */
  export const SPLIT_CHOICES = [[true, 'on'], [false, 'off']] as const;
  /** The ONE rendering of "not measured". Never 0, never 'both', never 'off'. */
  export const UNKNOWN_TEXT = '—';
  /** MOR-1384 — the restored audio-link-lost words. English is hardcoded here
   *  exactly like every other string on this surface (the MOR-1373 semantic
   *  i18n policy is still open); the v2 key `core.overlay.audioLinkLost` is
   *  NOT reused, and its "— reconnecting…" clause is deliberately dropped: a
   *  retry in flight is client behaviour this contract does not carry, and
   *  this surface may not consult the audio manager to find out. */
  export const LINK_LOST_TEXT = 'live audio link lost';
  /** Readiness words. `mismatch` names the consequence, not just the state. */
  export const READINESS_LABEL: Record<ModInputReadiness['status'], string> = {
    'not-applicable': 'n/a', ready: 'LAN', unknown: UNKNOWN_TEXT,
    mismatch: 'not LAN — web voice TX would modulate from the wrong source',
  };

  /** Usable ⇔ the radio HAS it, it is readable NOW, and it was actually read. */
  export const usable = (f: RxAudioField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  /** Honest text: an unread fact reads as unknown, never as a default. */
  export const textOf = (f: RxAudioField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : UNKNOWN_TEXT;
  const isValue = (f: RxAudioField<unknown>, value: unknown): boolean =>
    f.reading.status === 'known' && f.reading.value === value;
</script>

<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';

  interface Props {
    view: RadioViewModel;
    onMonitorMode?: (mode: MonitorMode) => void;
    onAfLevel?: (level: number) => void;
    onRoutingFocus?: (focus: AudioFocus) => void;
    onRoutingSplit?: (split: boolean) => void;
    onSetModInputLan?: () => void;
  }
  let {
    view, onMonitorMode, onAfLevel, onRoutingFocus, onRoutingSplit, onSetModInputLan,
  }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine):
   *  a radio with no audio chain gets no empty panel and no zone had to learn
   *  about it. */
  let rx = $derived(view.rxAudio);
  /** Rule (3): the FACT, not a capability re-derivation. */
  let liveOffered = $derived(rx?.liveAudio.structural === true);
  /** MOR-1384 — the v2 `RxAudioPanel` link-lost readout, restored from the SAME
   *  underlying fact (`liveAudio.operational` is `runtime.connectionAudio`, the
   *  identical audio-WS health the retired panel read as `isAudioConnected`).
   *
   *  All three legs are load-bearing, and `monitorMode === 'live'` is the
   *  EPISTEMIC one: the audio WS is opened BECAUSE the operator selects `live`
   *  (see rule 3), so while `local`/`mute` is selected a down link was never
   *  requested and is not a loss. Reporting "lost" there would be a claim about
   *  an observation that was never made — and an operator who trusts it
   *  mis-reads a working radio. Structurally absent live audio has no link to
   *  lose at all. Nothing here is new: no timer, no history, no retry state. */
  let linkLost = $derived(
    liveOffered && rx?.monitorMode === 'live' && rx.liveAudio.operational === false,
  );
  /** The slider position while AF is unread is a guess, so the control is
   *  inert then — the same "never act on an unknown reading" gate slice 1B
   *  applies, enforced on the widget AND in the handler. */
  function changeAf(level: number): void {
    if (rx && usable(rx.afLevel)) onAfLevel?.(level);
  }
</script>

{#if rx}
  <section class="rx-audio-surface" data-testid="rx-audio-surface" aria-label="Receive audio">
    <div
      class="rx-audio-row" role="radiogroup" aria-label="Monitor mode"
      data-testid="rx-audio-monitor" data-monitor-mode={rx.monitorMode}
    >
      {#each MONITOR_MODES as mode (mode)}
        {#if mode !== 'live' || liveOffered}
          <button
            type="button" role="radio" class="rx-audio-choice"
            data-testid={`rx-audio-monitor-${mode}`} data-mode={mode}
            data-live-link={mode === 'live' ? rx.liveAudio.operational : undefined}
            aria-checked={rx.monitorMode === mode}
            onclick={() => onMonitorMode?.(mode)}
          >{mode}</button>
        {/if}
      {/each}
    </div>

    <!-- Beside the monitor row, next to the `live` choice whose
         `data-live-link` already carries this fact machine-readably: the
         operator gets it in WORDS. Zero focusable elements, so the rx-audio
         zone's tab order is unchanged (MOR-1069/MOR-1304 mounting canon). -->
    {#if linkLost}
      <p class="rx-audio-row" data-testid="rx-audio-link">{LINK_LOST_TEXT}</p>
    {/if}

    {#if rx.afLevel.availability.structural}
      <label
        class="rx-audio-level" data-testid="rx-audio-af" data-observed={usable(rx.afLevel)}
      >
        <span class="rx-audio-name">AF</span>
        <!-- 0..1 — the contract's OWN unit (rule 4). No rescale in either
             direction: the value in is the fact, the value out is the intent. -->
        <input
          type="range" min="0" max="1" step="0.01"
          value={rx.afLevel.reading.status === 'known' ? rx.afLevel.reading.value : 0}
          disabled={!usable(rx.afLevel)}
          oninput={(event) => changeAf(event.currentTarget.valueAsNumber)}
        />
        <output data-testid="rx-audio-af-value">{textOf(rx.afLevel)}</output>
      </label>
    {/if}

    {#if rx.routingFocus.availability.structural}
      <div
        class="rx-audio-row" role="radiogroup" aria-label="Audio focus"
        data-testid="rx-audio-focus" data-observed={usable(rx.routingFocus)}
      >
        {#each FOCUS_CHOICES as focus (focus)}
          <button
            type="button" role="radio" class="rx-audio-choice"
            data-testid={`rx-audio-focus-${focus}`}
            aria-checked={isValue(rx.routingFocus, focus)}
            onclick={() => onRoutingFocus?.(focus)}
          >{focus}</button>
        {/each}
        <output data-testid="rx-audio-focus-value">{textOf(rx.routingFocus)}</output>
      </div>
    {/if}

    {#if rx.routingSplit.availability.structural}
      <div
        class="rx-audio-row" role="radiogroup" aria-label="Stereo split"
        data-testid="rx-audio-split" data-observed={usable(rx.routingSplit)}
      >
        {#each SPLIT_CHOICES as [value, label] (label)}
          <button
            type="button" role="radio" class="rx-audio-choice"
            data-testid={`rx-audio-split-${label}`}
            aria-checked={isValue(rx.routingSplit, value)}
            onclick={() => onRoutingSplit?.(value)}
          >split {label}</button>
        {/each}
        <output data-testid="rx-audio-split-value">{textOf(rx.routingSplit)}</output>
      </div>
    {/if}

    {#if rx.modInputSource.availability.structural}
      <p
        class="rx-audio-row" data-testid="rx-audio-mod-input"
        data-readiness={rx.modInputReadiness.status}
        data-observed={usable(rx.modInputSource)}
      >
        <span data-testid="rx-audio-mod-source">MOD: {rx.modInputSource.reading.status === 'known'
          ? modInputSourceLabel(rx.modInputSource.reading.value) ?? UNKNOWN_TEXT
          : UNKNOWN_TEXT}</span>
        <span data-testid="rx-audio-mod-readiness"
        >{READINESS_LABEL[rx.modInputReadiness.status]}</span>
        {#if rx.modInputReadiness.status === 'mismatch'}
          <!-- The one-click remedy, same command path as ModInputTxWarning's
               "Set LAN" (rule: a mismatch must never be a dead end). -->
          <button
            type="button" data-testid="rx-audio-mod-set-lan" onclick={() => onSetModInputLan?.()}
          >Set LAN</button>
        {/if}
      </p>
    {/if}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). Nothing here animates. */
  .rx-audio-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .rx-audio-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0; }
  .rx-audio-level { display: flex; align-items: baseline; gap: 0.5rem; }
  .rx-audio-name { min-width: 4ch; }
  .rx-audio-choice[aria-checked='true'] { font-weight: 700; }
  /* Second channel beside `data-observed`, never the only one: the unknown
     text itself is the primary one and survives forced-colors. */
  [data-observed='false'] { font-style: italic; }
  button:disabled, input:disabled { cursor: not-allowed; }
</style>
