/**
 * Skin abstraction for ValueControl renderers.
 *
 * A Skin provides Svelte component overrides for each renderer type.
 * When a skin is set on ValueControl, it delegates rendering to the
 * skin's component instead of the built-in renderer.
 */
import type { Component } from 'svelte';
import type {
  ContinuousScalarBinding,
  ContinuousScalarView,
} from '../../../primitives/scalar/continuous-scalar.svelte';
import type { PoliteControlAnnouncement } from '../../../primitives/control-feedback/control-feedback-presentation';
import type {
  LegacyReadingPresentation,
  ScalarAccessibilityPresentation,
} from './scalar-render-presentation';

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
  accessibility?: ScalarAccessibilityPresentation;
  legacy?: LegacyReadingPresentation;
}

export interface HBarValueProjection {
  readonly contextKey: string;
  positionOf(value: number): number | null;
  valueAt(position: number): number | null;
}

export interface HBarIssuedStatusSnapshot {
  readonly view: Readonly<Extract<ContinuousScalarView, { evidence: 'command-feedback' }>>;
  readonly announcement: Readonly<PoliteControlAnnouncement>;
}

export interface HBarIssuedStatusPresentation {
  readonly text: string | null;
  format(snapshot: Readonly<HBarIssuedStatusSnapshot>): string;
  accept(text: string | null): void;
}

export interface HBarSkinRendererProps extends SkinRendererProps {
  valueProjection?: Readonly<HBarValueProjection>;
  issuedStatusPresentation?: Readonly<HBarIssuedStatusPresentation>;
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
  hbar?: Component<HBarSkinRendererProps>;
  bipolar?: Component<SkinRendererProps>;
  discrete?: Component<DiscreteSkinRendererProps>;
}
