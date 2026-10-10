"""What the evaluation application needs from the outside world: somewhere to keep runs."""

from __future__ import annotations

from typing import Protocol

from pac.evaluation.domain.run import ScoredRun


class RunNotFound(LookupError):
    """No run with that id (or the id is not a valid run id)."""


class RunStore(Protocol):
    def save(self, run: ScoredRun, report: str) -> None:
        """Persist a scored run and its human-readable report, keyed by its run id."""
        ...

    def load(self, run_id: str) -> ScoredRun: ...

    def list_ids(self) -> list[str]:
        """Run ids, newest first."""
        ...
