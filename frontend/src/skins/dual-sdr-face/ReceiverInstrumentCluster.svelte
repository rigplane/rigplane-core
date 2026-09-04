<script lang="ts">
  import type { RadioViewModel } from '../../semantic/radio-view-model';
  import type { ScopeFrame } from '../../lib/runtime/adapters/scope-adapter';
  import ReceiverNeedleSMeter from './ReceiverNeedleSMeter.svelte';
  import ReceiverScopeWaterfall from './ReceiverScopeWaterfall.svelte';
  interface Props { view: RadioViewModel; receiver: 0 | 1; frame: ScopeFrame | null; }
  let { view, receiver, frame }: Props = $props();
  let receiverId = $derived(receiver === 0 ? 'MAIN' : 'SUB');
  let vfo = $derived(view.vfos.find((item) => item.receiver === receiverId));
  let meter = $derived(view.meters?.signal.reading.status === 'known' && receiver === 0 ? view.meters.signal.reading.value : null);
  let frequency = $derived(vfo?.frequencyHz === null || vfo?.frequencyHz === undefined ? '—' : vfo.frequencyHz.toLocaleString('en-US'));
</script>

<section class="cluster" data-receiver-cluster={receiver} aria-label={`${receiverId} receiver`}>
  <header><b>{receiverId}</b><span>VFO</span><span>{vfo?.mode ?? '—'}</span><span>{vfo?.filter ?? '—'}</span><span>BW —</span><span>SFT —</span></header>
  <ReceiverNeedleSMeter value={meter} />
  <output class="frequency" data-frequency>{frequency}</output>
  <div class="secondary">{vfo?.label ?? '—'} · {vfo?.mode ?? '—'} · {vfo?.filter ?? '—'}</div>
  <ReceiverScopeWaterfall {frame} />
</section>

<style>
  .cluster { min-width: 0; display: grid; grid-template-rows: auto auto auto auto 1fr; gap: 5px; padding: 9px; border: 1px solid #687479; background: #020606; color: #edf3f2; }
  header { display: flex; gap: 8px; align-items: center; font: 13px ui-monospace, monospace; } header b { color: #f4c35a; } header span { border: 1px solid #879397; padding: 2px 6px; }
  .frequency { color: #f7f8f5; font: clamp(28px, 4vw, 70px)/.95 ui-monospace, monospace; letter-spacing: -.08em; text-align: center; }
  .secondary { text-align: center; color: #afbdbe; font: 12px ui-monospace, monospace; }
</style>
