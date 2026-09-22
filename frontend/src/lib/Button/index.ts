/**
 * Control Button Library
 * 
 * Four button types for radio UI:
 * - DotButton: standard with colored dot indicator
 * - FillButton: filled with indicator color + glow
 * - HardwareButton: skeuomorphic with indicators
 * - HardwarePlainButton: skeuomorphic plain with warm glow
 */

export { default as ControlButton } from './ControlButton.svelte';
export { default as DotButton } from './DotButton.svelte';
export { default as FillButton } from './FillButton.svelte';
export { default as HardwareButton } from './HardwareButton.svelte';
export { default as HardwarePlainButton } from './HardwarePlainButton.svelte';
export { default as StatusIndicator } from './StatusIndicator.svelte';

export type {
  IndicatorColor,
  IndicatorStyle,
  GlowVariant,
  ButtonSurface,
  BaseButtonProps,
  DotButtonProps,
  FillButtonProps,
  HardwareButtonProps,
  HardwarePlainButtonProps,
} from './types';

export type { VisualFamily, RoleMapping } from './roleMapping';
export {
  MAPPING_DEFAULT,
  MAPPING_HARDWARE,
  MAPPING_MINIMAL,
  MAPPING_MIXED,
  PRESET_MAPPINGS,
} from './roleMapping';
