# Runtime `_KNOWN_COMMANDS` reverse audit

**Derived audit evidence — 2026-09-01.** This is not a runtime source of truth. It records a reproducible classification from the supplied read-only evidence, not a profile, API, or ticket change. The radio profile remains the radio-fact source of truth; any future callable-support relation belongs with the implementation. Context: MOR-2114 and MOR-2161; this audit does not claim either issue is closed.

## Pinned evidence and result

Current census ref: `8cc5471dbb60f246ccb7a17a5e29f75fd6f20a00`. Supplied reverse-classification sources: Markdown SHA-256 `f75b2287495e38fd1b40f70866051971c30cdc7b2b5d306d64a119c1a38b541b`; JSON SHA-256 `41bfdf563b91fc91122a9cacb4070a39e388420338b93d6b2623d529cb19e982`. Independent reverse-census verification source: SHA-256 `53f769853234b93a9bf0c7e2bd8b6cc010939542ffc318dd440a339e96996e82`.

Let `K` be the AST-literal strings in `CoreRadio._KNOWN_COMMANDS`. Let `D` be the union of `RadioProfile.command_names` after loading every `rigs/` configuration through `discover_rigs()` and `RigConfig.to_profile()`. Let `A` be the union of `absent_command_names`, reported only as provenance. `D` includes positive CI-V and CAT declarations, excludes `AbsentCommandSpec`, and deliberately does not use `CommandMap` (which drops CAT specs).

| Measure | Count |
|---|---:|
| `K` known names | 236 |
| `D` declared names | 502 |
| `K & D` | 201 |
| `K - D` known-not-declared-any-profile | 35 |
| `D - K` declared-not-known | 301 |
| `A` absent union | 73 |
| Python parse health | 714 parsed / 0 syntax errors |

The CSV has exactly 35 unique names: 16 `composite`, 17 `alias`, 1 `obsolete_synthetic`, and 1 `unsupported_fallback`. Loader positive control: `get_freq` is positively declared. Caller-scan positive controls include production calls to `radio.set_scope_dual(...)` and `radio.capture_scope_frame(...)`; therefore the recorded negative caller findings are not an empty-loader or failed-scan result.

## Historical 52 is a different denominator

At historical ref `df7b178816f1db04c581fcf00d1c9516fbc18144`, `_KNOWN_COMMANDS` had 238 names. The IC-7300 profile had 333 positive names, 27 explicit-absent names, and 360 total `RigConfig.commands` keys. Therefore `K - IC7300.profile.command_names` was 74, while the MOR-2114/MOR-2161 historical value was **52**:

```python
known = ast_literal_CoreRadio_KNOWN_COMMANDS(ref)
ic7300 = next(c for c in discover_rigs(snapshot / "rigs").values()
              if c.id == "icom_ic7300")
silent_fallback_names = known - set(ic7300.commands)
assert len(silent_fallback_names) == 52
```

This is the IC-7300-*mentioned* denominator: `cfg.commands` contains positive and explicit-absent keys, so 52 means the fallback advertised a name on which the IC-7300 profile was silent. It is neither the positive-declaration-only difference (74) nor the current all-profile `K - D` (35). The historical CI-V `command_map - K` result was 169. At the current ref, the same IC-7300 mentioned-name calculation is 50.

## Interpretation and ownership boundary

Composite operations are legitimate public operations: scope lifecycles, raw CI-V transport, and negotiated audio do not correspond to one finite profile command. They **must survive** any support-source-of-truth refactor. Do not delete `_KNOWN_COMMANDS` wholesale. A future, separately owned design may express each callable's exact primitive, alias, composite, or transport relation next to the callable, while profiles retain radio facts.

The one unsupported fallback (`get_memory_mode`) is an unconditional `NotImplementedError` stub; the one obsolete/synthetic helper (`get_mode_enum`) is deprecated legacy derivation. Their rows recommend a future narrowly reviewed cleanup. `src/rigplane/runtime/radio.py` is actively owned by MOR-2180 and was not changed by this audit.

## Reproduction

Run each against a clean archive, with the project virtual environment available:

```bash
REF=8cc5471dbb60f246ccb7a17a5e29f75fd6f20a00; SNAP=$(mktemp -d /tmp/known-current.XXXXXX)
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
assert 'get_freq' in d; print(len(trees),len(errors),len(k),len(d),len(k&d),len(k-d),len(d-k),len(a))
# expected: 714 0 236 502 201 35 301 73
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
