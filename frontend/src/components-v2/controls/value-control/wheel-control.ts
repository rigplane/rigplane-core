import type { ScalarStepInput } from '../../../primitives/scalar/continuous-scalar.svelte';

interface WheelView { readonly editable: boolean; readonly interactionEpoch?: number }
interface WheelLease { readonly view: WheelView; wheel(event: ScalarStepInput): void }
interface WheelControl { readonly view: WheelView | null | undefined; readonly lease: WheelLease }

export function wheelControl(node: HTMLElement, initial: WheelControl) {
  let options = initial;
  let armed = false;
  let epoch = initial.view?.interactionEpoch;
  let lastTime = 0;
  let lastDirection = 0;
  const originalTitle = node.getAttribute('title');
  const outline = node.style.outline;
  const offset = node.style.outlineOffset;

  function disarm() {
    armed = false;
    lastTime = 0;
    lastDirection = 0;
    node.dataset.wheelArmed = 'false';
    node.style.outline = outline;
    node.style.outlineOffset = offset;
    node.title = [originalTitle, 'Click or press Enter to adjust with the wheel'].filter(Boolean).join(' · ');
  }
  function current() {
    const view = options.lease.view;
    if (!view.editable || view.interactionEpoch !== epoch) disarm();
    epoch = view.interactionEpoch;
    return view.editable;
  }
  function arm() {
    if (!current()) return;
    armed = true;
    node.focus({ preventScroll: true });
    node.dataset.wheelArmed = 'true';
    node.style.outline = '2px solid var(--vc-accent, #00e5ff)';
    node.style.outlineOffset = '2px';
    node.title = 'Wheel adjustment active · Esc to finish';
  }
  function pointer(event: PointerEvent) {
    if (event.button === 0 && event.isPrimary !== false) arm();
  }
  function key(event: KeyboardEvent) {
    if (event.key === 'Escape') disarm();
    else if ((event.key === 'Enter' || event.key === ' ') && !event.repeat) {
      arm();
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }
  function wheel(event: WheelEvent) {
    if (!current() || !armed || event.ctrlKey || event.metaKey || event.shiftKey
      || !Number.isFinite(event.deltaY) || event.deltaY === 0
      || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
    const scale = event.deltaMode === 0 ? 1 : event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? 800 : 0;
    if (scale === 0) return;
    const magnitude = Math.abs(event.deltaY) * scale;
    const direction = event.deltaY > 0 ? -1 : 1;
    const elapsed = event.timeStamp - lastTime;
    const burst = lastDirection === direction && elapsed > 0 && elapsed < 160;
    const steps = Math.min(8, Math.max(1, Math.floor(magnitude / 120),
      burst ? Math.floor(magnitude / Math.max(8, elapsed)) : 1));
    lastTime = event.timeStamp;
    lastDirection = direction;
    event.preventDefault();
    options.lease.wheel({ direction, fine: false, steps });
  }
  disarm();
  node.addEventListener('pointerdown', pointer, true);
  node.addEventListener('keydown', key, true);
  node.addEventListener('wheel', wheel, { passive: false });
  node.addEventListener('blur', disarm);
  node.addEventListener('pointerleave', disarm);
  node.ownerDocument.defaultView?.addEventListener('blur', disarm);
  return {
    update(next: WheelControl) {
      if (next.lease !== options.lease) disarm();
      options = next;
      if (!next.view?.editable || next.view.interactionEpoch !== epoch) disarm();
      epoch = next.view?.interactionEpoch;
    },
    destroy() {
      disarm();
      node.removeEventListener('pointerdown', pointer, true);
      node.removeEventListener('keydown', key, true);
      node.removeEventListener('wheel', wheel);
      node.removeEventListener('blur', disarm);
      node.removeEventListener('pointerleave', disarm);
      node.ownerDocument.defaultView?.removeEventListener('blur', disarm);
      delete node.dataset.wheelArmed;
      if (originalTitle === null) node.removeAttribute('title');
      else node.title = originalTitle;
    },
  };
}
