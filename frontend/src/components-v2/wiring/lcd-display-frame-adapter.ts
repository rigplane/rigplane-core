import type { ScopeFrame } from '$lib/runtime/adapters/scope-adapter';

export type LcdDisplayFrameSource = 'audio-fft' | 'hardware';
export type LcdDisplayReceiver = 'MAIN' | 'SUB';
export interface LcdDisplayFrame {
  readonly source: LcdDisplayFrameSource;
  readonly receiver: LcdDisplayReceiver;
  readonly freshness: 'fresh';
  readonly startHz: number;
  readonly endHz: number;
  readonly normalizedBins: readonly number[];
}
export interface LcdDisplayFrames { readonly audio?: LcdDisplayFrame; readonly hardware?: LcdDisplayFrame }

export function toLcdDisplayFrame(
  frame: ScopeFrame | null | undefined,
  source: LcdDisplayFrameSource,
  expectedReceiver: LcdDisplayReceiver | null,
): LcdDisplayFrame | undefined {
  const receiver = frame?.receiver === 0 ? 'MAIN' : frame?.receiver === 1 ? 'SUB' : null;
  if (!frame || receiver === null || expectedReceiver === null || receiver !== expectedReceiver
    || !Number.isFinite(frame.startFreq) || !Number.isFinite(frame.endFreq)
    || frame.endFreq <= frame.startFreq || frame.pixels.length < 2) return undefined;
  return Object.freeze({ source, receiver, freshness: 'fresh', startHz: frame.startFreq,
    endHz: frame.endFreq, normalizedBins: Object.freeze(Array.from(frame.pixels, (sample) => sample / 255)) });
}
