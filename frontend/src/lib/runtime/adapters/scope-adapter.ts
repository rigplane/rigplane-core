/** Runtime-owned decoder for binary audio-scope frames. */

export interface ScopeFrame {
  readonly receiver: number;
  readonly mode: number;
  readonly startFreq: number;
  readonly endFreq: number;
  readonly pixels: Uint8Array;
}

export function parseScopeFrame(buf: ArrayBuffer): ScopeFrame | null {
  const view = new DataView(buf);
  if (view.byteLength < 16 || view.getUint8(0) !== 0x01) return null;
  const receiver = view.getUint8(1);
  const mode = view.getUint8(2);
  const startFreq = view.getUint32(3, true);
  const endFreq = view.getUint32(7, true);
  const pixelCount = view.getUint16(14, true);
  if (16 + pixelCount > view.byteLength) return null;
  return Object.freeze({
    receiver,
    mode,
    startFreq,
    endFreq,
    pixels: new Uint8Array(buf, 16, pixelCount),
  });
}
