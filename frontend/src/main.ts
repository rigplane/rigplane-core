// IMPORTANT: this side-effect import MUST be first.
//
// `migrate-legacy-storage` runs the v1.x → v2.x localStorage migration as a
// module-top side-effect. ES modules evaluate transitive imports BEFORE the
// importing module's body runs, so a function call here in main.ts's body
// would race against stores that read localStorage at module init
// (`layout.svelte.ts`, `theme-switcher.ts`, `lcd-contrast.svelte.ts`,
// `lcd-display-mode.svelte.ts`). By making this import the first sibling,
// its body — including the migration call — runs before any other import
// is evaluated.
import './lib/migrate-legacy-storage'
import { unregisterStaleServiceWorkers } from './lib/unregister-stale-service-workers'
import { startApp } from './boot'
import { webSocketBundle } from './lib/transport/transport-bundle'

// Stale pre-2.x Service Workers hijack fetch (see
// unregister-stale-service-workers.ts); drop them before the app mounts.
// Fire-and-forget: a failed cleanup must not block startup.
void unregisterStaleServiceWorkers().catch((error: unknown) => {
  console.warn('[rigplane] service-worker cleanup failed:', error)
})

// The station page rides WebSockets (C5b): the entry decides only the
// bundle; the boot sequence itself is boot.ts's one startApp, shared with
// bootRemote.
const app = startApp(webSocketBundle())

export default app
