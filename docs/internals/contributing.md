---
robots: noindex, follow
---

# Contributing

Contributions are welcome! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/rigplane/rigplane-core.git
cd rigplane
uv sync --all-extras
```

### Frontend static files (one-time after clone)

`src/rigplane/web/static/` is a symlink to `frontend/dist/`, which is gitignored.
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
# Full suite (hardware-gated integration tests skip automatically without hardware env vars)
uv run pytest tests/ -q --tb=short

# With verbose output
uv run pytest tests/ -v

# Specific test file
uv run pytest tests/test_commands.py -q --tb=short

# Specific test
uv run pytest tests/test_commands.py::test_get_frequency_builds_correct_frame -q --tb=short
```

### Continuous Integration

`docs/development/release-gates.md` is the
single source for the workflow list — triggers, runner labels, and what each
workflow runs. What a contributor needs to know:

- Every pull request to `main` gets the required `quick` status: the
  `quick.yml` `classify` job selects which checks run from the changed
  paths, and the self-hosted `quick` job runs them (hardware-gated tests
  skip automatically without hardware env vars).
- Documentation-only pull requests run no tests: `docs-only-quick.yml`
  publishes the required `quick` status as success for such a head.
- To force a full matrix run on demand:

```bash
gh workflow run "Tests (full matrix)" --ref main
# Release-candidate run, including the slow-marked tests:
gh workflow run "Tests (full matrix)" --ref main -f rc=true
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
└── integration/               # Mostly mocked/fake-radio tests; hardware-gated tests skip automatically
    ├── test_connect.py
    └── test_get_freq.py
```

Unit tests use mocked transports — **no radio required**. Integration tests in `tests/integration/` mostly run against mocked or fake radios too; only tests gated behind `ICOM_*` / `RIGPLANE_HW_SMOKE` environment variables need actual hardware, and those skip automatically without it.

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

#### Command Vocabulary Contract

`contracts/command-vocabulary.json` lists every name in
`web/handlers/control.py: ControlHandler._COMMANDS`, with the `result_keys` of
the `core/command_dispatch.py: CommandDescriptor` that `command_descriptor`
resolves for it, or `null` when none does. After changing the server command
set or a descriptor, regenerate it:

```bash
uv run python .github/scripts/gen-command-vocabulary.py
```

`tests/test_command_vocabulary_contract.py` fails while the file is stale.
`frontend/src/lib/runtime/commands/__tests__/command-vocabulary-contract.isolated.test.ts`
fails when a `RADIO_INTENT_NAMES` entry in `radio-intents.ts` is absent from
the file, or when an intent whose entry is not `null` has parameter names that
differ from its `result_keys`. `result_keys` are the keys of the server's
response (`CommandDescriptor.result`), not the parameters the server accepts,
so a change to the accepted parameter names is not caught.

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

1. Decide whether the radio belongs on an existing native provider path or the
   Hamlib-backed provider path.
2. For native CI-V radios, look up the default CI-V address and profile data.
3. Test basic operations (frequency, mode, meters/PTT as applicable).
4. Document any model-specific behavior and provider limitations.
5. Add to the radios table in `docs/guide/radios.md`.
6. Add protocol constants only when they belong to a native provider surface.

## Reporting Bugs

[Open an issue](https://github.com/rigplane/rigplane-core/issues/new) with:

- Radio model and firmware version
- Python version and OS
- Steps to reproduce
- Debug log output (`logging.basicConfig(level=logging.DEBUG)`)
- Expected vs actual behavior

## Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes with tests
4. Ensure all tests pass: `uv run pytest tests/ -q --tb=short`
5. Commit with conventional commit message (include issue scope: `feat(#N):`)
6. Open a PR against `main`
