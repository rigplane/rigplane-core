import { t } from '$lib/i18n';
import type { RadioHealthPublic } from '$lib/types/state';

export interface RadioHealthLabelInput {
  /** `getRadioHealth()`; only the cause and the readiness are read. */
  health: Pick<RadioHealthPublic, 'likelyCause' | 'readiness'> | null;
  /** `getRigConnected()`. */
  rigConnected: boolean;
  /** `getRadioReady()`. */
  radioReady: boolean;
  /**
   * The link state the indicator shows: `getRadioLinkState()`, or
   * 'disconnected' while the radio is known to be powered off.
   */
  radioState: string;
}

/**
 * The localized reason the radio link is not healthy, or '' when no reason
 * applies. The text follows the active locale.
 */
export function deriveRadioHealthLabel(
  { health, rigConnected, radioReady, radioState }: RadioHealthLabelInput,
): string {
  switch (health?.likelyCause) {
    case 'radio_network_lost':
      return t('core.statusbar.health.radioLinkLost');
    case 'radio_not_responding':
      return health.readiness === 'delayed'
        ? t('core.statusbar.health.radioResponseDelayed')
        : t('core.statusbar.health.radioNotResponding');
    case 'radio_powered_off_likely':
      return t('core.statusbar.health.radioPoweredOff');
    case 'server_unreachable':
      return t('core.statusbar.health.serverUnreachable');
    case 'operator_disconnected':
      return t('core.statusbar.health.operatorDisconnected');
    case 'radio_session_busy':
      return t('core.statusbar.health.radioSessionBusy');
    default:
      // `connection.svelte.ts: radioLinkSteady` reads 'degraded' when the
      // transport is up but the rig or readiness fact is missing.
      if (!rigConnected && radioState === 'degraded') {
        return t('core.statusbar.health.rigOffline');
      }
      if (!radioReady && radioState === 'degraded') {
        return t('core.statusbar.health.radioNotReady');
      }
      return '';
  }
}
