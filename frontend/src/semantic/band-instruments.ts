import type { Snippet } from 'svelte';
import { t } from '$lib/i18n';
import type { BandChoice } from './radio-view-model';

export interface BandInstrumentHandles {
  readonly bandChoice: Snippet;
}

export type BandControlLayout = Snippet<[BandInstrumentHandles]>;

export const mhz = (hz: number): string => `${(hz / 1e6).toFixed(3)} MHz`;

const DEFAULT_PERMIT_STATUS_KEY: Record<BandChoice['defaultHzTxPermit']['status'], string> = {
  allowed: 'core.band.tx.defaultPermit.status.allowed',
  denied: 'core.band.tx.defaultPermit.status.denied',
  unknown: 'core.band.tx.defaultPermit.status.unknown',
};

export const defaultPermitLabel = (choice: BandChoice): string =>
  t('core.band.tx.defaultPermit.label', {
    frequency: mhz(choice.defaultHz),
    status: t(DEFAULT_PERMIT_STATUS_KEY[choice.defaultHzTxPermit.status]),
  });
