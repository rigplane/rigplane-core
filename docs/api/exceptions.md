---
robots: noindex, follow
---

# Exceptions

All exceptions inherit from `RigplaneError` for easy catch-all handling.

## Hierarchy

```
RigplaneError
├── ConnectionError
├── AuthenticationError
├── CommandError
├── TimeoutError
└── AudioError
    ├── AudioCodecBackendError
    ├── AudioFormatError
    └── AudioTranscodeError
```

## Classes

### `RigplaneError`

```python
from rigplane import RigplaneError
```

Base exception for all rigplane errors. Catch this to handle any library error.

```python
try:
    async with create_radio(config) as radio:
        ...
except RigplaneError as e:
    print(f"rigplane error: {e}")
```

### `ConnectionError`

```python
from rigplane import ConnectionError
```

Raised when a connection to the radio fails or is lost.

- UDP socket creation failed
- Network unreachable
- Radio dropped the connection

!!! note
    This is `rigplane.ConnectionError`, not the built-in `builtins.ConnectionError`. Import explicitly to avoid shadowing.

### `AuthenticationError`

```python
from rigplane import AuthenticationError
```

Raised when authentication with the radio fails.

- Wrong username or password
- Account disabled
- Too many concurrent connections

### `CommandError`

```python
from rigplane import CommandError
```

Raised when a CI-V command is rejected by the radio (NAK response).

- Frequency out of range
- Invalid mode for current state
- Feature not supported by radio model

### `TimeoutError`

```python
from rigplane import TimeoutError
```

Raised when an operation doesn't complete within the timeout period.

- Discovery timed out (radio not found)
- CI-V command response not received
- Status packet not received during handshake

!!! note "TimeoutError vs built-in and asyncio"
    This is `rigplane.exceptions.TimeoutError`, not the built-in `builtins.TimeoutError` (Python 3.10+). Import explicitly to avoid shadowing (e.g. `from rigplane.exceptions import TimeoutError as IcomTimeoutError`). When handling timeouts, distinguish from `asyncio.TimeoutError`: the library converts asyncio timeouts to `rigplane.TimeoutError` in its own code; if you use `asyncio.wait_for()` or similar, you may need to catch both.

### `AudioError`

```python
from rigplane import AudioError
```

Base class for audio codec/transcoding failures.

### `AudioCodecBackendError`

```python
from rigplane import AudioCodecBackendError
```

Raised when no Opus backend is available for PCM/Opus conversion.

- `opuslib` not installed
- backend failed initialization

Typical actionable message:
`Audio codec backend unavailable; install rigplane[audio].`

### `AudioFormatError`

```python
from rigplane import AudioFormatError
```

Raised when provided audio frame format is invalid.

- Unsupported sample rate/channel/frame duration
- Wrong PCM frame byte length
- Empty/invalid Opus frame input

### `AudioTranscodeError`

```python
from rigplane import AudioTranscodeError
```

Raised when encode/decode fails in the codec backend.

## Usage Patterns

### Catch specific errors

```python
from rigplane import create_radio, LanBackendConfig
from rigplane.exceptions import (
    ConnectionError,
    AuthenticationError,
    CommandError,
    TimeoutError,
)

config = LanBackendConfig(host="192.168.1.100", username="u", password="p")
try:
    async with create_radio(config) as radio:
        await radio.set_frequency(999_999_999)
except ConnectionError:
    print("Cannot reach the radio")
except AuthenticationError:
    print("Check your credentials")
except CommandError:
    print("Radio rejected the command")
except TimeoutError:
    print("Radio not responding")
```

### Retry pattern

```python
import asyncio
from rigplane import create_radio, TimeoutError

async def get_frequency_with_retry(radio, retries=3):
    for attempt in range(retries):
        try:
            return await radio.get_frequency()
        except TimeoutError:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(0.5)
```
