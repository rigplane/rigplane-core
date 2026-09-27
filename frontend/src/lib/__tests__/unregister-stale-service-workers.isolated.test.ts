import { describe, expect, it, vi } from 'vitest'

import { unregisterStaleServiceWorkers } from '../unregister-stale-service-workers'

function installServiceWorkerContainer(registrations: unknown[]): void {
  Object.defineProperty(navigator, 'serviceWorker', {
    value: { getRegistrations: vi.fn().mockResolvedValue(registrations) },
    configurable: true,
  })
}

describe('unregisterStaleServiceWorkers', () => {
  it('unregisters every stale registration', async () => {
    const stale = [
      { unregister: vi.fn().mockResolvedValue(true) },
      { unregister: vi.fn().mockResolvedValue(true) },
    ]
    installServiceWorkerContainer(stale)
    await unregisterStaleServiceWorkers()
    expect(stale[0].unregister).toHaveBeenCalledOnce()
    expect(stale[1].unregister).toHaveBeenCalledOnce()
  })

  it('is a no-op when serviceWorker is unsupported', async () => {
    Object.defineProperty(navigator, 'serviceWorker', {
      value: undefined,
      configurable: true,
    })
    await expect(unregisterStaleServiceWorkers()).resolves.toBeUndefined()
  })
})
