<!--
  Semantic RIT/XIT + scan surface (MOR-1308, vocabulary slice 8B).

  Presentation only. Renders the MOR-1295 (slice 8A) `ritXit` and `scan` fact
  groups and emits control intents as callbacks. Holds no state, consults no
  controller, keys nothing (v3 ADR invariant 11 — same discipline as every
  other semantic surface in this directory).

  O1 (MOR-1295 verify report, binding on this ticket). `ritOffset`/`xitOffset`
  are TWO CONTRACT FIELDS backed by ONE raw register (`ritFreq`), mirroring
  v2's `RitXitPanel`. Showing them as independently-editable would misrepresent
  the radio, so this surface renders exactly ONE offset control, visible under
  EITHER capability gate, and picks which underlying command it calls
  (`onRitOffsetChange` / `onXitOffsetChange`) the same way v2's own
  `handleOffsetChange` does — `xitActive && !ritActive` selects XIT, otherwise
  RIT. Both callbacks are wired 1:1 to the shipped `makeRitXitHandlers()`,
  whose two offset handlers both write `ritFreq` via the identical
  `set_rit_frequency` command — proven end to end, not merely asserted, in
  `__tests__/semantic-ritxit-scan-wiring.component.test.ts`.

  O2. Legacy servers retain the -9999..9999 Hz / 50 Hz-step slider contract.
  When exact capability metadata is present, the validated domain supplies
  the native lattice and exact decode/encode path instead (MOR-1731).

  WRONG-VFO GUARD (S3b lesson, MOR-1322 verify report). RIT/XIT offsets
  target whichever receiver the radio currently has ACTIVE, and neither
  `set_rit_frequency` nor the toggle commands carry a receiver parameter —
  there is no way to address "the other" VFO even if this surface wanted to.
  v2 never gates on this (fail-open); this surface fails CLOSED instead, per
  the B-wave criterion: every RIT/XIT control disables itself while
  `view.activeReceiver` is unobserved, because an edit this surface cannot
  honestly attribute to a receiver is an edit it must refuse to dispatch.
  `scan`'s commands carry no such per-receiver ambiguity in v2 to diverge
  from, so this gate applies to `ritXit` only.

  `scan` HAS NO CAPABILITY TAG anywhere AT THE FACT LAYER (MOR-1295
  ruling): evidence is per-field "ever reported", so a partial reporter
  surfaces exactly the fields it has reported, no more — expected, not a
  bug. The COMMAND layer disagrees: `makeScanHandlers()`
  (panel-commands.ts) gates every scan intent on `hasCapability('scan')`,
  so a TYPE/SPAN/RESUME control that renders purely from "ever reported"
  evidence could still silently no-op on a radio that lacks the tag
  (MOR-2425 restore census §3). `scanCapable` below is that command-layer
  fact, read at the wiring seam (`SemanticRadioSurfaces.svelte`, the same
  "caps-echo display metadata" seam `hasDualReceiver` uses) and passed
  down as a plain prop — RadioViewModel carries no capabilities field, so
  this is not a fact-layer change. It HIDES, not merely disables, the
  TYPE/SPAN/RESUME button groups when false.

  SCAN TYPE OWNERSHIP (MOR-1495 review R2 — verifier-caught bootstrap
  deadlock). CI-V 0x0E is SET-ONLY: `scanType` can never become "known"
  before a scan has ever been started, so the original "restart with the
  last OBSERVED type, never a fabricated default" design meant NOTHING
  could ever be the first start — START was permanently disabled on a
  fresh connect, forever, because the only thing that could ever make
  `scanType` known was a scan_start command the disabled button could
  never send. `selectedType` below breaks that cycle: it is local UI
  state (v2 `ScanPanel`'s own `selectedType` shape/default — PROG, 0x01),
  not a claimed-observed radio fact, so sending it never fabricates an
  "observed" value — it is honestly what it is, an operator/default
  selection. `scanType`'s OWN displayed reading (`scan-type-value`) is
  untouched by this and still shows only the genuinely last-observed
  value, `UNKNOWN_TEXT` until one exists.

  MOR-2425 restores v2.11.1's TYPE/SPAN/RESUME affordances on
  `selectedType`'s foundation: six TYPE buttons set it and immediately
  call `onScanStart` (v2's own `handleTypeClick` — unconditional, even
  mid-scan, so picking a new type restarts the scan with it); the seven
  ΔF-SPAN buttons show only while the selection (or an observed active
  scan) is ΔF (0x03); RESUME is four buttons sending a literal value each
  (OFF/5s/10s/15s), replacing the single masked-cycle action — and,
  because none of the three reads `sc.scan*.reading` to decide whether it
  may fire, all three work even from a cold start where nothing has ever
  been observed, exactly like v2.11.1's did.
-->
<script module lang="ts">
  import type { RitXitField, ScanField } from './radio-view-model';
  import { pressedOf } from './pressed-of';

  export const UNKNOWN_TEXT = '—';
  /** O2 — v2's own legacy `RitXitPanel` bounds, verbatim. */
  export const OFFSET_MIN = -9999;
  export const OFFSET_MAX = 9999;
  export const OFFSET_STEP = 50;
  /** v2 `ScanPanel`'s own default scan type for the next START — PROG. */
  export const DEFAULT_SCAN_TYPE = 0x01;
  /** v2.11.1 `ScanPanel`'s own `scanTypes`/`dfSpans`/`resumeModes` tables,
   *  verbatim (`[value, label]`). */
  export const SCAN_TYPES = [
    [0x01, 'PROG'], [0x02, 'P2'], [0x03, 'ΔF'], [0x12, 'FINE'], [0x22, 'MEM'], [0x23, 'SEL'],
  ] as const;
  export const DF_SPANS = [
    [0xa1, '±5k'], [0xa2, '±10k'], [0xa3, '±20k'], [0xa4, '±50k'],
    [0xa5, '±100k'], [0xa6, '±500k'], [0xa7, '±1M'],
  ] as const;
  export const RESUME_MODES = [
    [0xd0, 'OFF'], [0xd1, '5s'], [0xd2, '10s'], [0xd3, '15s'],
  ] as const;
  const hex = (value: number): string => value.toString(16).padStart(2, '0');

  export const usable = (f: RitXitField<unknown> | ScanField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  export const textOf = (f: RitXitField<unknown> | ScanField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : UNKNOWN_TEXT;
  const isOn = (f: RitXitField<boolean>): boolean => f.reading.status === 'known' && f.reading.value === true;
</script>

<script lang="ts">
  import { decodeControlDomain, encodeControlDomain } from '$lib/radio/control-domain';
  import { exactDecimalNumber } from '$lib/types/exact-decimal';
  import type { ControlDomain } from '$lib/types/capabilities';
  import { bindToggleInstrument } from '../primitives/control-instruments/control-instrument-behavior';
  import type {
    RitXitScanInstrumentHandles, RitXitScanInstrumentLayout,
  } from './RitXitScanInstrumentHost.svelte';
  import type { RadioViewModel } from './radio-view-model';

  interface Props {
    view: RadioViewModel;
    onRitOffsetChange?: (hz: number) => void;
    onXitOffsetChange?: (hz: number) => void;
    onScanStart?: (type: number) => void;
    onScanStop?: () => void;
    onDfSpanChange?: (span: number) => void;
    onResumeModeChange?: (mode: number) => void;
    /** `hasCapability('scan')` at the wiring seam — see file header. Hides
     *  (never merely disables) the TYPE/SPAN/RESUME button groups. Fails
     *  closed: absent controls until a caller proves the tag is present. */
    scanCapable?: boolean;
    ritDomain?: ControlDomain | null;
    handles: RitXitScanInstrumentHandles;
    instrumentLayout?: RitXitScanInstrumentLayout;
  }
  let {
    view, onRitOffsetChange, onXitOffsetChange,
    onScanStart, onScanStop, onDfSpanChange, onResumeModeChange, scanCapable = false,
    ritDomain, handles, instrumentLayout,
  }: Props = $props();

  let rx = $derived(view.ritXit);
  let sc = $derived(view.scan);
  /** v2's `handleOffsetChange` selection, verbatim (O1). Decides only WHICH
   *  command fires — never what is displayed, since both facts read the
   *  identical register. */
  let xitLeads = $derived(rx !== undefined && isOn(rx.xitActive) && !isOn(rx.ritActive));
  let offset = $derived(rx && (xitLeads ? rx.xitOffset : rx.ritOffset));
  /** Wrong-VFO guard (S3b) — see file header. */
  let activeKnown = $derived(view.activeReceiver.status === 'known');
  let decodedOffset = $derived.by(() => {
    if (!offset || !usable(offset) || offset.reading.status !== 'known') return null;
    if (ritDomain === undefined) {
      return Number.isFinite(offset.reading.value)
        ? { value: offset.reading.value, text: String(offset.reading.value) } : null;
    }
    if (ritDomain === null) return null;
    const text = decodeControlDomain(ritDomain, offset.reading.value);
    if (text === null) return null;
    const value = Number(text);
    return Number.isFinite(value) ? { value, text } : null;
  });
  let canAdjustOffset = $derived(activeKnown && offset !== undefined
    && usable(offset) && decodedOffset !== null && ritDomain !== null);
  let scanningOn = $derived(sc !== undefined && usable(sc.scanning)
    && sc.scanning.reading.status === 'known' && sc.scanning.reading.value === true);
  /** Local UI selection for the NEXT scan START (MOR-1495 review R2 — see
   *  file header). NOT an observed radio fact, so it needs no `usable()`
   *  gate: it is honest about what it is from the moment it exists. */
  let selectedType = $state(DEFAULT_SCAN_TYPE);
  /** v2.11.1 `isDfSelected || (scanning && scanType === 0x03)`, verbatim —
   *  never reads `sc.scanType` to decide whether the SELECTION itself is
   *  ΔF, only whether an ALREADY-ACTIVE observed scan is. */
  let isDfSelected = $derived(selectedType === 0x03 || (scanningOn
    && sc?.scanType.reading.status === 'known' && sc.scanType.reading.value === 0x03));

  const scanToggle = bindToggleInstrument(() => ({
    field: sc?.scanning,
    invoke: (next) => {
      if (next) onScanStart?.(selectedType);
      else onScanStop?.();
    },
  }));
  /** v2.11.1 `handleTypeClick`, verbatim: sets the selection AND fires
   *  START immediately and unconditionally — even mid-scan, restarting it
   *  with the new type. Never reads `sc.scanType`; never disabled. */
  function selectScanType(type: number): void {
    selectedType = type;
    onScanStart?.(type);
  }
  function changeOffset(displayHz: number): void {
    if (!canAdjustOffset || !Number.isFinite(displayHz)) return;
    let raw = displayHz;
    if (ritDomain !== undefined) {
      if (ritDomain === null) return;
      raw = encodeControlDomain(ritDomain, exactDecimalNumber(displayHz)) ?? Number.NaN;
      if (!Number.isSafeInteger(raw)) return;
    }
    if (xitLeads) onXitOffsetChange?.(raw); else onRitOffsetChange?.(raw);
  }
  function offsetKeydown(event: KeyboardEvent): void {
    if (!canAdjustOffset || decodedOffset === null) return;
    const min = ritDomain?.raw_min ?? OFFSET_MIN;
    const max = ritDomain?.raw_max ?? OFFSET_MAX;
    let next: number;
    switch (event.key) {
      case 'ArrowRight': case 'ArrowUp': next = Math.min(decodedOffset.value + 50, max); break;
      case 'ArrowLeft': case 'ArrowDown': next = Math.max(decodedOffset.value - 50, min); break;
      case 'Home': next = min; break;
      case 'End': next = max; break;
      default: return;
    }
    event.preventDefault();
    changeOffset(next);
  }
</script>

{#snippet offsetSlot()}
  <label class="offset" data-testid="ritxit-offset"
    data-observed={offset !== undefined && usable(offset)}>
    <span>Offset</span>
    <input
      type="range"
      min={ritDomain?.raw_min ?? OFFSET_MIN}
      max={ritDomain?.raw_max ?? OFFSET_MAX}
      step={ritDomain?.raw_step ?? OFFSET_STEP}
      value={decodedOffset?.value ?? ritDomain?.raw_origin ?? 0}
      disabled={!canAdjustOffset}
      onkeydown={offsetKeydown}
      oninput={(event) => changeOffset(event.currentTarget.valueAsNumber)}
    />
    <output data-testid="ritxit-offset-value">{decodedOffset?.text ?? UNKNOWN_TEXT}</output>
  </label>
{/snippet}

{#if rx || sc}
  <section class="ritxit-scan-surface" data-testid="ritxit-scan-surface" aria-label="RIT, XIT and scan">
    {#if rx}
      <div class="row" data-testid="ritxit" data-active-vfo-known={activeKnown}>
        {#if instrumentLayout}
          {@render instrumentLayout(handles, offsetSlot)}
        {:else}
          {@render handles.rit()}
          {@render handles.xit()}
          {@render offsetSlot()}
          {@render handles.clear()}
        {/if}
      </div>
    {/if}

    {#if sc}
      <div class="row" data-testid="scan">
        {#if sc.scanning.availability.structural}
          <span data-testid="scan-status" data-observed={usable(sc.scanning)}>{textOf(sc.scanning)}</span>
          <button
            type="button" data-testid="scan-toggle" aria-pressed={pressedOf(sc.scanning)}
            disabled={!scanToggle.available}
            onclick={() => scanToggle.invoke()}
          >{scanningOn ? 'STOP' : 'START'}</button>
          {#if scanCapable}
            <div class="scan-choice-group" data-testid="scan-type-group">
              {#each SCAN_TYPES as [value, label] (value)}
                <button
                  type="button" class="scan-choice" data-testid={`scan-type-0x${hex(value)}`}
                  onclick={() => selectScanType(value)}
                >{label}</button>
              {/each}
            </div>
            {#if isDfSelected}
              <div class="scan-choice-group" data-testid="scan-span-group">
                {#each DF_SPANS as [value, label] (value)}
                  <button
                    type="button" class="scan-choice" data-testid={`scan-span-0x${hex(value)}`}
                    onclick={() => onDfSpanChange?.(value)}
                  >{label}</button>
                {/each}
              </div>
            {/if}
          {/if}
        {/if}
        {#if sc.scanType.availability.structural}
          <output data-testid="scan-type-value">{textOf(sc.scanType)}</output>
        {/if}
        {#if sc.scanResumeMode.availability.structural}
          <output data-testid="scan-resume-value">{textOf(sc.scanResumeMode)}</output>
          {#if scanCapable}
            <div class="scan-choice-group" data-testid="scan-resume-group">
              {#each RESUME_MODES as [value, label] (value)}
                <button
                  type="button" class="scan-choice" data-testid={`scan-resume-0x${hex(value)}`}
                  onclick={() => onResumeModeChange?.(value)}
                >{label}</button>
              {/each}
            </div>
          {/if}
        {/if}
      </div>
    {/if}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour (MOR-977, forced-colors). */
  .ritxit-scan-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; margin: 0; }
  .offset { display: flex; align-items: baseline; gap: 0.5rem; }
  .scan-choice-group { display: flex; flex-wrap: wrap; gap: 0.25rem; }
  [aria-pressed='true'] { font-weight: 700; }
  [data-observed='false'] { font-style: italic; }
  button:disabled, input:disabled { cursor: not-allowed; }
</style>
