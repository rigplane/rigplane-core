<script lang="ts">
  import { ValueControl } from '../controls/value-control';
  import { HardwareButton, HardwarePlainButton } from '../../lib/Button';
  import { formatOffsetKHz, shouldShowPanel } from './rit-utils';
  import { getShortcutHint } from '../layout/shortcut-hints';

  import { deriveRitXitProps, getRitXitHandlers } from '$lib/runtime/adapters/panel-adapters';

  const handlers = getRitXitHandlers();
  let p = $derived(deriveRitXitProps());

  let ritActive = $derived(p.ritActive);
  let ritOffset = $derived(p.ritOffset);
  let xitActive = $derived(p.xitActive);
  let xitOffset = $derived(p.xitOffset);
  let hasRit = $derived(p.hasRit);
  let hasXit = $derived(p.hasXit);
  const onRitToggle = handlers.onRitToggle;
  const onXitToggle = handlers.onXitToggle;
  const onRitOffsetChange = handlers.onRitOffsetChange;
  const onXitOffsetChange = handlers.onXitOffsetChange;
  const onClear = handlers.onClear;

  let visible = $derived(shouldShowPanel(hasRit, hasXit));
  let offsetValue = $derived(xitActive && !ritActive ? xitOffset : ritOffset);
  const ritShortcut = getShortcutHint('toggle_rit');
  const xitShortcut = getShortcutHint('toggle_xit');
  const clearShortcut = getShortcutHint('clear_rit_xit');

  function handleOffsetChange(value: number) {
    if (xitActive && !ritActive) {
      onXitOffsetChange(value);
      return;
    }
    onRitOffsetChange(value);
  }
</script>

{#if visible}
    <div class="panel-body">
      {#if hasRit}
        <div class="row">
          <HardwareButton indicator="dot" active={ritActive} color="cyan" onclick={onRitToggle} shortcutHint={ritShortcut} title={ritShortcut}>RIT</HardwareButton>
          <span class="offset" class:active={ritActive}>{formatOffsetKHz(ritOffset)}</span>
        </div>
      {/if}
      {#if hasXit}
        <div class="row">
          <HardwareButton indicator="dot" active={xitActive} color="orange" onclick={onXitToggle} shortcutHint={xitShortcut} title={xitShortcut}>XIT</HardwareButton>
          <span class="offset" class:active={xitActive}>{formatOffsetKHz(xitOffset)}</span>
        </div>
      {/if}
      <ValueControl
        label="Offset"
        value={offsetValue}
        min={-9999}
        max={9999}
        step={50}
        unit="kHz"
        displayFn={formatOffsetKHz}
        renderer="bipolar"
        accentColor="var(--v2-accent-cyan)"
        onChange={handleOffsetChange}
      variant="hardware-illuminated"
      />
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
