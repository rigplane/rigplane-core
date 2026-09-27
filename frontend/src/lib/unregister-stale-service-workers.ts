// Stale Service Worker registrations from the pre-2.x PWA build hijack
// fetch on iOS Safari (vite-plugin-pwa was removed, see vite.config.ts),
// so the page must unregister every registration it can see.
//
// This MUST run from the bundle: the server's CSP has no inline-script
// allowance, and a hash pinned in the source cannot match the built page
// reliably — the browser hashes the exact bytes between the <script> tags
// of the served index.html (MOR-2242).
export async function unregisterStaleServiceWorkers(): Promise<void> {
  if (!navigator.serviceWorker) return
  const registrations = await navigator.serviceWorker.getRegistrations()
  await Promise.all(registrations.map((registration) => registration.unregister()))
}
