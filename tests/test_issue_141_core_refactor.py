"""Regression tests for issue #141 (IC-7610 core extraction)."""

from rigplane.radio import CoreRadio, IcomRadio


def test_icom_radio_is_thin_wrapper_over_core() -> None:
    """Public IcomRadio must remain source-compatible wrapper over shared core."""
    assert issubclass(IcomRadio, CoreRadio)


def test_wrapper_constructor_signature_is_compatible() -> None:
    """Wrapper should instantiate with the same constructor as before."""
    radio = IcomRadio("192.168.1.100", model="IC-7610")
    assert radio.model == "IC-7610"
