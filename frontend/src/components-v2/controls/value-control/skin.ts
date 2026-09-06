/**
 * Skin abstraction for ValueControl renderers.
 *
 * A Skin provides Svelte component overrides for each renderer type.
 * When a skin is set on ValueControl, it delegates rendering to the
 * skin's component instead of the built-in renderer.
 */
import type { Component } from 'svelte';
import type { ContinuousScalarBinding } from '../../../primitives/scalar/continuous-scalar.svelte';
import type { LegacyReadingPresentation } from './scalar-render-presentation';

/** Binding and presentation inputs shared by every appearance renderer. */
export interface SkinRendererProps {
  binding: ContinuousScalarBinding;
  label: string;
  displayFn?: (v: number) => string;
  unknownDisplay?: string;
  accentColor?: string;
  fillColor?: string;
  fillGradient?: string[];
  trackColor?: string;
  showValue?: boolean;
  showLabel?: boolean;
  compact?: boolean;
  variant?: 'modern' | 'hardware' | 'hardware-illuminated';
  unit?: string;
  shortcutHint?: string | null;
  title?: string | null;
  legacy?: LegacyReadingPresentation;
}

/** Extra props for knob skin renderers. */
export interface KnobSkinRendererProps extends SkinRendererProps {
  arcAngle?: number;
  tickCount?: number;
  tickLabels?: string[];
}

/** Extra presentation inputs for discrete skin renderers. */
export interface DiscreteSkinRendererProps extends SkinRendererProps {
  tickLabels?: string[];
  showAllTicks?: boolean;
  tickStyle?: 'ruler' | 'led' | 'notch';
  dimmed?: boolean;
}

/** A skin provides component overrides per renderer type. */
export interface Skin {
  name: string;
  knob?: Component<KnobSkinRendererProps>;
  hbar?: Component<SkinRendererProps>;
  bipolar?: Component<SkinRendererProps>;
  discrete?: Component<DiscreteSkinRendererProps>;
}
