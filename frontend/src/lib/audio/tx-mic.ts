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

export type TxCodec = 'opus' | 'pcm16';

/** Outcome of adopting a server-advertised TX codec. */
export interface TxCodecSwitch {
  /** True when a live capture was actually moved onto PCM16. */
  switched: boolean;
  /**
   * Non-null when the PCM16 leg refused to start. The capture is left
   * untouched and still running on Opus — which the server cannot decode —
   * so the caller must end the TX session rather than report a working
   * fallback.
   */
  error: string | null;
}

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
  // Capture-local identity also cancels getUserMedia that settles after stop().
  private captureGeneration = 0;
  private removeTrackEnded: (() => void) | null = null;
  // Sticky: the server told us it cannot decode Opus (MOR-1791). Kept across
  // start/stop so every later PTT opens on PCM16 from the very first frame.
  private pcm16Pinned = false;

  constructor(sendFn: TxSendFn, private readonly onCaptureDied?: (reason: string) => void) {
    this.sendFn = sendFn;
  }

  get active(): boolean {
    return this._active;
  }

  /** Codec currently being emitted, or null when not capturing. */
  get codec(): TxCodec | null {
    if (!this._active) return null;
    return this.encoder !== null ? 'opus' : 'pcm16';
  }

  /**
   * Adopt the codec the server says it can accept (MOR-1791).
   *
   * The server cannot decode Opus on hosts without a native opus codec; it
   * knows this at TX start and now says so. Rather than key the radio while
   * every Opus frame is dropped fail-closed, switch to the PCM16 capture
   * path that already exists below — same path, same MediaStream, no second
   * permission prompt, no new transport.
   *
   * `'opus'` restores the default and never tears down a running capture:
   * when the decoder is available the client keeps its own codec choice,
   * exactly as before this negotiation existed.
   *
   * PCM16 is brought up BEFORE the Opus leg is torn down. A refusal must
   * never kill capture out from under a keyed transmitter, so on failure the
   * running Opus capture is left whole and the error is returned for the
   * caller to act on — it is never swallowed.
   */
  applyServerCodec(codec: TxCodec): TxCodecSwitch {
    if (codec === 'opus') {
      this.pcm16Pinned = false;
      return { switched: false, error: null };
    }
    this.pcm16Pinned = true;
    if (!this._active || this.encoder === null) return { switched: false, error: null };

    const error = this.startPcmFallback({ abortCapture: false });
    if (error !== null) return { switched: false, error };
    this.stopOpusCapture();
    return { switched: true, error: null };
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

    const generation = ++this.captureGeneration;
    let stream: MediaStream;
    try {
      stream = await getUserMedia({
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

    if (generation !== this.captureGeneration) {
      stream.getTracks().forEach(track => track.stop());
      return 'TX MIC: capture start cancelled';
    }
    this.stream = stream;
    this._active = true;
    const track = stream.getAudioTracks()[0];
    const onEnded = () => this.failCapture(generation, 'microphone track ended');
    track.addEventListener('ended', onEnded);
    this.removeTrackEnded = () => track.removeEventListener('ended', onEnded);
    if (track.readyState === 'ended') {
      onEnded();
      return 'TX MIC: microphone track ended';
    }
    this.seq = 0;
    this.pcmPending = [];

    if (this.pcm16Pinned || !TxMic.supportsWebCodecs()) {
      return this.startPcmFallback();
    }

    const processor = new MediaStreamTrackProcessor({ track });
    const reader = this.reader = processor.readable.getReader();

    let sentFrames = 0;
    const encoder = this.encoder = new AudioEncoder({
      output: (chunk: EncodedAudioChunk) => {
        if (!this._active || this.encoder !== encoder || generation !== this.captureGeneration) return;
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
        if (this.encoder === encoder) this.failCapture(generation, `encoder error: ${err.message}`);
      },
    });

    this.encoder.configure({
      codec: 'opus',
      sampleRate: SAMPLE_RATE,
      numberOfChannels: CHANNELS,
      bitrate: TX_BITRATE,
    });

    // Read loop
    void this.readLoop(reader, encoder, generation);
    return null;
  }

  stop(): void {
    this._active = false;
    ++this.captureGeneration;
    this.removeTrackEnded?.();
    this.removeTrackEnded = null;
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
    this.stopOpusCapture();
    if (this.stream) {
      this.stream.getTracks().forEach(t => t.stop());
      this.stream = null;
    }
  }

  /** Tear down the WebCodecs encoder leg, leaving the MediaStream open. */
  private stopOpusCapture(): void {
    const reader = this.reader;
    const encoder = this.encoder;
    // Invalidate before cancellation/close can deliver their terminal callbacks.
    this.reader = null;
    this.encoder = null;
    reader?.cancel().catch(() => {});
    try { encoder?.close(); } catch { /* ok */ }
  }

  /**
   * Bring up the PCM16 capture leg.
   *
   * `abortCapture` is true when this IS the capture (called from `start()`),
   * so a refusal must tear the whole attempt down. It is false when swapping
   * a live Opus capture over (`applyServerCodec`), where the running capture
   * must survive a refusal rather than die under a keyed transmitter.
   */
  private startPcmFallback({ abortCapture = true } = {}): string | null {
    const audioContextCtor = TxMic.audioContextCtor();
    if (!audioContextCtor || !this.stream) {
      return 'TX MIC: PCM capture not supported';
    }

    this.audioContext = new audioContextCtor({ sampleRate: SAMPLE_RATE });
    const actualRate = this.audioContext.sampleRate;
    if (Math.round(actualRate) !== SAMPLE_RATE) {
      // Read the rate BEFORE tearing down: both teardowns null out
      // `audioContext`, and reading it afterwards threw a TypeError instead
      // of returning this error string.
      if (abortCapture) {
        this.stop();
      } else {
        this.audioContext.close().catch(() => {});
        this.audioContext = null;
      }
      return `TX MIC: unsupported mic sample rate ${actualRate} Hz`;
    }

    this.pcmSource = this.audioContext.createMediaStreamSource(this.stream);
    const processor = this.pcmProcessor = this.audioContext.createScriptProcessor(1024, CHANNELS, CHANNELS);
    this.pcmProcessor.onaudioprocess = (event: AudioProcessingEvent) => {
      if (!this._active || this.pcmProcessor !== processor) return;
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

  private failCapture(generation: number, reason: string): void {
    if (!this._active || generation !== this.captureGeneration) return;
    this.stop();
    this.onCaptureDied?.(`TX MIC: ${reason}`);
  }

  private async readLoop(
    reader: ReadableStreamDefaultReader<AudioData>, encoder: AudioEncoder, generation: number,
  ): Promise<void> {
    const current = () => this._active && this.reader === reader && generation === this.captureGeneration;
    while (current()) {
      let result: ReadableStreamReadResult<AudioData>;
      try {
        result = await reader.read();
      } catch (err) {
        if (current()) this.failCapture(generation, `capture reader failed: ${String(err)}`);
        break;
      }
      if (result.done) {
        if (current()) this.failCapture(generation, 'capture reader ended');
        break;
      }
      try {
        if (current()) encoder.encode(result.value);
      } catch (err) {
        if (current()) this.failCapture(generation, `encoder failed: ${String(err)}`);
      } finally {
        result.value.close();
      }
    }
  }
}
