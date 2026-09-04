<script lang="ts">
  import type { RadioViewModel } from '../../semantic/radio-view-model';
  import type { ScopeFrame } from '../../lib/runtime/adapters/scope-adapter';
  import ReceiverInstrumentCluster from './ReceiverInstrumentCluster.svelte';
  export interface ScopeFrameSource {
    subscribe(listener: (frame: ScopeFrame) => void): () => void;
    subscribeHealth?(listener: (live: boolean) => void): () => void;
  }
  interface Props { view: RadioViewModel; scopeSource: ScopeFrameSource; onPreChange?: (level: number) => void; }
  let { view, scopeSource, onPreChange }: Props = $props();
  let frames: [ScopeFrame | null, ScopeFrame | null] = $state([null, null]);
  $effect(() => {
    const unsubscribeFrames = scopeSource.subscribe((incoming) => {
      if (incoming.receiver !== 0 && incoming.receiver !== 1) return;
      const frame = Object.freeze({ ...incoming, pixels: new Uint8Array(incoming.pixels) }) as ScopeFrame;
      frames = incoming.receiver === 0 ? [frame, frames[1]] : [frames[0], frame];
    });
    const unsubscribeHealth = scopeSource.subscribeHealth?.((live) => {
      if (!live) frames = [null, null];
    });
    return () => {
      unsubscribeFrames();
      unsubscribeHealth?.();
    };
  });
  let pre = $derived(view.rfFrontEnd?.preamp);
  let preBlocked = $derived(view.disabledReasons.some((reason) => reason.field === 'rfFrontEnd.preamp'));
  let preEnabled = $derived(pre?.availability.structural === true && pre.availability.operational === true && pre.reading.status === 'known' && !preBlocked && onPreChange !== undefined);
  let preNext = $derived.by(() => {
    if (!preEnabled || !pre || pre.reading.status !== 'known') return null;
    const current = pre.reading.value;
    const values = view.rfFrontEnd?.preValues ?? [];
    const index = values.indexOf(current);
    return index < 0 || values.length === 0 ? null : values[(index + 1) % values.length];
  });
</script>

<main class="face" data-testid="dual-sdr-face">
  <aside class="rail" aria-label="Receiver controls">
    <button data-control="ant" disabled>ANT<br />—</button><button data-control="pre" disabled={preNext === null} onclick={() => preNext !== null && onPreChange?.(preNext)}>P.AMP<br />{pre?.reading.status === 'known' ? pre.reading.value : '—'}</button>
    {#each ['att', 'ip', 'agc', 'vox', 'comp', 'mode'] as name}<button data-control={name} disabled>{name === 'ip' ? 'IP+' : name.toUpperCase()}<br />—</button>{/each}
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
