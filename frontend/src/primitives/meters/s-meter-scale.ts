export interface SmeterCalibrationPoint {
  raw: number;
  actual: number;
  label: string;
}

const MAX_RAW = 255;
const S9_DBM = -73;

function clampRaw(raw: number): number {
  return Math.max(0, Math.min(MAX_RAW, raw));
}

export function isSmeterCalibrated(calibration: readonly SmeterCalibrationPoint[]): boolean {
  return calibration.length >= 2;
}

export function getS9Raw(calibration: readonly SmeterCalibrationPoint[]): number {
  return calibration.find((point) => point.label === 'S9')?.raw ?? MAX_RAW / 2;
}

export function getScaleMaxRaw(calibration: readonly SmeterCalibrationPoint[]): number {
  return calibration[calibration.length - 1]?.raw ?? MAX_RAW;
}

export function getCalibratedScaleMaxRaw(calibration: readonly SmeterCalibrationPoint[]): number {
  return isSmeterCalibrated(calibration) ? getScaleMaxRaw(calibration) : MAX_RAW;
}

function interpolateRaw(raw: number, calibration: readonly SmeterCalibrationPoint[]): number {
  const value = clampRaw(raw);
  if (calibration.length === 0) return 0;
  if (value <= calibration[0].raw) return calibration[0].actual;
  for (let index = 0; index < calibration.length - 1; index += 1) {
    const start = calibration[index];
    const end = calibration[index + 1];
    if (value <= end.raw) {
      const t = (value - start.raw) / (end.raw - start.raw);
      return start.actual + t * (end.actual - start.actual);
    }
  }
  return calibration[calibration.length - 1].actual;
}

export function calibratedToRaw(actual: number, calibration: readonly SmeterCalibrationPoint[]): number {
  if (!isSmeterCalibrated(calibration)) return clampRaw(actual);

  const minActual = calibration[0].actual;
  const maxActual = calibration[calibration.length - 1].actual;
  const value = Math.max(minActual, Math.min(maxActual, actual));
  if (value <= minActual) return calibration[0].raw;
  for (let index = 0; index < calibration.length - 1; index += 1) {
    const start = calibration[index];
    const end = calibration[index + 1];
    if (value <= end.actual) {
      const span = end.actual - start.actual;
      const t = span === 0 ? 0 : (value - start.actual) / span;
      return start.raw + t * (end.raw - start.raw);
    }
  }
  return calibration[calibration.length - 1].raw;
}

function rawToSFloat(raw: number, calibration: readonly SmeterCalibrationPoint[]): number {
  const s9Raw = getS9Raw(calibration);
  const value = clampRaw(raw);
  const sPoints = calibration.filter((point) => /^S\d$/.test(point.label));
  if (sPoints.length < 2) return (value / s9Raw) * 9;

  for (let index = 0; index < sPoints.length - 1; index += 1) {
    const start = sPoints[index];
    const end = sPoints[index + 1];
    const startUnit = parseInt(start.label.slice(1), 10);
    const endUnit = parseInt(end.label.slice(1), 10);
    if (value <= end.raw) {
      const t = Math.max(0, (value - start.raw) / (end.raw - start.raw));
      return startUnit + t * (endUnit - startUnit);
    }
  }
  return 9;
}

export function rawToSegments(raw: number, calibration: readonly SmeterCalibrationPoint[]): number {
  const s9Raw = getS9Raw(calibration);
  const maxRaw = Math.max(s9Raw + 1, getScaleMaxRaw(calibration));
  const value = Math.max(0, Math.min(maxRaw, raw));
  if (value <= s9Raw) return (rawToSFloat(value, calibration) / 9) * 11;
  return 11 + ((value - s9Raw) / (maxRaw - s9Raw)) * 9;
}

export function rawToSUnit(raw: number, calibration: readonly SmeterCalibrationPoint[]): string {
  const value = clampRaw(raw);
  if (!isSmeterCalibrated(calibration)) return String(Math.round(value));
  const s9Raw = getS9Raw(calibration);
  if (value <= s9Raw) return `S${Math.min(9, Math.floor(rawToSFloat(value, calibration)))}`;

  const over = Math.round(interpolateRaw(value, calibration));
  return over > 0 ? `S9+${over}` : 'S9';
}

export function rawToDbm(raw: number, calibration: readonly SmeterCalibrationPoint[]): number {
  if (!isSmeterCalibrated(calibration)) return Math.round(clampRaw(raw));
  return Math.round(interpolateRaw(raw, calibration));
}

export function calibratedToSegments(actual: number, calibration: readonly SmeterCalibrationPoint[]): number {
  return rawToSegments(calibratedToRaw(actual, calibration), calibration);
}

export function calibratedToSUnit(actual: number, calibration: readonly SmeterCalibrationPoint[]): string {
  return rawToSUnit(calibratedToRaw(actual, calibration), calibration);
}

export function calibratedToDbm(
  actual: number,
  calibration: readonly SmeterCalibrationPoint[],
): number | null {
  if (!isSmeterCalibrated(calibration)) return null;
  const minActual = calibration[0].actual;
  const maxActual = calibration[calibration.length - 1].actual;
  return Math.round(S9_DBM + Math.max(minActual, Math.min(maxActual, actual)));
}

export function formatDbm(dbm: number | null): string {
  if (dbm === null) return 'uncalibrated';
  const sign = dbm < 0 ? '\u2212' : '+';
  return `${sign}${Math.abs(dbm)} dBm`;
}
