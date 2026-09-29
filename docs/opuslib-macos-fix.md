---
description: Fix opuslib library lookup errors on macOS for RigPlane audio streaming — the Homebrew opus dependency and the fallback paths RigPlane searches.
---

# macOS Opus Library Fix

## Problem

On macOS (especially with Apple Silicon), `ctypes.util.find_library('opus')` fails to locate Homebrew's libopus even when installed:

```
Exception: Could not find Opus library. Make sure it is installed.
```

## Root Cause

- `find_library()` searches system paths only
- Homebrew libs are in `/opt/homebrew/lib` (Apple Silicon) or `/usr/local/lib` (Intel)
- SIP (System Integrity Protection) prevents `DYLD_LIBRARY_PATH` from working in most contexts

## What RigPlane Does

RigPlane imports `opuslib` through its own lookup: when the platform search
finds no `opus` library, it tries `/opt/homebrew/lib/libopus.dylib`,
`/usr/local/lib/libopus.dylib` and `/usr/local/lib/libopus.so`, in that order
(`src/rigplane/audio/_transcoder.py: _opus_library_lookup`). No patch to
`opuslib` is needed; install libopus with Homebrew:

```bash
brew install opus
```

Other programs that import `opuslib` directly do not get this lookup.
