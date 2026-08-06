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

  O2. The -9999..9999 Hz / 50 Hz-step slider bounds are UI convenience
  (`RitXitPanel`'s own `ValueControl` bounds), not a radio fact — the X6200
  no-UI-tables lesson kept them out of the 8A contract, so this surface
  supplies them itself, unchanged from v2.

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

  `scan` HAS NO CAPABILITY TAG anywhere (MOR-1295 ruling): evidence is
  per-field "ever reported", so a partial reporter surfaces exactly the
  fields it has reported, no more — expected, not a bug. Scan TYPE and
  RESUME-MODE label tables are UI-only in v2 and are deliberately not
  reproduced here (out of scope, not backed by any 8A fact): scan is
  restarted with the last OBSERVED type, never a fabricated default, and
  resume mode is cycled by its raw masked value.
-->
<script module lang="ts">
  import type { RitXitField, ScanField } from './radio-view-model';

  export const UNKNOWN_TEXT = '—';
  /** O2 — v2's own `RitXitPanel` bounds, verbatim. */
  export const OFFSET_MIN = -9999;
  export const OFFSET_MAX = 9999;
  export const OFFSET_STEP = 50;

  export const usable = (f: RitXitField<unknown> | ScanField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  export const textOf = (f: RitXitField<unknown> | ScanField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : UNKNOWN_TEXT;
  const isOn = (f: RitXitField<boolean>): boolean => f.reading.status === 'known' && f.reading.value === true;
  /** F2 (fix round, verify-MOR-1308). `TxAuxSurface.svelte`'s `pressedOf`,
   *  verbatim shape: `boolean | undefined` so `aria-pressed` is OMITTED —
   *  never `"false"` — when the reading is unknown. Claiming "off" for a
   *  field this surface has never observed is the same fabrication the
   *  B-wave criterion forbids for the underlying dispatch below. */
  const pressedOf = (f: RitXitField<boolean> | ScanField<boolean>): boolean | undefined =>
    f.reading.status === 'known' ? f.reading.value : undefined;
</script>

<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';

  interface Props {
    view: RadioViewModel;
    onRitToggle?: () => void;
    onXitToggle?: () => void;
    onRitOffsetChange?: (hz: number) => void;
    onXitOffsetChange?: (hz: number) => void;
    onClear?: () => void;
    onScanStart?: (type: number) => void;
    onScanStop?: () => void;
    onResumeModeChange?: (mode: number) => void;
  }
  let {
    view, onRitToggle, onXitToggle, onRitOffsetChange, onXitOffsetChange, onClear,
    onScanStart, onScanStop, onResumeModeChange,
  }: Props = $props();

  let rx = $derived(view.ritXit);
  let sc = $derived(view.scan);
  /** v2's `handleOffsetChange` selection, verbatim (O1). Decides only WHICH
   *  command fires — never what is displayed, since both facts read the
   *  identical register. */
  let xitLeads = $derived(rx !== undefined && isOn(rx.xitActive) && !isOn(rx.ritActive));
  /** Wrong-VFO guard (S3b) — see file header. */
  let activeKnown = $derived(view.activeReceiver.status === 'known');
  let scanningOn = $derived(sc !== undefined && usable(sc.scanning)
    && sc.scanning.reading.status === 'known' && sc.scanning.reading.value === true);

  // F2 (fix round, verify-MOR-1308): gated on the field's OWN observation,
  // not just the wrong-VFO guard — mirrors `TxAuxSurface.svelte`'s `toggle`.
  // Firing over an unobserved reading arms a guess at the command bus
  // (`makeRitXitHandlers().onRitToggle`'s `?? false` optimism), exactly the
  // re-loosening the B-wave criterion forbids.
  function toggleRit(): void { if (rx && activeKnown && usable(rx.ritActive)) onRitToggle?.(); }
  function toggleXit(): void { if (rx && activeKnown && usable(rx.xitActive)) onXitToggle?.(); }
  function changeOffset(hz: number): void {
    const offset = rx && (xitLeads ? rx.xitOffset : rx.ritOffset);
    if (!activeKnown || !offset || !usable(offset)) return;
    if (xitLeads) onXitOffsetChange?.(hz); else onRitOffsetChange?.(hz);
  }
  // CLEAR is correctly LEFT UNGATED on field observation (F2 fix round):
  // `onClear` writes `freq: 0` absolutely, not a read-modify-write of the
  // current offset, so it is honest even while the offset is unobserved —
  // unlike the toggles, it never reads a guessed value to decide what to
  // send. Still gated on `activeKnown` (S3b — no receiver to attribute it to).
  function clear(): void { if (activeKnown) onClear?.(); }
  function toggleScan(): void {
    if (!sc || !usable(sc.scanning)) return;
    if (scanningOn) { onScanStop?.(); return; }
    if (usable(sc.scanType) && sc.scanType.reading.status === 'known') onScanStart?.(sc.scanType.reading.value);
  }
  function cycleResume(): void {
    if (!sc || !usable(sc.scanResumeMode) || sc.scanResumeMode.reading.status !== 'known') return;
    // F1 (fix round, verify-MOR-1308): the fact is the `& 0x0F` masked value
    // (8A), but the wire wants the full CI-V byte — the backend validates
    // `scan_set_resume: mode must be 0xD0-0xD3` (control.py:2283-2289). Same
    // split `ScanPanel.svelte` makes between `rm.value` and `rm.value & 0x0F`.
    onResumeModeChange?.(0xD0 | ((sc.scanResumeMode.reading.value + 1) % 4));
  }
</script>

{#if rx || sc}
  <section class="ritxit-scan-surface" data-testid="ritxit-scan-surface" aria-label="RIT, XIT and scan">
    {#if rx}
      {@const offset = xitLeads ? rx.xitOffset : rx.ritOffset}
      <div class="row" data-testid="ritxit" data-active-vfo-known={activeKnown}>
        {#if rx.ritActive.availability.structural}
          <button
            type="button" data-testid="ritxit-rit-toggle" aria-pressed={pressedOf(rx.ritActive)}
            disabled={!activeKnown || !usable(rx.ritActive)} onclick={toggleRit}
          >RIT</button>
        {/if}
        {#if rx.xitActive.availability.structural}
          <button
            type="button" data-testid="ritxit-xit-toggle" aria-pressed={pressedOf(rx.xitActive)}
            disabled={!activeKnown || !usable(rx.xitActive)} onclick={toggleXit}
          >XIT</button>
        {/if}
        <label class="offset" data-testid="ritxit-offset" data-observed={usable(offset)}>
          <span>Offset</span>
          <input
            type="range" min={OFFSET_MIN} max={OFFSET_MAX} step={OFFSET_STEP}
            value={offset.reading.status === 'known' ? offset.reading.value : 0}
            disabled={!activeKnown || !usable(offset)}
            oninput={(event) => changeOffset(event.currentTarget.valueAsNumber)}
          />
          <output data-testid="ritxit-offset-value">{textOf(offset)}</output>
        </label>
        <button type="button" data-testid="ritxit-clear" disabled={!activeKnown} onclick={clear}>CLEAR</button>
      </div>
    {/if}

    {#if sc}
      <div class="row" data-testid="scan">
        {#if sc.scanning.availability.structural}
          <span data-testid="scan-status" data-observed={usable(sc.scanning)}>{textOf(sc.scanning)}</span>
          <button
            type="button" data-testid="scan-toggle" aria-pressed={pressedOf(sc.scanning)}
            disabled={!usable(sc.scanning) || (!scanningOn && !usable(sc.scanType))}
            onclick={toggleScan}
          >{scanningOn ? 'STOP' : 'START'}</button>
        {/if}
        {#if sc.scanType.availability.structural}
          <output data-testid="scan-type-value">{textOf(sc.scanType)}</output>
        {/if}
        {#if sc.scanResumeMode.availability.structural}
          <output data-testid="scan-resume-value">{textOf(sc.scanResumeMode)}</output>
          <button
            type="button" data-testid="scan-resume-cycle" disabled={!usable(sc.scanResumeMode)}
            onclick={cycleResume}
          >RESUME ▶</button>
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
  [aria-pressed='true'] { font-weight: 700; }
  [data-observed='false'] { font-style: italic; }
  button:disabled, input:disabled { cursor: not-allowed; }
</style>
