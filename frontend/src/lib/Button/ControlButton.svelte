<script lang="ts">
  import { untrack } from 'svelte';
  import type { IndicatorColor, IndicatorStyle, GlowVariant, ButtonSurface } from './types';

  interface Props {
    active?: boolean;
    disabled?: boolean;
    compact?: boolean;
    surface?: ButtonSurface;
    indicatorStyle?: IndicatorStyle;
    indicatorColor?: IndicatorColor;
    glow?: GlowVariant;
    title?: string | null;
    shortcutHint?: string | null;
    onclick?: (event: MouseEvent) => void;
    onpointerdown?: (event: PointerEvent) => void;
    onpointerup?: (event: PointerEvent) => void;
    onpointercancel?: (event: PointerEvent) => void;
    onpointerleave?: (event: PointerEvent) => void;
    children?: any;
  }

  let {
    active = false,
    disabled = false,
    compact = false,
    surface = 'flat',
    indicatorStyle,
    indicatorColor,
    glow,
    title = null,
    shortcutHint = null,
    onclick,
    onpointerdown,
    onpointerup,
    onpointercancel,
    onpointerleave,
    children
  }: Props = $props();

  let localActive = $state(untrack(() => active));

  // Sync prop changes to local state
  $effect(() => {
    localActive = active;
  });

  function handleClick(event: MouseEvent) {
    if (disabled) return;
    onclick?.(event);
  }

  function handlePointerDown(event: PointerEvent) {
    if (disabled) return;
    onpointerdown?.(event);
  }

  function handlePointerUp(event: PointerEvent) {
    if (disabled) return;
    onpointerup?.(event);
  }

  function handlePointerCancel(event: PointerEvent) {
    if (disabled) return;
    onpointercancel?.(event);
  }

  function handlePointerLeave(event: PointerEvent) {
    if (disabled) return;
    onpointerleave?.(event);
  }

  // Compute glow attribute (only 'white' or 'warm', 'color' = omit)
  const glowAttr = $derived(glow && glow !== 'color' ? glow : undefined);
</script>

<button
  type="button"
  class="v2-control-button"
  class:v2-control-button--compact={compact}
  data-active={localActive}
  data-surface={surface !== 'flat' ? surface : undefined}
  data-indicator-style={indicatorStyle}
  data-indicator-color={indicatorColor}
  data-glow={glowAttr}
  title={title ?? shortcutHint ?? undefined}
  data-shortcut-hint={shortcutHint ?? undefined}
  {disabled}
  onclick={handleClick}
  onpointerdown={handlePointerDown}
  onpointerup={handlePointerUp}
  onpointercancel={handlePointerCancel}
  onpointerleave={handlePointerLeave}
>
  {@render children?.()}
</button>
