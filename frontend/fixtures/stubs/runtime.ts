/** MOR-1070 stub for `$lib/runtime` — fixture state/caps, no transport. */
import {
  harness, type FixtureFrameAuthority, type FixtureFrameEvidence,
} from '../harness-state';
import type { ScopeController } from '$lib/runtime/scope-controller.svelte';
import type { RxAudioTargetSnapshot } from '$lib/stores/audio.svelte';

/**
 * MOR-1320: `SemanticRadioSurfaces.svelte` gained a `runtime.audio` /
 * `runtime.connectionAudio` read in the MOR-1279 rxAudio slice
 * (`rxAudioSnapshot`'s `muted`/`rxEnabled`/`volume`/`connected`) and this stub
 * had no such fields — a `TypeError: Cannot read properties of undefined
 * (reading 'muted')` at first render, one seam over from the command-bus
 * export gaps this ticket also fixes. MOR-1392 adds the fixture-level audio
 * axis, so this stub now reads the selected fixture's merged runtime facts
 * from `harness`; fixtures without overrides retain the same construction-
 * time defaults through `DEFAULT_AUDIO_RUNTIME` in `harness-state.ts`.
 */
/**
 * MOR-1312 (slice 12B): `SemanticRadioSurfaces` gained a
 * `runtime.defaultScopeStatus` / `runtime.scope.hardwareScopeConnected` /
 * `runtime.radioPowerOn` read (`scopeDisplaySnapshot`) — the same class of
 * gap MOR-1320 fixed for `runtime.audio` above. No fixture varies
 * scope-runtime state (`fixtures/catalog.ts` has no such override), so these
 * are fixed, honest "never observed" defaults, not reads from `harness`.
 */
const DEFAULT_SCOPE_STATUS = {
  source: null, available: false, resourceSelected: false, demand: 0,
  lifecycle: 'inactive' as const, transport: 'disconnected' as const, frameSeen: false,
};
const FIXTURE_CONTROL_SESSION = Object.freeze({ state: 'connected' as const, epoch: 1 });
type FixtureAuthoritySubscriber = (next: Readonly<{
  state: typeof harness.state;
  caps: typeof harness.caps;
  session: typeof FIXTURE_CONTROL_SESSION;
  rxAudioTarget: RxAudioTargetSnapshot;
}>) => void;
const fixtureAuthoritySubscribers = new Set<FixtureAuthoritySubscriber>();

type FixturePresentationResource = 'hardware-scope' | 'audio-fft';
type FixturePresentationLease = Readonly<{
  resource: FixturePresentationResource;
  sessionEpoch: 'fixture';
}>;
const fixturePresentationLeases = new Set<FixturePresentationLease>();

/**
 * Fixture-only compatibility seam for explicitly selected LCD frame sources.
 * It records lease identity for balanced release but intentionally owns no
 * producer, controller, channel, or transport.
 */
export const presentationResources = Object.freeze({
  acquire(resource: FixturePresentationResource, consumer: string): FixturePresentationLease {
    const lease = Object.freeze({ resource, sessionEpoch: 'fixture' as const });
    fixturePresentationLeases.add(lease);
    harness.presentationAcquires.push({ resource, consumer });
    return lease;
  },
  release(lease: FixturePresentationLease): boolean {
    return fixturePresentationLeases.delete(lease);
  },
});

/**
 * The real ScopeFrameHost owns selection and resolution. This fixture-local
 * controller only supplies its input contract: the authority set by that host
 * is mirrored back with a null envelope, disconnected transport, and no
 * demand. Thus an LCD source selection can be observed without inventing a
 * socket, controller, binary frame, zero-fill, or live waveform.
 */
const fixtureScope = {
  get hardwareScopeConnected() { return false; },
  subscribeFrameEvidence(_listener: () => void): () => void { return () => {}; },
  setFrameAuthority(authority: FixtureFrameAuthority | null): void {
    harness.frameAuthority = authority === null ? null : Object.freeze({ ...authority });
  },
  snapshotFrameEvidence() {
    const authority = harness.frameAuthority ?? {
      source: 'hardware' as const, receiver: null, providerGeneration: null,
    };
    const evidence: FixtureFrameEvidence = {
      envelope: null,
      authority: {
        ...authority,
      },
      transportEpoch: null,
      demanded: false,
      transport: 'disconnected',
      nowMonotonic: 0,
    };
    harness.frameEvidence = Object.freeze({
      ...evidence,
      authority: Object.freeze({ ...evidence.authority }),
    });
    return Object.freeze({
      envelope: evidence.envelope,
      authority: Object.freeze({
        ...evidence.authority,
        transportEpoch: evidence.transportEpoch,
        demanded: evidence.demanded,
        transport: evidence.transport,
        nowMonotonic: evidence.nowMonotonic,
      }),
    });
  },
};

export const runtime = {
  get state() { return harness.state; },
  get caps() { return harness.caps; },
  // The fixture mounts one already-current state/caps snapshot for its whole
  // page lifetime. This is its fixed authority boundary, not a socket or a
  // transition producer; scope-frame transport remains separately absent.
  get controlSession() { return FIXTURE_CONTROL_SESSION; },
  subscribeControlSession(_handler: (next: typeof FIXTURE_CONTROL_SESSION) => void) {
    return () => {};
  },
  subscribeControlAuthority(handler: FixtureAuthoritySubscriber) {
    fixtureAuthoritySubscribers.add(handler);
    handler(Object.freeze({
      state: harness.state,
      caps: harness.caps,
      session: FIXTURE_CONTROL_SESSION,
      rxAudioTarget: Object.freeze({
        muted: harness.audioRuntime.muted,
        rxEnabled: harness.audioRuntime.rxEnabled,
      }),
    }));
    return () => { fixtureAuthoritySubscribers.delete(handler); };
  },
  get audio() {
    const { rxEnabled, volume, muted } = harness.audioRuntime;
    return { rxEnabled, volume, muted };
  },
  get connectionAudio() { return harness.audioRuntime.connectionAudio; },
  get defaultScopeStatus() { return DEFAULT_SCOPE_STATUS; },
  get radioPowerOn() { return null; },
  // ScopeFrameHost takes the concrete production controller type. This
  // fixture object implements only the three evidence methods it consumes;
  // the cast remains at the offline seam rather than teaching production code
  // about fixture state.
  get scope() { return fixtureScope as unknown as ScopeController; },
};
