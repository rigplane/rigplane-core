<!--
  Scope source badge (MOR-3158) — the small toolbar readout naming WHICH
  source feeds the central spectrum: RIG (hardware scope), AUDIO
  (audio FFT) or SDR (SoapySDR IQ panadapter). Pure props, zero runtime
  imports: the spectrum region passes `scopeSource` and, for the SDR
  source, the public `sdr` state leaf so the badge can colour itself by
  the SDR lifecycle (streaming / reconnecting / error) and carry
  `lastError` in its tooltip.

  Zero focusable elements, by construction — a source readout has no
  action (the mounting canon of `semantic/ScopeDisplaySurface.svelte`,
  MOR-1069). Colour is never the only state channel: `data-sdr-state`
  and the tooltip text carry the same facts machine- and human-readably.
-->
<script module lang="ts">
  import type { ScopeSourceId, SdrPublicState, SdrSourceState } from './sdr-contract';

  export const SCOPE_SOURCE_LABELS: Readonly<Record<ScopeSourceId, string>> = {
    hardware: 'RIG',
    audio_fft: 'AUDIO',
    sdr: 'SDR',
  };

  export function scopeSourceLabel(source: ScopeSourceId): string {
    return SCOPE_SOURCE_LABELS[source];
  }

  /** Three-tone SDR lifecycle classification, mirroring `indicatorTone`'s
   *  conventions (`components-v2/layout/StatusBar.svelte`) — reproduced,
   *  not imported, because the SDR state union is not the scope-health
   *  union and the mapping is SDR-specific. */
  export type SdrTone = 'green' | 'yellow' | 'red' | 'neutral';
  export function sdrStateTone(state: SdrSourceState): SdrTone {
    switch (state) {
      case 'streaming': return 'green';
      case 'starting':
      case 'reconnecting': return 'yellow';
      case 'error': return 'red';
      default: return 'neutral';
    }
  }

  /** The badge's tooltip/accessible-name text: the source, its SDR
   *  lifecycle state, and `lastError` when the payload reports one. */
  export function scopeSourceBadgeText(
    source: ScopeSourceId,
    sdr: SdrPublicState | null,
  ): string {
    const label = scopeSourceLabel(source);
    if (source !== 'sdr' || sdr === null) return `Scope source: ${label}`;
    const parts = [`${label} ${sdr.state}`];
    if (sdr.lastError !== null && sdr.lastError !== '') parts.push(sdr.lastError);
    return `Scope source: ${parts.join(' — ')}`;
  }
</script>

<script lang="ts">
  interface Props {
    source: ScopeSourceId | null;
    sdr?: SdrPublicState | null;
  }
  let { source, sdr = null }: Props = $props();

  let tone = $derived(source === 'sdr' && sdr !== null ? sdrStateTone(sdr.state) : 'neutral');
  let text = $derived(source !== null ? scopeSourceBadgeText(source, sdr) : '');
</script>

{#if source !== null}
  <span
    class="scope-source-badge"
    data-testid="scope-source-badge"
    data-source={source}
    data-tone={tone}
    data-sdr-state={source === 'sdr' && sdr !== null ? sdr.state : undefined}
    role="status"
    aria-label={text}
    title={text}
  >{scopeSourceLabel(source)}</span>
{/if}

<style>
  /* Structure only — colour comes from the shared accent tokens so every
     theme/design language restyles the badge without a per-skin rule. */
  .scope-source-badge {
    display: inline-flex;
    align-items: center;
    padding: 1px 6px;
    border: 1px solid var(--panel-border);
    border-radius: 3px;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.05em;
    white-space: nowrap;
    color: var(--v2-text-secondary, inherit);
  }

  .scope-source-badge[data-tone='green'] {
    color: var(--v2-accent-green, #22c55e);
    border-color: color-mix(in srgb, var(--v2-accent-green, #22c55e) 45%, transparent);
  }

  .scope-source-badge[data-tone='yellow'] {
    color: var(--v2-accent-yellow, #eab308);
    border-color: color-mix(in srgb, var(--v2-accent-yellow, #eab308) 45%, transparent);
  }

  .scope-source-badge[data-tone='red'] {
    color: var(--v2-accent-red, #ef4444);
    border-color: color-mix(in srgb, var(--v2-accent-red, #ef4444) 45%, transparent);
  }
</style>
