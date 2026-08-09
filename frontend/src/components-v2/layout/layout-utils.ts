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
 * Extract VFO state props for VfoPanel from the raw radioState object.
 * Falls back to safe defaults when the state is missing or partial.
 */
export function extractVfoState(radioState: any, receiver: 'main' | 'sub'): VfoStateProps {
  const vfo = radioState?.[receiver] ?? {};
  const activeReceiver = radioState?.activeReceiver ?? 'main';

  return {
    receiver,
    freq: vfo.freq ?? 14074000,
    mode: vfo.mode ?? 'USB',
    filter: vfo.filter ?? 'FIL1',
    sValue: vfo.sValue ?? 0,
    isActive: activeReceiver === receiver,
    badges: vfo.badges ?? {},
    rit: vfo.rit,
  };
}

/**
 * Extract meter props from radioState for MeterPanel.
 */
export function extractMeterState(radioState: any) {
  return {
    sValue: radioState?.main?.sValue ?? radioState?.main?.sMeter ?? 0,
    rfPower: radioState?.powerMeter ?? radioState?.tx?.rfPower ?? 0,
    swr: radioState?.swrMeter ?? radioState?.tx?.swr ?? 0,
    alc: radioState?.alcMeter ?? radioState?.tx?.alc ?? 0,
    txActive: radioState?.txActive ?? radioState?.ptt ?? false,
  };
}

/**
 * Returns true when radioState indicates audio capability is available.
 */
export function hasLiveAudioFromState(radioState: any): boolean {
  return radioState?.capabilities?.audio ?? false;
}
