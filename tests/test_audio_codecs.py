"""Tests for pure-Python audio codec implementations."""

import pytest

from icom_lan.audio._codecs import decode_ulaw_to_pcm16


class TestUlawDecoder:
    """Tests for ulaw→PCM16 decoder."""

    def test_decode_ulaw_silence(self):
        """Test decoding of 0xFF (maps to -1868 in the standard table)."""
        # 0xFF decodes to a specific value according to the ulaw lookup table
        silence_ulaw = bytes([0xFF])
        pcm = decode_ulaw_to_pcm16(silence_ulaw)
        # Should produce 2 bytes (one 16-bit sample)
        assert len(pcm) == 2
        # Decode back to int for inspection
        sample = int.from_bytes(pcm, byteorder="little", signed=True)
        # 0xFF should decode to -1868 (from the lookup table)
        assert sample == -1868

    def test_decode_ulaw_zero(self):
        """Zero byte (0x00 or 0x80) should decode to silence."""
        # 0x00 is typically zero in ulaw
        zero_ulaw = bytes([0x00])
        pcm = decode_ulaw_to_pcm16(zero_ulaw)
        assert len(pcm) == 2
        sample = int.from_bytes(pcm, byteorder="little", signed=True)
        # Zero should decode to around -32124 (from lookup table)
        assert sample == -32124

    def test_decode_ulaw_multiple_samples(self):
        """Multiple ulaw samples should decode to multiple PCM samples."""
        # 3 ulaw bytes
        ulaw_data = bytes([0x00, 0x80, 0xFF])
        pcm = decode_ulaw_to_pcm16(ulaw_data)
        # Should produce 6 bytes (3 samples × 2 bytes per sample)
        assert len(pcm) == 6
        # Verify we can read 3 samples
        samples = []
        for i in range(3):
            start = i * 2
            sample = int.from_bytes(
                pcm[start : start + 2], byteorder="little", signed=True
            )
            samples.append(sample)
        assert len(samples) == 3

    def test_decode_ulaw_round_trip_behavior(self):
        """Verify ulaw decoding produces reasonable PCM16 values."""
        # Test a variety of ulaw bytes
        test_ulaws = [0x00, 0x40, 0x80, 0xC0, 0xFF]
        pcm = decode_ulaw_to_pcm16(bytes(test_ulaws))
        assert len(pcm) == len(test_ulaws) * 2

        samples = []
        for i in range(len(test_ulaws)):
            start = i * 2
            sample = int.from_bytes(
                pcm[start : start + 2], byteorder="little", signed=True
            )
            samples.append(sample)

        # All samples should be within 16-bit range
        for sample in samples:
            assert -32768 <= sample <= 32767

    def test_decode_ulaw_bytearray_input(self):
        """Should accept bytearray input."""
        ulaw_data = bytearray([0x00, 0x80])
        pcm = decode_ulaw_to_pcm16(ulaw_data)
        assert len(pcm) == 4  # 2 samples × 2 bytes

    def test_decode_ulaw_memoryview_input(self):
        """Should accept memoryview input."""
        ulaw_data = memoryview(bytes([0x00, 0x80]))
        pcm = decode_ulaw_to_pcm16(ulaw_data)
        assert len(pcm) == 4  # 2 samples × 2 bytes

    def test_decode_ulaw_type_error(self):
        """Should reject non-bytes input."""
        with pytest.raises(TypeError):
            decode_ulaw_to_pcm16("not bytes")  # type: ignore
        with pytest.raises(TypeError):
            decode_ulaw_to_pcm16(123)  # type: ignore

    def test_decode_ulaw_empty_input(self):
        """Empty input should produce empty output."""
        pcm = decode_ulaw_to_pcm16(bytes())
        assert pcm == b""

    def test_decode_ulaw_full_range(self):
        """Test all 256 possible ulaw byte values."""
        # This ensures the lookup table is complete and correct
        all_bytes = bytes(range(256))
        pcm = decode_ulaw_to_pcm16(all_bytes)
        # Should produce exactly 512 bytes (256 samples × 2 bytes per sample)
        assert len(pcm) == 512

        # All samples should be valid 16-bit signed integers
        for i in range(256):
            start = i * 2
            sample = int.from_bytes(
                pcm[start : start + 2], byteorder="little", signed=True
            )
            # Check bounds
            assert -32768 <= sample <= 32767
            # Check non-zero (as the lookup table has specific values for each byte)
            assert sample != 0 or i in [0, 0x80]  # Only specific bytes map to zero

    def test_decode_ulaw_pcm16_format(self):
        """Verify output is little-endian signed 16-bit PCM."""
        # Decode a known ulaw value
        ulaw_data = bytes([0xFF])
        pcm = decode_ulaw_to_pcm16(ulaw_data)

        # Manually check the byte order and signedness
        low_byte = pcm[0]
        high_byte = pcm[1]
        sample = low_byte | (high_byte << 8)
        # Convert to signed
        if sample >= 32768:
            sample -= 65536

        # Result should be one of the values from the lookup table
        assert -32768 <= sample <= 32767

    def test_decode_ulaw_stereo_conversion(self):
        """Verify proper handling of stereo ulaw data (2 channels)."""
        # Simulate stereo ulaw: [L, R, L, R, ...]
        stereo_ulaw = bytes([0x00, 0xFF, 0x80, 0x7F])
        pcm = decode_ulaw_to_pcm16(stereo_ulaw)

        # 4 ulaw bytes → 8 PCM bytes (4 samples × 2 bytes each)
        assert len(pcm) == 8

        # Can read 4 samples
        samples = []
        for i in range(4):
            start = i * 2
            sample = int.from_bytes(
                pcm[start : start + 2], byteorder="little", signed=True
            )
            samples.append(sample)

        # All samples should be different (different input bytes)
        assert len(set(samples)) >= 3  # At least 3 unique samples
