import { describe, it } from 'vitest';

describe('two one-way writes', () => {
  it('writes foo one way', () => {
    window.foo = 'a';
  });

  it('writes foo the other way', () => {
    window.foo = 'b';
  });
});
