<!--
  Semantic DSP surface (MOR-1305, vocabulary slice 5B).

  Presentation only. It renders native continuous controls and
  finite-control handles owned by the persistent DSP hosts. It holds no
  state and consults no controller (v3 ADR invariant 11), the same
  discipline `TxAuxSurface` (MOR-1265) established.

  CARRY-FORWARDS (binding, from the MOR-1290 fact-layer decisions this
  surface must not relax):

  (1) `agcLabels`/`nbLevelMax`/`nbLevelPercent` are NOT facts — pure
      caps-echo display metadata (a slider ceiling and a percent-vs-raw
      display choice, `lib/runtime/props/panel-props.ts`'s own `toDspProps`
      precedent). They arrive as plain props, read directly off `caps` at the
      wiring seam (`SemanticRadioSurfaces.svelte`, which already holds
      `runtime.caps` for the view-model adapter call) — never folded into
      `DspViewModel`. The finite host consumes `agcLabels`; the scalar host
      consumes the two scalar display props.
  (2) `agcTimeConstant` may be `structural: true` with no real control on a
      radio that borrows the `agc` capability tag optimistically. That is an
      accepted fact-layer optimism, not something this surface special-cases
      — a present-but-never-observed field renders exactly like any other
      unobserved present field, honestly disabled.
  (3) Every reading here is rendered exactly as the fact group states it. No
      range-fallback plumbing, no re-derivation of `controlRangeFromCaps` —
      `nrLevelProjection` carries NR value/domain/usability and `nbDepth`
      already arrives display-scaled from the adapter.
  (4) `unknown` renders as `?`, never as a v2 fabricated default (0 dB, OFF,
      WIDE) — same fail-closed-presentation doctrine as `TxAuxSurface`.

  Two-level availability (MOR-977/1256): `structural: false` renders NOTHING;
  a present-but-unusable control stays visible and disabled rather than
  guessing a value.

  FINITE PENDING AFFORDANCE (MOR-1441 leg 2) is owned by
  `DspInstrumentHost`. `pendingNb`/`pendingNr` stay command-bus-blind display
  facts there and never become the arithmetic base for a toggle.
-->
<script module lang="ts">
  import type { DspField, DspViewModel } from './radio-view-model';
  import { NOTCH_WIDTH_LABELS, formatAgcTime } from '../components-v2/panels/dsp-panel-logic';
  export { DSP_TOGGLES, type DspToggleField } from './dsp-instruments';

  /** `[field, label, min, max, step, format?]` — `nrLevel`/`nbDepth` are
   *  ALREADY the adapter's display-scaled values (carry-forward 3); the rest
   *  are raw wire ranges, verbatim `DspPanel.svelte`'s own slider bounds.
   *  `nbLevel` is excluded from this array. */
  export const DSP_LEVELS = [
    ['nrLevel', 'NR level', 0, 15, 1],
    ['nbDepth', 'NB depth', 1, 10, 1],
    ['nbWidth', 'NB width', 0, 255, 1],
    ['notchFreq', 'Notch position', 0, 255, 1],
    ['manualNotchWidth', 'Notch width', 0, 2, 1, (v: number) => NOTCH_WIDTH_LABELS[v] ?? String(v)],
    ['agcTimeConstant', 'AGC time', 0, 9, 1, formatAgcTime],
  ] as const;
  export type DspLevelField = (typeof DSP_LEVELS)[number][0] | 'nbLevel';
  const [, , NR_FALLBACK_MIN, NR_FALLBACK_MAX, NR_FALLBACK_STEP] = DSP_LEVELS[0];

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

  type NrDomain = NonNullable<DspViewModel['nrLevelProjection']>['domain'];
  type NrPresentation = Readonly<{
    min: number; max: number; step: number; origin: number;
    value: number; text: string; usable: boolean;
  }>;

  const safeNrInteger = (value: unknown): value is number =>
    typeof value === 'number' && Number.isSafeInteger(value);
  const onNrLattice = (value: number, origin: number, step: number): boolean =>
    (BigInt(value) - BigInt(origin)) % BigInt(step) === 0n;
  const acceptsNrValue = (value: unknown, domain: NonNullable<NrDomain>): value is number =>
    safeNrInteger(value) && safeNrInteger(domain.origin)
    && safeNrInteger(domain.step) && domain.step > 0
    && value >= domain.min && value <= domain.max
    && onNrLattice(value, domain.origin, domain.step);

  function nrPresentation(dsp: DspViewModel): NrPresentation {
    const unavailable = {
      min: NR_FALLBACK_MIN, max: NR_FALLBACK_MAX, step: NR_FALLBACK_STEP,
      origin: NR_FALLBACK_MIN, value: NR_FALLBACK_MIN, text: '?', usable: false,
    } as const;
    try {
      const projection = dsp.nrLevelProjection;
      const domain = projection?.domain;
      if (!projection || !domain
        || !safeNrInteger(domain.min) || !safeNrInteger(domain.max)
        || !safeNrInteger(domain.step) || domain.step <= 0
        || !safeNrInteger(domain.origin)
        || domain.min >= domain.max || domain.origin < domain.min || domain.origin > domain.max
        || !onNrLattice(domain.min, domain.origin, domain.step)
        || !onNrLattice(domain.max, domain.origin, domain.step)) {
        return unavailable;
      }
      const projectionUsable = projection.adjustable === true
        && usable(dsp.nrLevel)
        && acceptsNrValue(projection.value, domain);
      return {
        ...domain,
        value: projectionUsable ? projection.value : domain.origin,
        text: projectionUsable ? String(projection.value) : '?',
        usable: projectionUsable,
      };
    } catch {
      return unavailable;
    }
  }
</script>

<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';
  import type { DspFiniteHandles, DspFiniteLayout } from './dsp-instruments';
  import type { DspScalarHandles, DspScalarLayout } from './dsp-scalars';

  interface Props {
    view: RadioViewModel;
    finiteHandles: DspFiniteHandles;
    finiteLayout?: DspFiniteLayout;
    scalarHandles?: DspScalarHandles;
    scalarLayout?: DspScalarLayout;
    onLevelChange?: (field: DspLevelField, value: number) => void;
  }
  let {
    view, finiteHandles, finiteLayout, scalarHandles, scalarLayout, onLevelChange,
  }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine). */
  let dsp = $derived(view.dsp);

  function level(field: DspLevelField, value: number): void {
    if (!dsp) return;
    if (field === 'nrLevel') {
      const presentation = nrPresentation(dsp);
      if (presentation.usable && acceptsNrValue(value, presentation)) {
        onLevelChange?.(field, value);
      }
      return;
    }
    if (usable(dsp[field])) onLevelChange?.(field, value);
  }
</script>

{#if dsp}
  <section class="dsp-surface" data-testid="dsp-surface" aria-label="DSP controls">
    {#if finiteLayout}
      {@render finiteLayout(finiteHandles)}
    {:else}
      <div class="dsp-row">
        {@render finiteHandles.nrActive()}
        {@render finiteHandles.nbActive()}
      </div>
    {/if}

    {#if scalarHandles && scalarLayout}
      {@render scalarLayout(scalarHandles)}
    {/if}

    {#each DSP_LEVELS as [field, label, min, max, step, format] (field)}
      {#if field === 'nbWidth'}
        {#if scalarHandles && !scalarLayout}{@render scalarHandles.nbWidth()}{/if}
      {:else if dsp[field].availability.structural}
        {@const nr = field === 'nrLevel' ? nrPresentation(dsp) : null}
        <label
          class="dsp-level" data-testid={`dsp-${field}`} data-field={field}
          data-disabled-reason={nr ? (nr.usable ? undefined : 'field-not-observed') : reasonOf(dsp[field])}
        >
          <span class="dsp-name">{label}</span>
          <input
            type="range" min={nr?.min ?? min} max={nr?.max ?? max} step={nr?.step ?? step}
            value={nr?.value ?? numberOf(dsp[field], min)}
            disabled={nr ? !nr.usable : !usable(dsp[field])}
            oninput={(event) => level(field, event.currentTarget.valueAsNumber)}
          />
          <output>{nr?.text ?? fmt(dsp[field], format)}</output>
        </label>
      {/if}
    {/each}

    {#if scalarHandles && !scalarLayout}{@render scalarHandles.nbLevel()}{/if}

    {#if !finiteLayout}
      {@render finiteHandles.notchMode()}
      {@render finiteHandles.agcMode()}
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
</style>
