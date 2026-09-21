/**
 * MOR-1555 — conformance profile registry.
 *
 * Maps a profile id to the fixture + shape metadata a declarative
 * conformance suite needs: the live-captured state/capabilities pair (see
 * each fixture loader's own file for capture provenance) plus the
 * radio-shape facts (`model`, `receivers`, `vfoScheme`, `vfoReadback`) a
 * future multi-profile table would key assertions on.
 *
 * `ic7300` is the live-captured single-receiver entry; `ftx1` is the
 * dual-receiver entry whose state capture carries 108 unobserved-null
 * leaves (MOR-2513). See each fixture loader's own file for capture
 * provenance.
 */
import type { Capabilities, VfoReadback, VfoScheme } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { IC7300_CAPABILITIES, IC7300_STATE } from '../fixtures/ic7300-profile';
import { FTX1_CAPABILITIES, FTX1_STATE } from '../fixtures/ftx1-profile';

export interface ConformanceProfile {
  state: ServerState;
  caps: Capabilities;
  model: string;
  receivers: number;
  vfoScheme: VfoScheme;
  vfoReadback?: VfoReadback;
}

export const PROFILES = {
  ic7300: {
    state: IC7300_STATE,
    caps: IC7300_CAPABILITIES,
    model: IC7300_CAPABILITIES.model,
    receivers: IC7300_CAPABILITIES.receivers,
    vfoScheme: IC7300_CAPABILITIES.vfoScheme,
    vfoReadback: IC7300_CAPABILITIES.vfoReadback,
  },
  ftx1: {
    state: FTX1_STATE,
    caps: FTX1_CAPABILITIES,
    model: FTX1_CAPABILITIES.model,
    receivers: FTX1_CAPABILITIES.receivers,
    vfoScheme: FTX1_CAPABILITIES.vfoScheme,
    vfoReadback: FTX1_CAPABILITIES.vfoReadback,
  },
} satisfies Record<string, ConformanceProfile>;
