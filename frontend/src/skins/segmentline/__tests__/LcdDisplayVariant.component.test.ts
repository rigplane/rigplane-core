import { afterEach, describe, expect, it } from 'vitest';
import {
  createRawSnippet, mount, unmount, type Snippet,
} from 'svelte';
import LcdDisplayVariant, {
  type LcdDisplayVariantId,
} from '../LcdDisplayVariant.svelte';

const IDS = ['peer', 'dominant', 'centerstage', 'panadapter'] as const;

const snippets = Object.fromEntries(IDS.map((id) => [
  id,
  createRawSnippet(() => ({
    render: () => `<span data-testid="variant-${id}">${id}</span>`,
  })),
])) as Record<LcdDisplayVariantId, Snippet>;

let component: ReturnType<typeof mount> | null = null;

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

function render(variant: LcdDisplayVariantId | 'invalid'): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(LcdDisplayVariant, {
    target,
    props: {
      variant: variant as LcdDisplayVariantId,
      peer: snippets.peer,
      dominant: snippets.dominant,
      centerstage: snippets.centerstage,
      panadapter: snippets.panadapter,
    },
  });
  return target;
}

describe('LcdDisplayVariant', () => {
  it.each(IDS)('selects only the required %s snippet', (id) => {
    const target = render(id);
    const rendered = target.querySelectorAll('[data-testid^="variant-"]');

    expect(rendered).toHaveLength(1);
    expect(rendered[0].getAttribute('data-testid')).toBe(`variant-${id}`);
  });

  it('renders no peer fallback for an invalid runtime value', () => {
    const target = render('invalid');

    expect(target.querySelectorAll('[data-testid^="variant-"]')).toHaveLength(0);
    expect(target.textContent).toBe('');
  });

  it('adds no interactive affordance', () => {
    const target = render('peer');

    expect(target.querySelectorAll(
      'button,input,select,a[href],[tabindex],[role="button"],[role="switch"]',
    )).toHaveLength(0);
  });
});
