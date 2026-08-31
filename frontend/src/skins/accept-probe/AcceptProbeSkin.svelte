<!--
  Accept Probe Skin — MOR-2035/MOR-2034 acceptance experiment.

  Tests the owner's acceptance criterion: "I can build a theme with its own
  S-meters and indicators ... without touching anything below the
  presentation layer." Built from docs/architecture/building-a-skin.md
  alone (see that doc's "Is a scaffold skin warranted?" section for the
  two sanctioned shapes — this is the second: a dedicated layout component,
  not a delegate into RadioLayout/LcdLayout).

  This is its own dedicated layout (registered manifest:
  presentation/layouts/accept-probe-declarations.ts's acceptProbeLayout) —
  it does not mount the shared semantic vertical (SemanticRadioSurfaces).
  It reaches live state through the pure state->props mappers the guide's
  "Read these first" table names (lib/runtime/props/panel-props.ts's
  toVfoProps/toMeterProps), supplied with ServerState/Capabilities from the
  `runtime` singleton ($lib/runtime) — the same calling convention
  lib/runtime/adapters/panel-adapters.ts's deriveVfoControlProps uses
  internally. Its own bespoke S-meter is
  components-v2/meters/AcceptProbeMeter.svelte (registered in
  components-v2/meters/__tests__/meter-contract.ts's METER_REGISTRY).

  Skins may not import $lib/stores/*, $lib/transport/*, or
  $lib/audio/audio-manager (eslint FORBIDDEN_SKINS_IMPORTS, MOR-2039) —
  this file imports neither; state and commands come only through
  lib/runtime/* and lib/runtime/adapters/*.

  Temporary: this skin exists only to probe the v3 authoring guide
  (MOR-2035 acceptance criterion for MOR-2034). Whether it stays in the
  tree is a decision for later, not made by this file.
-->
<script lang="ts">
  import { runtime } from '$lib/runtime';
  import { toVfoProps, toMeterProps } from '$lib/runtime/props/panel-props';
  import AcceptProbeMeter from '../../components-v2/meters/AcceptProbeMeter.svelte';

  const vfo = $derived(toVfoProps(runtime.state, 'main'));
  const meter = $derived(toMeterProps(runtime.state, runtime.caps));
  const freqDisplay = $derived(
    Number.isNaN(vfo.freq) ? '---' : (vfo.freq / 1_000_000).toFixed(6),
  );
</script>

<div class="accept-probe-skin" data-testid="accept-probe-skin">
  <div class="accept-probe-freq" data-testid="accept-probe-frequency">
    <span class="accept-probe-freq-value">{freqDisplay}</span>
    <span class="accept-probe-freq-unit">MHz</span>
    <span class="accept-probe-mode">{vfo.mode}</span>
  </div>

  <div
    class="accept-probe-indicator"
    data-testid="accept-probe-rx-tx-indicator"
    data-active={meter.txActive}
  >
    {meter.txActive ? 'TX' : 'RX'}
  </div>

  <AcceptProbeMeter value={meter.sValue} active={meter.txActive} />
</div>

<style>
  .accept-probe-skin {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding: 0.75rem;
    font-family: monospace;
    color: #eee;
    background: #111;
  }
  .accept-probe-freq {
    font-size: 1.5rem;
    font-variant-numeric: tabular-nums;
  }
  .accept-probe-freq-unit,
  .accept-probe-mode {
    font-size: 0.9rem;
    margin-left: 0.4rem;
    opacity: 0.8;
  }
  .accept-probe-indicator {
    display: inline-block;
    width: fit-content;
    padding: 0.1rem 0.5rem;
    border-radius: 3px;
    background: #333;
  }
  .accept-probe-indicator[data-active='true'] {
    background: #c62828;
    color: #fff;
  }
</style>
