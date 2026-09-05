import type { LcdSpectrumFrame } from '../../../skins/segmentline/lcd-display-contract';
import type { ScopePassbandDisplay } from './scope-passband-display';

export type ScopeDisplayProjection = Readonly<{
  frame: LcdSpectrumFrame;
  frameMode: number;
  passband: ScopePassbandDisplay;
}>;

export type ManagedScopeRegion = Readonly<{
  projection: ScopeDisplayProjection | null;
  demanded: boolean;
  setDemand(enabled: boolean): void;
}>;
