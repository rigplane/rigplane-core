import { t } from '$lib/i18n';
import { calibratedToSegments } from '../../components-v2/meters/smeter-scale';
import type {
  DisplayIndicator,
  DisplayOffset,
  DisplayTelemetry,
  DisplayValue,
  PeerSplitReceiverDisplay,
} from '../../semantic/radio-display-model';
import type { LcdAfFftInputState } from './LcdAfFft.svelte';

export interface FilterEnvelope {
  readonly points: string;
  readonly kind: 'single' | 'inner' | 'outer';
  readonly centerX: number;
}

// MOR-2650 (owner ruling 2026-09-26: no question marks anywhere in the
// interface): an unread value is unlit segments — the empty string — never
// '?'. An unsupported value is not drawn — also the empty string, never
// '—'. The caller's slot keeps its reserved box (min-width over the widest
// text the slot can render, tabular digits), so a first reading lights the
// slot without moving it. No invented value: unknown is not zero, OFF, or a
// default. `telemetryText` (MOR-2425/MOR-2540) already works this way and is
// reused as the treatment, not duplicated.
export function stateText<T>(field: DisplayValue<T>): string {
  return field.state === 'known' ? String(field.value) : '';
}

export function formatBandwidth(field: DisplayValue<number>): string {
  if (field.state !== 'known') return stateText(field);
  if (field.value >= 1000) return `${Number((field.value / 1000).toFixed(2))}k`;
  return String(Math.round(field.value));
}

export function formatOffset(field: DisplayOffset): string {
  if (field.state !== 'active' && field.state !== 'inactive') return '';
  if (field.offsetHz === undefined) return '';
  const sign = field.offsetHz < 0 ? '−' : '+';
  return `${sign}${(Math.abs(field.offsetHz) / 1000).toFixed(3)}`;
}

export function meterFill(field: DisplayValue<number>): number {
  return field.state === 'known'
    ? Math.max(0, Math.min(1, calibratedToSegments(field.value) / 20))
    : 0;
}

// MOR-2425 (R29/R32): a stale reading keeps its digits, same as a current
// one; only "never observed" and "idle" (not measuring in RX) collapse to
// an empty scale — no `?`/`STALE`/`IDLE` placeholder token. The accessible
// description below still names those two states, localized.
// MOR-2540 (owner ruling 2026-09-22): no `?` anywhere else either — the
// unsupported cases and the former ' ?' indeterminate-relevance cue are
// gone; an indeterminate reading keeps its digits, nothing more.
export function telemetryText(field: DisplayTelemetry): string {
  const tx = field.txDisplay;
  if (!tx) {
    if (field.state === 'known') return String(Number(field.value.toFixed(2)));
    return '';
  }
  if (!tx.supported) return '';
  if (tx.relevance === 'idle') return '';
  if (tx.observation.state !== 'current' && tx.observation.state !== 'stale') return '';
  return `${Number(tx.observation.value.toFixed(2))}`;
}

export function telemetryDescription(label: string, field: DisplayTelemetry): string {
  const tx = field.txDisplay;
  // MOR-2705 part 2: an accessible name names only what it is — the label.
  // An unsupported item is not drawn, so it has no accessible name; an
  // unread one is the label with no status word (never 'Unsupported' or
  // 'No reading'). The localized `idle` word and the `stale`/`current`
  // readings survive.
  if (!tx) {
    if (field.state === 'known') return `${label}: ${telemetryText(field)}`;
    return label;
  }
  if (!tx.supported) return label;
  if (tx.relevance === 'idle') return `${label}: ${t('core.meter.state.idle')}`;
  // An unread observation names only the label; the relevance cue (a
  // relevance modifier, not a status word) still applies for indeterminate.
  if (tx.observation.state !== 'stale' && tx.observation.state !== 'current')
    return tx.relevance === 'indeterminate' ? `${label}: RF relevance indeterminate.` : label;
  const cue = tx.relevance === 'indeterminate' ? 'RF relevance indeterminate. ' : '';
  return tx.observation.state === 'stale'
    ? `${label}: ${cue}Stale observation`
    : `${label}: ${cue}Current observation: ${Number(tx.observation.value.toFixed(2))}`;
}

function envelope(
  field: DisplayValue<number>,
  centerHz: number,
  kind: FilterEnvelope['kind'],
): FilterEnvelope | null {
  if (field.state !== 'known') return null;
  const width = 500;
  const height = 100;
  const top = 4;
  const bottom = height - 4;
  const center = Math.max(0, Math.min(width, width / 2 + (centerHz / 9000) * width));
  const half = Math.min(width * 0.45, (field.value / 9000) * width / 2);
  const slope = width * 0.08;
  return {
    kind,
    centerX: center,
    points: `${center - half - slope},${bottom} ${center - half},${top + 2} `
      + `${center + half},${top + 2} ${center + half + slope},${bottom}`,
  };
}

export function filterEnvelopes(receiver: PeerSplitReceiverDisplay): FilterEnvelope[] {
  const inner = receiver.pbtInnerHz;
  const outer = receiver.pbtOuterHz;
  if (inner.state === 'unknown' || outer.state === 'unknown') return [];
  if (inner.state === 'known' && outer.state === 'known' && inner.value !== outer.value) {
    return [
      envelope(receiver.bandwidthHz, inner.value, 'inner'),
      envelope(receiver.bandwidthHz, outer.value, 'outer'),
    ].filter((item): item is FilterEnvelope => item !== null);
  }
  if (receiver.ifShiftHz.state === 'unknown') return [];
  const centerHz = receiver.ifShiftHz.state === 'known' ? receiver.ifShiftHz.value : 0;
  const single = envelope(receiver.bandwidthHz, centerHz, 'single');
  return single ? [single] : [];
}

export function fftInputState(
  receiver: PeerSplitReceiverDisplay,
  normalizedBins: readonly number[] | undefined,
): LcdAfFftInputState {
  if (receiver.spectrum === 'unsupported') return 'unsupported';
  if (receiver.spectrum === 'unknown') return 'unknown';
  if (receiver.spectrum === 'inactive') return 'missing';
  return normalizedBins?.length ? 'live' : 'missing';
}

export function notchIndicators(
  field: DisplayValue<'off' | 'auto' | 'manual'>,
): { readonly notch: DisplayIndicator; readonly anf: DisplayIndicator } {
  if (field.state !== 'known') {
    return { notch: { state: field.state }, anf: { state: field.state } };
  }
  return {
    notch: { state: field.value === 'manual' ? 'active' : 'inactive' },
    anf: { state: field.value === 'auto' ? 'active' : 'inactive' },
  };
}
