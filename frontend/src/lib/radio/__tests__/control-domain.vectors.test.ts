import { describe, expect, it } from 'vitest';
import type { ExactDecimal } from '../../types/exact-decimal';
import type { ControlDomain } from '../../types/capabilities';
import { decodeControlDomain, encodeControlDomain, quantizeControlDomain } from '../control-domain';
import vectors from '../../../../../tests/fixtures/control-domain-vectors.json';

// Mirrors the document produced by scripts/gen_control_domain_vectors.py:
// one entry per normalized [controls.*] domain across every rig profile.
interface DomainEntry {
  readonly rig: string;
  readonly control: string;
  readonly domain: Record<string, unknown>;
  readonly decode: readonly (readonly [number, string])[];
  readonly encode: readonly (readonly [string, number | null])[];
  readonly quantize: readonly (readonly [string, string, string | null])[];
}

describe('control-domain Python contract vectors (MOR-2477)', () => {
  for (const entry of (vectors as { domains: unknown }).domains as unknown as DomainEntry[]) {
    describe(`${entry.rig} [controls.${entry.control}]`, () => {
      const domain = entry.domain as unknown as ControlDomain;
      it('decodes every raw vector identically to Python', () => {
        for (const [raw, display] of entry.decode) {
          expect(decodeControlDomain(domain, raw)).toBe(display);
        }
      });
      it('encodes every display vector identically to Python', () => {
        for (const [display, raw] of entry.encode) {
          expect(encodeControlDomain(domain, display as ExactDecimal)).toBe(raw);
        }
      });
      it('quantizes under every posture identically to Python', () => {
        for (const [quantization, display, expected] of entry.quantize) {
          const variant = { ...domain, quantization } as ControlDomain;
          expect(quantizeControlDomain(variant, display as ExactDecimal)).toBe(expected);
        }
      });
    });
  }
});
