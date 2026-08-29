# Integration Tests

Most of this directory runs with **no hardware attached**, on every PR.

The name is historical: it once held only tests that talked to a real Icom
transceiver. It now holds two populations, distinguished by marker:

| Population | Marker | Needs hardware? |
|---|---|---|
| Mock integration | `mock_integration` | No — runs everywhere, including CI |
| Hardware integration | `integration` without `mock_integration` | Yes — skips unless configured |

The hardware population is skipped, not failed, when its environment is not
configured. The skip is applied in `conftest.py:
pytest_collection_modifyitems`, which is the authority for what gets gated
and why.

To see the current split on your checkout:

```bash
uv run pytest tests/integration -m mock_integration --co -q      # no hardware needed
uv run pytest tests/integration -m "integration and not mock_integration" --co -q   # hardware
```

## Running

Always via `uv run` — never a bare `pytest`.

```bash
uv run pytest tests/integration -q                  # whole directory
uv run pytest tests/integration -m mock_integration # only the no-hardware tests
uv run pytest tests/integration/test_radio_integration.py -v
uv run pytest tests/integration/test_radio_integration.py::TestFrequency -v
uv run pytest tests/integration/test_radio_integration.py::TestFrequency::test_get_frequency -v
```

### Selecting and excluding by marker

`-m "not integration"` does **not** exclude this directory. Six files carry
only `mock_integration`, so a `not integration` filter still collects their
tests. Use both markers:

```bash
uv run pytest tests/ -m "not integration and not mock_integration"   # exclude this directory
uv run pytest tests/ -m "integration and not mock_integration"       # hardware tests only
```

Path-based exclusion (`--ignore=tests/integration`) also works, but see
**CI** below before adding it to anything that runs in CI.

## Markers

`integration`, `serial_integration`, `ic7610_parity` and `mock_integration`
are registered in `conftest.py: pytest_configure`, local to this directory —
not in the `[tool.pytest.ini_options]` `markers` list in `pyproject.toml`,
which registers a different set (`integration`, `hardware`, `e2e`, `slow`,
`validation_hardware`). A run that never collects this directory's
`conftest.py` therefore does not know the three local markers.

## Hardware configuration

Only needed for the hardware population.

### LAN radio

```bash
export ICOM_HOST=192.168.55.40      # Radio IP address
export ICOM_USER=your_username       # Radio username
export ICOM_PASS=your_password       # Radio password
export ICOM_RADIO_ADDR=0x98          # CI-V address (conftest default: IC-7610)
```

Prerequisites: an Icom radio with LAN/WiFi control (IC-7610, IC-705, IC-7300,
IC-9700, …) on the same network, with a username and password configured in
the radio.

### Serial radio

```bash
export ICOM_SERIAL_DEVICE=/dev/ttyUSB0
export ICOM_SERIAL_BAUDRATE=115200
export ICOM_SERIAL_RADIO_ADDR=0x98
```

### Hardware smoke

```bash
export RIGPLANE_HW_SMOKE=1
```

## Test Categories

| Class | Description | Safe? |
|-------|-------------|-------|
| `TestConnection` | Connect/disconnect | ✅ Yes |
| `TestFrequency` | Read/write frequency | ✅ Yes |
| `TestMode` | Read/write mode | ✅ Yes |
| `TestMeters` | Read S-meter, SWR, ALC, power | ✅ Yes |
| `TestPowerControl` | Set TX power | ✅ Yes |
| `TestPTT` | Toggle PTT | ⚠️ Gated (`ICOM_ALLOW_PTT=1`) |
| `TestVFO` | VFO selection | ✅ Yes |
| `TestSplit` | Split mode | ✅ Yes |
| `TestCW` | CW keying | ❌ Gated (`ICOM_ALLOW_CW_TX=1`) |
| `TestAudioTx` | Audio TX/full-duplex | ❌ Gated (`ICOM_ALLOW_AUDIO_TX=1`) |
| `TestStatus` | Comprehensive status | ✅ Yes |
| `TestReliabilityMatrix` | Wrap/ACK/longevity/contention/readiness | ⚠️ Partially gated |
| `TestControlApiExtended` | DATA/RF/AF/squelch/NB/NR/IP+/state restore | ✅ Yes |
| `TestAudioPcm` | PCM RX/TX path | ⚠️ PCM TX gated (`ICOM_ALLOW_AUDIO_TX=1`) |
| `TestScopeIntegration` | Scope enable/capture/disable | ❌ Gated (`ICOM_ALLOW_SCOPE=1`) |
| `TestNegativeAuthConnect` | Invalid auth / unreachable connect | ❌ Gated (`ICOM_ALLOW_NEGATIVE_TESTS=1`) |

### TX Safety Gates

TX-affecting tests are **disabled by default** and require explicit env flags:

```bash
export ICOM_ALLOW_PTT=1
export ICOM_ALLOW_CW_TX=1
export ICOM_ALLOW_AUDIO_TX=1
```

Power-cycle hardware test remains separately gated:

```bash
export ICOM_ALLOW_POWER_CONTROL=1
```

Additional reliability/media gates:

```bash
export ICOM_ALLOW_SESSION_CONTENTION=1   # two concurrent clients
export ICOM_LONG_SOAK_SECONDS=600        # long-run reliability test
export ICOM_ALLOW_SCOPE=1                # scope capture integration tests
export ICOM_ALLOW_NEGATIVE_TESTS=1       # bad credentials/unreachable host tests
export ICOM_PCM_REQUIRE_FRAMES=1         # set 0 for PCM smoke-only
```

## CI

This directory is **part of the per-PR gate**. All three workflows
(`quick.yml`, `full.yml`, `publish.yml`) run `uv run pytest tests/` with no
path exclusion, so the mock population is collected and must pass like any
other test. Only the hardware population skips there, because no CI runner
sets the variables above.

That the flag is absent is pinned by `tests/test_ci_pytest_invocation.py`,
which turns red if `--ignore=tests/integration` is reintroduced into any of
the three workflows' pytest invocations. Its module docstring records the
incident that motivated it and the limits of what it proves.

## Troubleshooting

Applies to the hardware population.

### Connection refused
- Check radio IP: `ping 192.168.55.40`
- Verify radio is powered on
- Verify LAN control is enabled in radio settings

### Authentication failed
- Check username/password in radio settings
- Some radios require a specific username (often "admin" or empty)

### Timeout errors
- Increase timeout in test: `IcomRadio(..., timeout=10.0)`
- Check network latency: `ping -c 5 192.168.55.40`

### Frequency not changing
- Some radios lock frequency when transmitting
- Check if PTT is stuck (power cycle radio if needed)
