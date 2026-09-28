<script lang="ts">
  import { onDestroy } from 'svelte';
  import { ValueControl } from '../controls/value-control';
  import { HardwareButton, HardwarePlainButton } from '../../lib/Button';
  import { formatOffsetKHz, shouldShowPanel } from './rit-utils';
  import { getShortcutHint } from '../layout/shortcut-hints';
  import { decodeControlDomain, encodeControlDomain } from '$lib/radio/control-domain';
  import type { ControlDomain } from '$lib/types/capabilities';
  import { finiteValue, valueText } from '../../primitives/reading-text';
  import {
    createContinuousScalar,
    type ContinuousScalarInput,
  } from '../../primitives/scalar/continuous-scalar.svelte';
  // MOR-2909: the legacy host rides Standard's own `offsetPolicy` —
  // imported from the semantic host, never copied (no new policy).
  import { offsetPolicy } from '../../semantic/RitXitScanSurface.svelte';

  import { deriveRitXitProps, getRitXitHandlers } from '$lib/runtime/adapters/panel-adapters';

  const handlers = getRitXitHandlers();
  let p = $derived(deriveRitXitProps());

  let ritActive = $derived(p.ritActive);
  let ritOffset = $derived(p.ritOffset);
  let xitActive = $derived(p.xitActive);
  let xitOffset = $derived(p.xitOffset);
  let hasRit = $derived(p.hasRit);
  let hasXit = $derived(p.hasXit);
  let ritDomain = $derived(p.ritDomain);
  const onRitToggle = handlers.onRitToggle;
  const onXitToggle = handlers.onXitToggle;
  const onRitOffsetChange = handlers.onRitOffsetChange;
  const onXitOffsetChange = handlers.onXitOffsetChange;
  const onClear = handlers.onClear;

  let visible = $derived(shouldShowPanel(hasRit, hasXit));
  let offsetValue = $derived(xitActive && !ritActive ? xitOffset : ritOffset);
  let canAdjustOffset = $derived(ritDomain === undefined
    ? Number.isFinite(offsetValue)
    : ritDomain !== null && canonicalRaw(ritDomain, offsetValue) !== null);
  const ritShortcut = getShortcutHint('toggle_rit');
  const xitShortcut = getShortcutHint('toggle_xit');
  const clearShortcut = getShortcutHint('clear_rit_xit');

  // MOR-1409 A12 (coordinator adjudication, Core #2317, comment 5246487510):
  // `hasRit`/`hasXit` gate on capability presence only — a connected
  // receiver that has never reported `ritFreq` (optional field) still
  // passes the gate with a `NaN` offset (panel-props.ts no longer
  // fabricates `?? 0`). `formatOffsetKHz` (rit-utils.ts, not an A12 owner)
  // has no NaN guard: `hz > 0` is false for NaN, so it falls to the
  // negative branch and renders the literal "−NaN kHz". MOR-2667: the
  // unread offset renders an unlit, EMPTY box in a reserved slot (the
  // `.offset` rule below) — never a `'--- kHz'` placeholder.
  function formatOffsetDisplay(hz: number): string {
    return valueText(finiteValue(hz), formatOffsetKHz);
  }

  function handleOffsetChange(value: number) {
    if (ritDomain !== undefined) {
      if (ritDomain === null) return;
      const encoded = canonicalRaw(ritDomain, value);
      if (encoded === null) return;
      value = encoded;
    }
    if (xitActive && !ritActive) {
      onXitOffsetChange(value);
      return;
    }
    onRitOffsetChange(value);
  }

  function canonicalRaw(domain: ControlDomain, value: number): number | null {
    try {
      const display = decodeControlDomain(domain, value);
      const encoded = display === null ? null : encodeControlDomain(domain, display);
      return encoded !== null && Number.isSafeInteger(encoded) ? encoded : null;
    } catch {
      return null;
    }
  }

  // MOR-2909: the offset rides the shared scalar binding with Standard's
  // `offsetPolicy` — a track double-click sends NO command (CLEAR stays the
  // reset), arrows step by the domain's `raw_step` immediately (50 Hz only
  // as the no-domain legacy fallback), pointer drags stay immediate.
  function offsetInput(): Readonly<ContinuousScalarInput> {
    const step = ritDomain?.raw_step ?? 50;
    return {
      evidence: 'reading',
      reading: Number.isFinite(offsetValue)
        ? { status: 'known', value: offsetValue }
        : { status: 'unknown' },
      ownerKey: 'ritxit-panel-offset',
      enabled: canAdjustOffset,
      request: handleOffsetChange,
      domain: {
        min: ritDomain?.raw_min ?? -9999,
        max: ritDomain?.raw_max ?? 9999,
        step,
        keyboardStep: step,
        fineStepDivisor: 1,
        defaultValue: ritDomain?.raw_origin ?? 0,
      },
    };
  }
  const offsetBinding = createContinuousScalar(offsetInput, offsetPolicy);
  onDestroy(() => offsetBinding.destroy());

  // MOR-1713 inventory marker (same shape as FilterPanel's binding rows):
  // the control rides the shared scalar machinery, not raw value/onChange.
  const feedbackIntegratedRange = { 'feedback-policy': 'feedback-integrated' } as const;
</script>

{#if visible}
    <div class="panel-body">
      {#if hasRit}
        <div class="row">
          <HardwareButton indicator="dot" active={ritActive} color="cyan" onclick={onRitToggle} shortcutHint={ritShortcut} title={ritShortcut}>RIT</HardwareButton>
          <span class="offset" class:active={ritActive}>{formatOffsetDisplay(ritOffset)}</span>
        </div>
      {/if}
      {#if hasXit}
        <div class="row">
          <HardwareButton indicator="dot" active={xitActive} color="orange" onclick={onXitToggle} shortcutHint={xitShortcut} title={xitShortcut}>XIT</HardwareButton>
          <span class="offset" class:active={xitActive}>{formatOffsetDisplay(xitOffset)}</span>
        </div>
      {/if}
      {#if canAdjustOffset}
        <ValueControl
          {...feedbackIntegratedRange}
          binding={offsetBinding}
          label="Offset"
          unit="kHz"
          displayFn={formatOffsetDisplay}
          renderer="bipolar"
          accentColor="var(--v2-accent-cyan)"
          variant="hardware-illuminated"
        />
      {/if}
      <div class="clear-row">
        <!-- action-button: momentary command, no sustained state -->
        <HardwarePlainButton onclick={onClear} title={clearShortcut} shortcutHint={clearShortcut}>CLEAR</HardwarePlainButton>
      </div>
    </div>
{/if}

<style>
  .panel-body {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px 8px;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .offset {
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    color: var(--v2-text-disabled);
    transition: color 150ms ease;
    /* MOR-2667: the offset box stays reserved in every state — 10ch covers
       the widest formatOffsetKHz text over the panel's fallback domain
       (±9999 Hz → '±10.00 kHz'); tabular digits keep a changing value
       from shifting the row. */
    min-inline-size: 10ch;
    font-variant-numeric: tabular-nums;
  }

  .offset.active {
    color: var(--v2-text-light);
  }

  .clear-row {
    display: flex;
    justify-content: flex-end;
    padding-top: 2px;
  }

</style>
