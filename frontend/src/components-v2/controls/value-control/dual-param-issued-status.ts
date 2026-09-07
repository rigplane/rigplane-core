import type { PoliteControlAnnouncement } from '../../../primitives/control-feedback/control-feedback-presentation';
import type {
  ContinuousPairLaneView,
  ContinuousPairView,
} from '../../../primitives/scalar/continuous-pair.svelte';

export type DualParamLane = 'rf' | 'sql';

export interface DualParamIssuedStatusSnapshot {
  readonly lane: DualParamLane;
  readonly view: Readonly<ContinuousPairView>;
  readonly laneView: Readonly<Extract<
    ContinuousPairLaneView,
    { evidence: 'command-feedback' }
  >>;
  readonly announcement: Readonly<PoliteControlAnnouncement>;
}

export interface DualParamLaneIssuedStatusPresentation {
  readonly text: string | null;
  format(snapshot: Readonly<DualParamIssuedStatusSnapshot>): string;
  accept(text: string | null): void;
}

export interface DualParamIssuedStatusPresentation {
  readonly rf: Readonly<DualParamLaneIssuedStatusPresentation>;
  readonly sql: Readonly<DualParamLaneIssuedStatusPresentation>;
}
