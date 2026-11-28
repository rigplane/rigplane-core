"""Direct-read outcome notes for the startup gate (MOR-4481).

Some backends read startup-relevant paths outside the scheduler's
request machinery — the Yaesu CAT and rigctld-client PTT/split lanes.
When such a direct read fails terminally (refused, malformed, unanswered)
the backend files a :class:`StartupReadNote` through
:func:`record_direct_read_outcome`. That function reuses the scheduler's
existing refusal/timeout records — it builds a
:class:`DeclaredCommandDefect` carrying the real wire command and routes it
through
:meth:`AcquisitionScheduler.record_declared_read_failure`, whose own
critical/optional split decides between a startup defect and a
refusal/timeout mark. No counter, tally, or second failure mechanism is
added here.

Timeouts are the one exception: a demoted lane's unanswered read keeps its
MOR-4456 containment (the read stays outstanding until the gate's
non-critical deadline, with no scheduler mark), so a timeout outcome only
stashes its wire evidence for the gate warning and records nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .acquisition_scheduler import (
    AcquisitionScheduler,
    DeclaredCommandDefect,
    DeclaredReadFailure,
)
from .state_pipeline_contracts import FieldPath

__all__ = [
    "StartupReadNote",
    "StartupReadOutcome",
    "record_direct_read_outcome",
]

#: How a direct lane read failed. Mirrors
#: :data:`AcquisitionScheduler`'s ``DeclaredReadFailure`` one to one.
StartupReadOutcome = Literal["refused", "malformed", "timeout"]

_OUTCOME_REASONS: dict[StartupReadOutcome, DeclaredReadFailure] = {
    "refused": "refused",
    "malformed": "malformed",
    "timeout": "timeout",
}


@dataclass(frozen=True, slots=True)
class StartupReadNote:
    """A direct lane read's terminal outcome, filed for the startup gate.

    ``command`` is the wire form the backend sent — ``"TX;"``, ``"ST;"``,
    ``"t"`` — verbatim from the transport error or the read itself, never
    re-guessed by the gate. ``frame`` carries the refused/malformed reply
    when the backend kept it.
    """

    label: str
    path: FieldPath
    command: str
    outcome: StartupReadOutcome
    frame: str = ""
    detail: str = ""


def record_direct_read_outcome(
    radio: object, note: StartupReadNote, *, now: float
) -> None:
    """File a direct read's terminal outcome for the startup gate.

    The note is stashed per path on the radio (``_startup_read_notes``) so
    the gate warning can name the real wire command even when the
    scheduler record keeps none. Refused and malformed outcomes are
    additionally recorded on the radio's acquisition scheduler through the
    existing declared-failure routing; timeout outcomes record nothing and
    keep their lane containment.
    """

    try:
        stash = getattr(radio, "_startup_read_notes", None)
        if not isinstance(stash, dict):
            stash = {}
            setattr(radio, "_startup_read_notes", stash)
        stash[note.path] = note
    except AttributeError:
        pass
    scheduler = getattr(radio, "_acquisition_scheduler", None)
    if not isinstance(scheduler, AcquisitionScheduler):
        return
    if note.outcome == "timeout":
        return
    defect = DeclaredCommandDefect(
        label=note.label,
        paths=(note.path,),
        command=note.command,
        frame=note.frame,
        detail=note.detail or f"direct read {note.outcome}: {note.path}",
    )
    scheduler.record_declared_read_failure(
        defect, reason=_OUTCOME_REASONS[note.outcome], now=now
    )
