// Audio protocol constants (must match server handlers.py)

export const AUDIO_HEADER_SIZE = 8;
export const MSG_TYPE_RX = 0x10;
export const MSG_TYPE_TX = 0x11;
export const CODEC_OPUS = 0x01;
export const CODEC_PCM16 = 0x02;
export const SAMPLE_RATE = 48000;
export const CHANNELS = 1;
export const FRAME_DURATION_MS = 20;
export const TX_BITRATE = 32000;

/** Build 8-byte audio header */
export function buildTxHeader(seq: number, codec = CODEC_OPUS): Uint8Array {
  const h = new Uint8Array(8);
  const v = new DataView(h.buffer);
  v.setUint8(0, MSG_TYPE_TX);
  v.setUint8(1, codec);
  v.setUint16(2, seq & 0xFFFF, true);
  v.setUint16(4, SAMPLE_RATE / 100, true);
  v.setUint8(6, CHANNELS);
  v.setUint8(7, FRAME_DURATION_MS);
  return h;
}

/** Parse an RX audio frame header */
export function parseRxHeader(buf: ArrayBuffer): {
  codec: number;
  sampleRate: number;
  channels: number;
  payload: Uint8Array;
} | null {
  if (buf.byteLength < AUDIO_HEADER_SIZE) return null;
  const v = new DataView(buf);
  if (v.getUint8(0) !== MSG_TYPE_RX) return null;
  return {
    codec: v.getUint8(1),
    sampleRate: v.getUint16(4, true) * 100,
    channels: v.getUint8(6),
    payload: new Uint8Array(buf, AUDIO_HEADER_SIZE),
  };
}
