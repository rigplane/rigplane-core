---
robots: noindex, follow
---

# Performance Analysis & Optimization (M6.3)

## Baseline Metrics (2026-03-23)

### Test Suite Performance
- **Unit tests (test_commands, test_civ, test_radio)**: 514 tests in 1.88s (3.6ms per test)
- **Full test suite**: 4492 tests (23ms median per test)
- **Slowest tests**:
  - `test_multiple_timeouts_followed_by_success`: 0.21s (timeout simulation)
  - `test_deadline_timeout_does_not_always_send_three_attempts`: 0.20s (timeout simulation)
  - `test_timeout_does_not_affect_subsequent_command`: 0.10s (timeout simulation)

### Key Observations
1. **Unit tests are fast** — 514 tests in <2s, 3.6ms median
2. **Slow tests are mostly timeout/stress simulations** — intentionally slow for testing robustness
3. **No obvious performance bottlenecks** in regular command path
4. **Test collection overhead** is minimal

## Potential Optimization Areas

### 1. CI-V Command Parsing
- **Current**: Sequential parsing of CI-V responses in `commands.py`
- **Opportunity**: Lazy evaluation of rarely-used fields
- **Impact**: Small (most operations are fast enough)
- **Effort**: Medium (refactoring required)

### 2. RadioPoller Efficiency
- **Current**: TOML-based command map lookup per poll cycle
- **Opportunity**: Cache compiled command sequence after first poll
- **Impact**: Medium (reduces TOML parsing overhead)
- **Effort**: Low (simple caching)

### 3. Audio Buffer Management
- **Current**: Dynamic allocation in audio streams
- **Opportunity**: Pre-allocate buffers for common sizes (16kHz, 20ms frames)
- **Impact**: Small (buffer allocation not a bottleneck)
- **Effort**: Low (simple pool implementation)

### 4. Web State Synchronization
- **Current**: Full state object serialization per update
- **Opportunity**: Delta encoding for incremental state updates
- **Impact**: Medium (reduces network payload)
- **Effort**: Medium (requires protocol change)

### 5. Test Parallelization
- **Current**: Sequential test execution
- **Opportunity**: Use pytest-xdist for parallel test runs
- **Impact**: High (3-4x speedup on multi-core)
- **Effort**: Low (pytest plugin)

## Completed Optimizations (M4-M5)

- ✅ Data-driven poller (TOML CommandMap) — reduced hardcoded command lists
- ✅ Plain CI-V fallback — eliminated receiver selector overhead for single-receiver radios
- ✅ Optimistic state updates — UI feedback without waiting for CI-V ACK
- ✅ Command deduplication in commander queue — reduced redundant transmissions

## Recommendations

### Priority 1 (High ROI, Low Effort)
- [x] Cache compiled poller command sequences ✅ ALREADY IMPLEMENTED
  - `_STATE_QUERIES` built once at init (line 607)
  - `_cmd_map` loaded once at init (line 597)
  - No per-cycle TOML parsing overhead
- [x] Add performance regression tests ✅ COMPLETE (2026-03-23)
  - 7 tests with SLO validation in `test_performance_regressions.py`
- [x] Profile CI-V command pipeline latency ✅ COMPLETE (2026-03-23)
  - **Profiling results established**:
    - Frame creation: 0.15–0.94 µs/op (avg 0.33 µs)
    - BCD encoding: 0.81 µs/op
    - Frame parsing: 0.02 µs/frame
    - Command queueing: 0.09 µs/cmd
    - Full frequency roundtrip: 1.04 µs/op
  - **Throughput**: 5.1M frames/sec, 1.2M BCD ops/sec
  - **Latency distribution**: p50=0.17µs, p95=0.25µs, p99=2.42µs
  - **Finding**: Command pipeline is already highly optimized; no bottlenecks identified
  - 8 profiling tests in `test_civ_command_profiling.py`

### Priority 2 (Medium ROI, Medium Effort)
- [x] Implement delta encoding for web state updates ✅ COMPLETE (2026-03-23)
  - DeltaEncoder module with diff/patch logic
  - 10-50x payload reduction for state broadcasts (~2KB → ~50-100 bytes)
  - Full state refresh every 100 updates prevents drift
  - 22 unit tests covering all paths (roundtrip, edge cases)
- [x] Add audio buffer pooling ✅ COMPLETE (2026-03-23)
  - AudioBufferPool: Pre-allocates buffers, thread-safe acquire/release via object id
  - Supports common audio frame sizes: 16kHz/48kHz mono/stereo at 20ms frames
  - LIFO reuse strategy for cache locality
  - 15 unit tests covering pool mechanics, reuse, thread safety, concurrent access
  - Performance: >50k acquire/release ops/sec, >30k ops/sec under concurrent load
  - Integrated into AudioBroadcaster for future codec optimization
- [x] Profile web audio streaming performance ✅ COMPLETE (2026-03-24)
  - Comprehensive benchmarking: 10 tests covering codecs, relay loop, full pipeline
  - Results: All operations exceed SLOs with 18-588× headroom
  - ulaw decode: 8.67µs p50, 18.84M samples/sec throughput
  - Frame encode: 0.17µs p50, 8.4M frames/sec throughput
  - Full pipeline: 25.5µs p50, 373.5µs p99 latency
  - Buffer pool: 99.5% allocation reduction in realistic streaming load
  - Documentation: docs/AUDIO_STREAMING_PROFILE.md with detailed analysis

### Priority 3 (Low ROI, High Effort)
- [ ] Refactor CI-V parsing for lazy evaluation
- [ ] Optimize command matrix lookups

### Not Viable
- ❌ pytest-xdist for parallel testing — incompatible with asyncio test mode
  - All radio tests use asyncio; xdist requires isolation that breaks shared fixtures
  - Test suite already fast (79s total); further optimization has diminishing returns

## Testing Performance

### Next Steps
1. Cache compiled poller command sequences (quick win)
2. Add latency regression tests for key operations
3. Profile real-time operations (audio streaming, scope updates)
4. Establish latency SLOs for user-facing operations (get_frequency, set_mode, etc.)

---

**Generated**: 2026-03-23
**Last Updated**: 2026-03-24 (M6.P2.3 audio streaming profiling complete)
**Status**: M6 Priority 2 (3/3 items complete: delta encoding, buffer pooling, audio profiling)
**Next**: M6 complete; ready for M7 (post-productization)
