import { afterEach, describe, expect, it } from 'vitest';
import { runAssertions } from '../../../../fixtures/assertions';
import { fixtureById } from '../../../../fixtures/catalog';

const inert = '<div class="freq" role="group" aria-disabled="true" tabindex="-1">14.250.000</div>';
const wrap = (html: string) => `<span data-vfo-freq data-freq-tunable="false">${html}</span>`;
function admitted(html: string): boolean {
  document.body.innerHTML = `<div data-testid="dual-receiver-cockpit">${html}</div>`;
  const result = runAssertions(fixtureById('caps-unloaded')!.expect!)
    .find(result => result.name === 'no-negative-tabindex-and-no-aria-hidden-control');
  expect(result).toBeDefined();
  return result!.ok;
}
afterEach(() => { document.body.innerHTML = ''; });

describe('fixture focus assertion retained readout exception', () => {
  it('admits the inert readout and ordinary controls', () => {
    expect(admitted(wrap(inert) + '<button>Stop</button><input><a href="#help">Help</a>')).toBe(true);
  });
  it('admits the restored current readout through the normal tabindex rule', () => {
    expect(admitted('<span data-vfo-freq data-freq-tunable="true">'
      + inert.replace('aria-disabled="true"', 'aria-disabled="false"').replace('tabindex="-1"', 'tabindex="0"')
      + '</span>')).toBe(true);
  });
  it.each([
    ['enabled frequency', wrap(inert.replace('aria-disabled="true"', 'aria-disabled="false"'))],
    ['enabled VFO hook', wrap(inert).replace('data-freq-tunable="false"', 'data-freq-tunable="true"')],
    ['missing VFO hook', inert],
    ['disabled button', '<button aria-disabled="true" tabindex="-1">Stop</button>'],
    ['disabled link', '<a href="#help" aria-disabled="true" tabindex="-1">Help</a>'],
    ['disabled input', '<input aria-disabled="true" tabindex="-1">'],
    ['unrelated group', '<div role="group" aria-disabled="true" tabindex="-1">Group</div>'],
    ['non-primitive group in VFO', wrap(inert.replace('class="freq"', ''))],
    ['wrong element in VFO', wrap(inert.replaceAll('div', 'button'))],
    ['wrong role in VFO', wrap(inert.replace('role="group"', 'role="button"'))],
    ['hidden readout', wrap(inert.replace('role="group"', 'role="group" aria-hidden="true"'))],
    ['hidden ancestor', `<section aria-hidden="true">${wrap(inert)}</section>`],
    ['hidden ordinary control', '<section aria-hidden="true"><button>Stop</button></section>'],
  ])('rejects %s', (_label, html) => { expect(admitted(html)).toBe(false); });
});
