// One boot path (C5b): the component-kit activation and App mount sequence
// lives here once; an entry decides only which transport bundle it installs
// before that sequence runs.
//
// `main.ts` — the station-served page — boots `webSocketBundle()`.
// `bootRemote` — the remote-link shell's entry — installs a
// `dataChannelBundle` over four injected native channels and reports link
// loss through `onLinkLost` (attach refusal/timeout, channel closed; PTT
// release and the notification are PR-B). A closed `RTCDataChannel` cannot
// be reopened, so recovery is the shell negotiating a fresh link and
// calling `replaceChannels` on the returned handle; until then the bundle
// hands out channels that close at once with the loss reason.
import type { LinkLostReason, RemoteChannels, TransportBundle } from './lib/transport/transport-bundle'
import { dataChannelBundle, installBundle } from './lib/transport/transport-bundle'

export const REMOTE_BOOT_API = 1

export interface RemoteBootOptions {
  onLinkLost(reason: LinkLostReason): void
}

export interface RemoteHandle {
  replaceChannels(channels: RemoteChannels): void
  shutdown(): void
}

export type { LinkLostReason, RemoteChannels } from './lib/transport/transport-bundle'

export async function startApp(bundle: TransportBundle) {
  installBundle(bundle)
  try {
    const [{ default: config }, { activateComponentKits }] = await Promise.all([
      import('../component-kits.config'),
      import('./component-kits/activation'),
    ])
    await activateComponentKits(config)

    const [{ mount }, , { default: App }] = await Promise.all([
      import('svelte'),
      import('./app.css'),
      import('./App.svelte'),
    ])
    const target = document.getElementById('app')
    if (!target) throw new Error('Application root #app was not found.')
    return mount(App, { target })
  } catch (error) {
    console.error('[rigplane] startup failed:', error)
    const target = document.getElementById('app')
    if (target) {
      const alert = document.createElement('p')
      alert.dataset.componentKitStartupError = ''
      alert.setAttribute('role', 'alert')
      alert.textContent = 'RigPlane could not start. Check the console for component kit configuration details.'
      target.replaceChildren(alert)
    }
    throw error
  }
}

// Boot the same UI over an injected remote link. The handle's methods are
// the bundle's own: recovery swaps the channels, shutdown drops them.
export function bootRemote(channels: RemoteChannels, opts: RemoteBootOptions): RemoteHandle {
  const bundle = dataChannelBundle(channels, opts.onLinkLost)
  // A startup failure renders startApp's own failure surface and logs;
  // onLinkLost carries link problems, not component-kit ones.
  void startApp(bundle).catch(() => {})
  return {
    replaceChannels: (channels) => bundle.replaceChannels(channels),
    shutdown: () => bundle.shutdown(),
  }
}
