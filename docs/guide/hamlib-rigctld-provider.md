---
description: Use Hamlib through an external rigctld process as a RigPlane provider, with assisted discovery, read-only validation, and troubleshooting guidance.
---

# Hamlib / External rigctld Provider

RigPlane can use Hamlib-supported radios through an external `rigctld` process.
In this mode, Hamlib talks to the radio and RigPlane talks to `rigctld` over the
standard TCP rigctl text protocol. The result is a normal RigPlane radio
connection for the Web UI, CLI, API, diagnostics, and the client-facing
`rigctld` endpoint.

This is different from RigPlane's native providers. Native providers speak the
radio protocol directly, such as Icom CI-V over LAN or USB serial, and can expose
radio-specific features when RigPlane has a tested profile for that model. The
Hamlib-backed provider is the long-tail compatibility path for radios where
Hamlib already has the CAT dialect.

## Why an external rigctld process?

RigPlane starts with this boundary:

```text
RigPlane -> TCP rigctld protocol -> external rigctld -> Hamlib -> radio
```

That boundary keeps Hamlib optional, process-isolated, and easy to install or
upgrade with your operating system's normal package manager. It also avoids
linking RigPlane directly to `libhamlib`, which is deferred unless a future
accepted spike proves that direct binding is needed and covers licensing,
packaging, API compatibility, and crash-containment requirements.

## Setup flow

### 1. Install Hamlib

Install Hamlib with your platform package manager:

```bash
# macOS
brew install hamlib

# Debian / Ubuntu
sudo apt install hamlib-utils

# Fedora
sudo dnf install hamlib
```

Confirm that `rigctld` and `rigctl` are available:

```bash
rigctld --version
rigctl --list | head
```

### 2. Start rigctld for the radio

Choose the Hamlib model ID for your radio, then start `rigctld` on loopback:

```bash
rigctld -m <HAMLIB_MODEL_ID> -r /dev/ttyUSB0 -s 38400 -T 127.0.0.1 -t 4532
```

The serial device, baud rate, and model ID depend on the radio. Keep the
`rigctld` listen address on `127.0.0.1` unless you intentionally expose it on a
trusted network.

Verify the endpoint with a read:

```bash
rigctl -m 2 -r 127.0.0.1:4532 f
rigctl -m 2 -r 127.0.0.1:4532 m
```

### 3. Point RigPlane at rigctld

Use the `rigctld` backend and the `rigctld` host/port:

```bash
rigplane --backend rigctld --host 127.0.0.1 --control-port 4532 status
rigplane --backend rigctld --host 127.0.0.1 --control-port 4532 web
```

You can also validate the running endpoint before selecting it:

```bash
rigplane discover --hamlib-validate --rigctld-host 127.0.0.1 --rigctld-port 4532
rigplane --json discover --serial --hamlib-candidates
```

## Assisted discovery

Assisted discovery helps setup tools and operators choose a Hamlib path. It can:

- list local serial devices that may be CAT control ports;
- load the installed Hamlib model catalog when `rigctl` or `rigctld` is present;
- attach model hints when device descriptions match known Hamlib metadata;
- validate a running external `rigctld` endpoint with safe reads;
- return structured JSON candidates for setup wizards.

It does not:

- auto-install Hamlib;
- start or supervise `rigctld`;
- try every Hamlib model against a serial port;
- claim that a low-confidence serial candidate is safe to auto-select;
- replace radio-specific setup checks such as baud rate, CI-V address, or CAT
  mode settings.

## Safety model

Discovery and validation are read-only first. `--hamlib-validate` uses only safe
read operations against the external `rigctld` endpoint:

- identity or info evidence;
- current frequency read;
- current mode read.

During discovery, RigPlane does not transmit, toggle PTT, write memories, set
frequency, set mode, send CW, issue raw CI-V, run `dump_state`, or make
persistent rig setting changes. Candidates that need operator confirmation are
reported as manual or confirm-model next actions instead of being auto-selected.

## Two rigctld directions

RigPlane uses the word `rigctld` in two separate places:

| Direction | What it is | Typical use |
|-----------|------------|-------------|
| Provider-facing external `rigctld` | Hamlib's `rigctld` process under RigPlane | RigPlane controls a Hamlib-supported radio |
| Client-facing RigPlane `rigctld` endpoint | RigPlane's own compatible TCP server | WSJT-X, JTDX, JS8Call, loggers, and other apps connect to RigPlane |

They can be used together, but they are not the same endpoint. For example,
RigPlane may connect to an external Hamlib `rigctld` on `127.0.0.1:4532` as its
radio provider, then expose its own client-facing endpoint on another port for
WSJT-X:

```bash
rigplane --backend rigctld --host 127.0.0.1 --control-port 4532 web --rigctld-port 4533
```

In WSJT-X, choose `Hamlib NET rigctl` and point it at RigPlane's client-facing
port, not the provider-facing Hamlib port, when you want RigPlane to remain the
station control point.

## Troubleshooting

### Hamlib tools are missing

If `rigplane discover --hamlib-candidates` reports
`hamlibCatalogUnavailable`, install Hamlib and confirm that `rigctl --list`
works in the same shell where you run RigPlane.

### Serial permission denied

On Linux, your user may need access to the serial device group, commonly
`dialout`:

```bash
sudo usermod -aG dialout "$USER"
```

Log out and back in after changing groups. On macOS, check Privacy & Security
prompts and make sure the terminal app can access removable devices if the OS
asks.

### Serial port is busy

Only one process can usually hold a USB serial CAT port. Stop other radio apps
such as flrig, wfview, vendor utilities, or another `rigctld` instance, then
restart the external `rigctld`.

### Model mismatch

If frequency or mode reads fail, confirm the Hamlib model ID:

```bash
rigctl --list | grep -Ei "IC-7300|FT-710|TX-500|X6100"
```

Restart `rigctld` with the corrected `-m` value. Also verify the radio-side CAT
settings: baud rate, CI-V address where applicable, CAT mode, and USB/serial
port selection.

### Validation fails

Run the same reads directly:

```bash
rigctl -m 2 -r 127.0.0.1:4532 f
rigctl -m 2 -r 127.0.0.1:4532 m
```

If those fail, fix the Hamlib/serial setup first. If they pass but RigPlane
validation does not, rerun with the same host and port and open an issue with
the model, OS, `rigctld` command line, and redacted RigPlane output.

## Related pages

- [Supported Radios](radios.md)
- [CLI Reference](cli.md#discover)
- [WSJT-X / JTDX / JS8Call Setup Guide](wsjtx-setup.md)
