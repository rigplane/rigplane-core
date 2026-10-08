/**
 * Mirror of MOR-3157 contract; replace with generated types when it lands.
 *
 * MOR-3157 will publish the SDR scope-source payload through the generated
 * frontend contracts (`lib/types/state.ts` server block, the capabilities
 * type) the same way MOR-881 publishes the public radio state. Until it
 * lands, this file is the single local mirror the SDR UI slices read, so
 * the swap is one import path per consumer. Do not re-mirror these shapes
 * anywhere else.
 */

/** `scopeSource` of the info payload: which source feeds the central spectrum. */
export type ScopeSourceId = 'hardware' | 'audio_fft' | 'sdr';

/** `sdr.state` values of the public `sdr` state leaf. */
export const SDR_SOURCE_STATES = [
  'disabled', 'starting', 'streaming', 'reconnecting', 'error',
] as const;
export type SdrSourceState = (typeof SDR_SOURCE_STATES)[number];

/** Public `sdr` state leaf (MOR-3157). */
export interface SdrPublicState {
  state: SdrSourceState;
  device: string | null;
  sampleRateHz: number | null;
  spanHz: number | null;
  txFrozen: boolean;
  overflowCount: number;
  lastError: string | null;
}

/** `sdrAvailable` of the info payload, read off the capabilities object. */
export interface SdrCapabilitiesCarrier {
  sdrAvailable?: boolean;
}

/**
 * The merged public state carries the `sdr` leaf once MOR-3157's server
 * sends it; the generated `ServerStatePublic` cannot name it yet, so the
 * reader narrows through this carrier instead of editing the generated
 * type. An absent, null, or malformed leaf reads as unknown (`null`) —
 * never a fabricated default.
 */
export function readSdrState(state: unknown): SdrPublicState | null {
  const leaf = (state as { sdr?: unknown } | null | undefined)?.sdr;
  if (leaf === null || leaf === undefined || typeof leaf !== 'object') return null;
  const candidate = leaf as { state?: unknown };
  return typeof candidate.state === 'string'
    && (SDR_SOURCE_STATES as readonly string[]).includes(candidate.state)
    ? leaf as SdrPublicState
    : null;
}

/** True only when the info payload explicitly advertises the SDR source. */
export function readSdrAvailable(caps: unknown): boolean {
  return (caps as SdrCapabilitiesCarrier | null | undefined)?.sdrAvailable === true;
}
