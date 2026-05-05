/**
 * Platform detection and PWA install prompt utilities.
 *
 * Extracted as pure functions for testability.
 */

export type Platform = 'ios' | 'android' | 'desktop';

const STORAGE_KEY = 'rigplane:install-dismissed';

/** Detect platform from user-agent string. */
export function detectPlatform(ua: string): Platform {
  // iOS: iPhone/iPad in Safari (not Chrome/Firefox wrappers)
  if (/iPhone|iPad|iPod/.test(ua) && /Safari/.test(ua) && !/CriOS|FxiOS/.test(ua)) {
    return 'ios';
  }
  if (/Android/.test(ua) && /Chrome/.test(ua)) {
    return 'android';
  }
  return 'desktop';
}

/** Check if app is running in standalone (installed) mode. */
export function isStandalone(): boolean {
  if (typeof window === 'undefined') return false;
  // iOS standalone check
  if ((navigator as any).standalone === true) return true;
  // Standard display-mode check
  if (typeof window.matchMedia === 'function' &&
      window.matchMedia('(display-mode: standalone)').matches) return true;
  return false;
}

/** Check if user previously dismissed the install prompt. */
export function isDismissed(): boolean {
  if (typeof localStorage === 'undefined') return false;
  return localStorage.getItem(STORAGE_KEY) === 'true';
}

/** Persist dismissal to localStorage. */
export function setDismissed(): void {
  if (typeof localStorage === 'undefined') return;
  localStorage.setItem(STORAGE_KEY, 'true');
}

/** Get instruction text for the given platform. */
export function getInstruction(platform: Platform, hasPrompt: boolean): string {
  switch (platform) {
    case 'ios':
      return 'Tap \u{1F4E4} then "Add to Home Screen"';
    case 'android':
      return hasPrompt ? '' : 'Tap \u22EE menu \u2192 "Install app"';
    case 'desktop':
      return hasPrompt ? '' : 'Install via browser menu for the best experience';
  }
}

/** Whether the platform supports a native install button (when prompt is available). */
export function hasInstallButton(platform: Platform, hasPrompt: boolean): boolean {
  return (platform === 'android' || platform === 'desktop') && hasPrompt;
}
