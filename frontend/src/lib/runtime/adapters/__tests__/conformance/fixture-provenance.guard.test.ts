/**
 * MOR-1557 — fixture provenance staleness guard.
 *
 * MOR-1553 item 1 found, by hand, that `ic7300-state.json` still carries the
 * PRE-MOR-1548 shape for `notch_filter`: a top-level `notchFilter` field
 * (plus its `fieldStatus.notchFilter` entry), even though `state.ts`
 * reclassified it as receiver-scoped (`main.notchFilter`) when the notch
 * readback was fixed to stop clobbering MAIN with SUB's fact (MOR-1548, PR
 * #2466). Nothing detected that drift automatically — this is the automatic
 * version, run for every profile in `conformance/profiles.ts` against the
 * CURRENT `ServerStatePublic`/`ReceiverStatePublic`/`Capabilities`
 * interfaces (parsed straight from `state.ts`/`capabilities.ts` via the TS
 * compiler API, so the guard can't itself drift from the real type).
 *
 * `fieldStatus` keys are checked against the UNION of two sources, since
 * neither alone is complete: `empty-store-field-status.json` (the backend's
 * CI-gated default-seed registry — covers reserved paths with no TS
 * counterpart, e.g. `main.antiVoxGain`) and a structural walk of the SAME
 * parsed interfaces (covers `connection.*`/`radioHealth.*`/`scopeControls.*`,
 * which only get a status once actually observed, so the empty-snapshot
 * registry never seeds them). `notchFilter` (dotless) fails both checks —
 * exactly the drift this guard exists to catch.
 *
 * A fixture with a top-level/receiver/fieldStatus field the current schema
 * no longer defines, or missing a newly-required field, fails here — UNLESS
 * allow-listed in `KNOWN_STALE_FIELDS` (used only for the one documented,
 * tracked drift; MOR-1553/MOR-1558).
 *
 * A second block cross-checks the retroactive `<name>-provenance.json`
 * sidecar (item 4) against the loader header's documented capture facts.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import * as ts from 'typescript';
import emptyFieldStatus from '$lib/state/__tests__/fixtures/empty-store-field-status.json';
import { PROFILES } from './profiles';

// process.cwd() (vitest's project root, `frontend/`), not import.meta.url —
// a sibling JSON import in this same file rewires import.meta.url to a
// virtual dev-server origin under the jsdom `fast` project, breaking
// fileURLToPath. process.cwd() is stable regardless.
const CONFORMANCE_DIR = resolve(process.cwd(), 'src/lib/runtime/adapters/__tests__/conformance');
const STATE_TS_PATH = resolve(process.cwd(), 'src/lib/types/state.ts');
const CAPABILITIES_TS_PATH = resolve(process.cwd(), 'src/lib/types/capabilities.ts');
const FIXTURES_DIR = resolve(CONFORMANCE_DIR, '../fixtures');

// Known-stale fixture fields, allow-listed pending re-capture (MOR-1558, C4
// of MOR-1426). Remove an entry ONLY once the fixture is genuinely
// re-captured — removing it while the fixture is unchanged must turn this
// guard red again (see PR body for the reproduction).
const KNOWN_STALE_FIELDS: Record<string, { stateTopLevel: string[]; fieldStatus: string[] }> = {
  ic7300: { stateTopLevel: ['notchFilter'], fieldStatus: ['notchFilter'] },
};

interface FieldInfo {
  name: string;
  required: boolean;
}

interface InterfaceInfo {
  fields: FieldInfo[];
  hasIndexSignature: boolean;
}

/** Extracts `PropertySignature` members of a top-level `interface <name>` via the TS AST. */
function parseInterfaceFields(sourceFile: ts.SourceFile, interfaceName: string): InterfaceInfo {
  let found: ts.InterfaceDeclaration | undefined;
  const visit = (node: ts.Node): void => {
    if (ts.isInterfaceDeclaration(node) && node.name.text === interfaceName) {
      found = node;
    }
    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  if (!found) {
    throw new Error(`interface ${interfaceName} not found in ${sourceFile.fileName}`);
  }
  const fields: FieldInfo[] = [];
  let hasIndexSignature = false;
  for (const member of found.members) {
    if (ts.isPropertySignature(member) && ts.isIdentifier(member.name)) {
      fields.push({ name: member.name.text, required: !member.questionToken });
    } else if (ts.isIndexSignatureDeclaration(member)) {
      hasIndexSignature = true;
    }
  }
  return { fields, hasIndexSignature };
}

const stateSourceFile = ts.createSourceFile(
  STATE_TS_PATH,
  readFileSync(STATE_TS_PATH, 'utf8'),
  ts.ScriptTarget.Latest,
  true,
);
const capabilitiesSourceFile = ts.createSourceFile(
  CAPABILITIES_TS_PATH,
  readFileSync(CAPABILITIES_TS_PATH, 'utf8'),
  ts.ScriptTarget.Latest,
  true,
);

const INTERFACE_CACHE = new Map<string, InterfaceInfo>();
function stateInterface(name: string): InterfaceInfo {
  let info = INTERFACE_CACHE.get(name);
  if (!info) {
    info = parseInterfaceFields(stateSourceFile, name);
    INTERFACE_CACHE.set(name, info);
  }
  return info;
}

const SERVER_STATE = stateInterface('ServerStatePublic');
const RECEIVER_STATE = stateInterface('ReceiverStatePublic');
const CAPABILITIES = parseInterfaceFields(capabilitiesSourceFile, 'Capabilities');

// Hand-written addition on top of the generated `ServerStatePublic` block
// (state.ts's own "Hand-written UI section") — not schema drift.
const HAND_WRITTEN_STATE_KEYS = ['transportSeq'];

const SERVER_STATE_NAMES = new Set([
  ...SERVER_STATE.fields.map((f) => f.name),
  ...HAND_WRITTEN_STATE_KEYS,
]);
const RECEIVER_STATE_NAMES = new Set(RECEIVER_STATE.fields.map((f) => f.name));
const REGISTERED_FIELD_STATUS_PATHS = new Set(Object.keys(emptyFieldStatus));

// `fieldStatus` dotted-path containers: a top-level field whose value is an
// object (e.g. `connection`, `main`) gets its own leaf entries
// (`connection.rigConnected`, `main.afLevel`). Maps the field name to the
// `state.ts` interface describing its members.
const FIELD_STATUS_CONTAINERS: Record<string, string> = {
  connection: 'ConnectionPublic',
  radioDetail: 'RadioDetailPublic',
  radioHealth: 'RadioHealthPublic',
  scopeControls: 'ScopeControlsPublic',
  main: 'ReceiverStatePublic',
  sub: 'ReceiverStatePublic',
};
// One level deeper: `main.vfoA.freqHz` / `main.unselectedVfo.mode`.
const RECEIVER_SUB_CONTAINERS: Record<string, string> = {
  vfoA: 'VfoSlotPublic',
  vfoB: 'VfoSlotPublic',
  unselectedVfo: 'VfoSlotPublic',
};

/** True if `path` is seeded by the backend's default field-status registry (see module doc). */
function isRegisteredFieldStatusPath(path: string): boolean {
  return REGISTERED_FIELD_STATUS_PATHS.has(path);
}

/** Structurally validates a `fieldStatus` dotted key path against the current `state.ts` types. */
function isStructurallyValidFieldStatusPath(path: string): boolean {
  const segments = path.split('.');
  const [head, ...rest] = segments;
  if (!SERVER_STATE_NAMES.has(head)) return false;
  if (rest.length === 0) return true;

  const containerName = FIELD_STATUS_CONTAINERS[head];
  if (!containerName) return false; // e.g. a scalar top-level field can't have a dotted child.
  const container = stateInterface(containerName);
  const [second, ...tail] = rest;
  if (!container.fields.some((f) => f.name === second)) return false;
  if (tail.length === 0) return true;

  if (containerName !== 'ReceiverStatePublic' || tail.length !== 1) return false;
  const subContainerName = RECEIVER_SUB_CONTAINERS[second];
  if (!subContainerName) return false;
  const subContainer = stateInterface(subContainerName);
  return subContainer.fields.some((f) => f.name === tail[0]);
}

function driftMessage(profileName: string, section: string, verb: string, fields: string[]): string {
  return (
    `Fixture '${profileName}' ${section} ${verb} field(s) [${fields.join(', ')}] — `
    + 're-capture required — see MOR-1410 bench session.'
  );
}

describe('fixture provenance staleness guard (MOR-1557)', () => {
  for (const [profileName, profile] of Object.entries(PROFILES)) {
    const allow = KNOWN_STALE_FIELDS[profileName] ?? { stateTopLevel: [], fieldStatus: [] };

    describe(profileName, () => {
      it('state: carries no top-level field ServerStatePublic no longer defines', () => {
        const extra = Object.keys(profile.state).filter(
          (key) => !SERVER_STATE_NAMES.has(key) && !allow.stateTopLevel.includes(key),
        );
        expect(extra, driftMessage(profileName, 'state', 'carries stale', extra)).toEqual([]);
      });

      it('state: has every field ServerStatePublic now requires', () => {
        const missing = SERVER_STATE.fields
          .filter((f) => f.required)
          .map((f) => f.name)
          .filter((name) => !(name in profile.state));
        expect(missing, driftMessage(profileName, 'state', 'is missing required', missing)).toEqual([]);
      });

      it.each(['main', 'sub'] as const)(
        'state.%s: carries no receiver field ReceiverStatePublic no longer defines',
        (slot) => {
          const receiver = profile.state[slot];
          if (!receiver) return;
          const extra = Object.keys(receiver).filter((key) => !RECEIVER_STATE_NAMES.has(key));
          expect(extra, driftMessage(profileName, `state.${slot}`, 'carries stale', extra)).toEqual([]);
        },
      );

      it('state.fieldStatus: carries no key path the current schema no longer defines', () => {
        const actual = Object.keys(profile.state.fieldStatus ?? {});
        const extra = actual.filter(
          (key) => !isRegisteredFieldStatusPath(key)
            && !isStructurallyValidFieldStatusPath(key)
            && !allow.fieldStatus.includes(key),
        );
        expect(extra, driftMessage(profileName, 'state.fieldStatus', 'carries stale', extra)).toEqual([]);
      });

      it('capabilities: has every field Capabilities now requires', () => {
        expect(CAPABILITIES.hasIndexSignature).toBe(true); // Capabilities is intentionally extensible.
        const missing = CAPABILITIES.fields
          .filter((f) => f.required)
          .map((f) => f.name)
          .filter((name) => !(name in profile.caps));
        expect(missing, driftMessage(profileName, 'capabilities', 'is missing required', missing)).toEqual([]);
      });
    });
  }
});

describe('retroactive provenance sidecar cross-check (MOR-1557 item 4)', () => {
  it('ic7300-provenance.json matches the loader header\'s documented capture facts', () => {
    const headerSource = readFileSync(resolve(FIXTURES_DIR, 'ic7300-profile.ts'), 'utf8');
    const provenance = JSON.parse(
      readFileSync(resolve(FIXTURES_DIR, 'ic7300-provenance.json'), 'utf8'),
    ) as {
      capturedAt: string;
      backendHeadSha: string;
      radioModel: string;
      receivers: number;
      retroactive: boolean;
    };

    const capturedMatch = headerSource.match(/Captured:\s*(\S+)/);
    const backendShaMatch = headerSource.match(/Backend SHA:\s*(\S+)/);

    expect(provenance.retroactive).toBe(true);
    expect(capturedMatch?.[1]).toBe(provenance.capturedAt);
    expect(backendShaMatch?.[1]).toBe(provenance.backendHeadSha);
    // model/receivers in the sidecar come from the capabilities fixture
    // itself (MOR-1557 item 4), not the header prose — cross-check them
    // against the live PROFILES-loaded capabilities object too.
    expect(provenance.radioModel).toBe(PROFILES.ic7300.caps.model);
    expect(provenance.receivers).toBe(PROFILES.ic7300.caps.receivers);
  });
});
