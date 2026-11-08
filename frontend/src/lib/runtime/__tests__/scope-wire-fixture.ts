import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import type { ScopeFrame } from '../scope-frame';

/** Test mutations start from the repository's real producer v2 fixture header. */
export function scopeWire(overrides: Partial<Omit<ScopeFrame, 'pixels'>> & { pixels?: Uint8Array | null } = {}): ArrayBuffer {
  const path = resolve(process.cwd(), '../tests/fixtures/scope-wire/radio.bin');
  const original = readFileSync(path);
  const wire = new DataView(original.buffer, original.byteOffset, original.byteLength);
  const identity = overrides.sourceId ?? 'scope';
  const pixels = overrides.pixels === undefined ? new Uint8Array([0, 64, 128, 255]) : overrides.pixels;
  const width = overrides.width ?? (pixels?.length || 4);
  const buffer = new ArrayBuffer(56 + identity.length + (pixels?.length ?? 0));
  const bytes = new Uint8Array(buffer);
  bytes.set(original.subarray(0, 56));
  const header = new DataView(buffer);
  header.setUint8(1, ['radio', 'audio_fft', 'iq_fft'].indexOf(overrides.source ?? 'radio'));
  header.setUint8(2, (overrides.outOfRange ? 1 : 0) | (pixels ? 2 : 0));
  header.setUint8(3, identity.length);
  const receiver = overrides.receiver === undefined ? 0 : overrides.receiver;
  header.setUint16(4, receiver === null ? 65535 : receiver, true);
  header.setUint16(6, width, true);
  header.setUint32(8, overrides.epoch ?? 1, true);
  header.setUint32(12, overrides.seq ?? 0, true);
  header.setBigInt64(16, overrides.sentAtMs == null ? -1n : BigInt(overrides.sentAtMs), true);
  const start = overrides.viewStartHz ?? wire.getFloat64(24, true);
  const end = overrides.viewEndHz ?? wire.getFloat64(32, true);
  header.setFloat64(24, start, true); header.setFloat64(32, end, true);
  header.setFloat64(40, overrides.coverageStartHz ?? start, true);
  header.setFloat64(48, overrides.coverageEndHz ?? end, true);
  bytes.set(Array.from(identity, character => character.charCodeAt(0)), 56);
  if (pixels) bytes.set(pixels, 56 + identity.length);
  return buffer;
}
export const scopeFixtures = {
  radio: Array.from(new Uint8Array(scopeWire({ sourceId: 'scope-1', epoch: 7, seq: 65535 }))),
  audio: Array.from(new Uint8Array(scopeWire({ source: 'audio_fft', sourceId: 'scope-1', epoch: 7,
    receiver: 1, seq: 17, viewStartHz: 7_100_000, viewEndHz: 7_103_000, pixels: new Uint8Array([1, 127, 254]) }))),
  outOfRange: Array.from(new Uint8Array(scopeWire({ sourceId: 'scope-1', epoch: 7, receiver: 1,
    viewStartHz: 144_000_000, viewEndHz: 148_000_000, outOfRange: true, pixels: null }))),
};


const goldenRoot = resolve(process.cwd(), '../tests/fixtures/scope-wire');
export const scopeWireGoldenNames = readdirSync(goldenRoot).filter(name => name.endsWith('.bin')).sort();
type ScopeWireGolden = {
  source: ScopeFrame['source']; source_id: string; receiver: number | null;
  view_start_hz: number; view_end_hz: number; coverage_start_hz: number; coverage_end_hz: number;
  width: number; pixels: number[] | null; out_of_range: boolean; epoch: number; seq: number; sent_at_ms: number | null;
};
export function loadScopeWireGolden(name: string): { buffer: ArrayBuffer; expected: ScopeWireGolden } {
  const bytes = readFileSync(resolve(goldenRoot, name));
  return { buffer: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer,
    expected: JSON.parse(readFileSync(resolve(goldenRoot, name.replace('.bin', '.json')), 'utf8')) };
}
