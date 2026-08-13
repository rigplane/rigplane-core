/**
 * Control Button Types
 */

export type IndicatorColor = 'cyan' | 'green' | 'amber' | 'red' | 'orange' | 'white' | 'yellow' | 'muted' | 'gray';

export type IndicatorStyle = 'ring' | 'dot' | 'edge-bottom' | 'edge-left' | 'fill';

export type GlowVariant = 'color' | 'white' | 'warm';

export type ButtonSurface = 'flat' | 'hardware';

export interface BaseButtonProps {
  /** Button label */
  label?: string;
  /** Active state */
  active?: boolean;
  /** Disabled state */
  disabled?: boolean;
  /** Compact size variant */
  compact?: boolean;
  /** Tooltip / accessible title */
  title?: string | null;
  /** Shortcut hint rendered as data-shortcut-hint attribute */
  shortcutHint?: string | null;
  /** Generic armed/pending signal (MOR-1519) — renders as `data-armed='true'`
   *  on the underlying `<button>` element (never on a wrapper — a wrapper
   *  can't be targeted by an attribute selector, and CSS `font-style` on a
   *  wrapper is silently beaten by the UA button stylesheet). See the
   *  ARMED-SIGNAL CONTRACT in `lib/runtime/adapters/panel-adapters.ts`. */
  armed?: boolean;
  /** `aria-describedby` passthrough — pairs with a `.sr-only` announcement
   *  element the caller renders (same pattern as `DspSurface.svelte`'s
   *  pending-toggle announcement). */
  describedBy?: string;
  /** Click handler */
  onclick?: (event: MouseEvent) => void;
  /** Pointer event handlers */
  onpointerdown?: (event: PointerEvent) => void;
  onpointerup?: (event: PointerEvent) => void;
  onpointercancel?: (event: PointerEvent) => void;
  onpointerleave?: (event: PointerEvent) => void;
}

export interface DotButtonProps extends BaseButtonProps {
  /** Indicator color */
  color?: IndicatorColor;
  /** Glow variant (defaults to 'color') */
  glow?: GlowVariant;
}

export interface FillButtonProps extends BaseButtonProps {
  /** Fill color */
  color?: IndicatorColor;
}

export interface HardwareButtonProps extends BaseButtonProps {
  /** Indicator style (dot, edge-left, edge-bottom) */
  indicator?: 'dot' | 'edge-left' | 'edge-bottom';
  /** Indicator color */
  color?: IndicatorColor;
}

export interface HardwarePlainButtonProps extends BaseButtonProps {
  /** Glow variant (defaults to 'warm') */
  glow?: 'white' | 'warm';
}
