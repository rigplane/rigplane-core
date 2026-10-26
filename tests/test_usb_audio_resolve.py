"""Tests for USB audio device resolution from serial port topology."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from rigplane.usb_audio_resolve import (
    AudioDeviceMapping,
    _extract_linux_tty_name,
    _is_usb_audio_codec,
    _normalize_alsa_device_name,
    _resolve_linux,
    _usb_device_node_from_realpath,
    resolve_audio_for_serial_port,
)


class TestIsUsbAudioCodec:
    def test_exact(self) -> None:
        assert _is_usb_audio_codec("USB Audio CODEC") is True

    def test_lowercase(self) -> None:
        assert _is_usb_audio_codec("usb audio codec") is True

    def test_mixed_case(self) -> None:
        assert _is_usb_audio_codec("USB audio CODEC") is True

    def test_no_match(self) -> None:
        assert _is_usb_audio_codec("Built-in Speaker") is False


# ---------------------------------------------------------------------------
# Integration tests — full resolution flow
# ---------------------------------------------------------------------------


class TestResolvePlatformDispatch:
    """Test that resolve_audio_for_serial_port dispatches correctly."""

    @patch("rigplane.usb_audio_resolve.platform")
    def test_unsupported_platform_returns_none(self, mock_platform: MagicMock) -> None:
        # macOS/Linux/Windows all have resolvers now; an unsupported platform
        # falls through to the name-based selection path.
        mock_platform.system.return_value = "FreeBSD"
        result = resolve_audio_for_serial_port("/dev/cuaU0")
        assert result is None


class TestAudioDeviceMapping:
    """Test the dataclass itself."""

    def test_creation(self) -> None:
        m = AudioDeviceMapping(
            rx_device_index=2,
            tx_device_index=1,
            serial_port="/dev/cu.usbserial-201410",
            location_prefix=0x2014,
        )
        assert m.rx_device_index == 2
        assert m.tx_device_index == 1
        assert m.serial_port == "/dev/cu.usbserial-201410"
        assert m.location_prefix == 0x2014

    def test_default_location_prefix(self) -> None:
        m = AudioDeviceMapping(
            rx_device_index=2,
            tx_device_index=1,
            serial_port="/dev/cu.usbserial-201410",
        )
        assert m.location_prefix is None

    def test_frozen(self) -> None:
        m = AudioDeviceMapping(
            rx_device_index=2,
            tx_device_index=1,
            serial_port="/dev/cu.usbserial-201410",
        )
        with pytest.raises(AttributeError):
            m.rx_device_index = 99  # type: ignore[misc]


class TestIsUsbAudioCodecXiegu:
    """MOR-219: recognise the C-Media / generic 'USB Audio Device' identity."""

    def test_usb_audio_device(self) -> None:
        assert _is_usb_audio_codec("USB Audio Device") is True

    def test_cmedia_vendor_dashed(self) -> None:
        assert _is_usb_audio_codec("C-Media USB Headphone Set") is True

    def test_cmedia_vendor_plain(self) -> None:
        assert _is_usb_audio_codec("CMedia Audio") is True

    def test_unrelated_still_false(self) -> None:
        assert _is_usb_audio_codec("Built-in Microphone") is False


class TestNameFallbackXiegu:
    """MOR-219: name-based fallback (Linux/Windows) prefers the C-Media codec
    over an unknown commodity device when topology is unavailable."""

    def test_cmedia_preferred_over_unknown_duplex(self) -> None:
        from rigplane.audio.usb_driver import (
            UsbAudioDevice,
            select_usb_audio_devices,
        )

        devices = [
            UsbAudioDevice(
                index=0,
                name="Some Cheap Headset",
                input_channels=1,
                output_channels=2,
            ),
            UsbAudioDevice(
                index=1,
                name="C-Media Electronics",
                input_channels=1,
                output_channels=2,
            ),
        ]
        rx, tx = select_usb_audio_devices(devices)
        assert rx.index == 1
        assert tx.index == 1


# ---------------------------------------------------------------------------
# MOR-228 — Linux sysfs topology resolution
# ---------------------------------------------------------------------------


def _mk_usb_device(
    root: Path,
    usb_dev_path: str,
    *,
    id_vendor: str | None = None,
    id_product: str | None = None,
    product: str | None = None,
) -> Path:
    """Create a fake USB *device* node directory under a fixture sysfs tree.

    ``usb_dev_path`` is relative to ``<root>/devices`` and ends in a USB
    device node name (e.g. ``usb1/1-1/1-1.1``). Writes the optional
    ``idVendor``/``idProduct``/``product`` attribute files. Returns the
    absolute path to the device node directory.
    """
    dev_dir = root / "devices" / usb_dev_path
    dev_dir.mkdir(parents=True, exist_ok=True)
    if id_vendor is not None:
        (dev_dir / "idVendor").write_text(id_vendor + "\n")
    if id_product is not None:
        (dev_dir / "idProduct").write_text(id_product + "\n")
    if product is not None:
        (dev_dir / "product").write_text(product + "\n")
    return dev_dir


def _link_tty(root: Path, tty_name: str, usb_dev_dir: Path) -> None:
    """Wire <root>/class/tty/<tty_name>/device → an interface under usb_dev_dir."""
    # The kernel's `device` symlink points at the *interface* node; create one.
    iface = usb_dev_dir / (os.path.basename(str(usb_dev_dir)) + ":1.0")
    iface.mkdir(parents=True, exist_ok=True)
    tty_class_dir = root / "class" / "tty" / tty_name
    tty_class_dir.mkdir(parents=True, exist_ok=True)
    (tty_class_dir / "device").symlink_to(iface, target_is_directory=True)


def _link_card(root: Path, card_n: int, usb_dev_dir: Path) -> None:
    """Wire <root>/class/sound/cardN/device → an interface under usb_dev_dir."""
    iface = usb_dev_dir / (os.path.basename(str(usb_dev_dir)) + ":1.0")
    iface.mkdir(parents=True, exist_ok=True)
    card_dir = root / "class" / "sound" / f"card{card_n}"
    card_dir.mkdir(parents=True, exist_ok=True)
    (card_dir / "device").symlink_to(iface, target_is_directory=True)


def _make_mock_sd_two_usb_named(name_a: str, name_b: str) -> MagicMock:
    """Two duplex USB-audio devices with distinct names, after a built-in.

    idx 0 -> Built-in Speaker (skipped)
    idx 1 -> name_a duplex (in/out)
    idx 2 -> name_b duplex (in/out)
    """
    devices: list[dict[str, Any]] = [
        {
            "name": "Built-in Speaker",
            "index": 0,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
        {
            "name": name_a,
            "index": 1,
            "max_input_channels": 1,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
        {
            "name": name_b,
            "index": 2,
            "max_input_channels": 1,
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        },
    ]
    sd = MagicMock()
    sd.query_devices.return_value = devices
    sd.default.device = [-1, -1]
    return sd


class TestExtractLinuxTtyName:
    """MOR-228: Linux tty names for both CDC-ACM and USB-serial nodes."""

    def test_ttyacm(self) -> None:
        assert _extract_linux_tty_name("/dev/ttyACM0") == "ttyACM0"

    def test_ttyusb(self) -> None:
        assert _extract_linux_tty_name("/dev/ttyUSB1") == "ttyUSB1"

    def test_ttyacm_high_index(self) -> None:
        assert _extract_linux_tty_name("/dev/ttyACM12") == "ttyACM12"

    def test_macos_path_is_none(self) -> None:
        assert _extract_linux_tty_name("/dev/cu.usbserial-201410") is None

    def test_unrelated_is_none(self) -> None:
        assert _extract_linux_tty_name("/dev/ttyS0") is None


class TestUsbDeviceNodeFromRealpath:
    """MOR-228: ascend an interface realpath to the USB device node."""

    def test_interface_to_device(self) -> None:
        real = "/sys/devices/pci0000:00/usb1/1-1/1-1.4/1-1.4:1.0"
        assert (
            _usb_device_node_from_realpath(real)
            == "/sys/devices/pci0000:00/usb1/1-1/1-1.4"
        )

    def test_root_port_device(self) -> None:
        real = "/sys/devices/pci0000:00/usb2/2-1/2-1:1.0"
        assert (
            _usb_device_node_from_realpath(real) == "/sys/devices/pci0000:00/usb2/2-1"
        )

    def test_no_usb_node(self) -> None:
        assert _usb_device_node_from_realpath("/sys/devices/platform/foo") is None


class TestResolveLinuxSysfs:
    """MOR-228: full Linux resolution against a fixture sysfs tree.

    Topology modelled:
      bus 1, hub 1-1: X6200 — CAT (ttyACM0) at 1-1.1, C-Media audio at 1-1.2
      bus 1, hub 1-2: FTDI radio — serial (ttyUSB0) at 1-2.1, audio at 1-2.2

    Each radio's CAT/serial port shares a deeper USB-path prefix with its OWN
    audio device (score 2: bus + hub) than with the other radio's audio
    (score 1: bus only), so each must resolve to its own card.
    """

    @staticmethod
    def _build_tree(root: Path) -> None:
        # X6200 CAT (CDC-ACM) and its C-Media audio share hub 1-1.
        x6200_cat = _mk_usb_device(
            root,
            "pci0000:00/usb1/1-1/1-1.1",
            id_vendor="1a86",
            id_product="55d4",
            product="USB Dual_Serial",
        )
        x6200_audio = _mk_usb_device(
            root,
            "pci0000:00/usb1/1-1/1-1.2",
            id_vendor="0d8c",
            id_product="0012",
            product="USB Audio Device",
        )
        # FTDI radio serial and its audio share a *different* hub 1-2.
        ftdi_serial = _mk_usb_device(
            root,
            "pci0000:00/usb1/1-2/1-2.1",
            id_vendor="0403",
            id_product="6001",
            product="USB Serial",
        )
        ftdi_audio = _mk_usb_device(
            root,
            "pci0000:00/usb1/1-2/1-2.2",
            id_vendor="08bb",
            id_product="2901",
            product="USB Audio CODEC",
        )
        _link_tty(root, "ttyACM0", x6200_cat)
        _link_tty(root, "ttyUSB0", ftdi_serial)
        _link_card(root, 0, x6200_audio)
        _link_card(root, 1, ftdi_audio)

    def test_x6200_resolves_to_cmedia(self, tmp_path: Path) -> None:
        self._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "USB Audio CODEC")
        result = _resolve_linux(
            "/dev/ttyACM0",
            sounddevice_module=sd,
            sysfs_root=str(tmp_path),
        )
        assert result is not None
        assert result.serial_port == "/dev/ttyACM0"
        # C-Media "USB Audio Device" duplex enumerates at sounddevice index 1.
        assert result.rx_device_index == 1
        assert result.tx_device_index == 1

    def test_ftdi_resolves_to_its_own_codec(self, tmp_path: Path) -> None:
        self._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "USB Audio CODEC")
        result = _resolve_linux(
            "/dev/ttyUSB0",
            sounddevice_module=sd,
            sysfs_root=str(tmp_path),
        )
        assert result is not None
        assert result.serial_port == "/dev/ttyUSB0"
        # "USB Audio CODEC" enumerates at sounddevice index 2.
        assert result.rx_device_index == 2
        assert result.tx_device_index == 2

    def test_each_port_resolves_to_its_own_device(self, tmp_path: Path) -> None:
        self._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "USB Audio CODEC")
        x6200 = _resolve_linux(
            "/dev/ttyACM0", sounddevice_module=sd, sysfs_root=str(tmp_path)
        )
        ftdi = _resolve_linux(
            "/dev/ttyUSB0", sounddevice_module=sd, sysfs_root=str(tmp_path)
        )
        assert x6200 is not None and ftdi is not None
        assert x6200.rx_device_index != ftdi.rx_device_index
        assert x6200.tx_device_index != ftdi.tx_device_index

    def test_unknown_tty_returns_none(self, tmp_path: Path) -> None:
        self._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "USB Audio CODEC")
        result = _resolve_linux(
            "/dev/ttyACM9",  # not wired into the fixture tree
            sounddevice_module=sd,
            sysfs_root=str(tmp_path),
        )
        assert result is None

    def test_non_linux_serial_path_returns_none(self, tmp_path: Path) -> None:
        self._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "USB Audio CODEC")
        result = _resolve_linux(
            "/dev/cu.usbserial-201410",  # macOS shape, not a Linux tty
            sounddevice_module=sd,
            sysfs_root=str(tmp_path),
        )
        assert result is None

    def test_no_usb_audio_cards_returns_none(self, tmp_path: Path) -> None:
        # Wire only the serial port; no ALSA cards under /class/sound.
        x6200_cat = _mk_usb_device(
            tmp_path,
            "pci0000:00/usb1/1-1/1-1.1",
            id_vendor="1a86",
            id_product="55d4",
            product="USB Dual_Serial",
        )
        _link_tty(tmp_path, "ttyACM0", x6200_cat)
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "USB Audio CODEC")
        result = _resolve_linux(
            "/dev/ttyACM0", sounddevice_module=sd, sysfs_root=str(tmp_path)
        )
        assert result is None


class TestNormalizeAlsaDeviceName:
    """MOR-549: strip PortAudio's ALSA decoration down to the card name."""

    def test_full_alsa_form(self) -> None:
        assert (
            _normalize_alsa_device_name("USB Audio CODEC: Audio (hw:2,0)")
            == "USB Audio CODEC"
        )

    def test_alsa_form_with_pcm_descriptor(self) -> None:
        assert (
            _normalize_alsa_device_name("USB Audio Device: USB Audio (hw:1,0)")
            == "USB Audio Device"
        )

    def test_plughw_suffix(self) -> None:
        assert (
            _normalize_alsa_device_name("USB Audio CODEC: Audio (plughw:2,0)")
            == "USB Audio CODEC"
        )

    def test_suffix_without_descriptor(self) -> None:
        assert _normalize_alsa_device_name("USB Audio CODEC (hw:2,0)") == (
            "USB Audio CODEC"
        )

    def test_plain_name_unchanged(self) -> None:
        assert _normalize_alsa_device_name("USB Audio CODEC") == "USB Audio CODEC"

    def test_macos_name_unchanged(self) -> None:
        assert _normalize_alsa_device_name("USB Audio Device") == "USB Audio Device"

    def test_colon_without_hw_suffix_unchanged(self) -> None:
        # Conservative: only strip the ": <descriptor>" tail when the name
        # carries an ALSA (hw:X,Y) suffix — never over-strip legitimate names.
        assert (
            _normalize_alsa_device_name("Radio: Special Edition")
            == "Radio: Special Edition"
        )


class TestResolveLinuxAlsaDeviceNames:
    """MOR-549: real ALSA enumerations decorate names as
    ``"<card>: <pcm> (hw:X,Y)"`` while sysfs ``product`` is the bare card name
    (``"USB Audio CODEC"``). Topology pairing must match despite the suffix.
    """

    def test_ftdi_resolves_with_realistic_alsa_names(self, tmp_path: Path) -> None:
        TestResolveLinuxSysfs._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named(
            "USB Audio Device: USB Audio (hw:1,0)",
            "USB Audio CODEC: Audio (hw:2,0)",
        )
        result = _resolve_linux(
            "/dev/ttyUSB0", sounddevice_module=sd, sysfs_root=str(tmp_path)
        )
        assert result is not None
        # sysfs product "USB Audio CODEC" must match the decorated ALSA name
        # at sounddevice index 2.
        assert result.rx_device_index == 2
        assert result.tx_device_index == 2

    def test_x6200_resolves_with_realistic_alsa_names(self, tmp_path: Path) -> None:
        TestResolveLinuxSysfs._build_tree(tmp_path)
        sd = _make_mock_sd_two_usb_named(
            "USB Audio Device: USB Audio (hw:1,0)",
            "USB Audio CODEC: Audio (hw:2,0)",
        )
        result = _resolve_linux(
            "/dev/ttyACM0", sounddevice_module=sd, sysfs_root=str(tmp_path)
        )
        assert result is not None
        assert result.rx_device_index == 1
        assert result.tx_device_index == 1


class TestResolveLinuxCompositeIdentity:
    """MOR-228: when two cards tie on USB-path prefix, the idVendor/idProduct
    matching the radio's OWN USB device breaks the tie (composite radio whose
    audio + CAT live on one physical device behind a shared hub)."""

    def test_vid_pid_tiebreak(self, tmp_path: Path) -> None:
        # All three sit on sibling ports of the SAME hub (1-1.x), so both audio
        # cards tie on USB-path prefix score against the serial port (common
        # ["1","1"] = 2). Only the idVendor:idProduct tie-break (+1) then
        # decides — the audio card sharing the serial port's VID:PID wins.
        # Distinct product names map each card unambiguously to its sounddevice
        # entry, so the asserted index proves *which* card was selected.
        cat = _mk_usb_device(
            tmp_path,
            "pci0000:00/usb1/1-1.1",
            id_vendor="0d8c",
            id_product="0012",
            product="USB Dual_Serial",
        )
        # NON-matching audio on the LOWER sibling port (1-1.2). Cards are
        # enumerated in ascending sysfs-path order, so this one is seen first
        # and would win the bare prefix tie by first-wins. Different VID:PID.
        # Its name must still be a recognised USB-audio token to be clustered.
        other_audio = _mk_usb_device(
            tmp_path,
            "pci0000:00/usb1/1-1.2",
            id_vendor="08bb",
            id_product="2901",
            product="C-Media USB Audio",
        )
        # OWN audio (the X6200 codec) on the higher port (1-1.3), sharing the
        # serial port's VID:PID. Ties on prefix; only the VID:PID +1 wins it.
        own_audio = _mk_usb_device(
            tmp_path,
            "pci0000:00/usb1/1-1.3",
            id_vendor="0d8c",
            id_product="0012",
            product="USB Audio Device",
        )
        _link_tty(tmp_path, "ttyACM0", cat)
        _link_card(tmp_path, 0, other_audio)
        _link_card(tmp_path, 1, own_audio)
        # idx 1 -> "USB Audio Device" (own); idx 2 -> "C-Media USB Audio" (other).
        sd = _make_mock_sd_two_usb_named("USB Audio Device", "C-Media USB Audio")
        result = _resolve_linux(
            "/dev/ttyACM0", sounddevice_module=sd, sysfs_root=str(tmp_path)
        )
        assert result is not None
        # The VID:PID tie-break selects own_audio → idx 1. Without it, the
        # first-enumerated "C-Media USB Audio" (idx 2) would win the prefix tie.
        assert result.rx_device_index == 1
        assert result.tx_device_index == 1


class TestResolveLinuxDispatch:
    """MOR-228: resolve_audio_for_serial_port dispatches to _resolve_linux."""

    @patch("rigplane.usb_audio_resolve.platform")
    @patch("rigplane.usb_audio_resolve._resolve_linux")
    def test_linux_delegates(
        self, mock_resolve: MagicMock, mock_platform: MagicMock
    ) -> None:
        mock_platform.system.return_value = "Linux"
        mock_resolve.return_value = AudioDeviceMapping(
            rx_device_index=1,
            tx_device_index=1,
            serial_port="/dev/ttyACM0",
            location_prefix=0x0101,
        )
        result = resolve_audio_for_serial_port("/dev/ttyACM0")
        assert result is not None
        assert result.rx_device_index == 1
        mock_resolve.assert_called_once()
