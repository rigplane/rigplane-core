/**
 * Skin registry for ValueControl.
 *
 * Import a skin by name:
 *   import { professionalSkin } from './skins';
 *   <ValueControl skin={professionalSkin} ... />
 */
import type { Skin } from '../skin';
import ProfessionalKnob from './ProfessionalKnob.svelte';

export const professionalSkin: Skin = {
  name: 'professional',
  knob: ProfessionalKnob,
};

/** All registered skins keyed by name. */
export const skins: Record<string, Skin> = {
  professional: professionalSkin,
};
