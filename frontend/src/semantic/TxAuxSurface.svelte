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

  /** On/off controls, `[field, label]`. ATU's reading is a three-state enum,
   *  the rest are booleans; `pressedOf` normalises both to one aria state. */
  export const TX_AUX_TOGGLES = [
    ['atu', 'ATU'], ['vox', 'VOX'], ['compressor', 'COMP'], ['monitor', 'MON'],
  ] as const;
  /** `[field, label, min, max, step]` in RAW wire units — the MOR-1244
   *  contract applies no normalisation, so these are exactly the ranges
   *  `TxPanel`/`VoxPanel` have always used (RF power 0..1, VOX delay 0..20,
   *  everything else 0..255). Rescaling here would silently move a level. */
  export const TX_AUX_LEVELS = [
    ['rfPower', 'RF power', 0, 1, 0.01],
    ['micGain', 'Mic gain', 0, 255, 1],
    ['driveGain', 'Drive gain', 0, 255, 1],
    ['voxGain', 'VOX gain', 0, 255, 1],
    ['antiVoxGain', 'Anti-VOX', 0, 255, 1],
    ['voxDelay', 'VOX delay', 0, 20, 1],
    ['compressorLevel', 'COMP level', 0, 255, 1],
    ['monitorLevel', 'MON level', 0, 255, 1],
  ] as const;
  export type TxAuxToggleField = (typeof TX_AUX_TOGGLES)[number][0];
  export type TxAuxLevelField = (typeof TX_AUX_LEVELS)[number][0];

  /** Usable ⇔ the radio HAS it, it is readable NOW, and it has been observed. */
  const usable = (f: TxAuxField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  /** The MOR-977 `DisabledReasonCode` a present-but-unusable control carries. */
  const reasonOf = (f: TxAuxField<unknown>): 'field-not-observed' | undefined =>
    usable(f) ? undefined : 'field-not-observed';
  const textOf = (f: TxAuxField<unknown>): string =>
    f.reading.status !== 'known' ? '?'
      : typeof f.reading.value === 'boolean' ? (f.reading.value ? 'on' : 'off')
        : String(f.reading.value);
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
            aria-pressed={pressedOf(txAux[field])}
            disabled={!usable(txAux[field])}
            onclick={() => toggle(field)}
          >{label}: {textOf(txAux[field])}</button>
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

    {#each TX_AUX_LEVELS as [field, label, min, max, step] (field)}
      {#if txAux[field].availability.structural}
        <label
          class="tx-aux-level" data-testid={`tx-aux-${field}`} data-field={field}
          data-disabled-reason={reasonOf(txAux[field])}
        >
          <span class="tx-aux-name">{label}</span>
          <input
            type="range" {min} {max} {step}
            value={numberOf(txAux[field], min)}
            disabled={!usable(txAux[field])}
            oninput={(event) => level(field, event.currentTarget.valueAsNumber)}
          />
          <output>{textOf(txAux[field])}</output>
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
  .tx-aux-toggle[aria-pressed='true'] { font-weight: 700; }
  .tx-aux-toggle:disabled, .tx-aux-tune:disabled { cursor: not-allowed; }
</style>
