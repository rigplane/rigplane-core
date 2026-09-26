// Audio protocol constants (must match server handlers.py)

export const AUDIO_HEADER_SIZE = 8;
export const MSG_TYPE_RX = 0x10;
export const MSG_TYPE_TX = 0x11;
export const CODEC_OPUS = 0x01;
export const CODEC_PCM16 = 0x02;
export const SAMPLE_RATE = 48000;
// Rates the radio audio contract admits (ICOM_AUDIO_SAMPLE_RATE). The PCM16
// TX leg may deliver any of these; anything else is refused, never guessed.
export const TX_PCM_SAMPLE_RATES = [8000, 16000, 24000, 48000] as const;
export const CHANNELS = 1;
export const FRAME_DURATION_MS = 20;
export const TX_BITRATE = 32000;

/** Build 8-byte audio header. `sampleRate` is the rate the payload is at. */
export function buildTxHeader(seq: number, codec = CODEC_OPUS, sampleRate = SAMPLE_RATE): Uint8Array {
  const h = new Uint8Array(8);
  const v = new DataView(h.buffer);
  v.setUint8(0, MSG_TYPE_TX);
  v.setUint8(1, codec);
  v.setUint16(2, seq & 0xFFFF, true);
  v.setUint16(4, sampleRate / 100, true);
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
