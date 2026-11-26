import { startApp } from './boot'
import { unregisterStaleServiceWorkers } from './lib/unregister-stale-service-workers'
import { webSocketBundle } from './lib/transport/transport-bundle'

// Stale pre-2.x Service Workers hijack fetch (see
// unregister-stale-service-workers.ts); drop them before the app mounts.
// Fire-and-forget: a failed cleanup must not block startup.
void unregisterStaleServiceWorkers().catch((error: unknown) => {
  console.warn('[rigplane] service-worker cleanup failed:', error)
})

export default startApp(webSocketBundle())
