# Transport

Low-level async UDP transport for the Icom LAN protocol. Most users should use [`create_radio`](public-api-surface.md) and the **Radio** API instead.

## Class: `IcomTransport`

```python
from rigplane import IcomTransport
```

Handles:

- UDP socket management via `asyncio.DatagramProtocol`
- Discovery handshake (Are You There → I Am Here → Are You Ready)
- Keep-alive ping loop (500ms interval)
- Sequence number tracking
- Retransmit request handling
- Packet queueing for consumers

### Constructor

```python
IcomTransport()
```

No parameters — the transport is configured via `connect()`.

---

## Methods

### `connect()`

```python
async def connect(self, host: str, port: int) -> None
```

Open UDP connection and perform discovery handshake.

| Parameter | Type | Description |
|-----------|------|-------------|
| `host` | `str` | Radio IP address |
| `port` | `int` | UDP port number |

**Raises:** `TimeoutError` if discovery fails after 10 attempts.

### `disconnect()`

```python
async def disconnect(self) -> None
```

Close the UDP connection and stop background tasks (ping, retransmit).

### `send_tracked()`

```python
async def send_tracked(self, data: bytes) -> None
```

Send a packet with automatic sequence number assignment and tracking for retransmission.

### `receive_packet()`

```python
async def receive_packet(self, timeout: float = 5.0) -> bytes
```

Wait for the next incoming packet.

**Raises:** `asyncio.TimeoutError` if no packet arrives within timeout.

### `start_ping_loop()`

```python
def start_ping_loop(self) -> None
```

Start the background ping task (500ms interval).

### `start_retransmit_loop()`

```python
def start_retransmit_loop(self) -> None
```

Start the background retransmit request task (100ms interval).

---

## Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `state` | `ConnectionState` | Current connection state |
| `my_id` | `int` | Local connection identifier |
| `remote_id` | `int` | Remote (radio) connection identifier |
| `send_seq` | `int` | Next outgoing tracked sequence number |
| `ping_seq` | `int` | Next outgoing ping sequence number |
| `tx_buffer` | `dict[int, bytes]` | Sent packets buffer (for retransmit) |

---

## Enum: `ConnectionState`

```python
from rigplane import ConnectionState
```

| Value | Description |
|-------|-------------|
| `DISCONNECTED` | Not connected |
| `CONNECTING` | Handshake in progress |
| `CONNECTED` | Ready for communication |

---

## Internal Protocol Details

### Packet Handling

When a packet arrives, the transport:

1. Checks if it's a **retransmit request** → resends buffered packet
2. Checks if it's a **ping** → sends pong reply
3. Tracks **sequence numbers** for gap detection
4. Queues the packet for `receive_packet()` consumers

### Retransmit Logic

- Missing sequence numbers are detected by gaps in incoming seq numbers
- Retransmit requests are sent every 100ms
- After 4 failed retransmit attempts, the missing packet is abandoned
- Buffer holds up to 500 sent packets
