---
robots: noindex, follow
---

# Protocol Deep Dive

The Icom LAN protocol is a proprietary UDP-based protocol used by Icom transceivers for remote control over Ethernet/WiFi. It was reverse-engineered by the [wfview](https://wfview.org/) project.

## Packet Structure

Every UDP packet starts with a fixed 16-byte header:

```
Offset  Size  Endian  Field
0x00    4     LE      Packet length (total, including header)
0x04    2     LE      Packet type
0x06    2     LE      Sequence number
0x08    4     LE      Sender ID
0x0C    4     LE      Receiver ID
```

## Packet Types

| Type | Name | Size | Description |
|------|------|------|-------------|
| `0x00` | DATA | Variable | Carries CI-V commands, audio, etc. |
| `0x01` | CONTROL | 0x10+ | Retransmit requests |
| `0x03` | ARE_YOU_THERE | 0x10 | Discovery request (broadcast) |
| `0x04` | I_AM_HERE | 0x10 | Discovery response |
| `0x05` | DISCONNECT | 0x10 | Disconnect notification |
| `0x06` | ARE_YOU_READY | 0x10 | Ready handshake |
| `0x07` | PING | 0x15 | Keep-alive ping/pong |

## Port Architecture

| Port | Name | Purpose |
|------|------|---------|
| 50001 | Control | Authentication, session management, keep-alive |
| 50002 | CI-V | CI-V serial command exchange |
| 50003 | Audio | Opus audio streaming |

The CI-V and audio port numbers are **negotiated** — they're reported in the status packet (0x50 bytes) after the conninfo exchange. However, in practice they are almost always control_port+1 and control_port+2.

## Discovery

```
Client → Radio:  ARE_YOU_THERE (0x03), 16 bytes
Radio → Client:  I_AM_HERE (0x04), 16 bytes, includes radio's sender_id
Client → Radio:  ARE_YOU_READY (0x06), 16 bytes
Radio → Client:  ARE_YOU_READY (0x06), 16 bytes (echo/ack)
```

This can also be broadcast (UDP broadcast to port 50001) for autodiscovery.

## Authentication

### Login Packet (0x80 bytes)

```
Offset  Size  Field
0x00    4     Packet length (0x80)
0x04    2     Type (0x00 = DATA)
0x06    2     Sequence
0x08    4     Sender ID
0x0C    4     Receiver ID
0x10    4     Payload size (0x70, big-endian)
0x14    1     Request/reply (0x01)
0x15    1     Request type (0x00 = login)
0x16    2     Inner sequence (big-endian)
0x1A    2     Token request ID
0x40    16    Encoded username
0x50    16    Encoded password
0x60    16    Computer name (ASCII)
```

### Credential Encoding

Icom uses a position-dependent substitution cipher:

1. For each character at position `i`: compute index = `ord(char) + i`
2. If index > 126: index = `32 + (index % 127)`
3. Look up in the substitution table (128-byte constant)

This is **obfuscation, not encryption**. The substitution table is publicly known.

### Auth Response (0x60 bytes)

```
Offset  Size  Field
0x1A    2     Token request ID (echoed)
0x1C    4     Session token
0x30    4     Error code (0 = success, 0xFEFFFFFF/0xFFFFFFFF = failure)
0x40    16    Connection type string
```

### Token Ack (0x40 bytes)

```
Offset  Size  Endian  Field
0x10    4     BE      Payload size
0x14    1             Request/reply (0x01)
0x15    1             Request type (0x02 = token ack)
0x16    2     BE      Inner sequence
0x1A    2     LE      Token request ID
0x1C    4     LE      Token
0x24    2     BE      Reset capability (0x0798)
```

## Conninfo Exchange

### Conninfo Packet (0x90 bytes)

Sent by both radio and client. Contains:

```
Offset  Size  Field
0x15    1     Request type (0x03 = conninfo)
0x1C    4     Token
0x20    16    GUID / MAC area
0x40    32    Device name
0x60    16    Encoded username
0x70    1     RX enable
0x71    1     TX enable
0x72    1     RX codec
0x73    1     TX codec
0x74    4     RX sample rate (BE)
0x78    4     TX sample rate (BE)
0x7C    4     CI-V local port (BE)
0x80    4     Audio local port (BE)
0x84    4     TX buffer latency ms (BE)
```

**Critical:** The client must echo the radio's GUID (0x20–0x2F from the radio's conninfo) in its own conninfo. Otherwise, the status packet will report CI-V port = 0.

### Status Packet (0x50 bytes)

Sent by the radio after receiving the client's conninfo:

```
Offset  Size  Endian  Field
0x30    4     LE      Error code
0x40    1             Disconnect flag
0x42    2     BE      CI-V port
0x46    2     BE      Audio port
```

## CI-V Over UDP

### CI-V Data Packet

CI-V frames are wrapped in UDP data packets on port 50002:

```
Offset  Size  Field
0x00    4     Total packet length
0x04    2     Type (0x00 = DATA)
0x06    2     Sequence
0x08    4     Sender ID
0x0C    4     Receiver ID
0x10    1     Reply marker (0xC1 for CI-V data)
0x11    2     CI-V frame length (LE)
0x13    2     Send sequence (BE)
0x15    ...   CI-V frame data
```

The CI-V header is 0x15 bytes (not 0x10 — there's a 5-byte sub-header after the standard packet header).

### OpenClose Packet (0x16 bytes)

Sent to start/stop the CI-V data stream:

```
Offset  Size  Field
0x10    2     Data (0x01C0)
0x13    2     Send sequence (BE)
0x15    1     Magic (0x04 = open, 0x00 = close)
```

## Keep-Alive

### Ping Packet (0x15 bytes)

```
Offset  Size  Field
0x00    4     Length (0x15)
0x04    2     Type (0x07 = PING)
0x06    2     Ping sequence
0x08    4     Sender ID
0x0C    4     Receiver ID
0x10    1     Flag (0x00 = request, 0x01 = reply)
0x11    4     Timestamp (LE, milliseconds)
```

Pings are sent every 500ms. The radio drops connections that stop pinging.

## Retransmit

If a sequence gap is detected, the receiver sends a retransmit request:

- **Single missing:** 0x10-byte control packet with type=0x01 and the missing seq in the seq field
- **Multiple missing:** 0x10+ byte packet with type=0x01, followed by pairs of (seq, seq) for each missing packet

## CI-V Frame Format

Standard Icom CI-V serial protocol, encapsulated in UDP:

```
FE FE <to> <from> <cmd> [<sub>] [<data>...] FD
```

- `FE FE` — preamble
- `<to>` — destination address (radio = 0x98 for IC-7610)
- `<from>` — source address (controller = 0xE0)
- `<cmd>` — command byte
- `<sub>` — sub-command (optional, depends on command)
- `<data>` — payload (variable length)
- `FD` — terminator

### BCD Frequency Encoding

Frequencies are encoded as 5-byte little-endian BCD:

```
14,074,000 Hz → 00 40 07 14 00

Byte 0: 00 → digits 0,0 (1 Hz, 10 Hz)
Byte 1: 40 → digits 4,0 (100 Hz, 1 kHz)
Byte 2: 07 → digits 0,7 (10 kHz, 100 kHz)
Byte 3: 14 → digits 1,4 (1 MHz, 10 MHz)
Byte 4: 00 → digits 0,0 (100 MHz, 1 GHz)
```

## References

- [wfview](https://wfview.org/) — open-source Icom remote control software (GPLv3)
- `packettypes.h` — packet structure definitions
- `icomudpbase.cpp` — base UDP handling
- `icomudphandler.cpp` — authentication and handshake
- `icomudpcivdata.cpp` — CI-V data flow
- `icomcommander.cpp` — CI-V command encoding

## Legal Note

Protocol reverse engineering for interoperability is protected by:

- **EU Directive 2009/24/EC** (Software Directive, Article 6)
- **US DMCA** interoperability exception (17 U.S.C. § 1201(f))

This library is a clean-room implementation based on protocol understanding, not a derivative of wfview's GPLv3 code.
