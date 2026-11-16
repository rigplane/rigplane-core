/**
 * Test-only boot entry for row R-A1 (C5b PR-A, MOR-4493): the page-side
 * half of the remote-boot seam. The real station page (`frontend/dist`,
 * `main.ts`) boots the WebSocket bundle; a remote shell's page boots
 * `bootRemote` over an injected `RemoteLink` instead. This entry IS that
 * shell, built from test code only:
 *
 * - `window.__bootRemote(channels)` creates the `RTCPeerConnection` and
 *   the `api` channel (the page, not Node), relays the offer through the
 *   harness's `__relayOffer`, sets the remote description, waits for the
 *   api channel to open, then calls the production `bootRemote({ api,
 *   createChannel }, { onLinkLost })` — `createChannel(label)` minting a
 *   FRESH native channel per call, configured from the channel table the
 *   row passes in (the pinned table of remote.spec.ts, which mirrors the
 *   production factories of src/rigplane/web/transport/webrtc.py).
 * - Every channel the page creates is recorded (label, open/close, frame
 *   counts) in `window.__remoteSnapshot()` for the row's assertions; the
 *   handle is `window.__remoteHandle`, and `window.__apiFetch` is the
 *   production `apiFetch` (the installed bundle's, so the row can prove
 *   shutdown rejects pending and new requests).
 *
 * Nothing here serves the UI or fakes a server: the fakes stay the two
 * the harness allows (the offer relay, the station.ts microphone stub).
 */
import { bootRemote, type RemoteHandle } from '../../../src/boot';
import { apiFetch } from '../../../src/lib/api/api-fetch';
import type { LinkLostReason } from '../../../src/lib/transport/transport-bundle';

/** One row of the pinned channel table (remote.spec.ts's CHANNELS). */
export interface EntryChannelSpec {
  label: string;
  ordered: boolean;
  maxRetransmits: number | null;
}

/** What the row reads back per created channel. */
export interface RecordedChannel {
  label: string;
  readyState: string;
  opened: boolean;
  closed: boolean;
  binaryFrames: number;
  textFrames: number;
}

interface Recorded {
  channel: RTCDataChannel;
  record: Omit<RecordedChannel, 'readyState'>;
}

interface RelayWindow extends Window {
  __relayOffer?: (sdp: string, type: string) => Promise<RTCSessionDescriptionInit & { status: string }>;
  __bootRemote?: (channels: EntryChannelSpec[]) => Promise<RecordedChannel[]>;
  __remoteSnapshot?: () => {
    channels: RecordedChannel[];
    linkLost: LinkLostReason[];
    loads: number;
    pcState: string;
  };
  __remoteHandle?: RemoteHandle;
  __apiFetch?: typeof apiFetch;
}

const win = window as RelayWindow;
const recorded: Recorded[] = [];
const linkLost: LinkLostReason[] = [];
let pc: RTCPeerConnection | null = null;
// A reload destroys the injected link (PR-B's must-not); the entry counts
// `load` events so the row can prove none happened. The entry module runs
// before the first `load`, so a boot without reloads reads exactly 1.
let loads = 0;
win.addEventListener('load', () => {
  loads += 1;
});

function record(channel: RTCDataChannel): RTCDataChannel {
  // addEventListener only: the production DataChannelTransport owns the
  // channel's on* properties, and both must see every event.
  const entry: Recorded = {
    channel,
    record: { label: channel.label, opened: false, closed: false, binaryFrames: 0, textFrames: 0 },
  };
  recorded.push(entry);
  channel.addEventListener('open', () => {
    entry.record.opened = true;
  });
  channel.addEventListener('close', () => {
    entry.record.closed = true;
  });
  channel.addEventListener('message', (event: MessageEvent) => {
    if (typeof event.data === 'string') entry.record.textFrames += 1;
    else entry.record.binaryFrames += 1;
  });
  return channel;
}

function channelOptions(label: string, table: EntryChannelSpec[]): RTCDataChannelInit {
  const spec = table.find((channel) => channel.label === label);
  if (!spec) throw new Error(`no pinned channel spec for label ${label}`);
  return spec.maxRetransmits === null
    ? { ordered: spec.ordered }
    : { ordered: spec.ordered, maxRetransmits: spec.maxRetransmits };
}

/** Wait for ICE gathering to complete (no trickle), as the P0 probe does. */
function waitForIceGathering(connection: RTCPeerConnection): Promise<void> {
  let candidates = 0;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(
        new Error(
          `ICE gathering did not complete (state=${connection.iceGatheringState}, candidates=${candidates})`,
        ),
      );
    }, 10_000);
    const done = () => {
      clearTimeout(timer);
      resolve();
    };
    connection.addEventListener('icecandidate', (event) => {
      if (event.candidate) candidates += 1;
    });
    if (connection.iceGatheringState === 'complete') done();
    else
      connection.addEventListener('icegatheringstatechange', () => {
        if (connection.iceGatheringState === 'complete') done();
      });
  });
}

function waitForOpen(channel: RTCDataChannel): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`channel ${channel.label} did not open (${channel.readyState})`));
    }, 15_000);
    const check = () => {
      if (channel.readyState === 'open') {
        clearTimeout(timer);
        resolve();
      }
    };
    channel.addEventListener('open', check);
    check();
  });
}

win.__remoteSnapshot = () => ({
  channels: recorded.map(({ channel, record }) => ({ ...record, readyState: channel.readyState })),
  linkLost: [...linkLost],
  loads,
  pcState: pc?.connectionState ?? 'never',
});

win.__apiFetch = apiFetch;

win.__bootRemote = async (channels: EntryChannelSpec[]) => {
  pc = new RTCPeerConnection();
  // The api channel exists before the offer: it is what puts the data
  // section into the SDP. One per link (the RemoteLink contract).
  const api = record(pc.createDataChannel('api', channelOptions('api', channels)));
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await waitForIceGathering(pc);
  const relay = win.__relayOffer;
  if (!relay) throw new Error('__relayOffer is not exposed');
  const answer = await relay(pc.localDescription!.sdp, pc.localDescription!.type);
  if (!answer || answer.status !== 'ok') throw new Error(`offer refused: ${JSON.stringify(answer)}`);
  await pc.setRemoteDescription({ sdp: answer.sdp, type: answer.type });
  await waitForOpen(api);
  // A fresh native channel per open, on the same peer connection, from
  // the same pinned table — the seam the row exists to prove.
  const createChannel = (label: string): RTCDataChannel =>
    record(pc!.createDataChannel(label, channelOptions(label, channels)));
  const handle = bootRemote(
    { api, createChannel },
    { onLinkLost: (reason) => linkLost.push(reason) },
  );
  win.__remoteHandle = handle;
  const snapshot = win.__remoteSnapshot;
  if (!snapshot) throw new Error('the remote snapshot was not installed');
  return snapshot().channels;
};
