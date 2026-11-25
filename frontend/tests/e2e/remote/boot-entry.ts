import { apiFetch } from '../../../src/lib/api/api-fetch';
import { parseCurrentScopeFrame } from '../../../src/lib/runtime/scope-frame';
import type { LinkLostReason, RemoteLink } from '../../../src/lib/transport/transport-bundle';
import wire from '../../../src/lib/transport/api-channel-wire.json';
import type { RelayedAnswer } from './station';

// Structural twin of boot.ts's RemoteHandle/bootRemote. The harness tsconfig
// type-checks with plain tsc, which resolves every module a type query
// touches: naming '../../../src/boot' here would pull boot.ts's dynamic
// component-kit/skin imports (plain tsc cannot read named type exports from
// .svelte modules) into this program. The runtime contract is still checked
// below: the glob finds the entry and loadBoot() verifies the export.
interface RemoteHandle {
  shutdown(): void;
}
interface BootModule {
  bootRemote(link: RemoteLink, options: { onLinkLost(reason: LinkLostReason): void }): RemoteHandle;
}

// Keep the shell buildable on the 3.3.0 control tree so the row reaches
// the browser and fails at the missing boot entry.
const bootModules = import.meta.glob<BootModule>('../../../src/boot.ts');

const links: OfferedLink[] = [];
let handle: RemoteHandle | undefined;

interface OfferedLink {
  peer: RTCPeerConnection;
  link: RemoteLink;
  channels: Array<{ channel: RTCDataChannel; frames: number }>;
  traffic: {
    scopeFrames: number;
    heartbeats: number;
    withholdAcks: boolean;
    withheldHeartbeats: number;
  };
  losses: LinkLostReason[];
}

function waitForIce(peer: RTCPeerConnection): Promise<void> {
  if (peer.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      peer.removeEventListener('icegatheringstatechange', changed);
      reject(new Error(`ICE gathering did not complete (${peer.iceGatheringState})`));
    }, 10_000);
    function changed() {
      if (peer.iceGatheringState !== 'complete') return;
      clearTimeout(timer);
      peer.removeEventListener('icegatheringstatechange', changed);
      resolve();
    }
    peer.addEventListener('icegatheringstatechange', changed);
  });
}

async function offer(): Promise<OfferedLink> {
  const peer = new RTCPeerConnection();
  const channels: OfferedLink['channels'] = [];
  const traffic = { scopeFrames: 0, heartbeats: 0, withholdAcks: false, withheldHeartbeats: 0 };
  function createChannel(label: string): RTCDataChannel {
    const spec = wire.data_channels.find((entry) => entry.label === label);
    if (!spec) throw new Error(`Unknown K5 label ${label}`);
    const channel = peer.createDataChannel(label, {
      ordered: spec.ordered,
      maxRetransmits: spec.max_retransmits ?? undefined,
    });
    const record = { channel, frames: 0 };
    channels.push(record);
    // Observe real traffic before the production adapter receives it.
    function observe(event: MessageEvent) {
      record.frames += 1;
      if (label === 'scope' && event.data instanceof ArrayBuffer) {
        const frame = parseCurrentScopeFrame(event.data, { source: 'radio', sourceId: 'radio', epoch: 0 });
        if (frame.ok && frame.frame.source === 'radio' && frame.frame.pixels !== null) traffic.scopeFrames += 1;
      }
      if (label === 'control' && typeof event.data === 'string'
        && JSON.parse(event.data).type === 'controller_heartbeat') {
        traffic.heartbeats += 1;
        if (traffic.withholdAcks) {
          traffic.withheldHeartbeats += 1;
          event.stopImmediatePropagation();
        }
      }
    }
    channel.addEventListener('message', observe, true);
    channel.addEventListener('close', () => channel.removeEventListener('message', observe, true), { once: true });
    return channel;
  }
  // API creates the SDP data section. The live mount requests the other channels.
  try {
    const api = createChannel('api');
    const offeredLink: OfferedLink = { peer, link: { api, createChannel }, channels, traffic, losses: [] };
    await peer.setLocalDescription(await peer.createOffer());
    await waitForIce(peer);
    const description = peer.localDescription;
    if (!description) throw new Error('The browser produced no local description');
    const { __relayOffer } = window as typeof window & {
      __relayOffer(sdp: string, type: string): Promise<RelayedAnswer>;
    };
    const answer = await __relayOffer(description.sdp, description.type);
    await peer.setRemoteDescription({ sdp: answer.sdp, type: answer.type });
    links.push(offeredLink);
    return offeredLink;
  } catch (error) {
    peer.close();
    throw error;
  }
}

async function loadBoot() {
  const load = bootModules['../../../src/boot.ts'];
  if (!load) throw new Error('This Core tree has no remote boot entry');
  const { bootRemote } = await load();
  if (typeof bootRemote !== 'function') throw new Error('This Core tree has no bootRemote export');
  return bootRemote;
}

const harness = {
  async start() {
    const boot = await loadBoot();
    const { link, losses } = await offer();
    handle = boot(link, { onLinkLost: (reason) => losses.push(reason) });
  },
  async replaceImmediately() {
    const boot = await loadBoot();
    const first = await offer();
    const last = await offer();
    handle = boot(first.link, { onLinkLost: (reason) => first.losses.push(reason) });
    handle = boot(last.link, { onLinkLost: (reason) => last.losses.push(reason) });
  },
  halfClose() {
    for (const label of ['scope', 'audio-text']) {
      const live = links[0]!.channels.find(({ channel }) => channel.label === label && channel.readyState === 'open');
      if (!live) throw new Error(`No open ${label} channel to close`);
      live.channel.close();
    }
  },
  withholdHeartbeatAcks() {
    links[0]!.traffic.withholdAcks = true;
  },
  async info() {
    const response = await apiFetch('/api/v1/info');
    const { server, model, proto, version } = await response.json();
    return { status: response.status, server, model, proto, version };
  },
  snapshot() {
    return {
      links: links.map(({ channels, traffic, losses }) => ({
        ...traffic, losses: [...losses],
        channels: channels.map(({ channel, frames }) => ({
          label: channel.label, ordered: channel.ordered, maxRetransmits: channel.maxRetransmits,
          state: channel.readyState, frames,
          coreHandlers: !!(channel.onopen || channel.onclose || channel.onerror || channel.onmessage),
        })),
      })),
    };
  },
};

window.addEventListener('pagehide', () => {
  handle?.shutdown();
  for (const { peer } of links) peer.close();
}, { once: true });

export type RemoteBootHarness = typeof harness;
window.__remoteBoot = harness;
