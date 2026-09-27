<script lang="ts">
  import type { RadioViewModel, DisplayObservation } from '../../semantic/radio-view-model';
  import { observationValue, readingValue, valueText } from '../../primitives/reading-text';
  import type { ScopeFrame } from '../../lib/runtime/adapters/scope-adapter';
  import { formatBandwidth } from '../segmentline/lcd-display-helpers';
  import ReceiverNeedleSMeter from './ReceiverNeedleSMeter.svelte';
  import ReceiverScopeWaterfall from './ReceiverScopeWaterfall.svelte';
  interface Props { view: RadioViewModel; receiver: 0 | 1; frame: ScopeFrame | null; }
  let { view, receiver, frame }: Props = $props();
  let receiverId = $derived(receiver === 0 ? 'MAIN' : 'SUB');
  let vfo = $derived(view.vfos.find((item) => item.receiver === receiverId));
  let indicator = $derived(view.receiverIndicators?.find((item) => item.receiver === receiverId));
  // MOR-2688 S4a: `bwRaw` and `meterValue` are value-or-nothing through the
  // `{reading}` status entry point (`readingValue`). `bwRaw` stays ONE
  // derivation: it feeds both the bandwidth slot's reserved width below
  // and its text.
  let meterValue = $derived(readingValue(indicator?.sMeter));
  let bwRaw = $derived(readingValue(indicator?.bandwidthHz));

  // MOR-2688 S4c: the display.state treatment enters through
  // `observationValue` — current/stale → the value, anything else →
  // nothing; an ABSENT observation returns the caller's legacy value.
  function obsText<T extends string | number>(display: DisplayObservation<T> | undefined, legacy: T | null | undefined): string {
    return valueText(observationValue(display, legacy ?? null), String);
  }
  function obsState<T>(display: DisplayObservation<T> | undefined, legacy: T | null | undefined): 'current' | 'unknown' {
    return observationValue(display, legacy ?? null) !== null ? 'current' : 'unknown';
  }
  let modeText = $derived(obsText(vfo?.display?.mode, vfo?.mode));
  let filterText = $derived(obsText(vfo?.display?.filter, vfo?.filter));
  let frequencyText = $derived(
    valueText(
      observationValue(vfo?.display?.frequencyHz, vfo?.frequencyHz ?? null),
      (v: number) => v.toLocaleString('en-US'),
    ),
  );

  // Reserved widths come from the mounted profile's choice sets — the
  // owner's rule: no radio-specific values hardcoded in components. A badge
  // exists only while the group that feeds it declares choices; otherwise
  // the function is not drawn. 'BW' reserves over the known max width plus
  // the filter-choice labels.
  let modeBadge = $derived.by(() => {
    const choices = view.modeFilter?.modeChoices ?? [];
    return choices.length > 0 ? { ch: Math.max(...choices.map((m) => m.length)) } : null;
  });
  let filterBadge = $derived.by(() => {
    const choices = view.modeFilter?.filterChoices ?? [];
    return choices.length > 0 ? { ch: Math.max(...choices.map((f) => f.length)) } : null;
  });
  let bandwidthCh = $derived.by(() => {
    const widths: number[] = [];
    if (view.modeFilter) {
      const maxWidth = readingValue(view.modeFilter.filterWidthMax);
      if (maxWidth !== null) {
        widths.push(formatBandwidth({ state: 'known', value: maxWidth }).length);
      }
      for (const label of view.modeFilter.filterChoices) { widths.push(label.length); }
    }
    if (bwRaw !== null) { widths.push(formatBandwidth({ state: 'known', value: bwRaw }).length); }
    return 2 + Math.max(...widths, 0);
  });
  // The frequency slot keeps a one-line-tall, data-wide box in EVERY state:
  // empty text must not collapse the grid row (review of PR #3760). The
  // width derivation runs over the profile's tuning envelope plus the
  // receiver's own readings — no radio constants in the component.
  let frequencyCh = $derived.by(() => {
    const values: number[] = [];
    const tuneMax = view.band?.tuneMaxHz;
    if (tuneMax !== null && tuneMax !== undefined) { values.push(tuneMax); }
    if (vfo?.frequencyHz !== null && vfo?.frequencyHz !== undefined) { values.push(vfo.frequencyHz); }
    const observed = observationValue(vfo?.display?.frequencyHz);
    if (observed !== null) { values.push(observed); }
    if (values.length === 0) { return 0; }
    return Math.max(...values).toLocaleString('en-US').length;
  });
</script>

<section class="cluster" data-receiver-cluster={receiver} aria-label={`${receiverId} receiver`}>
  <header><b>{receiverId}</b><span>VFO</span>
    {#if modeBadge}
      <span data-badge="mode" data-state={obsState(vfo?.display?.mode, vfo?.mode)} style:min-width={`${modeBadge.ch}ch`}>{modeText}</span>
    {/if}
    {#if filterBadge}
      <span data-badge="filter" data-state={obsState(vfo?.display?.filter, vfo?.filter)} style:min-width={`${filterBadge.ch}ch`}>{filterText}</span>
    {/if}
    <span class="bw" data-bandwidth style:min-width={`${bandwidthCh}ch`}>BW {valueText(bwRaw, (v) => formatBandwidth({ state: 'known', value: v }))}</span>
  </header>
  <ReceiverNeedleSMeter value={meterValue} />
  <output class="frequency" data-frequency data-state={obsState(vfo?.display?.frequencyHz, vfo?.frequencyHz)} style:min-height="1em" style:min-width={`${frequencyCh}ch`}>{frequencyText}</output>
  <div class="secondary">{vfo?.label ?? ''} · {modeText} · {filterText}</div>
  <ReceiverScopeWaterfall {frame} />
</section>

<style>
  .cluster { min-width: 0; display: grid; grid-template-rows: auto auto auto auto 1fr; gap: 5px; padding: 9px; border: 1px solid #687479; background: #020606; color: #edf3f2; }
  header { display: flex; gap: 8px; align-items: center; font: 13px ui-monospace, monospace; white-space: nowrap; } header b { color: #f4c35a; } header span { border: 1px solid #879397; padding: 2px 6px; }
  .frequency { color: #f7f8f5; font: clamp(28px, 4vw, 70px)/.95 ui-monospace, monospace; letter-spacing: -.08em; text-align: center; }
  .secondary { text-align: center; color: #afbdbe; font: 12px ui-monospace, monospace; }
</style>
