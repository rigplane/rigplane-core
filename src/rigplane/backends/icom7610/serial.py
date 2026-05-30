"""Serial adaptation layer for the IC-7610 backend."""

from __future__ import annotations

import logging

from ...exceptions import CommandError
from .._icom_serial_base import _IcomSerialRadioBase, _SERIAL_SCOPE_MIN_BAUD

logger = logging.getLogger(__name__)

__all__ = ["Icom7610SerialRadio"]


class Icom7610SerialRadio(_IcomSerialRadioBase):
    """IC-7610 backend wired to shared core over serial CI-V session driver.

    The audio TX (Opus + PCM) path and audio-driver teardown live in
    ``_IcomSerialRadioBase`` (lifted there in MOR-242); this subclass only
    carries the IC-7610-specific RX-PCM delegation and the scope baud
    guardrail that raises ``CommandError``.
    """

    _DEFAULT_MODEL = ""

    # ------------------------------------------------------------------
    # IC-7610 specific: stop_audio_rx_pcm delegates to stop_audio_rx_opus
    # ------------------------------------------------------------------

    async def stop_audio_rx_pcm(self) -> None:
        self._pcm_rx_user_callback = None
        await self.stop_audio_rx_opus()

    # ------------------------------------------------------------------
    # IC-7610 specific: scope guardrail uses CommandError
    # ------------------------------------------------------------------

    def _ensure_scope_baud_guardrail(self) -> None:
        if self._serial_baudrate >= _SERIAL_SCOPE_MIN_BAUD:
            return

        msg = (
            "Scope over serial requires baudrate >= "
            f"{_SERIAL_SCOPE_MIN_BAUD} for stable command path; got baudrate="
            f"{self._serial_baudrate}. Set allow_low_baud_scope=True to override."
        )
        if not self._allow_low_baud_scope:
            raise CommandError(msg)

        if not self._low_baud_scope_warned:
            logger.warning("%s Running with override may increase timeout risk.", msg)
            self._low_baud_scope_warned = True
