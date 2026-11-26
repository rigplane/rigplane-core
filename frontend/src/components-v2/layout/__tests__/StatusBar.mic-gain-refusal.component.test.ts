import { flushSync, mount, tick, unmount } from 'svelte';
import { expect, it, vi } from 'vitest';
import { webglFixture } from '$lib/renderers/__tests__/webgl-fixture';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import { setCapabilities } from '$lib/stores/capabilities.svelte';
import { getRadioState } from '$lib/stores/radio.svelte';
import goldens from '$lib/transport/__tests__/fixtures/state-wire-goldens.json';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import { connect, disconnect } from '$lib/transport/ws-client';
import { validateCapabilities } from '$lib/types/capabilities';
import { desktopV2Layout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import { resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY } from '../../../presentation/workspace/resolution';
import HostedRadioLayoutFixture from './fixtures/HostedRadioLayoutFixture.svelte';
import { stubResizeObserver } from './support/stub-resize-observer';

const fetchCapabilities = vi.hoisted(() => vi.fn());
vi.mock('$lib/transport/http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/http-client')>(), fetchCapabilities,
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => tx.controller,
}));
const tx = new ManagedAppTxHarness();

it('replays the FTX-1 mic-gain refusal and recovery into the desktop controls', async () => {
  const { micGainCapabilities, micGainRefused, micGainRecovered } = goldens['FTX-1'];
  const caps = validateCapabilities(structuredClone(micGainCapabilities));
  fetchCapabilities.mockResolvedValue(caps);
  vi.stubGlobal('WebSocket', MockWebSocket);
  instances.length = 0;
  const target = document.body.appendChild(document.createElement('div'));
  let component: ReturnType<typeof mount> | undefined;
  try {
    stubResizeObserver();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (
      this: HTMLCanvasElement, kind: string,
    ) {
      return kind === 'webgl2' ? webglFixture(this.width, this.height).gl : null;
    });
    expect(setCapabilities(caps)).toBe(true);
    connect('ws://test/api/v1/ws');
    const socket = instances[0];
    socket.simulateOpen();
    const replay = (data: unknown) => {
      socket.simulateMessage(JSON.stringify({ type: 'state_update', data }));
      flushSync();
    };
    replay(micGainRefused);

    const plan = resolveSurfacePlan(desktopV2Layout, readWorkspace({ version: 1 }).workspace);
    component = mount(HostedRadioLayoutFixture, {
      target, context: new Map([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]),
    });
    flushSync();
    const title = () => target.querySelector('.status-indicators .indicator')?.getAttribute('title');
    const summary = 'Radio ↔ Server: connected';
    const refusal = micGainRefused.data.fieldStatus.micGain;
    const expectedLines = [summary, refusal.message];

    // Removing the StatusBar message loop leaves the summary as the only line.
    expect(title()?.split('\n')).toEqual(expectedLines);
    target.querySelector<HTMLButtonElement>('[aria-label="MIC GAIN settings"]')!.click();
    await tick();
    const slider = () => target.querySelector('[data-testid="tx-aux-micGain"] [role="slider"]');
    expect(slider()?.getAttribute('aria-disabled')).toBe('true');

    // An extra public alias for the same store path still contributes one line.
    replay({
      ...micGainRefused, transportSeq: micGainRefused.transportSeq + 1,
      data: {
        ...micGainRefused.data,
        fieldStatus: { ...micGainRefused.data.fieldStatus, micGainAlias: refusal },
      },
    });
    expect(getRadioState()?.fieldStatus?.micGainAlias).toEqual(refusal);
    expect(title()?.split('\n')).toEqual(expectedLines);

    replay(micGainRecovered);
    expect(title()).toBe(summary);
    expect(slider()?.getAttribute('aria-disabled')).toBe('false');
  } finally {
    if (component) await unmount(component);
    disconnect();
    target.remove();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  }
});
