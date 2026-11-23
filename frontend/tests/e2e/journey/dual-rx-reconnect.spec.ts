import { writeFile } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';
import { validateCapabilities } from '../../../src/lib/types/capabilities';
import type { WsCommand, WsIncoming } from '../../../src/lib/types/protocol';
import { startJourneyServer } from './server-process';

const PORT = 18775;
const FREQ_HZ = 7_030_000;

interface RoutingWitness {
  controlOpens: number;
  stateUpdates: number;
  subCommandIds: string[];
  subResponses: number;
  graphs: { main?: GainNode; sub?: GainNode; starts: number }[];
}

async function installRoutingWitness(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const witness: RoutingWitness = { controlOpens: 0, stateUpdates: 0, subCommandIds: [], subResponses: 0, graphs: [] };
    (window as Window & { __RX_ROUTING__?: RoutingWitness }).__RX_ROUTING__ = witness;
    // Observe native sockets and graph nodes; all messages and audio still
    // travel through the production runtime, AudioManager and RxPlayer.
    const NativeSocket = window.WebSocket;
    window.WebSocket = class extends NativeSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols as never);
        if (new URL(String(url), location.href).pathname === '/api/v1/ws') {
          this.addEventListener('open', () => { witness.controlOpens += 1; });
          this.addEventListener('message', (event: MessageEvent) => {
            if (typeof event.data !== 'string') return;
            const message = JSON.parse(event.data) as WsIncoming;
            if (message.type === 'state_update') witness.stateUpdates += 1;
            if (message.type === 'response' && message.ok === true && witness.subCommandIds.includes(message.id)) {
              witness.subResponses += 1;
            }
          });
          const send = this.send.bind(this);
          this.send = (data) => {
            if (typeof data === 'string') {
              const message = JSON.parse(data) as WsCommand;
              if (message.type === 'cmd' && message.name === 'set_vfo' && message.params.vfo === 'SUB') witness.subCommandIds.push(message.id);
            }
            send(data);
          };
        }
      }
    };
    const graphs = new Map<BaseAudioContext, RoutingWitness['graphs'][number]>();
    const proto = AudioNode.prototype as unknown as {
      connect: (this: AudioNode, destination: unknown, ...rest: unknown[]) => unknown;
    };
    const connect = proto.connect;
    proto.connect = function (destination, ...rest) {
      if (this instanceof ChannelSplitterNode && destination instanceof GainNode) {
        let graph = graphs.get(this.context);
        if (!graph) {
          graph = { starts: 0 };
          graphs.set(this.context, graph);
          witness.graphs.push(graph);
        }
        if (rest[0] === 0) graph.main = destination;
        if (rest[0] === 1) graph.sub = destination;
      }
      return connect.call(this, destination, ...rest);
    };
    const nativeStart = AudioBufferSourceNode.prototype.start;
    AudioBufferSourceNode.prototype.start = function (...args) {
      const result = nativeStart.apply(this, args);
      const graph = graphs.get(this.context);
      if (graph) graph.starts += 1;
      return result;
    };
    Object.defineProperty(globalThis, 'AudioDecoder', { value: undefined, configurable: true });
    localStorage.setItem('icom.audio.focus', 'main');
    localStorage.setItem('icom.audio.split_stereo', '0');
    localStorage.setItem('icom.audio.main_gain_db', '-9');
    localStorage.setItem('icom.audio.sub_gain_db', '-6');
  });
}

async function routing(page: Page) {
  return page.evaluate(() => {
    const witness = (window as Window & { __RX_ROUTING__?: RoutingWitness }).__RX_ROUTING__!;
    const graph = witness.graphs.at(-1);
    return {
      controlOpens: witness.controlOpens,
      stateUpdates: witness.stateUpdates,
      subResponses: witness.subResponses,
      graphs: witness.graphs.length,
      contextState: graph?.main?.context.state ?? null,
      starts: graph?.starts ?? 0,
      main: graph?.main?.gain.value ?? null,
      sub: graph?.sub?.gain.value ?? null,
    };
  });
}

test('MOR-4296 explicit SUB persists and restores actual routing after control reconnect', async ({ page }, testInfo) => {
  test.setTimeout(90_000);
  const levelFile = testInfo.outputPath('rx-tone-dbfs');
  await writeFile(levelFile, '-30');
  const args = ['--audio', '--model', 'IC-7610', '--rx-tone-level-file', levelFile];
  const first = await startJourneyServer(PORT, FREQ_HZ, args);
  let replacement: Awaited<ReturnType<typeof startJourneyServer>> | undefined;
  try {
    await installRoutingWitness(page);
    await page.goto(`http://127.0.0.1:${PORT}/?ui=v2`, { waitUntil: 'domcontentloaded' });
    const caps = validateCapabilities(await (await page.request.get(`http://127.0.0.1:${PORT}/api/v1/capabilities`)).json());
    expect(caps.capabilities).toContain('lan_dual_rx_audio_routing');
    await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
    await page.getByTestId('rx-audio-monitor-live').click();
    await expect.poll(async () => (await routing(page)).starts).toBeGreaterThan(5);
    await page.locator('[data-active-receiver-segment="SUB"]').click();
    await expect.poll(async () => (await routing(page)).subResponses, {
      message: 'the real server accepts the SUB activation command sent by the UI',
    }).toBe(1);
    await expect.poll(() => page.evaluate(() => localStorage.getItem('icom.audio.focus')), {
      message: 'explicit SUB activation persists its focus instead of retaining seeded MAIN',
    }).toBe('sub');
    const subGain = Math.SQRT2 * 10 ** (-6 / 20);
    await expect.poll(async () => (await routing(page)).main).toBe(0);
    await expect.poll(async () => (await routing(page)).sub).toBeCloseTo(subGain, 5);
    const before = await routing(page);

    // Kill the owned real server, including both actual WebSocket sessions.
    // Capability loss must visibly neutralize the existing local graph.
    await first.kill();
    await expect(page.locator('[role="status"][title="Control WebSocket: disconnected"]')).toBeVisible();
    await expect.poll(async () => (await routing(page)).main, {
      message: 'capability loss neutralizes MAIN gain before reconnect',
    }).toBeCloseTo(Math.SQRT2, 5);
    await expect.poll(async () => (await routing(page)).sub).toBeCloseTo(Math.SQRT2, 5);
    const disconnected = await routing(page);

    replacement = await startJourneyServer(PORT, FREQ_HZ, args);
    await expect.poll(async () => (await routing(page)).controlOpens, { timeout: 30_000 }).toBeGreaterThan(before.controlOpens);
    await expect(page.locator('[role="status"][title="Control WebSocket: connected"]')).toBeVisible();
    await expect.poll(async () => (await routing(page)).stateUpdates).toBeGreaterThan(disconnected.stateUpdates);
    await expect.poll(async () => {
      const now = await routing(page);
      return now.starts > (now.graphs === disconnected.graphs ? disconnected.starts : 0) + 5;
    }, { timeout: 30_000, message: 'real replacement session successfully starts fresh RX audio' }).toBe(true);
    await expect.poll(async () => (await routing(page)).main, {
      message: 'new supported session restores actual SUB-only routing after observed neutral routing',
    }).toBe(0);
    await expect.poll(async () => (await routing(page)).sub).toBeCloseTo(subGain, 5);
    expect((await routing(page)).contextState).toBe('running');
    expect(await page.evaluate(() => localStorage.getItem('icom.audio.focus'))).toBe('sub');
    expect(await page.evaluate(() => localStorage.getItem('icom.audio.sub_gain_db'))).toBe('-6');
    const result = testInfo.outputPath('routing-reconnect.json');
    await writeFile(result, JSON.stringify({ before, disconnected, reconnected: await routing(page) }));
    await testInfo.attach('routing-reconnect', { path: result, contentType: 'application/json' });
  } finally {
    await replacement?.kill();
    await first.kill();
  }
});
