import type { Snippet } from 'svelte';
import { t } from '$lib/i18n';
import type { BandChoice } from './radio-view-model';

export interface BandInstrumentHandles {
  readonly bandChoice: Snippet<[compact?: boolean]>;
  readonly frequencyEntry: Snippet;
  readonly cancelFrequencyEntry: () => void;
}

export type BandControlLayout = Snippet<[BandInstrumentHandles]>;

export const mhz = (hz: number): string => `${(hz / 1e6).toFixed(3)} MHz`;

const DEFAULT_PERMIT_STATUS_KEY: Record<'allowed' | 'denied', string> = {
  allowed: 'core.band.tx.defaultPermit.status.allowed',
  denied: 'core.band.tx.defaultPermit.status.denied',
};

/** MOR-2684: every status word the permit caption can render across the
 *  bundled catalogs — en-US, ru-RU, and ja-JP each define the pair (MOR-2717
 *  translated the ja-JP pair).
 *  The caption's status slot is reserved at the widest of these, MEASURED
 *  in the caption's own font: the caption inherits the ambient font, which
 *  is not monospace, so a `ch` reservation would not be exact. Pinned
 *  against catalog drift by the MOR-2684 coverage test in
 *  `BandSurface.test.ts`. */
export const PERMIT_STATUS_TEXTS: readonly string[] = [
  'allowed', 'denied', 'разрешён', 'запрещён', '許可', '禁止',
];

export const defaultPermitLabel = (choice: BandChoice): string => {
  const frequency = mhz(choice.defaultHz);
  const permit = choice.defaultHzTxPermit;
  if (permit.status === 'unknown') {
    return t('core.band.tx.defaultPermit.unread', { frequency });
  }
  return t('core.band.tx.defaultPermit.label', {
    frequency,
    status: t(DEFAULT_PERMIT_STATUS_KEY[permit.status]),
  });
};

/** The caption's text BEFORE the reserved status slot, ending with the
 *  `': '` separator (2026-09-27 known-state ruling): the separator stays in
 *  the catalog-derived text node, never at the start of the inline-block
 *  status span, whose leading space a browser drops. Unread appends one
 *  space to the unread sentence so the text node keeps the SAME width as
 *  the known prefix (en/ru: the label is exactly the unread sentence plus
 *  `' '` plus the status word); the status slot then arrives into a
 *  reserved, equally wide box. */
export const defaultPermitPrefix = (choice: BandChoice): string => {
  const frequency = mhz(choice.defaultHz);
  const permit = choice.defaultHzTxPermit;
  if (permit.status === 'unknown') {
    return `${t('core.band.tx.defaultPermit.unread', { frequency })} `;
  }
  return t('core.band.tx.defaultPermit.label', { frequency, status: '' });
};

/** The caption's status word: `''` while unread — an unlit slot, never a
 *  dash or an invented verdict. */
export const defaultPermitStatus = (choice: BandChoice): string => {
  const permit = choice.defaultHzTxPermit;
  return permit.status === 'unknown'
    ? '' : t(DEFAULT_PERMIT_STATUS_KEY[permit.status]);
};

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
