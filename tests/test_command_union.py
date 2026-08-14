"""Public ``Command`` union coverage."""

from typing import get_args

from rigplane.runtime._poller_types import (
    Command,
    SetFilterShape,
    SetTunerStatus,
    Speak,
)


def test_command_union_includes_filter_shape_tuner_status_and_speech() -> None:
    """The public queue annotation accepts every supported command dataclass."""
    command_types = set(get_args(Command))

    assert {SetFilterShape, SetTunerStatus, Speak} <= command_types
