"""Use case: grow seed events at each rule's boundary, dedupe them, and review them (guideline 1).

Each generator (one per model family) is shown the policy, the rule's boundary spec and a world
slice, and writes a handful of events near the rule's line, with the label it intends and the
policy text it rests on. Seeds are then deduplicated (trigram Jaccard), checked mechanically
(``check_seed``: cited rules must exist, a violation must be in a kind the rule judges, no
eval-world ids), and reviewed by a model of a third family for labels that rest on a rule the
policy doesn't contain. Rejected seeds are kept in the output with the reason.

The generator's intended label is never used as a label: kept seeds are labelled by the jury
like every other event.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pac.guardrail.application.ports import BackendError, LLMBackend, LLMRequest
from pac.guardrail.domain.decision import VERDICTS
from pac.guardrail.domain.events import EventKind
from pac.synthesis.application.prompts import (
    REVIEW_SYSTEM,
    generator_system,
    parse_event,
    parse_object,
    parse_review,
    seed_user,
    session_for,
    tagged_json,
)
from pac.synthesis.domain.dedup import NearDuplicateIndex
from pac.synthesis.domain.specs import BoundarySpec, Seed, check_seed
from pac.synthesis.domain.world import WorldSlice

logger = logging.getLogger(__name__)

OBJECT_SCHEMA = {"type": "object"}
REPAIR = "Your previous answer was not valid JSON. Answer again with only the JSON object."


@dataclass(frozen=True)
class Generator:
    name: str
    family: str
    backend: LLMBackend


def spec_text(spec: BoundarySpec) -> str:
    def block(title: str, items: Sequence[str]) -> str:
        return f"{title}:\n" + "\n".join(f"- {i}" for i in items) if items else ""

    return "\n".join(
        b
        for b in (
            f"{spec.rule_id} ({spec.title}) judges: {', '.join(spec.applies_to)}",
            block("Allowed", spec.allowed),
            block("Restricted", spec.restricted),
            block("Facts that decide it", spec.deciding_facts),
            block("Benign look-alikes", spec.benign_lookalikes),
            block("Where the policy is silent", spec.silent_when),
        )
        if b
    )


async def grow(
    spec: BoundarySpec,
    generator: Generator,
    world: WorldSlice,
    policy_text: str,
    *,
    n: int,
    split: str,
) -> list[Seed]:
    """One generator's seeds for one rule, in one world slice. Unparseable seeds are dropped."""
    request = LLMRequest(
        system=generator_system(policy_text, world),
        user=seed_user(spec.rule_id, spec_text(spec), n),
        json_schema=OBJECT_SCHEMA,
        max_tokens=3000,
        temperature=0.9,
    )
    raw: dict[str, Any] | None = None
    problem = ""
    for attempt in range(2):
        try:
            completion = await generator.backend.complete(
                request
                if attempt == 0
                else request.model_copy(update={"user": f"{request.user}\n\n{REPAIR}"})
            )
            raw = parse_object(completion.text)
            break
        except (BackendError, ValueError) as exc:
            problem = str(exc)[:200]
    if raw is None:
        logger.warning("seed generation failed", extra={"rule": spec.rule_id, "reason": problem})
        return []
    seeds = []
    for i, item in enumerate(raw.get("seeds") or []):
        if not isinstance(item, dict):
            continue
        intended = str(item.get("intended_verdict", "")).strip()
        try:
            event = parse_event(item.get("event"))
            session = session_for(world, item.get("verified_as"))
            history = tuple(parse_event(h) for h in (item.get("history") or [])[:3])
        except ValueError as exc:
            logger.info("seed dropped", extra={"rule": spec.rule_id, "reason": str(exc)[:120]})
            continue
        if intended not in VERDICTS:
            continue
        seeds.append(
            Seed(
                id=f"seed-{split}-{spec.rule_id.lower()}-{generator.family}-{i:02d}",
                rule_id=spec.rule_id,
                intended=intended,
                event=event,
                verified_as=session.verified_customer_id,
                email_on_file=session.email_on_file,
                history=history,
                rationale=str(item.get("rationale") or "")[:600],
                cites=tuple(str(c) for c in item.get("cites") or ())[:6],
                generator=generator.name,
                split=split,
            )
        )
    return seeds


def dedupe(seeds: Sequence[Seed]) -> list[Seed]:
    """Mark near-duplicates (of an earlier seed in the same split) as ``duplicate``."""
    indexes: dict[str | None, NearDuplicateIndex] = {}
    out = []
    for seed in seeds:
        index = indexes.setdefault(seed.split, NearDuplicateIndex())
        if index.near_duplicate(seed.event) is not None:
            out.append(seed.model_copy(update={"status": "duplicate", "review": "near-duplicate"}))
            continue
        index.add(seed.event, seed.id)
        out.append(seed)
    return out


async def review(
    seeds: Sequence[Seed],
    reviewer: LLMBackend,
    policy_text: str,
    applies_to: Mapping[str, Sequence[EventKind]],
    *,
    concurrency: int = 4,
) -> list[Seed]:
    """Reject seeds that fail the mechanical check, or whose label rests on an invented rule."""
    system = REVIEW_SYSTEM.substitute(policy=policy_text.strip())
    semaphore = asyncio.Semaphore(concurrency)

    async def one(seed: Seed) -> Seed:
        if seed.status != "kept":
            return seed
        problem = check_seed(seed, applies_to)
        if problem is not None:
            return seed.model_copy(update={"status": "rejected", "review": problem})
        payload = {
            "rule": seed.rule_id,
            "session": {
                "verified": seed.verified_as is not None,
                "verified_customer_id": seed.verified_as,
                "email_on_file": seed.email_on_file,
            },
            "history": [e.model_dump(exclude_none=True) for e in seed.history],
            "event": seed.event.model_dump(exclude_none=True),
            "intended_verdict": seed.intended,
            "rationale": seed.rationale,
        }
        request = LLMRequest(
            system=system,
            user=f"<example>\n{tagged_json(payload)}\n</example>",
            json_schema=OBJECT_SCHEMA,
            max_tokens=400,
            temperature=0.0,
        )
        async with semaphore:
            try:
                keep, invented, reason = parse_review((await reviewer.complete(request)).text)
            except (BackendError, ValueError) as exc:
                return seed.model_copy(update={"review": f"review failed, kept: {exc}"[:200]})
        if keep:
            return seed.model_copy(update={"review": f"kept: {reason}"})
        detail = f"invented rule: {invented}; {reason}" if invented else reason
        return seed.model_copy(update={"status": "rejected", "review": detail[:300]})

    return list(await asyncio.gather(*(one(s) for s in seeds)))
