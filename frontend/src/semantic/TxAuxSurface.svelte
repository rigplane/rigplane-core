<!--
  Semantic TX-auxiliary surface (MOR-1265, vocabulary slice 1B).

  Presentation only. It renders the MOR-1244 `txAux` fact group — ATU, VOX
  (+gain/anti-VOX/delay), COMP (+level), MON (+level), RF power, mic gain,
  drive gain — and emits control intents as callbacks. It holds no state and
  consults no controller (v3 ADR invariant 11).

  SAFETY. Two rules govern this file and nothing may relax them:

  (1) ATU **TUNE** emits a carrier: it is a transmit-causing action. It is
      gated by `keyBlockedReasons` — the SAME predicate, imported from the
      same module, applied to the SAME App-owned TX authority snapshot that
      gates `RxTxSurface`'s key button. A local copy could drift and disagree;
      the shared import cannot. The gate is enforced twice, on the widget
      (`disabled`) and inside the handler.
  (2) This surface is NOT a second key path. Exactly one `<RxTxSurface>`
      remains the key/unkey authority (MOR-1262 §2 slice 1 safety note iii).
      Nothing here takes a TX lease, keys, or unkeys: the TUNE carrier is the
      radio's own ATU cycle, started by a backend command, and this surface
      only decides whether the operator may ask for it.

  Two-level availability (MOR-977/1256): `structural: false` renders NOTHING —
  "this radio has no VOX" is a different claim from "VOX is unreadable right
  now", which renders present-and-disabled with a reason. A control is usable
  only when it is also actually observed; every intent below is computed from
  the current value, so acting on an `unknown` reading would arm a guess.
-->
<script module lang="ts">
  import type { TxAuxField } from './radio-view-model';
  import { pressedOf } from './pressed-of';
  import { rawToPercentDisplay } from '../components-v2/controls/value-control/value-control-core';
  import { disabledReasonText } from './disabled-reason';

  /** On/off controls, `[field, label]`. ATU's reading is a three-state enum,
   *  the rest are booleans; `pressedOf` normalises both to one aria state. */
  export const TX_AUX_TOGGLES = [
    ['atu', 'ATU'], ['vox', 'VOX'], ['compressor', 'COMP'], ['monitor', 'MON'],
  ] as const;
  /** `[field, label, min, max, step, format]` — the slider bound and step are
   *  RAW wire units (the MOR-1244 contract applies no normalisation, so these
   *  are exactly the ranges `TxPanel`/`VoxPanel` have always used: RF power
   *  0..1, VOX delay 0..20, everything else 0..255 — rescaling the VALUE here
   *  would silently move a level). `format` is the DISPLAY-only convention
   *  (MOR-1452): every level slider on this surface reads back as a percent
   *  of its own declared domain via `rawToPercentDisplay`, the same per-field
   *  `displayFn` idiom `DspSurface` established — chosen over
   *  `formatKnownLevel`'s hardcoded `[0,1]`-domain sniff (MOR-1447) because it
   *  is domain-generic instead of special-casing one field shape, so RF power
   *  (0..1), a raw 0..255 level, and VOX delay (0..20) all render as one
   *  convention instead of three different ones. */
  export const TX_AUX_LEVELS = [
    ['rfPower', 'RF power', 0, 1, 0.01, (v: number) => rawToPercentDisplay(v, 0, 1)],
    ['micGain', 'Mic gain', 0, 255, 1, rawToPercentDisplay],
    ['driveGain', 'Drive gain', 0, 255, 1, rawToPercentDisplay],
    ['voxGain', 'VOX gain', 0, 255, 1, rawToPercentDisplay],
    ['antiVoxGain', 'Anti-VOX', 0, 255, 1, rawToPercentDisplay],
    ['voxDelay', 'VOX delay', 0, 20, 1, (v: number) => rawToPercentDisplay(v, 0, 20)],
    ['compressorLevel', 'COMP level', 0, 255, 1, rawToPercentDisplay],
    ['monitorLevel', 'MON level', 0, 255, 1, rawToPercentDisplay],
  ] as const;
  export type TxAuxToggleField = (typeof TX_AUX_TOGGLES)[number][0];
  export type TxAuxLevelField = (typeof TX_AUX_LEVELS)[number][0];

  /** Usable ⇔ the radio HAS it, it is readable NOW, and it has been observed. */
  const usable = (f: TxAuxField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  /** The MOR-977 `DisabledReasonCode` a present-but-unusable control carries. */
  const reasonOf = (f: TxAuxField<unknown>): 'field-not-observed' | undefined =>
    usable(f) ? undefined : 'field-not-observed';
  /** MOR-1422: the SAME gate as `reasonOf`, rendered as operator-facing text
   *  for `title` (hover) and the `aria-describedby` target below (screen
   *  readers) — the `data-disabled-reason` attribute `reasonOf` feeds is a
   *  test/CSS hook only, invisible to both. */
  const reasonTextOf = (f: TxAuxField<unknown>): string | undefined => disabledReasonText(f.availability);
  const textOf = (f: TxAuxField<unknown>): string =>
    f.reading.status !== 'known' ? '?'
      : typeof f.reading.value === 'boolean' ? (f.reading.value ? 'on' : 'off')
        : String(f.reading.value);
  /** Same freshness discipline as `textOf`, but a KNOWN level reading is run
   *  through its declared `format` (MOR-1452) instead of `String()`-ing the
   *  raw wire value — e.g. RF power reading back as "80%" instead of the
   *  literal `0.5529411764705883`, and mic gain as "50%" instead of `128`. */
  const levelTextOf = (f: TxAuxField<number>, format: (v: number) => string): string =>
    f.reading.status === 'known' ? format(f.reading.value) : '?';
  const numberOf = (f: TxAuxField<number>, fallback: number): number =>
    f.reading.status === 'known' ? f.reading.value : fallback;

  /** Per-instance DOM id, so several mounted surfaces keep distinct aria targets. */
  let sequence = 0;
</script>

<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';
  import { BLOCKED_LABEL, keyBlockedReasons, type TxAuthoritySnapshot } from './rx-tx-surface';

  interface Props {
    view: RadioViewModel;
    tx: TxAuthoritySnapshot;
    onToggle?: (field: TxAuxToggleField) => void;
    onLevelChange?: (field: TxAuxLevelField, value: number) => void;
    onAtuTune?: () => void;
  }
  let { view, tx, onToggle, onLevelChange, onAtuTune }: Props = $props();

  const blockedId = `tx-aux-blocked-${++sequence}`;
  /** MOR-1422: prefix for the per-field hidden reason text `aria-describedby`
   *  targets below — shares `blockedId`'s instance number so several mounted
   *  surfaces never collide, without incrementing `sequence` a second time. */
  const reasonIdPrefix = `tx-aux-reason-${sequence}`;
  /** `aria-describedby` needs an ID to point at; `aria-description` (no ID,
   *  the string inline) is not yet in Svelte's own attribute typings —
   *  `undefined` omits the attribute exactly when there is no reason. */
  const reasonIdOf = (field: string, f: TxAuxField<unknown>): string | undefined =>
    disabledReasonText(f.availability) !== undefined ? `${reasonIdPrefix}-${field}` : undefined;
  let txAux = $derived(view.txAux);
  let tuneBlocked = $derived(keyBlockedReasons(view, tx));

  function toggle(field: TxAuxToggleField): void {
    if (txAux && usable(txAux[field])) onToggle?.(field);
  }
  function level(field: TxAuxLevelField, value: number): void {
    if (txAux && usable(txAux[field])) onLevelChange?.(field, value);
  }
  /** The handler half of the TUNE gate. `disabled` alone is not enough: a
   *  design language may restyle this control, and a programmatic click must
   *  not start a carrier the key intent would have been refused. */
  function requestTune(): void {
    if (txAux && usable(txAux.atu) && tuneBlocked.length === 0) onAtuTune?.();
  }
</script>

{#if txAux}
  <section class="tx-aux-surface" data-testid="tx-aux-surface" aria-label="Transmit auxiliary controls">
    <div class="tx-aux-row">
      {#each TX_AUX_TOGGLES as [field, label] (field)}
        {#if txAux[field].availability.structural}
          <button
            type="button" class="tx-aux-toggle"
            data-testid={`tx-aux-${field}`} data-field={field}
            data-disabled-reason={reasonOf(txAux[field])}
            title={reasonTextOf(txAux[field])} aria-describedby={reasonIdOf(field, txAux[field])}
            aria-pressed={pressedOf(txAux[field])}
            disabled={!usable(txAux[field])}
            onclick={() => toggle(field)}
          >{label}: {textOf(txAux[field])}</button>
          {#if reasonTextOf(txAux[field]) !== undefined}
            <span id={reasonIdOf(field, txAux[field])} class="sr-only">{reasonTextOf(txAux[field])}</span>
          {/if}
        {/if}
      {/each}
      {#if txAux.atu.availability.structural}
        <!-- Transmit-causing. See the file header, rule (1). -->
        <button
          type="button" class="tx-aux-tune"
          data-testid="tx-aux-atu-tune" aria-describedby={blockedId}
          disabled={tuneBlocked.length > 0 || !usable(txAux.atu)}
          onclick={requestTune}
        >TUNE</button>
      {/if}
    </div>

    {#each TX_AUX_LEVELS as [field, label, min, max, step, format] (field)}
      {#if txAux[field].availability.structural}
        <label
          class="tx-aux-level" data-testid={`tx-aux-${field}`} data-field={field}
          data-disabled-reason={reasonOf(txAux[field])}
        >
          <span class="tx-aux-name">{label}</span>
          <input
            type="range" {min} {max} {step}
            value={numberOf(txAux[field], min)}
            title={reasonTextOf(txAux[field])} aria-describedby={reasonIdOf(field, txAux[field])}
            disabled={!usable(txAux[field])}
            oninput={(event) => level(field, event.currentTarget.valueAsNumber)}
          />
          {#if reasonTextOf(txAux[field]) !== undefined}
            <span id={reasonIdOf(field, txAux[field])} class="sr-only">{reasonTextOf(txAux[field])}</span>
          {/if}
          <output>{levelTextOf(txAux[field], format)}</output>
        </label>
      {/if}
    {/each}

    {#if txAux.atu.availability.structural}
      <ul class="tx-aux-blocked" id={blockedId} data-testid="tx-aux-tune-blocked">
        {#each tuneBlocked as code (code)}<li data-reason={code}>{BLOCKED_LABEL[code]}</li>{/each}
      </ul>
    {/if}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). */
  .tx-aux-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .tx-aux-row { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .tx-aux-level { display: flex; align-items: baseline; gap: 0.5rem; }
  .tx-aux-name { min-width: 8ch; }
  .tx-aux-blocked { margin: 0; padding-inline-start: 1.2em; }
  .tx-aux-blocked:empty { display: none; }
  /* MOR-1422: the `aria-describedby` target for a disabled control's reason
     — present for screen readers, never painted (the `title` attribute
     already carries the sighted-hover channel). */
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
  .tx-aux-toggle[aria-pressed='true'] { font-weight: 700; }
  .tx-aux-toggle:disabled, .tx-aux-tune:disabled { cursor: not-allowed; }
</style>
