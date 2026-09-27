/** Layout utility helpers for RadioLayout. */

export interface VfoStateProps {
  receiver: 'main' | 'sub';
  freq: number;
  mode: string;
  filter: string;
  sValue: number;
  isActive: boolean;
  badges: Record<string, boolean | string>;
  rit?: { active: boolean; offset: number };
}

/**
 * Returns true when radioState indicates audio capability is available.
 */
export function hasLiveAudioFromState(radioState: any): boolean {
  return radioState?.capabilities?.audio ?? false;
}
