// Run the localStorage migration before evaluating App or any of its stores.
import './lib/migrate-legacy-storage'
import { controllerClient } from './lib/transport/controller-client'
import {
  currentBundle, dataChannelBundle, installBundle,
  type LinkLostReason, type RemoteLink, type TransportBundle,
} from './lib/transport/transport-bundle'

export type { LinkLostReason, RemoteLink } from './lib/transport/transport-bundle'

export const REMOTE_BOOT_API = 1

export interface RemoteBootOptions {
  onLinkLost(reason: LinkLostReason): void
}

export interface RemoteHandle {
  shutdown(): void
}

type ObserveTeardown = (finished: Promise<void>) => void

let activation: Promise<void> | undefined

function activateKits(): Promise<void> {
  return activation ??= Promise.all([
    import('../component-kits.config'),
    import('./component-kits/activation'),
  ]).then(([{ default: config }, { activateComponentKits }]) => activateComponentKits(config))
    .catch((error: unknown) => {
      activation = undefined
      throw error
    })
}

export async function startApp(
  bundle: TransportBundle,
  stillWanted: () => boolean = () => true,
  onRemoteTeardown?: ObserveTeardown,
) {
  try {
    await activateKits()
    if (!stillWanted()) return null
    const [{ mount }, , { default: App }] = await Promise.all([
      import('svelte'),
      import('./app.css'),
      import('./App.svelte'),
    ])
    if (!stillWanted()) return null
    const target = document.getElementById('app')
    if (!target) throw new Error('Application root #app was not found.')
    installBundle(bundle)
    target.querySelector('[data-component-kit-startup-error]')?.remove()
    return mount(App, onRemoteTeardown ? { target, props: { onRemoteTeardown } } : { target })
  } catch (error) {
    if (!stillWanted()) return null
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

let activeRemote: RemoteHandle | undefined
let retiring = Promise.resolve()

export function bootRemote(link: RemoteLink, options: RemoteBootOptions): RemoteHandle {
  activeRemote?.shutdown()
  let stopped = false
  const bundle = dataChannelBundle(link, (reason) => {
    handle.shutdown()
    options.onLinkLost(reason)
  })
  const stopWatching = controllerClient.onLoss(() => {
    if (currentBundle() === bundle) bundle.lose('session_lost')
  })
  let appTeardown: Promise<void> | undefined
  const app = retiring.then(() => stopped ? null : startApp(bundle, () => !stopped, (finished) => {
    appTeardown = finished
  }))
  const handle: RemoteHandle = {
    shutdown() {
      if (stopped) return
      stopped = true
      activeRemote = undefined
      stopWatching()
      bundle.shutdown()
      if (currentBundle() === bundle) controllerClient.lose()
      retiring = app.then(async (mounted) => {
        if (mounted === null) return
        const { unmount } = await import('svelte')
        await unmount(mounted)
        await appTeardown
      }).catch((error: unknown) => console.error('[rigplane] remote retirement failed:', error))
    },
  }
  activeRemote = handle
  void app.catch(() => handle.shutdown())
  return handle
}
