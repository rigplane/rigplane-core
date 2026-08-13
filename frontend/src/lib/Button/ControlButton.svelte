<script lang="ts">
  import { untrack } from 'svelte';
  import type { IndicatorColor, IndicatorStyle, GlowVariant, ButtonSurface } from './types';
  // MOR-1536: the shared armed-state CSS seat — see the file's own doc
  // comment. Imported here (not by every caller) because this is the one
  // place `data-armed` is ever rendered onto the DOM.
  import './control-button.css';

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
    /** MOR-1519 — see `types.ts`'s `BaseButtonProps.armed` doc comment.
     *  Rendered as `data-armed` on THIS `<button>` element (never a
     *  wrapper) so both the CSS selector and any AT/test query can target
     *  the actual interactive element directly. */
    armed?: boolean;
    /** Pairs with a caller-rendered `.sr-only` announcement (MOR-1519). */
    describedBy?: string;
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
    armed = false,
    describedBy,
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
  data-armed={armed || undefined}
  aria-describedby={describedBy}
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
