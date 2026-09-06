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

async function startApp() {
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

const app = startApp()

export default app
