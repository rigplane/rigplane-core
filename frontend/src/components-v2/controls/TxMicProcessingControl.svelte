<script lang="ts">
  // MOR-4596: the TX microphone processing switch. One control, hosted in the
  // TX levels dialog (TxPanel: phone TX settings sheet and the standard
  // sidebars) and in the desktop Settings dialog where TxPanel is replaced by
  // the semantic deck. The mode is a per-browser preference read at the next
  // capture start.
  import { t } from '$lib/i18n';
  import {
    getTxMicProcessing,
    setTxMicProcessing,
    type TxMicProcessing,
  } from '$lib/stores/tx-mic-processing.svelte';

  const modes: readonly TxMicProcessing[] = ['raw', 'browser'];
  const mode = $derived(getTxMicProcessing());
</script>

<fieldset class="tx-mic-processing" data-testid="tx-mic-processing">
  <legend>{t('core.txMic.processing.label')}</legend>
  {#each modes as option (option)}
    <label class="tx-mic-processing-row">
      <input
        type="radio"
        name="tx-mic-processing"
        value={option}
        data-testid={`tx-mic-processing-${option}`}
        checked={mode === option}
        onchange={() => setTxMicProcessing(option)}
      />
      <span>{t(`core.txMic.processing.${option}`)}</span>
    </label>
  {/each}
  <p class="tx-mic-processing-help">{t('core.txMic.processing.help')}</p>
</fieldset>

<style>
  .tx-mic-processing {
    margin: 8px 0 0;
    padding: 8px 0 0;
    border: 0;
    border-top: 1px solid var(--v2-border);
    color: var(--v2-text-primary, #fff);
    font-size: 12px;
  }
  legend {
    padding: 0;
    font-size: 11px;
    letter-spacing: 0.08em;
    color: var(--v2-text-dim);
  }
  .tx-mic-processing-row {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 32px;
  }
  .tx-mic-processing-help {
    margin: 4px 0 0;
    color: var(--v2-text-dim);
    font-size: 11px;
  }
</style>
