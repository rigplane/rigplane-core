<script lang="ts">
  // MOR-4386: the panorama zoom cluster — a 2x2 square in the bottom-right
  // corner of the spectrum area: [−] [+] over [S] [B]. One shared component
  // for every skin and radio; it only forwards to the panel's existing
  // nudgeScopeZoom / applyScopePreset (no mechanism of its own).
  import { t } from '../../lib/i18n';
  import type { ScopeOverlayTargets, ScopePreset } from '../../lib/runtime/scope-presets';

  interface Props {
    targets: ScopeOverlayTargets;
    /** The pinned view: the key that opened it is lit, and pressing it again follows the VFO. */
    pinnedView: { startHz: number; endHz: number } | null;
    onZoom: (factor: number) => void;
    onPreset: (preset: ScopePreset) => void;
    onFollow: () => void;
  }
  let { targets, pinnedView, onZoom, onPreset, onFollow }: Props = $props();

  function isPinned(preset: ScopePreset | null): boolean {
    return preset !== null && pinnedView !== null && 'startHz' in preset.view
      && preset.view.startHz === pinnedView.startHz && preset.view.endHz === pinnedView.endHz;
  }
  let segmentPinned = $derived(isPinned(targets.segment));
  let bandPinned = $derived(isPinned(targets.band));
  let segmentName = $derived(targets.segment ? t(`core.scope.segment.${targets.segment.kind}`) : '');
  function press(preset: ScopePreset | null, pinned: boolean): void {
    if (preset === null) return;
    if (pinned) onFollow(); else onPreset(preset);
  }
</script>

<div class="zoom-overlay" data-testid="scope-zoom-overlay" role="group" aria-label={t('core.scope.zoomGroup')}>
  <button type="button" data-testid="scope-zoom-out" aria-label={t('core.scope.zoomOut')}
    title={t('core.scope.zoomOut')} onclick={() => onZoom(2)}>&#8722;</button>
  <button type="button" data-testid="scope-zoom-in" aria-label={t('core.scope.zoomIn')}
    title={t('core.scope.zoomIn')} onclick={() => onZoom(0.5)}>+</button>
  <button type="button" data-testid="scope-zoom-segment" data-segment-kind={targets.segment?.kind ?? 'none'}
    disabled={targets.segment === null} aria-pressed={segmentPinned}
    aria-label={targets.segment ? t('core.scope.zoomSegment', { segment: segmentName }) : t('core.scope.zoomSegmentNone')}
    title={targets.segment ? t('core.scope.zoomSegment', { segment: segmentName }) : t('core.scope.zoomSegmentNone')}
    onclick={() => press(targets.segment, segmentPinned)}>S</button>
  <button type="button" data-testid="scope-zoom-band" disabled={targets.band === null} aria-pressed={bandPinned}
    aria-label={targets.band ? t('core.scope.zoomBand', { band: targets.band.label }) : t('core.scope.zoomBandNone')}
    title={targets.band ? t('core.scope.zoomBand', { band: targets.band.label }) : t('core.scope.zoomBandNone')}
    onclick={() => press(targets.band, bandPinned)}>B</button>
</div>

<style>
  .zoom-overlay {
    position: absolute;
    right: 8px;
    bottom: 8px;
    z-index: 3;
    display: grid;
    grid-template-columns: repeat(2, 28px);
    grid-auto-rows: 28px;
    gap: 4px;
    opacity: 0.6;
    transition: opacity 120ms;
    /* The cluster's gaps stay transparent to drag-pan and click-to-tune;
       only the buttons take pointer events (the panel's drag start already
       ignores presses that land on a button). */
    pointer-events: none;
  }
  .zoom-overlay:hover, .zoom-overlay:focus-within { opacity: 1; }
  button {
    pointer-events: auto;
    padding: 0;
    font: 600 13px/1 var(--font-mono, monospace);
    color: var(--text);
    background: rgba(12, 16, 22, 0.72);
    border: 1px solid var(--panel-border);
    border-radius: var(--radius);
    cursor: pointer;
  }
  button:hover:not(:disabled) { background: rgba(32, 40, 52, 0.85); }
  button[aria-pressed='true'] { border-color: var(--accent, #4aa3ff); color: var(--accent, #4aa3ff); }
  button:disabled { opacity: 0.45; color: var(--text-muted); cursor: default; }
</style>
