// One boot path (C5b): the component-kit activation and App mount sequence
// lives here once; an entry decides only which transport bundle it installs
// before that sequence runs.
//
// `main.ts` — the station-served page — boots `webSocketBundle()`.
// `bootRemote` — the remote-link shell's entry — installs a
// `dataChannelBundle` over an injected `RemoteLink` and reports link loss
// through `onLinkLost` (attach refusal/timeout, channel closed; PTT release
// and the notification are PR-B). A closed `RTCDataChannel` cannot be
// reopened, so recovery is the shell negotiating a fresh link and booting
// again on it; until then the bundle hands out channels that close at once
// with the loss reason.
import type { LinkLostReason, RemoteLink, TransportBundle } from './lib/transport/transport-bundle'
import { dataChannelBundle, installBundle } from './lib/transport/transport-bundle'

export const REMOTE_BOOT_API = 1

export interface RemoteBootOptions {
  onLinkLost(reason: LinkLostReason): void
}

export interface RemoteHandle {
  shutdown(): void
}

export type { LinkLostReason, RemoteLink } from './lib/transport/transport-bundle'

export async function startApp(bundle: TransportBundle, stillWanted: () => boolean = () => true) {
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
    if (!stillWanted()) return null // shutdown outran the async chain: never mount
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

// Boot the same UI over an injected remote link. `shutdown` drops the link
// (the bundle's own teardown) and unmounts the App; a start still in flight
// — async kit activation or imports — never mounts after it.
export function bootRemote(link: RemoteLink, opts: RemoteBootOptions): RemoteHandle {
  const bundle = dataChannelBundle(link, opts.onLinkLost)
  let down = false
  let app: Awaited<ReturnType<typeof startApp>> = null
  const unmountApp = (): void => {
    if (app === null) return
    const mounted = app
    app = null
    void import('svelte').then(({ unmount }) => unmount(mounted))
  }
  // A startup failure renders startApp's own failure surface and logs;
  // onLinkLost carries link problems, not component-kit ones.
  void startApp(bundle, () => !down).then((mounted) => {
    if (mounted === null) return
    app = mounted
    if (down) unmountApp() // shutdown arrived while the mount was resolving
  }).catch(() => {})
  return {
    shutdown: () => {
      if (down) return
      down = true
      bundle.shutdown()
      unmountApp()
    },
  }
}
