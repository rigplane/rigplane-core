<script lang="ts">
  import type { IcomTouchscreenDisplayModel } from '../../semantic/icom-touchscreen-display-model';
  import ReceiverInstrumentCluster from './ReceiverInstrumentCluster.svelte';
  import ReceiverScopePane from './ReceiverScopePane.svelte';

  interface Props { model: IcomTouchscreenDisplayModel; }
  let { model }: Props = $props();

  const railControls = ['ANT', 'P.AMP', 'ATT', 'IP+', 'AGC', 'VOX', 'COMP'];
  const scopeControls = ['MENU1', 'EDGE', 'HOLD', 'CENT/FIX', 'MAIN/SUB', 'DUAL', 'EXPD/SET'];
</script>

<main class="face" data-testid="icom-touchscreen-face">
  <aside class="left-rail" aria-label="Unwired shared radio controls">
    {#each railControls as label}
      <button type="button" disabled data-control-contract="unwired" data-fixture-only="true">
        {label}
      </button>
    {/each}
  </aside>

  <div class="cluster-placement cluster-placement--main">
    <ReceiverInstrumentCluster model={model.receivers[0]} />
  </div>
  <div class="cluster-placement cluster-placement--sub">
    <ReceiverInstrumentCluster model={model.receivers[1]} />
  </div>

  <section class="shared-bridge" aria-label="Shared receiver bridge">
    <span>DUAL RECEIVER</span>
    <button type="button" disabled data-control-contract="unwired" data-fixture-only="true">
      DUAL-W
    </button>
    <span data-tx-authority="external">TX authority external</span>
  </section>

  <header class="scope-header">SPECTRUM SCOPE</header>
  <div class="scope-placement scope-placement--main">
    <ReceiverScopePane receiver="MAIN" state={model.receivers[0].scope} />
  </div>
  <div class="scope-placement scope-placement--sub">
    <ReceiverScopePane receiver="SUB" state={model.receivers[1].scope} />
  </div>

  <nav class="bottom-rail" aria-label="Unwired scope controls">
    {#each scopeControls as label}
      <button type="button" disabled data-control-contract="unwired" data-fixture-only="true">
        {label}
      </button>
    {/each}
  </nav>
</main>

<style>
  .face {
    position: relative;
    box-sizing: border-box;
    container-type: inline-size;
    width: 100%;
    aspect-ratio: 1734 / 1000;
    display: grid;
    grid-template-columns: 10.6% minmax(0, 1fr) minmax(0, 1fr);
    grid-template-rows: minmax(18rem, 53.7fr) auto minmax(11rem, 37.9fr) minmax(3.5rem, 7.4fr);
    grid-template-areas:
      'rail main sub'
      'rail scope-title scope-title'
      'rail scope-main scope-sub'
      'bottom bottom bottom';
    gap: 0.4rem;
    min-width: 58rem;
    min-height: 0;
    padding: 0.55rem;
    color: #edf4f2;
    background: linear-gradient(150deg, #182124, #030606 58%);
    border: 0.32rem solid #606a6c;
    border-radius: 0.7rem;
    box-shadow: inset 0 0 0 1px #111819, inset 0 0 1.4rem #000;
  }

  .left-rail {
    grid-area: rail;
    display: grid;
    grid-template-rows: repeat(7, minmax(0, 1fr));
    gap: 0.28rem;
  }

  .cluster-placement--main { grid-area: main; }
  .cluster-placement--sub { grid-area: sub; }
  .cluster-placement { min-width: 0; }
  .cluster-placement > :global(.cluster) { height: 100%; box-sizing: border-box; }

  .shared-bridge {
    position: absolute;
    top: 1.1rem;
    left: 49.5%;
    z-index: 2;
    display: grid;
    gap: 0.24rem;
    width: 12%;
    padding: 0.35rem;
    transform: translateX(-1.5%);
    border: 1px solid #657173;
    background: #151d1f;
    color: #c8d3d1;
    font: 0.66rem ui-monospace, monospace;
    text-align: center;
  }

  .scope-header {
    grid-area: scope-title;
    padding: 0.2rem;
    border: 1px solid #596769;
    background: #283436;
    font: 0.78rem ui-monospace, monospace;
    text-align: center;
    letter-spacing: 0.12em;
  }

  .scope-placement--main { grid-area: scope-main; }
  .scope-placement--sub { grid-area: scope-sub; }
  .scope-placement { min-width: 0; }
  .scope-placement > :global(.scope-pane) { height: 100%; box-sizing: border-box; }

  .bottom-rail {
    grid-area: bottom;
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 0.42rem;
  }

  button {
    min-width: 0;
    color: #9da9a8;
    background: linear-gradient(#303b3d, #111718);
    border: 2px solid #566265;
    border-radius: 0.2rem;
    font: 0.72rem ui-monospace, monospace;
  }

  [data-tx-authority='external'] { color: #80908f; }
</style>
