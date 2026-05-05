"""Concrete DSP nodes for audio processing pipelines."""

from __future__ import annotations

from icom_lan.dsp.nodes.base import GainNode, PassthroughNode
from icom_lan.dsp.nodes.nr_scipy import NRScipyNode

__all__ = ["GainNode", "NRScipyNode", "PassthroughNode"]
