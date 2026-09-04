<script lang="ts">
  import type {
    DisplayFact,
    ReceiverInstrumentDisplayModel,
  } from '../../semantic/icom-touchscreen-display-model';
  import ReceiverNeedleSMeter from './ReceiverNeedleSMeter.svelte';

  interface Props { model: ReceiverInstrumentDisplayModel; }
  let { model }: Props = $props();

  function text<T>(fact: DisplayFact<T>, format: (value: T) => string = String): string {
    return fact.value === undefined ? (fact.status === 'unsupported' ? 'N/A' : '—') : format(fact.value);
  }

  function frequency(value: number): string {
    return `${Math.round(value)}`.replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }
</script>

<section
  class="cluster"
  data-receiver-cluster={model.receiver}
  data-receiver-activity={model.receiverActivity}
  aria-label={`${model.receiver} receiver instruments`}
>
  <header>
    <strong>{model.receiver}</strong>
    <span data-mode-state={model.mode.status}>{text(model.mode)}</span>
    <span data-filter-state={model.filter.status}>{text(model.filter)}</span>
    <span data-bandwidth-state={model.bandwidthHz.status}>
      BW {text(model.bandwidthHz, (value) => `${value} Hz`)}
    </span>
  </header>

  <ReceiverNeedleSMeter state={model.sMeter} ticks={model.sMeterTicks} />

  <output class="frequency" data-frequency-state={model.frequencyHz.status}>
    {text(model.frequencyHz, frequency)}
  </output>

  <div class="unwired-row" aria-label="Unwired receiver controls">
    {#each ['MODE', 'FILTER', 'BW', 'SFT'] as label}
      <button
        type="button"
        disabled
        data-control-contract="unwired"
        data-fixture-only="true"
      >{label}</button>
    {/each}
  </div>
</section>

<style>
  .cluster {
    min-width: 0;
    display: grid;
    grid-template-rows: auto minmax(5.4rem, 0.75fr) minmax(3.8rem, auto) auto;
    gap: 0.35rem;
    padding: 0.55rem;
    border: 1px solid #5c696b;
    background: #020606;
    color: #edf4f2;
  }

  .cluster[data-receiver-activity='inactive'] { border-color: #3d494b; }
  .cluster[data-receiver-activity='unknown'] { border-style: dashed; }

  header {
    display: grid;
    grid-template-columns: auto repeat(3, minmax(0, auto));
    gap: 0.35rem;
    align-items: center;
    font: 0.72rem ui-monospace, monospace;
  }

  header strong { color: #f3c766; }

  header span {
    min-width: 0;
    overflow: hidden;
    padding: 0.2rem 0.3rem;
    border: 1px solid #647173;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .frequency {
    min-height: 1em;
    overflow: hidden;
    color: #f5f8f5;
    font: clamp(2rem, 5.2cqi, 4.3rem)/0.95 ui-monospace, monospace;
    letter-spacing: -0.075em;
    text-align: center;
    white-space: nowrap;
  }

  .frequency[data-frequency-state='unknown'],
  .frequency[data-frequency-state='inactive'] { color: #81908f; }

  .unwired-row {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.25rem;
  }

  button {
    min-width: 0;
    padding: 0.28rem;
    color: #8f9b9a;
    background: #151c1d;
    border: 1px solid #4b5658;
    font: 0.66rem ui-monospace, monospace;
  }
</style>
