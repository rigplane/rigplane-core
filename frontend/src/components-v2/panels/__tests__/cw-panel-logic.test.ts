import { describe, it, expect } from 'vitest';
import {
  isBreakInActive,
  isApfActive,
} from '../cw-panel-logic';

describe('cw-panel-logic', () => {
  describe('isBreakInActive', () => {
    it('returns false for 0', () => {
      expect(isBreakInActive(0)).toBe(false);
    });

    it('returns true for 1', () => {
      expect(isBreakInActive(1)).toBe(true);
    });

    it('returns true for 2', () => {
      expect(isBreakInActive(2)).toBe(true);
    });
  });

  describe('isApfActive', () => {
    it('returns false for 0', () => {
      expect(isApfActive(0)).toBe(false);
    });

    it('returns true for 1', () => {
      expect(isApfActive(1)).toBe(true);
    });

    it('returns true for 2', () => {
      expect(isApfActive(2)).toBe(true);
    });
  });
});
