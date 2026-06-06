export interface BadgeItem {
  label: string;
  active: boolean;
  color: string;
}

/**
 * Returns the badge color for a given badge key and receiver by reading from CSS custom properties.
 * 
 * Fallback chain (in order):
 * 1. --v2-badge-{receiver}-{type}-color (e.g., --v2-badge-main-digi-sel-color)
 * 2. --v2-badge-{type}-color (e.g., --v2-badge-digi-sel-color)
 * 3. --v2-receiver-{receiver}-accent (e.g., --v2-receiver-main-accent)
 * 4. --v2-badge-default-color
 * 5. 'cyan' (hardcoded fallback)
 * 
 * Theme files can override per-receiver or shared badge colors:
 * 
 * Example 1: Receiver-specific override
 *   --v2-badge-main-digi-sel-color: cyan;   // MAIN DIGI-SEL cyan
 *   --v2-badge-sub-digi-sel-color: green;   // SUB DIGI-SEL green
 * 
 * Example 2: Shared badge color
 *   --v2-badge-nr-color: orange;  // Both MAIN and SUB NR orange
 * 
 * Example 3: Receiver accent (automatic MAIN/SUB distinction)
 *   --v2-receiver-main-accent: cyan;
 *   --v2-receiver-sub-accent: orange;
 *   // Badges without specific color use receiver accent
 */
function _badgeColor(key: string, receiver: 'main' | 'sub'): string {
  if (typeof window === 'undefined' || !document.documentElement) {
    return 'cyan';  // SSR fallback
  }
  
  // Normalize key: "DIGI-SEL" → "digi-sel", "IP+" → "ip-plus"
  const normalizedKey = key.toLowerCase().replace(/\+/g, '-plus');
  const styles = getComputedStyle(document.documentElement);
  
  // 1. Try receiver-specific badge token
  const receiverBadgeToken = `--v2-badge-${receiver}-${normalizedKey}-color`;
  let color = styles.getPropertyValue(receiverBadgeToken).trim();
  if (color) return color;
  
  // 2. Try badge-agnostic token
  const badgeToken = `--v2-badge-${normalizedKey}-color`;
  color = styles.getPropertyValue(badgeToken).trim();
  if (color) return color;
  
  // 3. Fall back to receiver accent
  const accentToken = `--v2-receiver-${receiver}-accent`;
  color = styles.getPropertyValue(accentToken).trim();
  if (color) return color;
  
  // 4. Fall back to default badge color
  const defaultToken = '--v2-badge-default-color';
  color = styles.getPropertyValue(defaultToken).trim();
  if (color) return color;
  
  // 5. Hardcoded fallback
  return 'cyan';
}

/**
 * Converts a badges record into a flat array of BadgeItem objects.
 *
 * Rules:
 * - Keys present in the record are always included (even if value is false).
 * - boolean true  → label = KEY (uppercased), active = true
 * - boolean false → label = KEY (uppercased), active = false
 * - string value  → label = value (used as-is, e.g. 'P1', 'AUTO'), active = true
 * 
 * @param badges - Badge record from radio state
 * @param receiver - 'main' or 'sub' receiver (affects badge colors)
 */
export function formatBadges(
  badges: Record<string, boolean | string>,
  receiver: 'main' | 'sub' = 'main'
): BadgeItem[] {
  return Object.entries(badges).map(([key, value]) => {
    if (typeof value === 'string') {
      return { label: value, active: true, color: _badgeColor(key, receiver) };
    }
    return { label: key.toUpperCase(), active: value, color: _badgeColor(key, receiver) };
  });
}

/**
 * Formats a RIT/XIT offset (given in Hz) as a compact signed kHz string,
 * e.g. '+0.12 kHz'. The value stays in Hz; only the display is kHz. (MOR-480)
 */
export function formatRitOffset(offsetHz: number): string {
  const sign = offsetHz >= 0 ? '+' : '−';
  const khz = (Math.abs(offsetHz) / 1000).toFixed(2);
  return `${sign}${khz} kHz`;
}
