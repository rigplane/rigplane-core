<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import { getSelectedFrequencyReadout } from '../../component-kits/activation';
  import StandardFrequencyReadout from './StandardFrequencyReadout.svelte';
  import {
    createFrequencyInteraction, createFrequencyInteractionLease,
  } from './frequency-interaction.svelte';
  import { projectFrequencyReadout } from './frequency-readout';

  interface Props {
    /**
     * CONFIRMED radio truth — and, deliberately, the SOLE arithmetic base
     * for every gesture below. MOR-1441 regression (verifier-reproduced):
     * an earlier revision let `freq` carry the PENDING target while a hot
     * burst was in flight, so `adjustFreqByDigit` computed off a value that
     * already included the burst's own not-yet-confirmed delta — each tick
     * added `(pending − confirmed) + step` instead of `step`, a positive
     * feedback loop that compounded roughly every pacing window (10 ticks
     * of +10 Hz intent measured out to +1910 Hz actual; 30 ticks to
     * +15.7 MHz — a TX-out-of-band hazard). `freq` must never be sourced
     * from `pendingDisplayHz` — that is precisely the bug.
     */
    freq: number | null;
    displayHz?: number | null;
    disabled?: boolean;
    contextKey?: string;
    compact?: boolean;
    active?: boolean;
    receiver?: 'main' | 'sub';
    minFreq?: number;
    maxFreq?: number;
    onFreqChange?: (freq: number) => void;
    /**
     * MOR-1441 — the pending (not-yet-confirmed) tuning target, DISPLAY
     * ONLY: when non-null the digit readout shows THIS value instead of
     * `freq` (so the operator sees where a hot burst is heading) and marks
     * `data-freq-status="pending"` — but every gesture still computes its
     * next target from `freq` alone. Never plumb this into `adjustFreqByDigit`
     * or any arithmetic path; see the `freq` doc above for why.
     */
    pendingDisplayHz?: number | null;
    /**
     * MOR-1441 (B2) — already-localized text announcing the pending state
     * to assistive tech. The `data-freq-status`/italic marker is a VISUAL
     * channel only; without a rendered word (screen-reader convention, see
     * `TxAuxSurface.svelte`'s `.sr-only`/`aria-describedby` pair) an AT user
     * hears the pending frequency read as though it were confirmed. Passed
     * in rather than resolved here — this primitive stays i18n-blind, same
     * as every other `primitives/` component.
     */
    pendingAnnouncement?: string;
    /**
     * MOR-1480 — when true (the default), this primitive emits its own
     * `data-vfo-freq` + `data-vfo-active` (mirroring `active`) on its
     * focusable root, so the MOR-1444 keyboard routing guard
     * (`isFrequencyDisplayFocused` in `keyboard-map.ts`) recognizes ANY
     * mount without a bespoke wrapper.
     * DEFENSE-IN-DEPTH ONLY (verifier F1): no current mount depends on this
     * — the `VfoPanel`/`VfoHeader` header path it originally targeted renders
     * on no shipping skin, and `VfoSurface.svelte` (the one live mount) opts
     * out with `vfoFreqHook={false}`. Kept so a future non-semantic mount of
     * this primitive self-qualifies without bespoke wrapper markup.
     * `VfoSurface.svelte` already supplies its own equivalent hook —
     * on the SAME wrapper element its own tests key `data-freq-tunable` off
     * of — so it opts out here with `vfoFreqHook={false}` to avoid a second,
     * nested `[data-vfo-freq]` match for every tunable tile.
     */
    vfoFreqHook?: boolean;
  }

  let {
    freq,
    displayHz,
    disabled = false,
    contextKey,
    compact = false,
    active = true,
    receiver = 'main',
    minFreq = 0,
    maxFreq = 999_000_000,
    onFreqChange,
    pendingDisplayHz = null,
    pendingAnnouncement,
    vfoFreqHook = true,
  }: Props = $props();

  let model = $derived(projectFrequencyReadout({
    confirmedHz: freq,
    displayHz,
    pendingDisplayHz,
    pendingAnnouncement,
  }));
  const owner = createFrequencyInteraction({
    get confirmedHz() { return freq; },
    get digits() { return model.digits; },
    get disabled() { return disabled; },
    get contextKey() { return contextKey; },
    get receiver() { return receiver; },
    get minFreq() { return minFreq; },
    get maxFreq() { return maxFreq; },
    get onFreqChange() { return onFreqChange; },
  });
  let selectedRenderer = $derived.by(() => {
    void contextKey;
    return getSelectedFrequencyReadout();
  });
  let attachedContextKey = untrack(() => contextKey);
  let attachedRenderer = untrack(() => selectedRenderer);
  const attachLease = (key: string | undefined, renderer: typeof selectedRenderer) => createFrequencyInteractionLease(
    owner,
    () => Object.is(contextKey, key) && getSelectedFrequencyReadout() === renderer,
  );
  let lease = $state.raw(attachLease(attachedContextKey, attachedRenderer));
  $effect.pre(() => {
    const nextContextKey = contextKey;
    const nextRenderer = selectedRenderer;
    if (Object.is(nextContextKey, attachedContextKey) && nextRenderer === attachedRenderer) return;
    lease.revoke();
    attachedContextKey = nextContextKey;
    attachedRenderer = nextRenderer;
    lease = attachLease(nextContextKey, nextRenderer);
  });
  onDestroy(() => lease.revoke());
</script>

{#if selectedRenderer}
  {@const Renderer = selectedRenderer}
  <Renderer
    {model}
    presentation="interactive"
    interaction={lease.interaction}
    {compact}
    {active}
    {receiver}
    {vfoFreqHook}
  />
{:else}
  <StandardFrequencyReadout
    {model}
    presentation="interactive"
    interaction={lease.interaction}
    {compact}
    {active}
    {receiver}
    {vfoFreqHook}
  />
{/if}
