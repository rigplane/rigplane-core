<script lang="ts">
  import { untrack } from 'svelte';
  import type { IndicatorColor, IndicatorStyle, GlowVariant, ButtonSurface } from './types';
  // MOR-1536: the shared armed-state CSS seat — see the file's own doc
  // comment. Imported here (not by every caller) because this is the one
  // place `data-armed` is ever rendered onto the DOM.
  // MOR-1541: renamed from `control-button.css` — that basename collided
  // with `components-v2/controls/control-button.css` (a different file,
  // the `.v2-control-button` base-style sheet), which made the two easy to
  // confuse by name alone.
  import './control-button-armed.css';

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
    ariaLabel?: string;
    ariaExpanded?: boolean;
    ariaControls?: string;
    /** Widget semantics the presets never needed (MOR-2509): a bridge key
     *  renders as `role="switch"`/`role="radio"` with a tri-state
     *  `aria-checked`, none of which the flat `aria-pressed`-style props
     *  above can express. */
    role?: string;
    ariaChecked?: boolean | 'true' | 'false' | 'mixed';
    tabindex?: number;
    onkeydown?: (event: KeyboardEvent) => void;
    /** Data attributes rendered verbatim on the `<button>` (undefined and
     *  null members omitted), so pinned `data-*` hooks stay on the
     *  interactive element itself. */
    data?: Record<string, string | number | boolean | undefined | null>;
    /** Reserves the dot indicator's padding slot without painting a dot,
     *  so lamp and lampless keys in one row keep the same label axis. */
    reserveIndicator?: boolean;
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
    ariaLabel,
    ariaExpanded,
    ariaControls,
    role,
    ariaChecked,
    tabindex,
    onkeydown,
    data,
    reserveIndicator = false,
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
  data-reserve-indicator={reserveIndicator || undefined}
  role={role}
  aria-checked={ariaChecked}
  tabindex={tabindex}
  aria-describedby={describedBy}
  aria-label={ariaLabel}
  aria-expanded={ariaExpanded}
  aria-controls={ariaControls}
  title={title ?? shortcutHint ?? undefined}
  data-shortcut-hint={shortcutHint ?? undefined}
  {disabled}
  onclick={handleClick}
  onkeydown={onkeydown}
  onpointerdown={handlePointerDown}
  onpointerup={handlePointerUp}
  onpointercancel={handlePointerCancel}
  onpointerleave={handlePointerLeave}
  {...Object.fromEntries(Object.entries(data ?? {}).filter(([, value]) => value !== undefined && value !== null))}
>
  {@render children?.()}
</button>
