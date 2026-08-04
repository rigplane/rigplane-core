/** MOR-1070 stub for `$lib/runtime` — fixture state/caps, no transport. */
import { harness } from '../harness-state';

export const runtime = {
  get state() { return harness.state; },
  get caps() { return harness.caps; },
};
