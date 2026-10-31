// Effective band-plan region (MOR-3631): the one state read by the
// SpectrumToolbar region selector and by the SpectrumPanel -> BandPlanOverlay
// wiring. It is loaded from `GET /api/v1/band-plan/config` and changed with
// `POST /api/v1/band-plan/config`, whether or not a toolbar is mounted (the
// portrait layout hides it), so the overlay never guesses a region.

import { getAuthHeaders } from '$lib/auth';

const CONFIG_URL = '/api/v1/band-plan/config';

/** `null` until the server has said which region is in effect. */
let region = $state<string | null>(null);
let availableRegions = $state<string[]>([]);

let inflight: { request: Promise<void>; epoch: number } | null = null;
// Bumped by every accepted region change, so a config read that started
// before it cannot write the old region back (and is not reused after it).
let epoch = 0;

export function getBandPlanRegion(): string | null {
  return region;
}

export function getAvailableBandPlanRegions(): string[] {
  return availableRegions;
}

/** Read the server's band-plan config. Concurrent callers share one request. */
export function loadBandPlanConfig(): Promise<void> {
  if (inflight && inflight.epoch === epoch) return inflight.request;
  const startedAt = epoch;
  const request: Promise<void> = (async () => {
    try {
      const resp = await fetch(CONFIG_URL, { headers: getAuthHeaders() });
      if (!resp.ok) return;
      const config = await resp.json();
      if (startedAt !== epoch) return;
      if (typeof config.region === 'string' && config.region !== '') {
        region = config.region;
      }
      availableRegions = config.availableRegions ?? [];
    } catch {
      // Offline or an older server: the region stays unknown, which never
      // authorises the bundled US copy (see localCopyServesRegion).
    }
  })().finally(() => {
    if (inflight?.request === request) inflight = null;
  });
  inflight = { request, epoch: startedAt };
  return request;
}

/** Ask the server to switch region; the new region is in effect when true. */
export async function selectBandPlanRegion(next: string): Promise<boolean> {
  try {
    const resp = await fetch(CONFIG_URL, {
      method: 'POST',
      headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ region: next }),
    });
    if (!resp.ok) return false;
    epoch += 1;
    region = next;
    return true;
  } catch {
    return false;
  }
}

export function resetBandPlanRegionForTests(): void {
  region = null;
  availableRegions = [];
  inflight = null;
  epoch = 0;
}
