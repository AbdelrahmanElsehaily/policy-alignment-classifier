"""Use case: label one event with labelling functions and a jury of model families (guideline 5).

Following PoLL (Replacing Judges with Juries, arXiv 2404.18796), several smaller judges from
different model families vote, rather than one large judge deciding alone: their errors are
less correlated, and their disagreement is a signal of a hard case. Following DynaGuard
(arXiv 2509.02563), the policy is decomposed: each juror labels the event against each rule
that can judge its kind, not the policy as a whole. The labelling functions decide the
mechanical rules before any juror is asked.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pac.guardrail.application.ports import BackendError, LLMBackend, LLMRequest
from pac.guardrail.domain.events import ClassificationRequest, EventKind
from pac.synthesis.application.prompts import JURY_SYSTEM, jury_schema, jury_user, parse_jury
from pac.synthesis.domain.jury import Ballot, Consensus, aggregate
from pac.synthesis.domain.labelling import labelling_functions
from pac.synthesis.domain.specs import rules_for

logger = logging.getLogger(__name__)

REPAIR = (
    "Your previous answer was not valid. Answer again with only the JSON object, with an entry "
    "for every listed rule."
)


@dataclass(frozen=True)
class Juror:
    """One member of the jury: a model behind the LLM port, and the family it belongs to."""

    name: str
    family: str
    backend: LLMBackend


class JuryLabeller:
    """Labels events: labelling functions first, then each juror per rule, then a majority."""

    def __init__(
        self,
        jurors: Sequence[Juror],
        policy_text: str,
        applies_to: Mapping[str, Sequence[EventKind]],
        *,
        max_tokens: int = 1200,
        use_lfs: bool = True,
        temperature: float | None = 0.0,
    ) -> None:
        if len({j.family for j in jurors}) < len(jurors):
            raise ValueError("every juror must come from a different model family")
        self._jurors = list(jurors)
        self._system = JURY_SYSTEM.substitute(policy=policy_text.strip())
        self._applies_to = applies_to
        self._max_tokens = max_tokens
        self._use_lfs = use_lfs
        self._temperature = temperature

    @property
    def jurors(self) -> list[Juror]:
        return list(self._jurors)

    @property
    def applies_to(self) -> Mapping[str, Sequence[EventKind]]:
        return self._applies_to

    async def label(
        self, request: ClassificationRequest, *, with_history: bool = False
    ) -> Consensus:
        rules = rules_for(request.event.kind, self._applies_to)
        lfs = labelling_functions(request, use_history=with_history) if self._use_lfs else {}
        ballots = await asyncio.gather(
            *(self._ask(j, request, rules, with_history=with_history) for j in self._jurors)
        )
        return aggregate(ballots, rules, lfs)

    async def ballots(
        self, request: ClassificationRequest, *, with_history: bool = False
    ) -> list[Ballot]:
        rules = rules_for(request.event.kind, self._applies_to)
        return list(
            await asyncio.gather(
                *(self._ask(j, request, rules, with_history=with_history) for j in self._jurors)
            )
        )

    async def _ask(
        self,
        juror: Juror,
        request: ClassificationRequest,
        rules: Sequence[str],
        *,
        with_history: bool,
    ) -> Ballot:
        user = jury_user(request, rules, with_history=with_history)
        problem = "no attempt"
        for attempt in range(2):
            llm_request = LLMRequest(
                system=self._system,
                user=user if attempt == 0 else f"{user}\n\n{REPAIR}",
                json_schema=jury_schema(rules),
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )
            try:
                completion = await juror.backend.complete(llm_request)
            except BackendError as exc:
                problem = f"backend: {exc}"[:300]
                logger.warning("juror failed", extra={"juror": juror.name, "reason": problem})
                continue
            try:
                votes = parse_jury(completion.text, rules)
            except ValueError as exc:
                problem = f"unparseable: {exc}"[:300]
                continue
            return Ballot(juror=juror.name, family=juror.family, votes=votes)
        return Ballot(juror=juror.name, family=juror.family, error=problem)
