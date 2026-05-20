/**
 * TX Microphone — captures mic, encodes Opus when available, otherwise sends PCM16.
 *
 * WebCodecs is not consistently available in embedded WebViews, so the PCM16
 * path keeps Tauri packaged builds usable while the backend handles the radio
 * contract.
 */

import { buildTxHeader, TX_BITRATE, SAMPLE_RATE, CHANNELS, CODEC_PCM16 } from './constants';

export type TxSendFn = (data: ArrayBuffer) => void;
type LegacyGetUserMedia = (
  constraints: MediaStreamConstraints,
  success: (stream: MediaStream) => void,
  failure: (error: DOMException | Error) => void,
) => void;

type NavigatorWithLegacyMedia = Navigator & {
  getUserMedia?: LegacyGetUserMedia;
  webkitGetUserMedia?: LegacyGetUserMedia;
  mozGetUserMedia?: LegacyGetUserMedia;
};

export class TxMic {
  private stream: MediaStream | null = null;
  private encoder: AudioEncoder | null = null;
  private reader: ReadableStreamDefaultReader<AudioData> | null = null;
  private audioContext: AudioContext | null = null;
  private pcmSource: MediaStreamAudioSourceNode | null = null;
  private pcmProcessor: ScriptProcessorNode | null = null;
  private pcmPending: number[] = [];
  private seq = 0;
  private _active = false;
  private sendFn: TxSendFn;

  constructor(sendFn: TxSendFn) {
    this.sendFn = sendFn;
  }

  get active(): boolean {
    return this._active;
  }

  /** Check if browser supports TX mic */
  static supported(): boolean {
    return TxMic.getUserMedia() !== null && (TxMic.supportsWebCodecs() || TxMic.supportsPcmCapture());
  }

  private static supportsWebCodecs(): boolean {
    return (
      typeof AudioEncoder !== 'undefined' &&
      typeof MediaStreamTrackProcessor !== 'undefined'
    );
  }

  private static supportsPcmCapture(): boolean {
    const audioContextCtor = TxMic.audioContextCtor();
    return TxMic.getUserMedia() !== null && audioContextCtor !== null;
  }

  private static audioContextCtor(): typeof AudioContext | null {
    const globals = globalThis as typeof globalThis & {
      webkitAudioContext?: typeof AudioContext;
    };
    return globals.AudioContext ?? globals.webkitAudioContext ?? null;
  }

  private static getUserMedia(): ((constraints: MediaStreamConstraints) => Promise<MediaStream>) | null {
    if (typeof navigator === 'undefined') return null;

    if (navigator.mediaDevices?.getUserMedia) {
      return navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    }

    const legacyNavigator = navigator as NavigatorWithLegacyMedia;
    const legacy =
      legacyNavigator.getUserMedia ??
      legacyNavigator.webkitGetUserMedia ??
      legacyNavigator.mozGetUserMedia;
    if (!legacy) return null;

    return (constraints: MediaStreamConstraints) =>
      new Promise((resolve, reject) => {
        legacy.call(legacyNavigator, constraints, resolve, reject);
      });
  }

  async start(): Promise<string | null> {
    if (this._active) return null;

    const getUserMedia = TxMic.getUserMedia();
    if (!getUserMedia || !TxMic.supported()) {
      return 'TX MIC: microphone capture not supported';
    }

    try {
      this.stream = await getUserMedia({
        audio: {
          channelCount: CHANNELS,
          sampleRate: SAMPLE_RATE,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
    } catch {
      return 'TX MIC: permission denied';
    }

    this._active = true;
    this.seq = 0;
    this.pcmPending = [];

    if (!TxMic.supportsWebCodecs()) {
      return this.startPcmFallback();
    }

    const track = this.stream.getAudioTracks()[0];
    const processor = new MediaStreamTrackProcessor({ track });
    this.reader = processor.readable.getReader();

    let sentFrames = 0;
    this.encoder = new AudioEncoder({
      output: (chunk: EncodedAudioChunk) => {
        const payload = new Uint8Array(chunk.byteLength);
        chunk.copyTo(payload);
        const header = buildTxHeader(this.seq++);
        const frame = new Uint8Array(header.length + payload.length);
        frame.set(header);
        frame.set(payload, header.length);
        this.sendFn(frame.buffer);
        sentFrames++;
        if (sentFrames <= 3 || sentFrames % 50 === 0) {
          console.log(`[TxMic] sent frame #${sentFrames}, size=${frame.length} bytes`);
        }
      },
      error: (err: DOMException) => {
        console.warn('TxMic: encoder error', err);
      },
    });

    this.encoder.configure({
      codec: 'opus',
      sampleRate: SAMPLE_RATE,
      numberOfChannels: CHANNELS,
      bitrate: TX_BITRATE,
    });

    // Read loop
    this.readLoop();
    return null;
  }

  stop(): void {
    this._active = false;
    if (this.pcmProcessor) {
      this.pcmProcessor.disconnect();
      this.pcmProcessor.onaudioprocess = null;
      this.pcmProcessor = null;
    }
    if (this.pcmSource) {
      this.pcmSource.disconnect();
      this.pcmSource = null;
    }
    if (this.audioContext) {
      this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }
    this.pcmPending = [];
    if (this.reader) {
      this.reader.cancel().catch(() => {});
      this.reader = null;
    }
    if (this.encoder) {
      try { this.encoder.close(); } catch { /* ok */ }
      this.encoder = null;
    }
    if (this.stream) {
      this.stream.getTracks().forEach(t => t.stop());
      this.stream = null;
    }
  }

  private startPcmFallback(): string | null {
    const audioContextCtor = TxMic.audioContextCtor();
    if (!audioContextCtor || !this.stream) {
      return 'TX MIC: PCM capture not supported';
    }

    this.audioContext = new audioContextCtor({ sampleRate: SAMPLE_RATE });
    if (Math.round(this.audioContext.sampleRate) !== SAMPLE_RATE) {
      this.stop();
      return `TX MIC: unsupported mic sample rate ${this.audioContext.sampleRate} Hz`;
    }

    this.pcmSource = this.audioContext.createMediaStreamSource(this.stream);
    this.pcmProcessor = this.audioContext.createScriptProcessor(1024, CHANNELS, CHANNELS);
    this.pcmProcessor.onaudioprocess = (event: AudioProcessingEvent) => {
      if (!this._active) return;
      const input = event.inputBuffer.getChannelData(0);
      this.queuePcmSamples(input);
    };
    this.pcmSource.connect(this.pcmProcessor);
    this.pcmProcessor.connect(this.audioContext.destination);
    return null;
  }

  private queuePcmSamples(input: Float32Array): void {
    for (const sample of input) {
      this.pcmPending.push(sample);
    }

    const frameSamples = Math.floor(SAMPLE_RATE * 0.02);
    while (this.pcmPending.length >= frameSamples) {
      const frame = this.pcmPending.splice(0, frameSamples);
      const payload = new Uint8Array(frameSamples * 2);
      const view = new DataView(payload.buffer);
      for (let i = 0; i < frameSamples; i += 1) {
        const clamped = Math.max(-1, Math.min(1, frame[i] ?? 0));
        const pcm = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
        view.setInt16(i * 2, Math.round(pcm), true);
      }
      const header = buildTxHeader(this.seq++, CODEC_PCM16);
      const packet = new Uint8Array(header.length + payload.length);
      packet.set(header);
      packet.set(payload, header.length);
      this.sendFn(packet.buffer);
    }
  }

  private async readLoop(): Promise<void> {
    let samplesRead = 0;
    console.log('[TxMic] read loop started');
    while (this._active && this.reader) {
      let result: ReadableStreamReadResult<AudioData>;
      try {
        result = await this.reader.read();
      } catch (err) {
        console.error('[TxMic] read error:', err);
        break;
      }
      if (!result || result.done) {
        console.log('[TxMic] read loop done');
        break;
      }
      if (this.encoder && this._active) {
        this.encoder.encode(result.value);
        samplesRead++;
        if (samplesRead <= 3 || samplesRead % 100 === 0) {
          console.log(`[TxMic] encoded sample #${samplesRead}`);
        }
      }
      result.value.close();
    }
    console.log('[TxMic] read loop exited, samples=' + samplesRead);
  }
}
