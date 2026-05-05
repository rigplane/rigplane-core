# Security

## Overview

rigplane implements Icom's proprietary LAN protocol for controlling amateur radio transceivers. This document describes security properties, known limitations, and best practices.

## Threat Model

The library is designed for use on **trusted local networks** (home LAN, shack network). The Icom protocol was designed for convenience, not security.

### What rigplane protects

- ✅ **No credential storage** — credentials are passed as parameters or environment variables, never written to disk by the library
- ✅ **No hardcoded secrets** — no default passwords or keys in the codebase
- ✅ **Clean exception handling** — errors don't leak credentials in tracebacks
- ✅ **Minimal attack surface** — core requires only `pyserial`; optional extras are well-scoped
- ✅ **Controlled network listeners** — the library initiates outbound UDP connections; the optional web server (`rigplane web`) and rigctld server (`rigplane serve`) bind to configurable addresses and support auth tokens

### What the Icom protocol does NOT provide

- ❌ **No encryption** — all traffic is plaintext UDP
- ❌ **No TLS/DTLS** — packets can be sniffed by anyone on the network
- ❌ **Weak credential obfuscation** — substitution cipher, not encryption (see below)
- ❌ **No replay protection** — captured packets could be replayed
- ❌ **No packet authentication** — packets can be spoofed by a network attacker

These are **limitations of Icom's protocol design**, not bugs in this library. The same limitations apply to Icom's own RS-BA1 software and wfview.

## Credential Obfuscation

Icom's protocol encodes credentials using a position-dependent substitution table. This is **obfuscation, not encryption**:

- The substitution table is publicly known (published in wfview source code)
- Anyone with network access can decode captured credentials
- This is equivalent to Base64 in terms of security — it prevents casual viewing but not determined attackers

### Implications

- Do **not** reuse your radio's network password for other services
- Treat the radio's network password as "shared LAN secret", not as a security barrier
- If your network is compromised, assume radio credentials are compromised too

## Network Security Recommendations

### For Home Networks

1. **Use a dedicated VLAN or subnet** for radio equipment if your router supports it
2. **Don't expose ports 50001–50003** to the internet (no port forwarding)
3. **Use WPA3** for WiFi networks with radios (IC-705)
4. **Static IP** for the radio — avoids DNS/DHCP-based attacks

### For Remote Access

If you need remote control:

1. **Use a VPN** (WireGuard, OpenVPN) — encrypts all traffic, including Icom's plaintext UDP
2. **Never forward ports directly** — the protocol has no authentication beyond the substitution cipher
3. **Set strong passwords** on the VPN, not just on the radio

### For Shared Networks (Field Day, club stations)

1. Use unique radio network credentials
2. Monitor for unexpected connections (the radio's connection log)
3. Consider a dedicated WiFi network for radio equipment

## Code Security

### Input Validation

| Input | Validation |
|-------|-----------|
| Frequency | Positive integer, BCD encoding limits to 10 digits (≤ 9,999,999,999 Hz) |
| Power level | 0–255 range check |
| Mode | Enum validation |
| Credentials | Truncated to 16 characters (protocol limit) |
| CI-V address | 1-byte value (0x00–0xFF) |
| CW text | ASCII encoding (non-ASCII silently dropped) |
| Timeout | Positive float |

### No Arbitrary Code Execution

The library processes binary protocol data with fixed-format parsing (struct.unpack). There is no:

- `eval()` or `exec()` on received data
- Deserialization of untrusted objects (no pickle, no yaml.load)
- Shell command construction from radio data
- File system access based on radio responses

### Dependencies

Runtime: **`pyserial`** and **`pyserial-asyncio`** (core); optional extras (`opuslib`, `sounddevice`, `numpy`, `pillow`, `cryptography`) installed only when requested. Minimal supply-chain surface.

## Reporting Vulnerabilities

If you discover a security vulnerability, please report it responsibly:

1. **Do not** open a public GitHub issue
2. Email: **morozsm@gmail.com** with subject line `[SECURITY] rigplane`
3. Include: description, reproduction steps, potential impact
4. Allow 90 days for a fix before public disclosure

## Audit Checklist

For security-conscious users, verify:

- [ ] Environment variables (`ICOM_PASS`) are not logged or exposed in process listings
- [ ] `.env` files are in `.gitignore`
- [ ] Network between client and radio is trusted (or VPN-protected)
- [ ] Radio firmware is up to date
- [ ] Radio network user has a unique password (not reused elsewhere)
- [ ] UDP ports 50001–50003 are not forwarded to the internet
