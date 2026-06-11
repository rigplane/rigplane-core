import { clampFilterWidth } from '../../components-v2/panels/filter-controls';

export interface PassbandGeometry {
  leftPx: number;
  rightPx: number;
  widthPx: number;
}

function clamp(value: number, min: number, max: number): number {
  return value < min ? min : value > max ? max : value;
}

// Modes whose passband is centered on the carrier (±width/2): CW/RTTY by
// display convention, and double-sideband / FM-family modes (#1719).
const SYMMETRIC_PASSBAND_MODES = new Set([
  'CW',
  'CW-R',
  'RTTY',
  'RTTY-R',
  'AM',
  'AM-N',
  'FM',
  'FM-N',
  'WFM',
  'DATA-FM',
  'DATA-FM-N',
  'C4FM-DN',
  'C4FM-VW',
  'DV',
]);

function isSymmetricPassbandMode(normalizedMode: string): boolean {
  return SYMMETRIC_PASSBAND_MODES.has(normalizedMode);
}

export function getPassbandEdgesHz(mode: string, passbandHz: number, shiftHz: number): {
  leftHz: number;
  rightHz: number;
} {
  const normalizedMode = mode.toUpperCase();

  if (normalizedMode === 'LSB') {
    return {
      leftHz: shiftHz - passbandHz,
      rightHz: shiftHz,
    };
  }

  if (isSymmetricPassbandMode(normalizedMode)) {
    return {
      leftHz: shiftHz - passbandHz / 2,
      rightHz: shiftHz + passbandHz / 2,
    };
  }

  return {
    leftHz: shiftHz,
    rightHz: shiftHz + passbandHz,
  };
}

export function getPassbandGeometry(
  mode: string,
  passbandHz: number,
  shiftHz: number,
  spanHz: number,
  widthPx: number,
  tunePx?: number,
): PassbandGeometry | null {
  if (passbandHz <= 0 || spanHz <= 0 || widthPx <= 0) {
    return null;
  }

  const { leftHz, rightHz } = getPassbandEdgesHz(mode, passbandHz, shiftHz);
  const centerPx = tunePx ?? widthPx / 2;
  const hzToPx = widthPx / spanHz;

  const unclampedLeftPx = centerPx + leftHz * hzToPx;
  const unclampedRightPx = centerPx + rightHz * hzToPx;
  const sortedLeftPx = Math.min(unclampedLeftPx, unclampedRightPx);
  const sortedRightPx = Math.max(unclampedLeftPx, unclampedRightPx);
  const leftPx = clamp(sortedLeftPx, 0, widthPx);
  const rightPx = clamp(sortedRightPx, 0, widthPx);

  return {
    leftPx,
    rightPx,
    widthPx: Math.max(0, rightPx - leftPx),
  };
}

export function canResizeFromRightEdge(mode: string): boolean {
  return mode.toUpperCase() !== 'LSB';
}

export function getFilterWidthFromRightEdgePx(
  mode: string,
  shiftHz: number,
  spanHz: number,
  widthPx: number,
  rightEdgePx: number,
  maxHz?: number,
  stepHz?: number,
): number | null {
  if (spanHz <= 0 || widthPx <= 0 || !canResizeFromRightEdge(mode)) {
    return null;
  }

  const hzPerPx = spanHz / widthPx;
  const rightEdgeHz = (rightEdgePx - widthPx / 2) * hzPerPx;
  const normalizedMode = mode.toUpperCase();

  let widthHz: number;
  if (isSymmetricPassbandMode(normalizedMode)) {
    widthHz = (rightEdgeHz - shiftHz) * 2;
  } else {
    widthHz = rightEdgeHz - shiftHz;
  }

  return clampFilterWidth(widthHz, maxHz, stepHz);
}