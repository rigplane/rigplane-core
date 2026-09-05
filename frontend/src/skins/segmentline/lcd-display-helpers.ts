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

export function stateText<T>(field: DisplayValue<T>): string {
  return field.state === 'known' ? String(field.value) : field.state === 'unknown' ? '?' : '—';
}

export function formatBandwidth(field: DisplayValue<number>): string {
  if (field.state !== 'known') return stateText(field);
  if (field.value >= 1000) return `${Number((field.value / 1000).toFixed(2))}k`;
  return String(Math.round(field.value));
}

export function formatOffset(field: DisplayOffset): string {
  if (field.state !== 'active' && field.state !== 'inactive') {
    return field.state === 'unknown' ? '?' : '—';
  }
  if (field.offsetHz === undefined) return '—';
  const sign = field.offsetHz < 0 ? '−' : '+';
  return `${sign}${(Math.abs(field.offsetHz) / 1000).toFixed(3)}`;
}

export function meterFill(field: DisplayValue<number>): number {
  return field.state === 'known'
    ? Math.max(0, Math.min(1, calibratedToSegments(field.value) / 20))
    : 0;
}

export function telemetryText(field: DisplayTelemetry): string {
  const tx = field.txDisplay;
  if (!tx) return field.state === 'known' ? String(Number(field.value.toFixed(2))) : '?';
  if (!tx.supported) return '?';
  if (tx.relevance === 'idle') return 'IDLE';
  if (tx.observation.state === 'stale') return 'STALE';
  if (tx.observation.state !== 'current') return '?';
  return `${Number(tx.observation.value.toFixed(2))}${tx.relevance === 'indeterminate' ? ' ?' : ''}`;
}

export function telemetryDescription(label: string, field: DisplayTelemetry): string {
  const tx = field.txDisplay;
  if (!tx) return `${label}: ${field.state === 'known' ? telemetryText(field)
    : field.state === 'unsupported' ? 'Unsupported' : 'Not observed'}`;
  if (!tx.supported) return `${label}: Unsupported`;
  if (tx.relevance === 'idle') return `${label}: Not measuring in RX`;
  const cue = tx.relevance === 'indeterminate' ? 'RF relevance indeterminate. ' : '';
  return `${label}: ${cue}${tx.observation.state === 'stale' ? 'Stale observation'
    : tx.observation.state === 'current' ? `Current observation: ${Number(tx.observation.value.toFixed(2))}` : 'Not observed'}`;
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
