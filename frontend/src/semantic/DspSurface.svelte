<!--
  Semantic DSP surface (MOR-1305, vocabulary slice 5B).

  Presentation only. It renders the MOR-1290 `dsp` fact group — NR
  (active/level), NB (active/level/depth/width), notch (mode/freq/manual
  width), AGC (mode/choice set/time constant) — and emits control intents as
  callbacks. It holds no state and consults no controller (v3 ADR invariant
  11), the same discipline `TxAuxSurface` (MOR-1265) established.

  CARRY-FORWARDS (binding, from the MOR-1290 fact-layer decisions this
  surface must not relax):

  (1) `agcLabels`/`nbLevelMax`/`nbLevelPercent` are NOT facts — pure
      caps-echo display metadata (a slider ceiling and a percent-vs-raw
      display choice, `lib/runtime/props/panel-props.ts`'s own `toDspProps`
      precedent). They arrive as plain props, read directly off `caps` at the
      wiring seam (`SemanticRadioSurfaces.svelte`, which already holds
      `runtime.caps` for the view-model adapter call) — never folded into
      `DspViewModel`, and never re-derived here from a capabilities import
      this file is not allowed to hold.
  (2) `agcTimeConstant` may be `structural: true` with no real control on a
      radio that borrows the `agc` capability tag optimistically. That is an
      accepted fact-layer optimism, not something this surface special-cases
      — a present-but-never-observed field renders exactly like any other
      unobserved present field, honestly disabled.
  (3) Every reading here is rendered exactly as the fact group states it. No
      range-fallback plumbing, no re-derivation of `controlRangeFromCaps` —
      `nrLevel`/`nbDepth` already arrive display-scaled from the adapter.
  (4) `unknown` renders as `?`, never as a v2 fabricated default (0 dB, OFF,
      WIDE) — same fail-closed-presentation doctrine as `TxAuxSurface`.

  Two-level availability (MOR-977/1256): `structural: false` renders NOTHING;
  a present-but-unusable control stays visible and disabled rather than
  guessing a value.
-->
<script module lang="ts">
  import type { DspField } from './radio-view-model';
  import { buildAgcOptions } from '../components-v2/panels/agc-utils';
  import { NOTCH_WIDTH_LABELS, formatAgcTime } from '../components-v2/panels/dsp-panel-logic';
  import { rawToPercentDisplay } from '../components-v2/controls/value-control/value-control-core';

  /** On/off controls, `[field, label]`. */
  export const DSP_TOGGLES = [['nrActive', 'NR'], ['nbActive', 'NB']] as const;
  /** `[field, label, min, max, step, format?]` — `nrLevel`/`nbDepth` are
   *  ALREADY the adapter's display-scaled values (carry-forward 3); the rest
   *  are raw wire ranges, verbatim `DspPanel.svelte`'s own slider bounds.
   *  `nbLevel` is excluded — its ceiling is the caps-echoed `nbLevelMax` prop,
   *  not a static bound, and is rendered separately below. */
  export const DSP_LEVELS = [
    ['nrLevel', 'NR level', 0, 15, 1],
    ['nbDepth', 'NB depth', 1, 10, 1],
    ['nbWidth', 'NB width', 0, 255, 1],
    ['notchFreq', 'Notch freq', 0, 3000, 1],
    ['manualNotchWidth', 'Notch width', 0, 2, 1, (v: number) => NOTCH_WIDTH_LABELS[v] ?? String(v)],
    ['agcTimeConstant', 'AGC time', 0, 9, 1, formatAgcTime],
  ] as const;
  export type DspToggleField = (typeof DSP_TOGGLES)[number][0];
  export type DspLevelField = (typeof DSP_LEVELS)[number][0] | 'nbLevel';
  const NOTCH_MODES = ['off', 'auto', 'manual'] as const;

  /** Usable ⇔ the radio HAS it, it is readable NOW, and it has been observed. */
  const usable = (f: DspField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  const reasonOf = (f: DspField<unknown>): 'field-not-observed' | undefined =>
    usable(f) ? undefined : 'field-not-observed';
  const numberOf = (f: DspField<number>, fallback: number): number =>
    f.reading.status === 'known' ? f.reading.value : fallback;
  const fmt = (f: DspField<unknown>, format?: (v: number) => string): string => {
    if (f.reading.status !== 'known') return '?';
    const v = f.reading.value;
    return typeof v === 'boolean' ? (v ? 'on' : 'off') : format ? format(v as number) : String(v);
  };
  const pressedOf = (f: DspField<unknown>): boolean | undefined =>
    f.reading.status === 'known' ? f.reading.value !== false : undefined;
</script>

<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';

  interface Props {
    view: RadioViewModel;
    agcLabels?: Record<string, string>;
    nbLevelMax?: number;
    nbLevelPercent?: boolean;
    onToggle?: (field: DspToggleField, next: boolean) => void;
    onLevelChange?: (field: DspLevelField, value: number) => void;
    onNotchModeChange?: (mode: 'off' | 'auto' | 'manual') => void;
    onAgcModeChange?: (mode: number) => void;
  }
  let {
    view, agcLabels = {}, nbLevelMax = 255, nbLevelPercent = false,
    onToggle, onLevelChange, onNotchModeChange, onAgcModeChange,
  }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine). */
  let dsp = $derived(view.dsp);
  let agcOptions = $derived(dsp ? buildAgcOptions([...dsp.agcModes], agcLabels) : []);
  let nbLevelFormat = $derived(
    nbLevelPercent ? (v: number) => rawToPercentDisplay(v, 0, nbLevelMax) : undefined,
  );

  function toggle(field: DspToggleField): void {
    const f = dsp?.[field];
    if (f && usable(f) && f.reading.status === 'known') onToggle?.(field, !f.reading.value);
  }
  function level(field: DspLevelField, value: number): void {
    if (dsp && usable(dsp[field])) onLevelChange?.(field, value);
  }
  function notch(mode: (typeof NOTCH_MODES)[number]): void {
    if (dsp && usable(dsp.notchMode)) onNotchModeChange?.(mode);
  }
  function agc(mode: number): void {
    if (dsp && usable(dsp.agcMode)) onAgcModeChange?.(mode);
  }
</script>

{#if dsp}
  <section class="dsp-surface" data-testid="dsp-surface" aria-label="DSP controls">
    <div class="dsp-row">
      {#each DSP_TOGGLES as [field, label] (field)}
        {#if dsp[field].availability.structural}
          <button
            type="button" class="dsp-toggle" data-testid={`dsp-${field}`} data-field={field}
            data-disabled-reason={reasonOf(dsp[field])} aria-pressed={pressedOf(dsp[field])}
            disabled={!usable(dsp[field])} onclick={() => toggle(field)}
          >{label}: {fmt(dsp[field])}</button>
        {/if}
      {/each}
    </div>

    {#each DSP_LEVELS as [field, label, min, max, step, format] (field)}
      {#if dsp[field].availability.structural}
        <label
          class="dsp-level" data-testid={`dsp-${field}`} data-field={field}
          data-disabled-reason={reasonOf(dsp[field])}
        >
          <span class="dsp-name">{label}</span>
          <input
            type="range" {min} {max} {step} value={numberOf(dsp[field], min)}
            disabled={!usable(dsp[field])}
            oninput={(event) => level(field, event.currentTarget.valueAsNumber)}
          />
          <output>{fmt(dsp[field], format)}</output>
        </label>
      {/if}
    {/each}

    {#if dsp.nbLevel.availability.structural}
      <label class="dsp-level" data-testid="dsp-nbLevel" data-field="nbLevel" data-disabled-reason={reasonOf(dsp.nbLevel)}>
        <span class="dsp-name">NB level</span>
        <input
          type="range" min={0} max={nbLevelMax} step={1} value={numberOf(dsp.nbLevel, 0)}
          disabled={!usable(dsp.nbLevel)}
          oninput={(event) => level('nbLevel', event.currentTarget.valueAsNumber)}
        />
        <output>{fmt(dsp.nbLevel, nbLevelFormat)}</output>
      </label>
    {/if}

    {#if dsp.notchMode.availability.structural}
      <div class="dsp-row" data-testid="dsp-notchMode" data-disabled-reason={reasonOf(dsp.notchMode)}>
        {#each NOTCH_MODES as mode (mode)}
          <button
            type="button" class="dsp-choice" data-testid={`dsp-notchMode-${mode}`}
            aria-pressed={dsp.notchMode.reading.status === 'known' && dsp.notchMode.reading.value === mode}
            disabled={!usable(dsp.notchMode)} onclick={() => notch(mode)}
          >{mode}</button>
        {/each}
      </div>
    {/if}

    {#if dsp.agcMode.availability.structural}
      <div class="dsp-row" data-testid="dsp-agcMode" data-disabled-reason={reasonOf(dsp.agcMode)}>
        {#each agcOptions as option (option.value)}
          <button
            type="button" class="dsp-choice" data-testid={`dsp-agcMode-${option.value}`}
            aria-pressed={dsp.agcMode.reading.status === 'known' && dsp.agcMode.reading.value === option.value}
            disabled={!usable(dsp.agcMode)} onclick={() => agc(option.value)}
          >{option.label}</button>
        {/each}
      </div>
    {/if}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). */
  .dsp-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .dsp-row { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .dsp-level { display: flex; align-items: baseline; gap: 0.5rem; }
  .dsp-name { min-width: 8ch; }
  .dsp-toggle[aria-pressed='true'], .dsp-choice[aria-pressed='true'] { font-weight: 700; }
  .dsp-toggle:disabled, .dsp-choice:disabled { cursor: not-allowed; }
</style>
