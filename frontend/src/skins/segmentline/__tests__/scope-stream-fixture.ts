import { ScopeStream } from '../../../lib/runtime/scope-stream';
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
  const buffer = new ArrayBuffer(16 + frame.pixels.length);
  const view = new DataView(buffer);
  view.setUint8(0, 1); view.setUint8(1, frame.receiver === 'MAIN' ? 0 : 1);
  view.setUint32(3, frame.startHz, true); view.setUint32(7, frame.endHz, true);
  view.setUint16(14, frame.pixels.length, true);
  new Uint8Array(buffer, 16).set(frame.pixels);
  receive(buffer);
  if (frame.freshness === 'stale') stream.setFreshness('stale');
  return stream;
}
