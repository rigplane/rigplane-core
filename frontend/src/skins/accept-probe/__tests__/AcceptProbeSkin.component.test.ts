/**
 * MOR-2035/MOR-2034 acceptance probe — real mount pin for the `accept-probe`
 * entrypoint (skins/accept-probe/AcceptProbeSkin.svelte).
 * `skins/__tests__/entrypoints.test.ts`'s `SKIN_ENTRYPOINT_COVERAGE` points
 * its `covered-elsewhere` entry for `accept-probe` at this file — its
 * `entryComponentFile: 'AcceptProbeSkin.svelte'` is checked by a substring
 * match against this file's own source text, which is why the entrypoint's
 * filename is named explicitly above, not just through the `loadSkin('accept-probe')`
 * call below — mirroring `dual-receiver-cockpit`'s entry there. That file's
 * own header warns a `covered-elsewhere` pin is only a substring-mention
 * check, not proof of behavior — this suite is the actual behavioral proof:
 * it mounts the real component through `loadSkin`, the same function
 * `App.svelte` calls, and asserts its own bespoke markup (frequency
 * readout, RX/TX indicator, bespoke S-meter) actually renders.
 */
import { mount, unmount, flushSync } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';
import { loadSkin } from '../../registry';

let component: ReturnType<typeof mount> | null = null;
let target: HTMLElement | null = null;

afterEach(() => {
  if (component) unmount(component);
  target?.remove();
  component = null;
  target = null;
});

describe('AcceptProbeSkin entrypoint', () => {
  it('loads via loadSkin("accept-probe") and mounts its own bespoke markup', async () => {
    const Component = await loadSkin('accept-probe');
    target = document.createElement('div');
    document.body.appendChild(target);
    component = mount(Component, { target });
    flushSync();

    expect(target.querySelector('[data-testid="accept-probe-skin"]')).not.toBeNull();
    expect(target.querySelector('[data-testid="accept-probe-frequency"]')).not.toBeNull();
    expect(target.querySelector('[data-testid="accept-probe-rx-tx-indicator"]')).not.toBeNull();
    // The bespoke meter (components-v2/meters/AcceptProbeMeter.svelte,
    // registered in meter-contract.ts's METER_REGISTRY) actually mounts
    // as part of this skin's own composition, not just registered in
    // isolation.
    expect(target.querySelector('[data-testid="accept-probe-meter"]')).not.toBeNull();
  });
});
