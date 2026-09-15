# Runtime callable-support reverse audit

**Derived audit evidence — updated 2026-09-02.** This document is not a runtime source of truth. Profiles own direct radio facts, command-builder `cmd_map_key` metadata owns the primitive each implementation uses, and `src/rigplane/runtime/callable_support.py: CALLABLE_RELATIONS` owns only the relations between public operations and those facts.

## Pinned evidence and result

Pre-removal census ref: `0878667ec36e39c418efa084a6e73d9e706d8c4c`. The executable literal was removed by implementation commit `8ddc3bdaea0bbed2ef8208d8e6721ec1a0c3f816`. The CSV preserves the original 2026-09-01 reverse-classification provenance and records the corrected rulings where that evidence was later superseded.

At the pinned pre-removal ref, let `K` be the former AST literal, `D` the union of `RadioProfile.command_names` after loading all eight `rigs/` configurations, and `A` the union of explicit absences.

| Measure | Count |
|---|---:|
| `K` known names | 236 |
| `D` declared names | 500 |
| `K & D` | 201 |
| `K - D` known-not-declared-any-profile | 35 |
| `D - K` declared-not-known | 299 |
| `A` absent union | 79 |

The 35 historical names now partition into 16 `composite`, 16 `alias`, 1 `implementation_defect`, 1 `obsolete_synthetic`, and 1 `unsupported_fallback`. Exactly 32 have runtime relations. `set_scope_dual` is fail-closed pending MOR-2113; `get_mode_enum` and `get_memory_mode` also have no relation. Their methods remain for compatibility, but `supports_command` returns false unless a future direct profile fact or reviewed relation establishes support.

## Historical 52 is a different denominator

At historical ref `df7b178816f1db04c581fcf00d1c9516fbc18144`, `_KNOWN_COMMANDS` had 238 names. The IC-7300 profile had 333 positive names, 27 explicit-absent names, and 360 total `RigConfig.commands` keys. Therefore `K - IC7300.profile.command_names` was 74, while the MOR-2114/MOR-2161 historical value was **52**:

```python
known = ast_literal_CoreRadio_KNOWN_COMMANDS(ref)
ic7300 = next(c for c in discover_rigs(snapshot / "rigs").values()
              if c.id == "icom_ic7300")
silent_fallback_names = known - set(ic7300.commands)
assert len(silent_fallback_names) == 52
```

This is the IC-7300-*mentioned* denominator: `cfg.commands` contains positive and explicit-absent keys, so 52 means the fallback advertised a name on which the IC-7300 profile was silent. It is neither the positive-declaration-only difference (74) nor the pre-removal all-profile `K - D` (35). The historical CI-V `command_map - K` result was 169. At the pinned pre-removal ref, the same IC-7300 mentioned-name calculation is 50.

## Interpretation and ownership boundary

The registry contains 16 builder-derived aliases, five builder-derived composites, ten audio-capability operations, and one CI-V protocol operation. Builder targets are evaluated from their existing `cmd_map_key`; the registry does not duplicate profile command-key strings or branch on model/vendor identity. Explicit profile absence wins over a direct positive declaration or a relation.

`get_mode_info` derives base support from `get_mode`; SUB use separately requires receiver 1 and `get_unselected_mode`. The exact ten audio operations derive from the profile's `audio` capability, while `send_civ` derives only from `protocol.type = "civ"`.

## Reproduction

Run each against a clean archive, with the project virtual environment available:

```bash
REF=0878667ec36e39c418efa084a6e73d9e706d8c4c; SNAP=$(mktemp -d /tmp/known-current.XXXXXX)
git archive "$REF" | tar -x -C "$SNAP"
SNAP="$SNAP" PYTHONPATH="$SNAP/src" /Users/moroz/Projects/rigplane-core/.venv/bin/python - <<'PY'
import ast, os
from pathlib import Path
from rigplane.profiles.rig_loader import discover_rigs
r=Path(os.environ['SNAP']); paths=tuple(p for p in r.rglob('*.py') if not {'.git','.venv','node_modules'}.intersection(p.relative_to(r).parts)); errors=[]; trees={}
for p in paths:
    try: trees[p]=ast.parse(p.read_text(),filename=str(p))
    except SyntaxError as e: errors.append((str(p),e.lineno,e.msg))
assert not errors
cls=next(n for n in trees[r/'src/rigplane/runtime/radio.py'].body if isinstance(n,ast.ClassDef) and n.name=='CoreRadio')
k=next(frozenset(ast.literal_eval(n.value.args[0])) for n in cls.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id=='_KNOWN_COMMANDS')
ps=[c.to_profile() for c in discover_rigs(r/'rigs').values()]; d=frozenset().union(*(p.command_names for p in ps)); a=frozenset().union(*(p.absent_command_names for p in ps))
assert 'get_freq' in d; print(len(k),len(d),len(k&d),len(k-d),len(d-k),len(a))
# expected: 236 500 201 35 299 79
PY
```

```bash
REF=df7b178816f1db04c581fcf00d1c9516fbc18144; SNAP=$(mktemp -d /tmp/known-history.XXXXXX)
git archive "$REF" | tar -x -C "$SNAP"
SNAP="$SNAP" PYTHONPATH="$SNAP/src" /Users/moroz/Projects/rigplane-core/.venv/bin/python - <<'PY'
import ast, os
from pathlib import Path
from rigplane.profiles.rig_loader import discover_rigs
r=Path(os.environ['SNAP']); t=ast.parse((r/'src/rigplane/runtime/radio.py').read_text()); c=next(n for n in t.body if isinstance(n,ast.ClassDef) and n.name=='CoreRadio'); k=next(frozenset(ast.literal_eval(n.value.args[0])) for n in c.body if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id=='_KNOWN_COMMANDS'); p=next(c for c in discover_rigs(r/'rigs').values() if c.id=='icom_ic7300'); q=p.to_profile(); print(len(k),len(q.command_names),len(q.absent_command_names),len(p.commands),len(k-q.command_names),len(k-set(p.commands)),len(set(q.command_map or ())-k))
# expected: 238 333 27 360 74 52 169
PY
```

Validate the durable view with `python -c` CSV parsing, uniqueness/category checks, and source-name equality as shown in the PR validation record.
