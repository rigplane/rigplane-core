import type { Capabilities, ControlRange } from '../types/capabilities';

// Capabilities fetched once from GET /api/v1/capabilities
let capabilities = $state<Capabilities | null>(null);

const STATE_CONTRACT_VERSION = 1;

function validProviderGeneration(value: unknown): value is number {
  return typeof value === 'number'
    && Number.isSafeInteger(value)
    && value >= 0;
}

function hasCurrentEpoch(value: unknown): value is Capabilities {
  if (!value || typeof value !== 'object') return false;
  const record = value as Record<string, unknown>;
  return record.stateContractVersion === STATE_CONTRACT_VERSION
    && validProviderGeneration(record.providerGeneration);
}

export function getCapabilities(): Capabilities | null {
  return capabilities;
}

export function setCapabilities(caps: Capabilities): boolean {
  if (!hasCurrentEpoch(caps)) {
    capabilities = null;
    return false;
  }
  capabilities = caps;
  return true;
}

/** Clear capability truth whenever the provider epoch is no longer proven. */
export function clearCapabilities(): void {
  capabilities = null;
}

/** True only when capabilities and radio state prove the same provider epoch. */
export function capabilitiesMatchGeneration(providerGeneration: unknown): boolean {
  return validProviderGeneration(providerGeneration)
    && capabilities !== null
    && (capabilities as Record<string, unknown>).stateContractVersion === STATE_CONTRACT_VERSION
    && (capabilities as Record<string, unknown>).providerGeneration === providerGeneration;
}

export function hasSpectrum(): boolean {
  return capabilities?.scope ?? false;
}

export function hasAnyScope(): boolean {
  // True if hardware scope OR audio FFT scope available
  return (capabilities?.scope ?? false) || getScopeSource() === 'audio_fft';
}

export function getScopeSource(): string | null {
  return capabilities?.scopeSource ?? null;
}

export function isAudioFftScope(): boolean {
  return getScopeSource() === 'audio_fft';
}

export function hasAudioFft(): boolean {
  return capabilities?.audioFftAvailable ?? false;
}

export function hasAudio(): boolean {
  return capabilities?.audio ?? false;
}

export function hasDualReceiver(): boolean {
  return capabilities?.capabilities.includes('dual_rx') ?? false;
}

export function hasTx(): boolean {
  return capabilities?.tx ?? false;
}

export function getSupportedModes(): string[] {
  return capabilities?.modes ?? [];
}

export function getSupportedFilters(): string[] {
  return capabilities?.filters ?? [];
}

export function getKeyboardConfig() {
  return capabilities?.keyboard ?? null;
}

export function getAttValues(): number[] {
  return capabilities?.attValues ?? [0, 6, 12, 18];
}

export function getAttLabels(): Record<string, string> {
  return capabilities?.attLabels ?? {};
}

export function getPreValues(): number[] {
  return capabilities?.preValues ?? [0, 1];
}

export function getPreLabels(): Record<string, string> {
  return capabilities?.preLabels ?? {};
}

export function getAgcModes(): number[] {
  return capabilities?.agcModes ?? [1, 2, 3];
}

export function getAgcLabels(): Record<string, string> {
  return capabilities?.agcLabels ?? { "1": "FAST", "2": "MID", "3": "SLOW" };
}

export function getAntennaCount(): number {
  return capabilities?.antennas ?? 1;
}

export function getVfoScheme(): string {
  return capabilities?.vfoScheme ?? 'main_sub';
}

export function hasCapability(name: string): boolean {
  return capabilities?.capabilities.includes(name) ?? false;
}

/**
 * Label for a receiver (MAIN / SUB).
 *
 * Use when the UI wants the *receiver* name — e.g. dual-RX panels that
 * show which physical receiver is active. Independent of VFO slot: a
 * single receiver can tune VFO A *or* VFO B.
 */
export function receiverLabel(id: 'MAIN' | 'SUB'): string {
  return id;
}

/**
 * Label for a VFO slot (VFO A / VFO B).
 *
 * Use when the UI wants the *VFO slot* name — e.g. split indicators,
 * A/B swap buttons. Independent of receiver: MAIN receiver has both
 * VFO A and VFO B on radios like IC-7610 / IC-9700.
 */
export function vfoSlotLabel(slot: 'A' | 'B'): string {
  return slot === 'A' ? 'VFO A' : 'VFO B';
}

let _vfoLabelWarned = false;

/**
 * @deprecated Conflates receiver identity with VFO slot. Use
 *   {@link receiverLabel} for MAIN/SUB or {@link vfoSlotLabel} for VFO A/B.
 *   Scheduled for removal one minor version after 0.16.
 */
export function vfoLabel(slot: 'A' | 'B'): string {
  if (!_vfoLabelWarned) {
    _vfoLabelWarned = true;
    // eslint-disable-next-line no-console
    console.warn('[deprecated] vfoLabel(...) — use receiverLabel/vfoSlotLabel');
  }
  const scheme = capabilities?.vfoScheme ?? 'main_sub';
  if (scheme === 'ab') return slot === 'A' ? 'VFO A' : 'VFO B';
  return slot === 'A' ? 'MAIN' : 'SUB';
}

export function getControlRange(name: string): ControlRange | null {
  return capabilities?.controls?.[name] ?? null;
}

export function getMeterCalibration(
  meterType: string,
): { raw: number; actual: number; label: string }[] | null {
  return capabilities?.meterCalibrations?.[meterType] ?? null;
}

export function getMeterRedline(meterType: string): number | null {
  return capabilities?.meterRedlines?.[meterType] ?? null;
}

export function getSmeterCalibration(): { raw: number; actual: number; label: string }[] | null {
  return getMeterCalibration('s_meter');
}

export function getSmeterRedline(): number | null {
  return getMeterRedline('s_meter');
}
