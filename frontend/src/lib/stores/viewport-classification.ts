export function isMobileViewport(width: number, height: number, hasTouch: boolean): boolean {
  return width < 640 || (hasTouch && height < 500);
}
