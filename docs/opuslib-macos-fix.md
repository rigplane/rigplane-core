---
description: Fix opuslib install errors on macOS for RigPlane audio streaming — Homebrew opus dependency, dynamic library path, and verifying that codec loading succeeds.
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

## Solution

Patch `opuslib` to add Homebrew fallback paths:

**File:** `.venv/lib/python3.11/site-packages/opuslib/api/__init__.py`

```python
lib_location = find_library('opus')

# Fallback for macOS Homebrew
if lib_location is None:
    import os
    homebrew_paths = ['/opt/homebrew/lib/libopus.dylib', '/usr/local/lib/libopus.dylib']
    for path in homebrew_paths:
        if os.path.exists(path):
            lib_location = path
            break

if lib_location is None:
    raise Exception('Could not find Opus library...')
```

## Installation

```bash
# 1. Install Homebrew opus
brew install opus

# 2. Apply patch (automated script)
python scripts/patch_opuslib_macos.py

# 3. Verify
python -c "import opuslib; print('OK')"
```

## Upstream

This workaround should be submitted as a PR to `opuslib` upstream:
https://github.com/OnBeep/opuslib

## Alternative Solutions

1. **Install system-wide opus** (requires sudo, not recommended)
2. **Build wheels with bundled libopus** (complex, maintenance burden)
3. **Use PyOpus instead** (different API, requires code changes)

## Impact

Without this fix:
- TX audio transcoding fails silently (transcoder=False)
- Opus frames sent directly to IC-7610 → no modulation
- RX audio works (no transcoding needed)

With this fix:
- TX audio Opus→PCM16 transcoding works
- IC-7610 receives correct PCM16 data
- Modulation visible on waterfall
