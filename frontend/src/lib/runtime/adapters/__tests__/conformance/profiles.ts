/**
 * MOR-1555 — conformance profile registry.
 *
 * Maps a profile id to the fixture + shape metadata a declarative
 * conformance suite needs: the live-captured state/capabilities pair (see
 * each fixture loader's own file for capture provenance) plus the
 * radio-shape facts (`model`, `receivers`, `vfoScheme`, `vfoReadback`) a
 * future multi-profile table would key assertions on.
 *
 * `ic7300` is the sole entry today — ported as-is from MOR-1428's
 * `fixtures/ic7300-profile.ts` loader, which remains the single source of
 * truth for the fixture JSON pair and its capture provenance.
 */
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { IC7300_CAPABILITIES, IC7300_STATE } from '../fixtures/ic7300-profile';

export interface ConformanceProfile {
  state: ServerState;
  caps: Capabilities;
  model: string;
  receivers: number;
  vfoScheme: string;
  vfoReadback: string;
}

export const PROFILES = {
  ic7300: {
    state: IC7300_STATE,
    caps: IC7300_CAPABILITIES,
    model: 'IC-7300',
    receivers: 1,
    vfoScheme: 'ab',
    vfoReadback: 'selected_unselected',
  },
} satisfies Record<string, ConformanceProfile>;
