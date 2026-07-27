import type { Capabilities, VfoScheme } from '$lib/types/capabilities';

export type ReceiverId = 'MAIN' | 'SUB';
export type VfoSlotId = 'A' | 'B';
export type ScopeSource = 'hardware' | 'audio_fft';
export type CapabilityDiagnostic =
  | 'invalid-topology' | 'malformed-capability-tags'
  | 'dual-rx-contradiction'
  | 'dual-rx-unavailable'
  | 'scope-capability-contradiction'
  | 'audio-capability-contradiction'
  | 'malformed-audio-fft' | 'audio-fft-without-audio' | 'invalid-scope-source';
export interface ReceiverTopology {
  scheme: VfoScheme;
  structuralCount: 1 | 2;
  structuralReceivers: readonly ReceiverId[];
  operationalReceivers: readonly ReceiverId[];
  slots: Readonly<Partial<Record<ReceiverId, readonly VfoSlotId[] | null>>>;
}
export interface PresentationCapabilities {
  topology: ReceiverTopology | null;
  scope: {
    hardwareScopeAvailable: boolean;
    audioFftAvailable: boolean;
    availableSources: readonly ScopeSource[];
    defaultSource: ScopeSource | null;
  };
  diagnostics: readonly CapabilityDiagnostic[];
}
export function derivePresentationCapabilities(caps: Capabilities): PresentationCapabilities {
  const diagnostics: CapabilityDiagnostic[] = [];
  const validTags = Array.isArray(caps.capabilities)
    && caps.capabilities.every((tag) => typeof tag === 'string');
  const tags = new Set(validTags ? caps.capabilities : []);
  if (!validTags) diagnostics.push('malformed-capability-tags');

  const expectedCount = caps.vfoScheme === 'single' || caps.vfoScheme === 'ab' ? 1
    : caps.vfoScheme === 'ab_shared' || caps.vfoScheme === 'main_sub' ? 2 : null;
  let topology: ReceiverTopology | null = null;
  if (expectedCount === null || caps.receivers !== expectedCount) {
    diagnostics.push('invalid-topology');
  } else {
    const dual = tags.has('dual_rx');
    const structuralReceivers: ReceiverId[] = expectedCount === 2 ? ['MAIN', 'SUB'] : ['MAIN'];
    const slots = caps.vfoScheme === 'ab'
      ? { MAIN: ['A', 'B'] as const }
      : caps.vfoScheme === 'main_sub'
        ? { MAIN: ['A', 'B'] as const, SUB: ['A', 'B'] as const }
        : expectedCount === 2 ? { MAIN: null, SUB: null } : { MAIN: null };
    let operationalReceivers = structuralReceivers;
    if (expectedCount === 1 && dual) diagnostics.push('dual-rx-contradiction');
    if (expectedCount === 2 && !dual) {
      diagnostics.push('dual-rx-unavailable');
      operationalReceivers = ['MAIN'];
    }
    topology = {
      scheme: caps.vfoScheme, structuralCount: expectedCount,
      structuralReceivers, operationalReceivers, slots,
    };
  }

  function agreed(value: unknown, tag: string, diagnostic: CapabilityDiagnostic): boolean {
    const tagPresent = tags.has(tag);
    if (typeof value !== 'boolean' || value !== tagPresent) {
      diagnostics.push(diagnostic);
      return false;
    }
    return value;
  }

  const hardwareScopeAvailable = agreed(
    caps.scope, 'scope', 'scope-capability-contradiction',
  );
  const runtimeAudio = agreed(caps.audio, 'audio', 'audio-capability-contradiction');
  const rawFft = caps.audioFftAvailable ?? false;
  if (typeof rawFft !== 'boolean') diagnostics.push('malformed-audio-fft');
  if (rawFft === true && !runtimeAudio) diagnostics.push('audio-fft-without-audio');
  const audioFftAvailable = rawFft === true && runtimeAudio;
  const availableSources: ScopeSource[] = [
    ...(hardwareScopeAvailable ? ['hardware' as const] : []),
    ...(audioFftAvailable ? ['audio_fft' as const] : []),
  ];
  const source = caps.scopeSource;
  const defaultSource = (source === 'hardware' && hardwareScopeAvailable)
    || (source === 'audio_fft' && audioFftAvailable) ? source : null;
  if (source != null && defaultSource === null) diagnostics.push('invalid-scope-source');

  return {
    topology,
    scope: { hardwareScopeAvailable, audioFftAvailable, availableSources, defaultSource },
    diagnostics,
  };
}
