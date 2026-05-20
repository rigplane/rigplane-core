import { describe, it, expect } from 'vitest';
import { buildTxHeader, parseRxHeader, AUDIO_HEADER_SIZE, MSG_TYPE_RX, MSG_TYPE_TX,
  CODEC_OPUS, CODEC_PCM16, SAMPLE_RATE, CHANNELS, FRAME_DURATION_MS } from '../constants';

describe('buildTxHeader', () => {
  it('produces correct 8-byte header', () => {
    const v = new DataView(buildTxHeader(42).buffer);
    expect(v.getUint8(0)).toBe(MSG_TYPE_TX);
    expect(v.getUint8(1)).toBe(CODEC_OPUS);
    expect(v.getUint16(2, true)).toBe(42);
    expect(v.getUint16(4, true)).toBe(SAMPLE_RATE / 100);
    expect(v.getUint8(6)).toBe(CHANNELS);
    expect(v.getUint8(7)).toBe(FRAME_DURATION_MS);
  });
  it('can mark PCM16 TX frames', () => {
    const v = new DataView(buildTxHeader(42, CODEC_PCM16).buffer);
    expect(v.getUint8(0)).toBe(MSG_TYPE_TX);
    expect(v.getUint8(1)).toBe(CODEC_PCM16);
  });
  it('wraps seq at 16-bit boundary', () => {
    // 0x10000 should roll over to 0x0000
    expect(new DataView(buildTxHeader(0x10000).buffer).getUint16(2, true)).toBe(0);
    // 0x1FFFF & 0xFFFF = 0xFFFF
    expect(new DataView(buildTxHeader(0x1FFFF).buffer).getUint16(2, true)).toBe(0xFFFF);
  });
});

describe('parseRxHeader', () => {
  it('parses valid PCM16 frame', () => {
    const buf = new ArrayBuffer(AUDIO_HEADER_SIZE + 960);
    const v = new DataView(buf);
    v.setUint8(0, MSG_TYPE_RX); v.setUint8(1, CODEC_PCM16);
    v.setUint16(4, SAMPLE_RATE / 100, true); v.setUint8(6, 1);
    const hdr = parseRxHeader(buf)!;
    expect(hdr.codec).toBe(CODEC_PCM16);
    expect(hdr.sampleRate).toBe(SAMPLE_RATE);
    expect(hdr.payload.byteLength).toBe(960);
  });
  it('rejects too-short or wrong type', () => {
    expect(parseRxHeader(new ArrayBuffer(4))).toBeNull();
    const buf = new ArrayBuffer(AUDIO_HEADER_SIZE);
    new DataView(buf).setUint8(0, 0xFF);
    expect(parseRxHeader(buf)).toBeNull();
  });
});
