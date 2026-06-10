<script lang="ts">
  import { HardwareButton } from '$lib/Button';
  import { getShortcutHint } from '../layout/shortcut-hints';
  import { deriveModeProps, getModeHandlers } from '$lib/runtime/adapters/panel-adapters';
  import { MOD_INPUT_SOURCES } from '$lib/radio/mod-input';
  import { t } from '$lib/i18n';

  const handlers = getModeHandlers();
  let p = $derived(deriveModeProps());

  // Destructure for template readability
  let currentMode = $derived(p.currentMode);
  let modes = $derived(p.modes);
  let dataMode = $derived(p.dataMode);
  let hasDataMode = $derived(p.hasDataMode);
  let dataModeCount = $derived(p.dataModeCount ?? 0);
  let dataModeLabels = $derived(p.dataModeLabels ?? { '0': 'OFF', '1': 'D1', '2': 'D2', '3': 'D3' });
  // MOR-616: MOD-input source of the active DATA group (null until read).
  let modInputSource = $derived(p.modInputSource ?? null);
  let hasModInput = $derived(p.hasModInput ?? false);
  const onModeChange = handlers.onModeChange;
  const onDataModeChange = handlers.onDataModeChange;
  const onModInputChange = handlers.onModInputChange;

  // Canonical display order — covers both IC-7610 and Yaesu naming conventions.
  const modeOrder = [
    'USB', 'LSB',
    'CW', 'CW-R', 'CW-U', 'CW-L',       // IC-7610: CW/CW-R, Yaesu: CW-U/CW-L
    'RTTY', 'RTTY-R', 'RTTY-L', 'RTTY-U', // IC-7610: RTTY/RTTY-R, Yaesu: RTTY-L/RTTY-U
    'PSK', 'PSK-R',
    'DATA-U', 'DATA-L', 'DATA-FM', 'DATA-FM-N',
    'AM', 'AM-N', 'FM', 'FM-N',
    'C4FM-DN', 'C4FM-VW',
  ];

  let orderedModes = $derived(modeOrder.filter((mode) => modes.includes(mode)));
  let extraModes = $derived(modes.filter((mode) => !modeOrder.includes(mode)));
  let visibleModes = $derived([...orderedModes, ...extraModes]);
  let dataOptions = $derived(
    Array.from({ length: Math.max(0, dataModeCount) + 1 }, (_, index) => ({
      value: index,
      label: dataModeLabels[String(index)] ?? (index === 0 ? 'OFF' : `D${index}`),
    })),
  );

  function modeShortcut(mode: string): string | null {
    return getShortcutHint('mode_select', (binding) => binding.params?.mode === mode);
  }

  const dataShortcut = getShortcutHint('cycle_data_mode');
</script>

<div class="panel-body" data-mode-panel="true" data-highlight={undefined}>
    <div class="mode-grid">
      {#each visibleModes as mode}
        <HardwareButton
          active={currentMode === mode}
          indicator="edge-left"
          color="cyan"
          title={modeShortcut(mode)}
          shortcutHint={modeShortcut(mode)}
          onclick={() => onModeChange(mode)}
        >
          {mode}
        </HardwareButton>
      {/each}
    </div>

    {#if hasDataMode && dataOptions.length === 2}
      <HardwareButton
        active={dataMode > 0}
        indicator="edge-left"
        color="red"
        title={dataShortcut}
        shortcutHint={dataShortcut}
        onclick={() => onDataModeChange(dataMode > 0 ? 0 : 1)}
      >
        DATA
      </HardwareButton>
    {:else if hasDataMode && dataOptions.length > 2}
      <div class="section-label">DATA</div>
      <div class="data-grid">
        {#each dataOptions as option}
          <HardwareButton
            active={dataMode === option.value}
            indicator="edge-left"
            color="cyan"
            title={dataShortcut}
            shortcutHint={dataShortcut}
            onclick={() => onDataModeChange(option.value)}
          >
            {option.label}
          </HardwareButton>
        {/each}
      </div>
    {/if}

    {#if hasModInput}
      <!-- MOR-616: MOD-input source of the active DATA group (DATA OFF/D1/D2/D3).
           Tracks front-panel changes via the backend readback; selecting a
           source emits the matching set_data*_mod_input command. -->
      <div class="mod-input-row">
        <span class="section-label">{t('core.modePanel.modInputLabel')}</span>
        <select
          class="mod-input-select"
          data-testid="mod-input-select"
          aria-label={t('core.modePanel.modInputAria')}
          title={t('core.modePanel.modInputAria')}
          value={modInputSource === null ? '' : String(modInputSource)}
          onchange={(e) => onModInputChange(Number(e.currentTarget.value))}
        >
          {#if modInputSource === null}
            <option value="" disabled>—</option>
          {/if}
          {#each MOD_INPUT_SOURCES as option (option.value)}
            <option value={String(option.value)}>{option.label}</option>
          {/each}
        </select>
      </div>
    {/if}
  </div>

<style>
  .panel-body {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 7px 8px;
  }

  .mode-grid,
  .data-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 4px;
  }

  .section-label {
    color: var(--v2-text-dim);
    font-family: 'Roboto Mono', monospace;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
  }

  .mode-grid > :global(button),
  .data-grid > :global(button) {
    min-width: 0;
  }

  .mod-input-row {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .mod-input-select {
    flex: 1;
    min-width: 0;
    background: var(--v2-bg-input, #111);
    color: var(--v2-text-primary, #ddd);
    border: 1px solid var(--v2-border, #333);
    border-radius: 3px;
    font-family: 'Roboto Mono', monospace;
    font-size: 11px;
    padding: 3px 4px;
  }

  .mod-input-select:focus-visible {
    outline: 1px solid var(--v2-accent-cyan, #0ff);
    outline-offset: 1px;
  }
  </style>