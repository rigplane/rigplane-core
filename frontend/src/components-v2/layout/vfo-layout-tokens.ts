export type VfoLayoutProfile = 'baseline' | 'wide';
export type VfoMeterVariant = 'vfo' | 'vfo-wide';

export interface VfoLayoutScaleOverrides {
  topRowScale?: number;
  frequencyScale?: number;
  badgeScale?: number;
  bridgeScale?: number;
}

export interface VfoLayoutTokens {
  bridgeWidth: string;
  bridgePadX: string;
  headerBadgeHeight: string;
  badgeInsetY: string;
  headerGroupGap: string;
  headerBadgeGap: string;
  controlStripGap: string;
  panelPadX: string;
  panelMeterPadX: string;
  panelBodyPadX: string;
  panelBodyPadBottom: string;
  panelBodyGap: string;
  displayRowGap: string;
  frequencySize: string;
  frequencyLetterSpacing: string;
  opsGap: string;
  opsPaddingY: string;
  opsStackGap: string;
  opsSecondaryMarginTop: string;
  opsSecondaryPaddingTop: string;
  opsBadgeWidth: string;
  opsBadgeHeight: string;
  opsBadgePaddingX: string;
  opsBadgeRadius: string;
  opsBadgeFontSize: string;
  headerBadgePaddingX: string;
  controlBadgePaddingX: string;
  panelBadgeRadius: string;
  controlBadgeHeight: string;
  controlBadgeMinHeight: string;
  controlBadgeFontSize: string;
  meterVariant: VfoMeterVariant;
}

interface VfoLayoutTokenBase {
  referenceWidth: number;
  bridgeWidth: number;
  bridgePadX: number;
  headerBadgeHeight: number;
  badgeInsetY: number;
  headerGroupGap: number;
  headerBadgeGap: number;
  panelPadX: number;
  panelMeterPadX: number;
  panelBodyPadX: number;
  panelBodyPadBottom: number;
  panelBodyGap: number;
  controlStripGap: number;
  displayRowGap: number;
  frequencySize: number;
  frequencyLetterSpacing: number;
  opsGap: number;
  opsPaddingY: number;
  opsStackGap: number;
  opsSecondaryMarginTop: number;
  opsSecondaryPaddingTop: number;
  opsBadgeWidth: number;
  opsBadgeHeight: number;
  opsBadgePaddingX: number;
  opsBadgeRadius: number;
  opsBadgeFontSize: number;
  headerBadgePaddingX: number;
  controlBadgePaddingX: number;
  panelBadgeRadius: number;
  controlBadgeMinHeight: number;
  controlBadgeFontSize: number;
  meterVariant: VfoMeterVariant;
}

// Top-row scaling is intentionally centralized here. Do not tune frequency,
// meter, and badge sizes independently in component CSS. If the composition
// needs visual changes, adjust the scale model or the explicit override knobs
// below so all top-row elements stay in proportion across resolutions.
const VFO_LAYOUT_BASE: Record<VfoLayoutProfile, VfoLayoutTokenBase> = {
  baseline: {
    referenceWidth: 1360,
    bridgeWidth: 132,
    bridgePadX: 4,
    headerBadgeHeight: 12,
    badgeInsetY: 3,
    headerGroupGap: 5,
    headerBadgeGap: 3,
    panelPadX: 10,
    panelMeterPadX: 6,
    panelBodyPadX: 10,
    panelBodyPadBottom: 0,
    panelBodyGap: 4,
    controlStripGap: 4,
    displayRowGap: 12,
    frequencySize: 22,
    frequencyLetterSpacing: 0.025,
    opsGap: 4,
    opsPaddingY: 4,
    opsStackGap: 4,
    opsSecondaryMarginTop: 0,
    opsSecondaryPaddingTop: 4,
    opsBadgeWidth: 62,
    opsBadgeHeight: 21,
    opsBadgePaddingX: 8,
    opsBadgeRadius: 4,
    opsBadgeFontSize: 10,
    headerBadgePaddingX: 5,
    controlBadgePaddingX: 6,
    panelBadgeRadius: 3,
    controlBadgeMinHeight: 16,
    controlBadgeFontSize: 7,
    meterVariant: 'vfo',
  },
  wide: {
    referenceWidth: 1600,
    bridgeWidth: 132,
    bridgePadX: 5,
    headerBadgeHeight: 12,
    badgeInsetY: 3,
    headerGroupGap: 5,
    headerBadgeGap: 3,
    panelPadX: 10,
    panelMeterPadX: 6,
    panelBodyPadX: 10,
    panelBodyPadBottom: 0,
    panelBodyGap: 4,
    controlStripGap: 4,
    displayRowGap: 12,
    frequencySize: 22,
    frequencyLetterSpacing: 0.025,
    opsGap: 4,
    opsPaddingY: 4,
    opsStackGap: 4,
    opsSecondaryMarginTop: 0,
    opsSecondaryPaddingTop: 5,
    opsBadgeWidth: 64,
    opsBadgeHeight: 21,
    opsBadgePaddingX: 8,
    opsBadgeRadius: 4,
    opsBadgeFontSize: 10,
    headerBadgePaddingX: 5,
    controlBadgePaddingX: 6,
    panelBadgeRadius: 3,
    controlBadgeMinHeight: 16,
    controlBadgeFontSize: 7,
    meterVariant: 'vfo-wide',
  },
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function toPx(value: number): string {
  return `${Number(value.toFixed(2))}px`;
}

function toEm(value: number): string {
  return `${Number(value.toFixed(4))}em`;
}

function parseOverrideValue(value: string | null): number | undefined {
  if (!value) {
    return undefined;
  }

  const parsed = Number(value);
  if (!Number.isFinite(parsed)) {
    return undefined;
  }

  return clamp(parsed, 0.5, 1.8);
}

function resolveAutoTopRowScale(profile: VfoLayoutProfile, width?: number | null): number {
  if (width == null) {
    return 1;
  }

  const { referenceWidth } = VFO_LAYOUT_BASE[profile];
  return clamp(width / referenceWidth, 0.9, 1.12);
}

export function parseVfoLayoutScaleOverrides(search?: string | URLSearchParams | null): VfoLayoutScaleOverrides {
  if (!search) {
    return {};
  }

  const params = typeof search === 'string'
    ? new URLSearchParams(search.startsWith('?') ? search.slice(1) : search)
    : search;

  return {
    topRowScale: parseOverrideValue(params.get('vfoScale')),
    frequencyScale: parseOverrideValue(params.get('vfoFreqScale')),
    badgeScale: parseOverrideValue(params.get('vfoBadgeScale')),
    bridgeScale: parseOverrideValue(params.get('vfoBridgeScale')),
  };
}

export function resolveVfoLayoutProfile(width?: number | null): VfoLayoutProfile {
  if (width == null) {
    return 'baseline';
  }

  return width >= 1500 ? 'wide' : 'baseline';
}

export function getVfoLayoutTokens(
  profile: VfoLayoutProfile,
  options: { width?: number | null; overrides?: VfoLayoutScaleOverrides } = {},
): VfoLayoutTokens {
  const base = VFO_LAYOUT_BASE[profile];
  const overrides = options.overrides ?? {};
  const sharedScale = resolveAutoTopRowScale(profile, options.width) * (overrides.topRowScale ?? 1);
  const bridgeScale = clamp(sharedScale * (overrides.bridgeScale ?? 1), 0.85, 1.25);
  const frequencyScale = clamp(sharedScale * (overrides.frequencyScale ?? 1), 0.75, 1.3);
  const badgeScale = clamp(sharedScale * (overrides.badgeScale ?? 1), 0.85, 1.25);

  const headerBadgeHeight = Math.round(base.headerBadgeHeight * badgeScale);
  const badgeInsetY = Math.round(base.badgeInsetY * badgeScale);
  const panelBodyGap = Math.round(base.panelBodyGap * badgeScale);
  const controlBadgeHeight = Math.round(base.controlBadgeMinHeight * badgeScale);

  return {
    bridgeWidth: toPx(base.bridgeWidth * bridgeScale),
    bridgePadX: toPx(base.bridgePadX * bridgeScale),
    headerBadgeHeight: toPx(headerBadgeHeight),
    badgeInsetY: toPx(badgeInsetY),
    headerGroupGap: toPx(base.headerGroupGap * badgeScale),
    headerBadgeGap: toPx(base.headerBadgeGap * badgeScale),
    controlStripGap: toPx(base.controlStripGap * badgeScale),
    panelPadX: toPx(base.panelPadX),
    panelMeterPadX: toPx(base.panelMeterPadX),
    panelBodyPadX: toPx(base.panelBodyPadX),
    panelBodyPadBottom: toPx(base.panelBodyPadBottom),
    panelBodyGap: toPx(panelBodyGap),
    displayRowGap: toPx(base.displayRowGap),
    frequencySize: toPx(base.frequencySize * frequencyScale),
    frequencyLetterSpacing: toEm(base.frequencyLetterSpacing),
    opsGap: toPx(base.opsGap * bridgeScale),
    opsPaddingY: toPx(base.opsPaddingY * bridgeScale),
    opsStackGap: toPx(base.opsStackGap * bridgeScale),
    opsSecondaryMarginTop: toPx(base.opsSecondaryMarginTop),
    opsSecondaryPaddingTop: toPx(base.opsSecondaryPaddingTop * bridgeScale),
    opsBadgeWidth: toPx(base.opsBadgeWidth * bridgeScale),
    opsBadgeHeight: toPx(base.opsBadgeHeight * bridgeScale),
    opsBadgePaddingX: toPx(base.opsBadgePaddingX * bridgeScale),
    opsBadgeRadius: toPx(base.opsBadgeRadius * bridgeScale),
    opsBadgeFontSize: toPx(base.opsBadgeFontSize * bridgeScale),
    headerBadgePaddingX: toPx(base.headerBadgePaddingX * badgeScale),
    controlBadgePaddingX: toPx(base.controlBadgePaddingX * badgeScale),
    panelBadgeRadius: toPx(base.panelBadgeRadius * badgeScale),
    controlBadgeHeight: toPx(controlBadgeHeight),
    controlBadgeMinHeight: toPx(controlBadgeHeight),
    controlBadgeFontSize: toPx(base.controlBadgeFontSize * badgeScale),
    meterVariant: base.meterVariant,
  };
}

export function vfoLayoutStyleVars(
  profile: VfoLayoutProfile,
  options: { width?: number | null; overrides?: VfoLayoutScaleOverrides } = {},
): string {
  const tokens = getVfoLayoutTokens(profile, options);
  const vars: Record<string, string> = {
    '--vfo-bridge-width': tokens.bridgeWidth,
    '--vfo-bridge-pad-x': tokens.bridgePadX,
    '--vfo-header-badge-height': tokens.headerBadgeHeight,
    '--vfo-badge-inset-y': tokens.badgeInsetY,
    '--vfo-header-group-gap': tokens.headerGroupGap,
    '--vfo-header-badge-gap': tokens.headerBadgeGap,
    '--vfo-control-strip-gap': tokens.controlStripGap,
    '--vfo-panel-pad-x': tokens.panelPadX,
    '--vfo-panel-meter-pad-x': tokens.panelMeterPadX,
    '--vfo-panel-body-pad-x': tokens.panelBodyPadX,
    '--vfo-panel-body-pad-bottom': tokens.panelBodyPadBottom,
    '--vfo-panel-body-gap': tokens.panelBodyGap,
    '--vfo-display-row-gap': tokens.displayRowGap,
    '--vfo-frequency-size': tokens.frequencySize,
    '--vfo-frequency-letter-spacing': tokens.frequencyLetterSpacing,
    '--vfo-ops-gap': tokens.opsGap,
    '--vfo-ops-padding-y': tokens.opsPaddingY,
    '--vfo-ops-stack-gap': tokens.opsStackGap,
    '--vfo-ops-secondary-margin-top': tokens.opsSecondaryMarginTop,
    '--vfo-ops-secondary-padding-top': tokens.opsSecondaryPaddingTop,
    '--vfo-ops-badge-width': tokens.opsBadgeWidth,
    '--vfo-ops-badge-height': tokens.opsBadgeHeight,
    '--vfo-ops-badge-padding-x': tokens.opsBadgePaddingX,
    '--vfo-ops-badge-radius': tokens.opsBadgeRadius,
    '--vfo-ops-badge-font-size': tokens.opsBadgeFontSize,
    '--vfo-header-badge-padding-x': tokens.headerBadgePaddingX,
    '--vfo-control-badge-padding-x': tokens.controlBadgePaddingX,
    '--vfo-panel-badge-radius': tokens.panelBadgeRadius,
    '--vfo-control-badge-height': tokens.controlBadgeHeight,
    '--vfo-control-badge-min-height': tokens.controlBadgeMinHeight,
    '--vfo-control-badge-font-size': tokens.controlBadgeFontSize,
  };

  return Object.entries(vars)
    .map(([name, value]) => `${name}: ${value}`)
    .join('; ');
}