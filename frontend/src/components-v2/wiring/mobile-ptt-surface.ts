import { createPttGesture, type PttGesture } from './tx-ptt-gesture';

export type MobilePttSurface = 'portrait' | 'landscape';
export interface MobilePttHost {
  pttOn(): void;
  pttOff(): void;
}
export type MobilePttSurfaceDeps = {
  schedule(callback: () => void, ms: number): unknown;
  cancel(handle: unknown): void;
  doubleTapMs?: number;
};
export interface MobilePttBinding {
  down(): void;
  up(): void;
  fabDown(): void;
  fabUp(): void;
  destroy(): void;
}

export function createMobilePttSurface(
  surface: MobilePttSurface,
  host: MobilePttHost,
  deps: MobilePttSurfaceDeps,
): MobilePttBinding {
  let alive = true;
  const gesture: PttGesture = createPttGesture({
    pttOn: host.pttOn,
    pttOff: host.pttOff,
  }, deps);
  const portrait = () => alive && surface === 'portrait';
  return {
    down: gesture.down,
    up: gesture.up,
    fabDown: () => { if (portrait()) gesture.down(); },
    fabUp: () => { if (portrait()) gesture.up(); },
    destroy: () => { alive = false; gesture.destroy(); },
  };
}
