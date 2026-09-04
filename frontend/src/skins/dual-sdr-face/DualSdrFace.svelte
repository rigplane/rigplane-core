<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { RadioViewModel } from '../../semantic/radio-view-model';
  import type { ScopeFrame } from '../../lib/runtime/adapters/scope-adapter';
  import ReceiverInstrumentCluster from './ReceiverInstrumentCluster.svelte';
  export interface ScopeFrameSource { subscribe(listener: (frame: ScopeFrame) => void): () => void; }
  interface Props { view: RadioViewModel; scopeFrames: ScopeFrameSource; onPreChange?: (level: number) => void; }
  let { view, scopeFrames, onPreChange }: Props = $props();
  let frames: [ScopeFrame | null, ScopeFrame | null] = $state([null, null]);
  const unsubscribe = scopeFrames.subscribe((frame) => { if (frame.receiver === 0 || frame.receiver === 1) frames = frame.receiver === 0 ? [frame, frames[1]] : [frames[0], frame]; });
  onDestroy(unsubscribe);
  let preEnabled = $derived(view.rfFrontEnd?.preamp.availability.structural === true && view.rfFrontEnd.preamp.availability.operational === true && view.rfFrontEnd.preamp.reading.status === 'known' && onPreChange !== undefined);
  let preNext = $derived(view.rfFrontEnd?.preamp.reading.status === 'known' ? view.rfFrontEnd.preamp.reading.value : 0);
  const disabled = ['att', 'ip', 'agc', 'vox', 'comp', 'ant', 'menu1', 'edge', 'hold', 'cent-fix', 'main-sub', 'dual', 'expd-set', 'mode'];
</script>

<main class="face" data-testid="dual-sdr-face">
  <aside class="rail" aria-label="Receiver controls">
    <button data-control="ant" disabled>ANT<br />—</button><button data-control="pre" disabled={!preEnabled} onclick={() => onPreChange?.(preNext)}>P.AMP<br />{preNext}</button>
    {#each ['att', 'ip', 'agc', 'vox', 'comp'] as name}<button data-control={name} disabled>{name === 'ip' ? 'IP+' : name.toUpperCase()}<br />—</button>{/each}
  </aside>
  <section class="instruments"><ReceiverInstrumentCluster {view} receiver={0} frame={frames[0]} /><ReceiverInstrumentCluster {view} receiver={1} frame={frames[1]} /></section>
  <div class="status">SPECTRUM SCOPE · {view.scopeControls?.mode.reading.status === 'known' ? `MODE ${view.scopeControls.mode.reading.value}` : '—'}</div>
  <nav class="softkeys" aria-label="Scope softkeys">{#each ['menu1', 'edge', 'hold', 'cent-fix', 'main-sub', 'dual', 'expd-set'] as name}<button data-control={name} disabled>{name === 'cent-fix' ? 'CENT/FIX' : name === 'main-sub' ? 'MAIN/SUB' : name === 'expd-set' ? 'EXPD/SET' : name.toUpperCase()}</button>{/each}</nav>
</main>

<style>
  .face { container-type: inline-size; display: grid; grid-template-columns: minmax(82px, .12fr) 1fr; grid-template-rows: 1fr auto auto; min-height: 500px; padding: 10px; gap: 8px; color: #edf4f2; background: linear-gradient(145deg, #131c1e, #020404 55%); border: 4px solid #5b6668; border-radius: 12px; }
  .rail { grid-row: 1 / 3; display: grid; grid-template-rows: repeat(7, 1fr); gap: 3px; } button { color: #ecf1ef; background: linear-gradient(#2f3a3c, #111718); border: 2px solid #667276; border-radius: 4px; font: 14px ui-monospace, monospace; } button:not(:disabled) { color: #c8fbff; border-color: #20b7c7; } button:disabled { opacity: .72; }
  .instruments { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; min-height: 0; }.status { grid-column: 2; text-align: center; background: #334044; border: 1px solid #758084; font: 17px ui-monospace, monospace; }.softkeys { grid-column: 1 / -1; display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; min-height: 54px; }
  @container (max-width: 800px) { .face { grid-template-columns: 1fr; } .rail { grid-row: auto; grid-template-columns: repeat(7, 1fr); grid-template-rows: none; min-height: 65px; } .instruments { grid-template-columns: 1fr; } .status { grid-column: 1; } .softkeys { gap: 3px; } }
</style>
