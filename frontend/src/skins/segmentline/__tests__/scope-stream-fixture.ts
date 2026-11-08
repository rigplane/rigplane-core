import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
import { radio } from '$lib/stores/radio.svelte';
import type { ServerState } from '$lib/types/state';
import observedState from '$lib/runtime/adapters/__tests__/fixtures/ic7300-state.json';
import { ScopeStream } from '../../../lib/runtime/scope-stream';
export function installObservedAudioState(): () => void {
  const previous = radio.current;
  radio.current = structuredClone(observedState) as unknown as ServerState;
  return () => { radio.current = previous; };
}
export interface LcdSpectrumFrame {
  source: 'hardware' | 'audio-fft'; receiver: 'MAIN' | 'SUB'; freshness: 'fresh' | 'stale';
  startHz: number; endHz: number; pixels: Uint8Array | null;
}

/** Legacy test scenarios enter the consumer through the real binary parser. */
export function lcdStreamFixture(candidate: unknown): ScopeStream | null {
  if (candidate === null || typeof candidate !== 'object') return null;
  const frame = candidate as Partial<LcdSpectrumFrame>;
  if ((frame.source !== 'hardware' && frame.source !== 'audio-fft')
    || (frame.receiver !== 'MAIN' && frame.receiver !== 'SUB')
    || !(frame.pixels instanceof Uint8Array)
    || typeof frame.startHz !== 'number' || !Number.isFinite(frame.startHz)
    || typeof frame.endHz !== 'number' || !Number.isFinite(frame.endHz)) return null;
  let receive = (_buffer: ArrayBuffer) => {};
  const source = frame.source === 'hardware' ? 'radio' : 'audio_fft';
  const stream = new ScopeStream({ subscribe(sink) { receive = sink; return () => {}; }, close() {} },
    { source, sourceId: source === 'radio' ? 'scope' : 'audio-scope', epoch: 1 }, 'borrowed');
  const buffer = scopeWire({ source, sourceId: source === 'radio' ? 'scope' : 'audio-scope',
    receiver: frame.receiver === 'MAIN' ? 0 : 1,
    viewStartHz: frame.startHz + (source === 'audio_fft' ? 14_188_000 : 0),
    viewEndHz: frame.endHz + (source === 'audio_fft' ? 14_188_000 : 0), pixels: frame.pixels });
  receive(buffer);
  if (frame.freshness === 'stale') stream.setFreshness('stale');
  return stream;
}
