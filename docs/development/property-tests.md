# Property tests

`tests/property/` holds Hypothesis property tests, marked `property`.
`tests/property/conftest.py` registers two profiles and loads one for the
whole pytest process:

| Profile | Selected by | Settings |
|---|---|---|
| `ci` | default | `max_examples=50`, `derandomize=True`, `deadline=None`, `database=None` |
| `nightly` | `HYPOTHESIS_PROFILE=nightly` | `max_examples=1000`, `deadline=None`, `database=None` |

`ci` is derandomized, so the same tree and Hypothesis version generate the
same examples on every run. `nightly` draws random examples on each run; a
failure prints the falsifying example, and with no example database it is not
replayed on the next run — copy it into an `@example(...)` to keep it.

```bash
uv run pytest tests/property
HYPOTHESIS_PROFILE=nightly uv run pytest tests/property
```
