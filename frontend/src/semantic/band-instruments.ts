import type { Snippet } from 'svelte';
import { t } from '$lib/i18n';
import type { BandChoice } from './radio-view-model';

export interface BandInstrumentHandles {
  readonly bandChoice: Snippet;
  readonly frequencyEntry: Snippet;
}

export type BandControlLayout = Snippet<[BandInstrumentHandles]>;

export const UNKNOWN_TEXT = '—';

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

/** Decimal means exact MHz; a bare integer prefers kHz when both readings fit. */
export function interpretFrequencyEntry(raw: string, minHz: number, maxHz: number): number | null {
  const text = raw.trim();
  if (text === '' || !Number.isFinite(minHz) || !Number.isFinite(maxHz)) return null;
  if (text.includes('.')) return parseMhzToHz(text, minHz, maxHz);
  if (!/^\d+$/.test(text)) return null;
  const value = Number(text);
  const asKhz = value * 1000;
  if (asKhz >= minHz && asKhz <= maxHz) return asKhz;
  if (value >= minHz && value <= maxHz) return value;
  return null;
}

function parseMhzToHz(text: string, minHz: number, maxHz: number): number | null {
  const match = /^(\d+)\.(\d+)$/.exec(text);
  if (!match) return null;
  const [, whole, fraction] = match;
  if (fraction.length > 6) return null;
  const hz = Number(whole) * 1_000_000 + Number(fraction.padEnd(6, '0'));
  return hz >= minHz && hz <= maxHz ? hz : null;
}
