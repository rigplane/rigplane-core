"""Forward-extension test for the :class:`UsbAudioCapable` capability.

The load-bearing assertion of epic #1322 (Capability 4/4): the
architecture admits new radio backends purely by **structural
conformance** to the public Capability Protocols — no upper-layer
(web/rigctld) code change needed.

If a new backend declares ``has_usb_audio: bool = True`` as a class
attribute, the web layer's ``runtime_capabilities`` helper picks it
up via ``isinstance(radio, AudioCapable | UsbAudioCapable)`` and keeps
the ``"audio"`` UI capability advertised — without any registry,
plugin mechanism, or string discriminator.
"""

from __future__ import annotations

from icom_lan import UsbAudioCapable


class _StubUsbAudioRadio:
    """Structural stub that satisfies :class:`UsbAudioCapable`."""

    has_usb_audio: bool = True


class _StubNonUsbAudioRadio:
    """Structural stub that does NOT satisfy :class:`UsbAudioCapable`."""

    # Deliberately no has_usb_audio attribute.


def test_stub_with_attribute_satisfies_protocol() -> None:
    """A class attribute structurally satisfies the
    ``@property``-shaped Protocol member under
    :func:`runtime_checkable`."""
    stub = _StubUsbAudioRadio()
    assert isinstance(stub, UsbAudioCapable)
    assert stub.has_usb_audio is True


def test_stub_without_attribute_fails_protocol() -> None:
    """The sentinel attribute matters: backends without it are NOT
    detected as ``UsbAudioCapable`` (this is what keeps marker-style
    Protocols from matching every object — see the docstring)."""
    stub = _StubNonUsbAudioRadio()
    assert not isinstance(stub, UsbAudioCapable)


def test_yaesu_cat_radio_declares_usb_audio() -> None:
    """The shipping :class:`YaesuCatRadio` declares
    ``has_usb_audio = True``, so it satisfies :class:`UsbAudioCapable`
    structurally."""
    from icom_lan.backends.yaesu_cat.radio import YaesuCatRadio

    assert YaesuCatRadio.has_usb_audio is True
