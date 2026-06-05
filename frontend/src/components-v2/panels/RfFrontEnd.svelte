<script lang="ts">
  import { ValueControl, rawToPercentDisplay } from '../controls/value-control';
  import DualParamRenderer from '../controls/value-control/DualParamRenderer.svelte';
  import AttenuatorControl from '../controls/AttenuatorControl.svelte';
  import { HardwareButton } from '$lib/Button';
  import { shouldShowPanel } from './rf-frontend-utils';
  import { getShortcutHint } from '../layout/shortcut-hints';

  import { deriveRfFrontEndProps, getRfFrontEndHandlers } from '$lib/runtime/adapters/panel-adapters';

  const handlers = getRfFrontEndHandlers();
  let p = $derived(deriveRfFrontEndProps());

  let rfGain = $derived(p.rfGain);
  let squelch = $derived(p.squelch);
  let att = $derived(p.att);
  let pre = $derived(p.pre);
  let preDisabled = $derived(p.preDisabled);
  let preDisabledReason = $derived(p.preDisabledReason);
  let digiSel = $derived(p.digiSel);
  let ipPlus = $derived(p.ipPlus);
  const onRfGainChange = handlers.onRfGainChange;
  const onSquelchChange = handlers.onSquelchChange;
  const onAttChange = handlers.onAttChange;
  const onPreChange = handlers.onPreChange;
  const onDigiSelToggle = handlers.onDigiSelToggle;
  const onIpPlusToggle = handlers.onIpPlusToggle;

  let showRfGain = $derived(p.showRfGain);
  let showSquelch = $derived(p.showSquelch);
  let showAtt = $derived(p.showAtt);
  let showPre = $derived(p.showPre);
  let showDigiSel = $derived(p.showDigiSel);
  let showIpPlus = $derived(p.showIpPlus);
  let showRfSqlDual = $derived(showRfGain && showSquelch);
  let visible = $derived(shouldShowPanel(showRfGain, showAtt, showPre, showSquelch));

  let attValues = $derived(p.attValues);
  let attLabels = $derived(p.attLabels);
  let preOptions = $derived(p.preOptions);
  const rfGainShortcut = getShortcutHint('adjust_rf_gain');
  const attShortcut = getShortcutHint('cycle_att');
  const preShortcut = getShortcutHint('cycle_preamp');
</script>

{#if visible}
  <div class="controls">
    {#if showRfSqlDual}
      <DualParamRenderer
        rfValue={rfGain}
        sqlValue={squelch}
        min={0}
        max={255}
        step={1}
        rfAccentColor="#22C55E"
        sqlAccentColor="#F59E0B"
        shortcutHint={rfGainShortcut}
        title={rfGainShortcut}
        onRfChange={onRfGainChange}
        onSqlChange={onSquelchChange}
        variant="hardware-illuminated"
      />
    {:else if showRfGain}
      <div data-control="rf-gain">
        <ValueControl
          value={rfGain}
          min={0}
          max={255}
          step={1}
          label="RF Gain"
          renderer="hbar"
          displayFn={rawToPercentDisplay}
          accentColor="#22C55E"
          shortcutHint={rfGainShortcut}
          title={rfGainShortcut}
          onChange={onRfGainChange}
          variant="hardware-illuminated"
        />
      </div>
    {/if}

    {#if showAtt}
      {#if attValues.length <= 2}
        <HardwareButton
          active={att > 0}
          indicator="edge-left"
          color="amber"
          title={attShortcut}
          shortcutHint={attShortcut}
          onclick={() => onAttChange(att > 0 ? 0 : attValues[attValues.length - 1])}
        >
          ATT
        </HardwareButton>
      {:else}
        <div class="control-row" data-shortcut-hint={attShortcut ?? undefined} title={attShortcut ?? undefined}>
          <span class="control-label">ATT</span>
          <AttenuatorControl values={attValues} selected={att} onchange={onAttChange} labels={attLabels} shortcutHint={attShortcut} title={attShortcut} />
        </div>
      {/if}
    {/if}

    {#if showPre}
      <div class="control-row" data-shortcut-hint={preShortcut ?? undefined} title={preShortcut ?? undefined}>
        <span class="control-label">PRE</span>
        <div class="button-group">
          {#each preOptions as option}
            <HardwareButton
              active={pre === option.value}
              indicator="edge-left"
              color="cyan"
              disabled={preDisabled}
              title={preDisabled ? preDisabledReason : preShortcut}
              shortcutHint={preShortcut}
              onclick={() => onPreChange(option.value)}
            >
              {option.label}
            </HardwareButton>
          {/each}
        </div>
      </div>
    {/if}

    {#if showDigiSel || showIpPlus}
      <div class="button-row">
        {#if showDigiSel}
          <HardwareButton
            active={digiSel}
            indicator="edge-left"
            color="green"
            onclick={() => onDigiSelToggle(!digiSel)}
          >
            DIGI-SEL
          </HardwareButton>
        {/if}
        {#if showIpPlus}
          <HardwareButton
            active={ipPlus}
            indicator="edge-left"
            color="cyan"
            onclick={() => onIpPlusToggle(!ipPlus)}
          >
            IP+
          </HardwareButton>
        {/if}
      </div>
    {/if}
  </div>
{/if}

<style>
  .controls {
    padding: 8px 10px 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .control-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    min-width: 0;
  }

  .control-label {
    color: var(--v2-text-dim);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.06em;
    flex-shrink: 0;
    min-width: 34px;
  }

  .control-row > :global(.segmented-button),
  .control-row > :global(.att-control),
  .control-row > .button-group {
    flex: 1 1 auto;
    min-width: 0;
  }

  .control-row :global(.segment),
  .control-row :global(.more-button) {
    min-height: 28px;
    padding: 4px 8px;
    font-size: 10px;
  }

  .control-row :global(.segmented-button) {
    width: 100%;
  }

  .control-row :global(.segment) {
    flex: 1 1 0;
    min-width: 0;
  }

  .button-row {
    display: flex;
    gap: 6px;
  }

  .button-row > :global(button) {
    flex: 1 1 0;
    min-width: 0;
  }

  .button-group {
    display: flex;
    gap: 4px;
    flex: 1 1 auto;
    min-width: 0;
  }

  .button-group > :global(button) {
    flex: 1 1 0;
    min-width: 0;
  }
</style>
