import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import ValueControl from '../ValueControl.svelte';
import HBarRenderer from '../HBarRenderer.svelte';
import BipolarRenderer from '../BipolarRenderer.svelte';
import DiscreteRenderer from '../DiscreteRenderer.svelte';
import ProfessionalKnob from '../skins/ProfessionalKnob.svelte';
import type { Skin } from '../skin';
import {
  createBipolarContinuousScalarPolicy,
  createContinuousScalar,
  createDiscreteContinuousScalarPolicy,
  createHBarContinuousScalarPolicy,
  createKnobContinuousScalarPolicy,
  createRenderedNativeRangeContinuousScalarPolicy,
} from '../../../../primitives/scalar/continuous-scalar.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountControl(props: ComponentProps<typeof ValueControl>) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(ValueControl, { target, props });
  flushSync();
  components.push(component);
  return target;
}

function getSlider(target: HTMLElement) {
  return target.querySelector('[role="slider"]') as HTMLElement;
}

function getLabel(target: HTMLElement) {
  return target.querySelector('.vc-label') as HTMLElement;
}

function getValueDisplay(target: HTMLElement) {
  return target.querySelector('.vc-value') as HTMLElement;
}

beforeEach(() => {
  components = [];
});

afterEach(() => {
  components.forEach(c => unmount(c));
  document.body.innerHTML = '';
});

describe('ValueControl wrapper', () => {
  it('renders a caller-owned HBar binding synchronously without raw bounds', () => {
    const binding = createContinuousScalar(
      () => ({
        evidence: 'reading' as const,
        reading: { status: 'known' as const, value: 40 },
        ownerKey: 'direct-hbar',
        domain: { min: 20, max: 60, step: 5, defaultValue: null, fineStepDivisor: 5 },
        enabled: true,
        request: vi.fn(),
      }),
      createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 0 }),
    );

    const target = mountControl({ binding, label: 'Direct', renderer: 'hbar' });

    expect(getValueDisplay(target)?.textContent).toBe('40');
    expect(getSlider(target).getAttribute('aria-valuemin')).toBe('20');
    expect(getSlider(target).getAttribute('aria-valuemax')).toBe('60');
    expect(target.querySelector('.vc-hbar')?.getAttribute('style'))
      .toContain('--vc-fill-percent: 50%');
    binding.destroy();
  });

  it('renders a caller-owned Bipolar binding synchronously without raw bounds', () => {
    const binding = createContinuousScalar(
      () => ({
        evidence: 'reading' as const,
        reading: { status: 'known' as const, value: -25 },
        ownerKey: 'direct-bipolar',
        domain: { min: -100, max: 100, step: 5, defaultValue: 0, fineStepDivisor: 10 },
        enabled: true,
        request: vi.fn(),
      }),
      createBipolarContinuousScalarPolicy({ debounceMs: 0 }),
    );

    const target = mountControl({ binding, label: 'Direct', renderer: 'bipolar' });

    expect(getValueDisplay(target)?.textContent).toBe('-25');
    expect(getSlider(target).getAttribute('aria-valuemin')).toBe('-100');
    expect(getSlider(target).getAttribute('aria-valuemax')).toBe('100');
    expect(target.querySelector('.vc-bipolar')?.getAttribute('style'))
      .toContain('--vc-current: 37.5%');
    binding.destroy();
  });

  it('renders a caller-owned Discrete binding synchronously without raw bounds', () => {
    const binding = createContinuousScalar(
      () => ({
        evidence: 'reading' as const,
        reading: { status: 'known' as const, value: 4 },
        ownerKey: 'direct-discrete',
        domain: { min: 0, max: 8, step: 1, defaultValue: null, fineStepDivisor: 10 },
        enabled: true,
        request: vi.fn(),
      }),
      createDiscreteContinuousScalarPolicy({ debounceMs: 0 }),
    );

    const target = mountControl({
      binding, label: 'Direct discrete', renderer: 'discrete', tickStyle: 'led',
    });

    expect(getValueDisplay(target)?.textContent).toBe('4');
    expect(getSlider(target).getAttribute('aria-valuemin')).toBe('0');
    expect(getSlider(target).getAttribute('aria-valuemax')).toBe('8');
    expect(target.querySelector('.vc-discrete')?.getAttribute('style'))
      .toContain('--vc-fill-percent: 50%');
    binding.destroy();
  });

  it('renders a caller-owned Knob binding synchronously without raw bounds', () => {
    const binding = createContinuousScalar(
      () => ({
        evidence: 'reading' as const,
        reading: { status: 'known' as const, value: 40 },
        ownerKey: 'direct-knob',
        domain: { min: 20, max: 60, step: 5, defaultValue: null, fineStepDivisor: 5 },
        enabled: true,
        request: vi.fn(),
      }),
      createKnobContinuousScalarPolicy({ debounceMs: 0 }),
    );

    const target = mountControl({ binding, label: 'Direct knob', renderer: 'knob' });

    expect(target.querySelector('.vc-knob-value')?.textContent).toBe('40');
    expect(getSlider(target).getAttribute('aria-valuemin')).toBe('20');
    expect(getSlider(target).getAttribute('aria-valuemax')).toBe('60');
    expect(getSlider(target).getAttribute('aria-valuenow')).toBe('40');
    binding.destroy();
  });

  it('renders HBarRenderer when renderer is hbar', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Test',
      renderer: 'hbar',
      onChange: vi.fn(),
    });
    expect(target.querySelector('.vc-hbar')).toBeTruthy();
  });

  it('renders BipolarRenderer when renderer is bipolar', () => {
    const target = mountControl({
      value: 0,
      min: -100,
      max: 100,
      step: 1,
      label: 'Test',
      renderer: 'bipolar',
      onChange: vi.fn(),
    });
    expect(target.querySelector('.vc-bipolar')).toBeTruthy();
  });

  it('renders KnobRenderer when renderer is knob', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Test',
      renderer: 'knob',
      onChange: vi.fn(),
    });
    expect(target.querySelector('.vc-knob')).toBeTruthy();
  });

  it.each([
    ['hbar', '.vc-bipolar'],
    ['bipolar', '.vc-hbar'],
    ['discrete', '.vc-discrete'],
    ['knob', '.pro-knob'],
  ] as const)('uses the typed skin map for %s', (renderer, selector) => {
    const skin: Skin = {
      name: 'complete-test-skin',
      hbar: BipolarRenderer,
      bipolar: HBarRenderer,
      discrete: DiscreteRenderer,
      knob: ProfessionalKnob,
    };
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 10,
      label: 'Complete skin',
      renderer,
      skin,
      onChange: vi.fn(),
    });

    expect(target.querySelector(selector)).toBeTruthy();
    expect(getSlider(target).getAttribute('aria-valuenow')).toBe('50');
  });
});

describe('HBarRenderer', () => {
  it('renders label', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Volume',
      renderer: 'hbar',
      onChange: vi.fn(),
    });
    expect(getLabel(target)?.textContent).toBe('Volume');
  });

  it('renders value', () => {
    const target = mountControl({
      value: 75,
      min: 0,
      max: 100,
      step: 1,
      label: 'Level',
      renderer: 'hbar',
      onChange: vi.fn(),
    });
    expect(getValueDisplay(target)?.textContent).toContain('75');
  });

  it('renders value with unit', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Freq',
      unit: 'Hz',
      renderer: 'hbar',
      onChange: vi.fn(),
    });
    expect(getValueDisplay(target)?.textContent).toContain('Hz');
  });

  it('uses custom displayFn', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Custom',
      displayFn: (v) => `${v}%`,
      renderer: 'hbar',
      onChange: vi.fn(),
    });
    expect(getValueDisplay(target)?.textContent).toBe('50%');
  });

  it('has correct ARIA attributes', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'ARIA Test',
      renderer: 'hbar',
      onChange: vi.fn(),
    });
    const slider = getSlider(target);
    expect(slider?.getAttribute('role')).toBe('slider');
    expect(slider?.getAttribute('aria-valuemin')).toBe('0');
    expect(slider?.getAttribute('aria-valuemax')).toBe('100');
    expect(slider?.getAttribute('aria-valuenow')).toBe('50');
    expect(slider?.getAttribute('aria-label')).toBe('ARIA Test');
  });

  it('applies compact class', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Compact',
      renderer: 'hbar',
      compact: true,
      onChange: vi.fn(),
    });
    expect(target.querySelector('.compact')).toBeTruthy();
  });

  it('applies disabled class and aria-disabled', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Disabled',
      renderer: 'hbar',
      disabled: true,
      onChange: vi.fn(),
    });
    expect(target.querySelector('.disabled')).toBeTruthy();
    expect(getSlider(target)?.getAttribute('aria-disabled')).toBe('true');
  });

  it('applies custom accent color', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Color',
      renderer: 'hbar',
      accentColor: '#FF6600',
      onChange: vi.fn(),
    });
    const wrapper = target.querySelector('.vc-hbar') as HTMLElement;
    expect(wrapper?.getAttribute('style')).toContain('#FF6600');
  });

  it('sets data-shortcut-hint attribute', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Shortcut',
      renderer: 'hbar',
      shortcutHint: 'Ctrl+V',
      onChange: vi.fn(),
    });
    const wrapper = target.querySelector('.vc-hbar') as HTMLElement;
    expect(wrapper?.getAttribute('data-shortcut-hint')).toBe('Ctrl+V');
  });

  it('fires onChange on keyboard navigation', () => {
    const onChange = vi.fn();
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 10,
      label: 'Keyboard',
      renderer: 'hbar',
      debounceMs: 0,
      onChange,
    });
    const slider = getSlider(target);
    slider?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(onChange).toHaveBeenCalledWith(60);
  });

  it('fires onChange with fine step on shift+keyboard', () => {
    const onChange = vi.fn();
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 10,
      fineStepDivisor: 10,
      label: 'Fine',
      renderer: 'hbar',
      debounceMs: 0,
      onChange,
    });
    const slider = getSlider(target);
    slider?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', shiftKey: true, bubbles: true }));
    expect(onChange).toHaveBeenCalledWith(51);
  });

  it('resets to default on double-click', () => {
    const onChange = vi.fn();
    const target = mountControl({
      value: 75,
      min: 0,
      max: 100,
      step: 1,
      defaultValue: 50,
      label: 'Reset',
      renderer: 'hbar',
      debounceMs: 0,
      onChange,
    });
    const slider = getSlider(target);
    slider?.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    expect(onChange).toHaveBeenCalledWith(50);
  });

  it('supports legacy onchange prop', () => {
    const onchange = vi.fn();
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 10,
      label: 'Legacy',
      renderer: 'hbar',
      debounceMs: 0,
      onChange: undefined as any,
      onchange,
    });
    const slider = getSlider(target);
    slider?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(onchange).toHaveBeenCalledWith(60);
  });

  it('sends one command per distinct value while a drag jitters inside one step', () => {
    const request = vi.fn<(value: number) => void>();
    const binding = createContinuousScalar(
      () => ({
        evidence: 'reading' as const,
        reading: { status: 'known' as const, value: 100 },
        ownerKey: 'nb-level',
        domain: { min: 0, max: 255, step: 1, defaultValue: null, fineStepDivisor: 1 },
        enabled: true,
        request,
      }),
      createRenderedNativeRangeContinuousScalarPolicy(),
    );
    const target = mountControl({ binding, label: 'NB level', renderer: 'hbar' });
    const slider = getSlider(target);
    const container = target.querySelector('.vc-hbar') as HTMLDivElement;
    vi.spyOn(container, 'getBoundingClientRect')
      .mockReturnValue({ left: 0, width: 255 } as DOMRect);
    Object.assign(slider, {
      setPointerCapture: vi.fn(),
      releasePointerCapture: vi.fn(),
      hasPointerCapture: () => false,
    });

    slider.dispatchEvent(new PointerEvent('pointerdown', {
      pointerId: 1, clientX: 138, bubbles: true,
    }));
    for (const clientX of [138.1, 137.9, 138.4, 138.05, 137.6]) {
      slider.dispatchEvent(new PointerEvent('pointermove', {
        pointerId: 1, clientX, bubbles: true,
      }));
    }
    slider.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1, bubbles: true }));

    expect(request.mock.calls).toEqual([[138]]);
    binding.destroy();
  });
});

describe('BipolarRenderer', () => {
  it('uses keyboardStep for origin-anchored bipolar keyboard gestures', () => {
    const onChange = vi.fn();
    const target = mountControl({
      value: 0,
      min: -9999,
      max: 9999,
      step: 1,
      defaultValue: 0,
      keyboardStep: 50,
      label: 'RIT',
      renderer: 'bipolar',
      debounceMs: 0,
      onChange,
    });
    const slider = getSlider(target);

    expect(onChange).not.toHaveBeenCalled();

    slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }));
    slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    expect(onChange.mock.calls).toEqual([[50], [0], [-50]]);

    slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true }));
    slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
    slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(onChange.mock.calls.slice(-3)).toEqual([[-9999], [9999], [9999]]);
  });

  it('keeps pointer and wheel interaction on the radio lattice', () => {
    const onChange = vi.fn();
    const target = mountControl({
      value: 0,
      min: -9999,
      max: 9999,
      step: 1,
      defaultValue: 0,
      keyboardStep: 50,
      label: 'RIT',
      renderer: 'bipolar',
      debounceMs: 0,
      onChange,
    });
    const slider = getSlider(target);
    const container = target.querySelector('.vc-bipolar') as HTMLDivElement;
    vi.spyOn(container, 'getBoundingClientRect').mockReturnValue({ left: 0, width: 100 } as DOMRect);
    Object.assign(slider, { setPointerCapture: vi.fn(), releasePointerCapture: vi.fn() });

    slider.dispatchEvent(new PointerEvent('pointerdown', { pointerId: 1, clientX: 50.01, bubbles: true }));
    slider.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true, cancelable: true }));

    expect(onChange.mock.calls.flat().every((value) => Number.isInteger(value))).toBe(true);
  });

  it.each([0, -50, Number.NaN, 2.5])('falls back to the declared lattice for invalid keyboardStep %s', (keyboardStep) => {
    const onChange = vi.fn();
    const target = mountControl({
      value: 0,
      min: -9999,
      max: 9999,
      step: 5,
      defaultValue: 0,
      keyboardStep,
      label: 'RIT',
      renderer: 'bipolar',
      debounceMs: 0,
      onChange,
    });

    getSlider(target).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));

    expect(onChange).toHaveBeenCalledWith(5);
  });

  it('does not emit NaN for a non-finite keyboardStep', () => {
    const onChange = vi.fn();
    const target = mountControl({
      value: 0,
      min: -9999,
      max: 9999,
      step: 5,
      defaultValue: 0,
      keyboardStep: Infinity,
      label: 'RIT',
      renderer: 'bipolar',
      debounceMs: 0,
      onChange,
    });

    getSlider(target).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));

    expect(onChange).toHaveBeenCalledWith(5);
    expect(onChange.mock.calls.flat().every(Number.isFinite)).toBe(true);
  });

  it('renders polarity markers', () => {
    const target = mountControl({
      value: 0,
      min: -100,
      max: 100,
      step: 1,
      label: 'Bipolar',
      renderer: 'bipolar',
      onChange: vi.fn(),
    });
    const axis = target.querySelector('.vc-axis')?.textContent ?? '';
    expect(axis).toContain('-');
    expect(axis).toContain('0');
    expect(axis).toContain('+');
  });

  it('formats positive values with plus sign', () => {
    const target = mountControl({
      value: 50,
      min: -100,
      max: 100,
      step: 1,
      label: 'Positive',
      renderer: 'bipolar',
      onChange: vi.fn(),
    });
    expect(getValueDisplay(target)?.textContent).toContain('+50');
  });

  it('formats zero without sign', () => {
    const target = mountControl({
      value: 0,
      min: -100,
      max: 100,
      step: 1,
      label: 'Zero',
      renderer: 'bipolar',
      onChange: vi.fn(),
    });
    expect(getValueDisplay(target)?.textContent?.trim()).toBe('0');
  });

  it('sets centered fill variables', () => {
    const target = mountControl({
      value: -50,
      min: -100,
      max: 100,
      step: 1,
      label: 'Center',
      renderer: 'bipolar',
      onChange: vi.fn(),
    });
    const wrapper = target.querySelector('.vc-bipolar') as HTMLElement;
    const style = wrapper?.getAttribute('style') ?? '';
    expect(style).toContain('--vc-center: 50%');
    expect(style).toContain('--vc-fill-start: 25%');
    expect(style).toContain('--vc-fill-end: 50%');
  });

  it('resets to 0 on double-click by default', () => {
    const onChange = vi.fn();
    const target = mountControl({
      value: 75,
      min: -100,
      max: 100,
      step: 1,
      label: 'Reset Zero',
      renderer: 'bipolar',
      debounceMs: 0,
      onChange,
    });
    const slider = getSlider(target);
    slider?.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    expect(onChange).toHaveBeenCalledWith(0);
  });
});

describe('KnobRenderer', () => {
  it('renders SVG knob', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Knob',
      renderer: 'knob',
      onChange: vi.fn(),
    });
    expect(target.querySelector('.vc-knob-svg')).toBeTruthy();
  });

  it('renders value display', () => {
    const target = mountControl({
      value: 75,
      min: 0,
      max: 100,
      step: 1,
      label: 'Knob Value',
      renderer: 'knob',
      onChange: vi.fn(),
    });
    const valueDisplay = target.querySelector('.vc-knob-value');
    expect(valueDisplay?.textContent).toContain('75');
  });

  it('renders tick marks when tickCount > 0', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Ticks',
      renderer: 'knob',
      tickCount: 5,
      onChange: vi.fn(),
    });
    const ticks = target.querySelectorAll('.vc-knob-tick');
    expect(ticks.length).toBe(6); // tickCount + 1
  });

  it('fires onChange on keyboard', () => {
    const onChange = vi.fn();
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 10,
      label: 'Knob Key',
      renderer: 'knob',
      debounceMs: 0,
      onChange,
    });
    const slider = getSlider(target);
    slider?.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    expect(onChange).toHaveBeenCalledWith(60);
  });

  it('applies compact sizing', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Compact Knob',
      renderer: 'knob',
      compact: true,
      onChange: vi.fn(),
    });
    expect(target.querySelector('.compact')).toBeTruthy();
  });
});

describe('DiscreteRenderer', () => {
  it('renders LED strip when tickStyle is led', () => {
    const target = mountControl({
      value: 4,
      min: 0,
      max: 8,
      step: 1,
      label: 'LED',
      renderer: 'discrete',
      variant: 'hardware-illuminated',
      tickStyle: 'led',
      onChange: vi.fn(),
    });
    expect(target.querySelector('.vc-discrete-led-strip--hw')).toBeTruthy();
    expect(target.querySelector('.hil-thumb')).toBeFalsy();
  });

  it('renders ruler ticks below track when tickStyle is ruler', () => {
    const target = mountControl({
      value: 2,
      min: 0,
      max: 8,
      step: 1,
      label: 'Ruler',
      renderer: 'discrete',
      variant: 'hardware-illuminated',
      tickStyle: 'ruler',
      onChange: vi.fn(),
    });
    expect(target.querySelector('.vc-discrete-ruler--below')).toBeTruthy();
    expect(target.querySelector('.vc-discrete-ruler-tick')).toBeTruthy();
  });

  it('renders notch layer when tickStyle is notch', () => {
    const target = mountControl({
      value: 3,
      min: 0,
      max: 8,
      step: 1,
      label: 'Notch',
      renderer: 'discrete',
      variant: 'hardware-illuminated',
      tickStyle: 'notch',
      onChange: vi.fn(),
    });
    expect(target.querySelector('.vc-discrete-notch-layer--slot')).toBeTruthy();
    expect(target.querySelector('.vc-discrete-notch')).toBeTruthy();
  });
});

describe('fill percentage', () => {
  it('sets correct fill at min value', () => {
    const target = mountControl({
      value: 0,
      min: 0,
      max: 100,
      step: 1,
      label: 'Min',
      renderer: 'hbar',
      onChange: vi.fn(),
    });
    const wrapper = target.querySelector('.vc-hbar') as HTMLElement;
    expect(wrapper?.getAttribute('style')).toContain('--vc-fill-percent: 0%');
  });

  it('sets correct fill at max value', () => {
    const target = mountControl({
      value: 100,
      min: 0,
      max: 100,
      step: 1,
      label: 'Max',
      renderer: 'hbar',
      onChange: vi.fn(),
    });
    const wrapper = target.querySelector('.vc-hbar') as HTMLElement;
    expect(wrapper?.getAttribute('style')).toContain('--vc-fill-percent: 100%');
  });

  it('sets correct fill at middle value', () => {
    const target = mountControl({
      value: 50,
      min: 0,
      max: 100,
      step: 1,
      label: 'Mid',
      renderer: 'hbar',
      onChange: vi.fn(),
    });
    const wrapper = target.querySelector('.vc-hbar') as HTMLElement;
    expect(wrapper?.getAttribute('style')).toContain('--vc-fill-percent: 50%');
  });
});
