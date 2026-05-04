# Contributing

Contributions are welcome! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/morozsm/icom-lan.git
cd icom-lan
uv sync --all-extras
```

### Frontend static files (one-time after clone)

`src/icom_lan/web/static/` is a symlink to `frontend/dist/`, which is gitignored.
Web-server tests that serve static files will fail on a clean clone until you build once:

```bash
cd frontend
npm ci
npm run build
cd ..
```

CI runs this automatically; you only need it locally if you're running web-server tests.

## Running Tests

```bash
# All unit tests (skip integration tests requiring hardware)
uv run pytest tests/ -q --tb=short --ignore=tests/integration

# With verbose output
uv run pytest tests/ -v --ignore=tests/integration

# Specific test file
uv run pytest tests/test_commands.py -q --tb=short

# Specific test
uv run pytest tests/test_commands.py::test_get_frequency_builds_correct_frame -q --tb=short
```

### Continuous Integration

The repository has three Actions workflows, tiered to keep billable minutes low:

| Workflow | When it runs | What it does |
|---|---|---|
| **Tests (quick)** | push/PR to `main` **only** when one of these paths changes: `src/**`, `tests/**`, `frontend/**`, `pyproject.toml`, `uv.lock`, `.importlinter`, `.github/workflows/**` | Single Python 3.11 job: `ruff`, `import-linter`, `pytest` (no hardware integration). The frontend block (`npm ci`, type-check, vitest, build, mypy on `src/icom_lan/web`) only runs when files under `frontend/**` or `src/icom_lan/web/**` actually changed. ~2–5 min. Doc-only edits and CLAUDE.md changes don't trigger CI at all — use `gh workflow run "Tests (quick)"` if you need a manual run. |
| **Tests (full matrix)** | cron Mon/Wed/Fri 03:00 UTC, manual `workflow_dispatch`, **or** any push whose commit message contains `[full-ci]` | Full 3.11/3.12/3.13 matrix with frontend, mypy, import-linter and the whole pytest suite. |
| **Publish to PyPI** | on a published GitHub Release | Runs the full validation matrix first; the build/publish jobs only start if validation is green. |

To force a full matrix run on demand:

```bash
git commit -m "chore: rebuild CI cache [full-ci]"
# or
gh workflow run "Tests (full matrix)"
```

### Test Structure

```
tests/
├── conftest.py                # Shared fixtures (FakeRadio, packet builders)
├── test_auth.py               # Credential encoding, login/conninfo packets
├── test_commands.py           # CI-V frame building and parsing
├── test_protocol.py           # Header parsing/serialization
├── test_radio.py              # IcomRadio high-level API (mocked transport)
├── test_transport.py          # IcomTransport (mocked UDP)
├── test_rigctld_handler.py    # Rigctld command handler unit tests
├── test_rigctld_protocol.py   # Rigctld wire protocol parsing/formatting
├── test_rigctld_server.py     # Rigctld server lifecycle tests
├── test_golden_protocol.py    # Golden fixture runner (parse → execute → format)
├── test_server_wire.py        # TCP wire integration tests (real server, mock radio)
├── test_data_mode.py          # DATA/packet mode semantics
├── golden/
│   └── protocol_golden.json   # 45 golden response fixtures (all commands + errors)
└── integration/               # Real hardware tests (require a radio)
    ├── test_connect.py
    └── test_get_freq.py
```

Unit tests use mocked transports — **no radio required**. Integration tests are in `tests/integration/` and require actual hardware.

#### Golden Protocol Tests

The `tests/golden/` directory contains JSON fixtures that define the exact wire-level
input/output contract for the rigctld protocol. Each fixture specifies a command input,
mock radio state, and the expected byte-exact response in both normal and extended mode.

To run only golden tests:

```bash
uv run pytest tests/test_golden_protocol.py tests/test_server_wire.py -v
```

When adding a new rigctld command, add corresponding fixtures to `protocol_golden.json`
to lock down the wire format.

## Code Style

- **Type annotations** on all public functions
- **Docstrings** on all public classes and methods (Google/NumPy style)
- Follow existing code patterns
- Core library depends only on `pyserial`/`pyserial-asyncio`; avoid adding other runtime dependencies

## Commit Messages

We use [Conventional Commits](https://www.conventionalcommits.org/) with issue scope:

```
feat(#123): add AGC level control
fix(#456): handle timeout during conninfo exchange
docs(#789): add IC-705 setup instructions
test(#012): add VFO swap command test
refactor: restructure command parsing (no issue scope for non-issue changes)
chore: bump version to 0.3.0
```

Always include the issue number in the scope (e.g., `#123`) for feature, fix, and docs commits tied to GitHub issues.

## Adding a New CI-V Command

1. **Add the command builder** in `commands.py`:

    ```python
    def get_agc(to_addr: int = IC_7610_ADDR, from_addr: int = CONTROLLER_ADDR) -> bytes:
        """Build a 'get AGC' CI-V command."""
        return build_civ_frame(to_addr, from_addr, 0x16, sub=0x12)
    ```

2. **Add a response parser** if needed (also in `commands.py`)

3. **Add the high-level method** in `radio.py`:

    ```python
    async def get_agc(self) -> int:
        """Get the AGC level."""
        self._check_connected()
        civ = get_agc(to_addr=self._radio_addr)
        resp = await self._send_civ_raw(civ)
        return parse_level_response(resp)
    ```

4. **Add a CLI command** in `cli.py` if user-facing

5. **Write tests** in `tests/test_commands.py` and `tests/test_radio.py`

6. **Update documentation**:
    - `docs/guide/commands.md` — user-facing command docs
    - `docs/api/radio.md` — API reference
    - `docs/api/commands.md` — low-level command reference

## Adding a New Radio Model

1. Look up the radio's default CI-V address
2. Test basic operations (frequency, mode, meters)
3. Document any model-specific behavior
4. Add to the radios table in `docs/guide/radios.md`
5. Add the CI-V address constant in `commands.py` if desired

## Reporting Bugs

[Open an issue](https://github.com/morozsm/icom-lan/issues/new) with:

- Radio model and firmware version
- Python version and OS
- Steps to reproduce
- Debug log output (`logging.basicConfig(level=logging.DEBUG)`)
- Expected vs actual behavior

## Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes with tests
4. Ensure all tests pass: `uv run pytest tests/ -q --tb=short --ignore=tests/integration`
5. Commit with conventional commit message (include issue scope: `feat(#N):`)
6. Open a PR against `main`
