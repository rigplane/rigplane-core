"""Tests for rigplane.scope.levels.AdaptiveLevelMapper (MOR-3152).

The mapper was extracted from AudioFftScope's adaptive dB→pixel window
(MOR-512). The golden tests pin byte-identical behaviour: the golden pixel
frames below were recorded from the PRE-REFACTOR implementation (the old
private helpers in audio/fft_scope.py) before it was deleted, and the same
synthetic dB sequence must reproduce them through both the new mapper and
the AudioFftScope wrapper.
"""

from __future__ import annotations

import numpy as np

from rigplane.audio.fft_scope import AudioFftScope
from rigplane.scope.levels import AdaptiveLevelMapper

_PIXEL_MAX = 160

# Golden fixture shape: fft_size=256 → 129 rfft bins, sample_rate=48000.
_GOLDEN_FFT_SIZE = 256
_GOLDEN_SAMPLE_RATE = 48000
_GOLDEN_NUM_FRAMES = 19  # 4 noise + 6 tone + 6 post-tone + 3 loud

# Golden pixel frames recorded from the pre-refactor AudioFftScope
# (src/rigplane/audio/fft_scope.py at commit dfc1ef3a2, the MOR-3152 parent):
# each frame of ``_golden_db_frames()`` was mapped through that commit's
# ``_update_db_window()`` plus its ``_process_chunk()`` pixel arithmetic.
# To regenerate, check out the parent commit and replay the capture. One hex
# string per frame (129 pixel bytes each).
_GOLDEN_FULL_SPECTRUM_HEX = [
    "1d0d180d111a112019141217171817160918000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d11150619100e100c0b0f1912131018191d000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d1717110d1a101a0e141313140d15121f14000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d0a131d1c13181a1f1723201219150e071b000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c131d192115110b80161c0c121319111419000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c09201a1e1b0b1b801e1f10191113161c0f000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c11241a1210220c8018201510150d151814000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d0d110e19151a1880170d071515131d110a000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d0d251b190e12108010080b161322171115000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d120c111a131c11800f1d0c1210180e1307000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d10161e1211181d241a0d1b161a1b0b1612000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d161b1a2015130e0d2a11230b1e18141214000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d070611122d1317190a1815100e161c0c17000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d0e1511121d100f140d1213110d1507161d000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d16111016180815211a130e04101f14161f000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d1b12161e1b1c1814191b19131121191311000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0",
    "a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a09f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f",
    "a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383838383",
]

# Same frames through a scope with set_mode_bandwidth(3600): the audio
# in-band rule narrows the estimation slice (upper edge min(1800, 3500) Hz).
_GOLDEN_CROPPED_3600_HEX = [
    "1d0d180d111a11201a151217181818160a18000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d1216071a100e100c0c0f1a13141119191e000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d1818120e1b101b0f151413150e15131f14000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d0b131d1c14191b1f172321121a150f081b000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1d131e192116120c80161c0c131319111419000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c09201a1e1b0a1b7f1e1f10191113161b0f000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c10231a1110220c7f18201410150d151813000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c0c100e18141a187f170d071414121d1109000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c0c251b190d120f7f10080b151222171015000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c120b111a131c10800f1d0c1210170d1207000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c10151e1210171d241a0d1a15191a0a1512000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c151a192014120d0d2911230a1d18141213000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c070511112d1317190917140f0d161c0b17000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c0d1410121c100e130d1112100d1507151c000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c16100f161708142019120d03101f14151e000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "1c1a11151d1a1b1713181a18121020181210000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000",
    "a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0",
    "a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a09d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d9d",
    "a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0a0828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282828282",
]


def _golden_db_frames(
    fft_size: int = _GOLDEN_FFT_SIZE,
    sample_rate: int = _GOLDEN_SAMPLE_RATE,
    seed: int = 3152,
) -> list[np.ndarray]:
    """Deterministic bimodal dB frame sequence exercising the adaptive window.

    Frames 0-3: band-noise only — the tracked floor/ceil seed and settle.
    Frames 4-9: a +30 dB in-band tone appears — the ceil attacks.
    Frames 10-15: tone gone — the ceil decays slowly.
    Frames 16-18: near-full-scale loud frames — margins/clamps/min-span edges.

    Shape mirrors the live FTX-1 capture (MOR-512): in-band band-noise ~-102 dB
    over 100-3200 Hz, quiet ~-126 dB out-of-band, hot DC bin.
    """
    n_bins = fft_size // 2 + 1
    bin_res = sample_rate / fft_size
    freqs = np.arange(n_bins) * bin_res
    inband = (freqs >= 100.0) & (freqs <= 3200.0)
    tone_bin = int(1500.0 / bin_res)
    frames: list[np.ndarray] = []
    for i in range(_GOLDEN_NUM_FRAMES):
        rng = np.random.default_rng(seed * 1000 + i)
        db = np.full(n_bins, -126.0)
        db[inband] = -102.1 + rng.standard_normal(int(inband.sum())) * 2.0
        db[0] = -100.0
        if 4 <= i <= 9:
            db[tone_bin] = -72.1
        if i >= 16:
            db = np.full(n_bins, -40.0)
            db[inband] = -6.0 + rng.standard_normal(int(inband.sum())) * 1.0
            db[0] = -3.0
        frames.append(db)
    return frames


def _pre_refactor_pixels(db: np.ndarray, db_floor: float, db_ceil: float) -> bytes:
    """Replicate the pre-refactor ``_process_chunk`` pixel arithmetic."""
    db_range = db_ceil - db_floor
    pixels_float = (db - db_floor) / db_range * _PIXEL_MAX
    return bytes(np.clip(pixels_float, 0, _PIXEL_MAX).astype(np.uint8))


def _scope_frame_pixels(scope: AudioFftScope, db: np.ndarray) -> bytes:
    """Map ``db`` through the AudioFftScope window path (old code path)."""
    db_floor, db_ceil = scope._update_db_window(db)
    return _pre_refactor_pixels(db, db_floor, db_ceil)


# ── Golden: old and new code paths must be byte-identical ────────────────────


class TestAdaptiveLevelMapperGolden:
    """The refactor must not change a single output pixel (MOR-3152).

    The same synthetic dB sequence is replayed through the new mapper
    (``rigplane.scope.levels``) and through the AudioFftScope wrapper; both
    must reproduce the golden frames recorded from the pre-refactor
    implementation before its private window helpers were deleted.
    """

    def test_mapper_reproduces_golden_full_spectrum(self):
        # Audio in-band rule at 256/48000: lo=int(50/187.5)=0,
        # hi=int(3500/187.5)=18 (bin_res = 187.5 Hz).
        mapper = AdaptiveLevelMapper(inband_bins=(0, 18))
        for db, golden_hex in zip(
            _golden_db_frames(), _GOLDEN_FULL_SPECTRUM_HEX, strict=True
        ):
            assert mapper.map(db) == bytes.fromhex(golden_hex)

    def test_mapper_reproduces_golden_cropped_bandwidth(self):
        # set_mode_bandwidth(3600) narrows the estimation slice:
        # hi=int(min(1800, 3500)/187.5)=9.
        mapper = AdaptiveLevelMapper(inband_bins=(0, 9))
        for db, golden_hex in zip(
            _golden_db_frames(), _GOLDEN_CROPPED_3600_HEX, strict=True
        ):
            assert mapper.map(db) == bytes.fromhex(golden_hex)

    def test_audio_fft_scope_path_reproduces_golden(self):
        """The AudioFftScope wrapper (previously the only code path) is
        byte-identical to the golden for both the full-spectrum and the
        cropped in-band configurations."""
        for bandwidth, golden_hex in (
            (None, _GOLDEN_FULL_SPECTRUM_HEX),
            (3600, _GOLDEN_CROPPED_3600_HEX),
        ):
            scope = AudioFftScope(
                fft_size=_GOLDEN_FFT_SIZE,
                fps=100,
                avg_count=1,
                sample_rate=_GOLDEN_SAMPLE_RATE,
            )
            scope.set_center_freq(14_074_000)
            if bandwidth is not None:
                scope.set_mode_bandwidth(bandwidth)
            for db, golden in zip(_golden_db_frames(), golden_hex, strict=True):
                assert _scope_frame_pixels(scope, db) == bytes.fromhex(golden)

    def test_wrapper_and_mapper_paths_agree_at_production_fft_size(self):
        """Differential at the production fft_size=2048: the AudioFftScope
        window path and a directly-driven mapper produce identical windows
        and identical pixel bytes frame by frame."""
        frames = _golden_db_frames(fft_size=2048, sample_rate=48000)
        scope = AudioFftScope(fft_size=2048, fps=100, avg_count=1, sample_rate=48000)
        scope.set_center_freq(14_074_000)
        # Audio in-band rule at 2048/48000: lo=int(50/23.4375)=2,
        # hi=int(3500/23.4375)=149.
        mapper_window = AdaptiveLevelMapper(inband_bins=(2, 149))
        mapper_pixels = AdaptiveLevelMapper(inband_bins=(2, 149))
        for db in frames:
            mapper_floor, mapper_ceil = mapper_window.window(db)
            mapper_bytes = mapper_pixels.map(db)
            scope_floor, scope_ceil = scope._update_db_window(db)
            assert (mapper_floor, mapper_ceil) == (scope_floor, scope_ceil)
            assert mapper_bytes == _pre_refactor_pixels(db, scope_floor, scope_ceil)


# ── Unit behaviour ───────────────────────────────────────────────────────────


class TestAdaptiveLevelMapper:
    """Mapper behaviour on synthetic spectra."""

    def test_flat_noise_maps_low(self):
        """A flat noise field must stay near the bottom of the display.

        The minimum-span guard prevents the auto-range from collapsing the
        window onto the noise and stretching it across the screen.
        """
        mapper = AdaptiveLevelMapper()
        rng = np.random.default_rng(100)
        for _ in range(8):
            db = np.full(129, -100.0) + rng.standard_normal(129) * 0.5
            pixels = mapper.map(db)
            assert isinstance(pixels, bytes)
            assert len(pixels) == 129
            values = np.frombuffer(pixels, dtype=np.uint8)
            assert int(values.max()) < 0.30 * _PIXEL_MAX

    def test_tone_30db_above_floor_maps_near_top(self):
        """A tone 30 dB above the noise floor must render near the top of
        the display, clearly separated from the surrounding noise."""
        mapper = AdaptiveLevelMapper()
        rng = np.random.default_rng(200)
        for _ in range(5):
            mapper.map(np.full(200, -100.0) + rng.standard_normal(200) * 1.0)

        db = np.full(200, -100.0) + rng.standard_normal(200) * 1.0
        tone_bin = 150
        db[tone_bin] = -70.0  # 30 dB above the noise floor
        values = np.frombuffer(mapper.map(db), dtype=np.uint8)

        tone_px = float(values[tone_bin])
        noise_median_px = float(np.median(np.delete(values, tone_bin)))
        assert tone_px > 0.60 * _PIXEL_MAX, (
            f"tone only reached {tone_px:.0f}/{_PIXEL_MAX} — poor contrast"
        )
        assert tone_px - noise_median_px > 0.35 * _PIXEL_MAX, (
            f"tone {tone_px:.0f} not separated from noise {noise_median_px:.0f}"
        )

    def test_ceil_attacks_fast_and_decays_slowly(self):
        """The ceil must rise fast when a strong plateau appears and fall
        slowly once it disappears (no window pump on a vanishing signal)."""
        n = 200
        rng = np.random.default_rng(300)

        def quiet_db() -> np.ndarray:
            db = np.full(n, -140.0) + rng.standard_normal(n) * 0.5
            db[120:] = -100.0 + rng.standard_normal(n - 120) * 0.5
            return db

        def hot_db() -> np.ndarray:
            db = quiet_db()
            db[180:] = -30.0 + rng.standard_normal(n - 180) * 0.5
            return db

        mapper = AdaptiveLevelMapper()
        for _ in range(3):
            _, ceil_quiet = mapper.window(quiet_db())

        # Attack: one hot frame must move the ceil up by a large fraction of
        # the quiet→hot gap (ceil_attack = 0.30 per frame).
        _, ceil_attack = mapper.window(hot_db())
        for _ in range(9):
            _, ceil_hot = mapper.window(hot_db())
        gap = ceil_hot - ceil_quiet
        assert gap > 0
        assert ceil_attack - ceil_quiet > 0.20 * gap, (
            "ceil attack too slow: moved "
            f"{ceil_attack - ceil_quiet:.1f} dB of a {gap:.1f} dB gap in one frame"
        )

        # Decay: one quiet frame must move the ceil down by only a small
        # fraction of the gap (ceil_decay = 0.05 per frame) …
        _, ceil_decay_1 = mapper.window(quiet_db())
        assert ceil_hot - ceil_decay_1 < 0.15 * gap, (
            "ceil snapped down after the signal disappeared: dropped "
            f"{ceil_hot - ceil_decay_1:.1f} dB of a {gap:.1f} dB gap in one frame"
        )
        # … and it must still be clearly elevated (and decaying) 20 frames
        # later instead of collapsing onto the quiet level.
        for _ in range(19):
            _, ceil_decay_20 = mapper.window(quiet_db())
        assert ceil_quiet < ceil_decay_20 < ceil_decay_1, (
            "ceil must keep decaying slowly, not collapse or stick: "
            f"quiet={ceil_quiet:.1f} after1={ceil_decay_1:.1f} "
            f"after20={ceil_decay_20:.1f}"
        )

    def test_inband_range_equivalent_to_full_and_settable(self):
        """An in-band range covering the whole array estimates identically to
        the full-array default, whether set at construction or later."""
        db = np.linspace(-120.0, -60.0, 100)
        mapper_full = AdaptiveLevelMapper()
        mapper_range = AdaptiveLevelMapper(inband_bins=(0, 99))
        mapper_set = AdaptiveLevelMapper()
        mapper_set.set_inband_bins(0, 99)
        for _ in range(3):
            full_window = mapper_full.window(db)
            assert mapper_range.window(db) == full_window
            assert mapper_set.window(db) == full_window

    def test_degenerate_inband_range_falls_back_to_full_array(self):
        """An in-band range wider than a short input must not crash or
        mis-slice: it falls back to estimating over the full array."""
        db = np.array([-90.0, -80.0])
        mapper_limited = AdaptiveLevelMapper(inband_bins=(2, 149))
        mapper_full = AdaptiveLevelMapper()
        for _ in range(3):
            assert mapper_limited.window(db) == mapper_full.window(db)

    def test_reset_reseeds_window(self):
        """After reset() the next window is seeded from the next frame,
        exactly like a freshly constructed mapper."""
        db = np.linspace(-120.0, -60.0, 80)
        mapper = AdaptiveLevelMapper()
        for _ in range(5):
            mapper.window(db)
        mapper.reset()
        fresh = AdaptiveLevelMapper()
        assert mapper.window(db) == fresh.window(db)

    def test_pixel_max_parameter_bounds_output(self):
        mapper = AdaptiveLevelMapper(pixel_max=100)
        rng = np.random.default_rng(400)
        for _ in range(3):
            db = np.full(64, -100.0) + rng.standard_normal(64) * 1.0
            values = np.frombuffer(mapper.map(db), dtype=np.uint8)
            assert int(values.max()) <= 100
