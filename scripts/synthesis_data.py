"""The data phases of scripts/synthesize.py: seeds, conversations, singles, labels, assembly.

Each phase reads the previous phase's files under data/synthetic/ and writes its own, so a phase
can be rerun alone (model answers come from the response cache).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import synthesis_runtime as rt

from pac.guardrail.domain.decision import Verdict
from pac.guardrail.domain.events import ClassificationRequest, Session, TraceEvent
from pac.guardrail.domain.value_object import ValueObject
from pac.settings import REPO_ROOT
from pac.simulation.application.agent import SimulatedAgent
from pac.simulation.application.customer import CustomerBrief, SimulatedCustomer
from pac.simulation.application.recording import record_conversation
from pac.simulation.domain.back_office import BackOffice
from pac.synthesis.application.labeller import JuryLabeller
from pac.synthesis.application.prompts import generator_system, parse_generated, targeted_user
from pac.synthesis.application.seeding import Generator, dedupe, grow, review, spec_text
from pac.synthesis.domain.faults import ToolFault, applies, faulted_response, is_fault
from pac.synthesis.domain.jury import Ballot, Consensus, aggregate
from pac.synthesis.domain.labelling import labelling_functions
from pac.synthesis.domain.language import event_language
from pac.synthesis.domain.pairs import Contrast, contrasts
from pac.synthesis.domain.records import (
    AttackStyle,
    Cell,
    Source,
    SyntheticEvent,
    SynthSplit,
    ToolOutcome,
    position_of,
)
from pac.synthesis.domain.scenarios import Brief, agent_instructions, plan_briefs
from pac.synthesis.domain.specs import BOUNDARY_SPECS, SPECS, Seed, rules_for
from pac.synthesis.domain.world import WorldSlice, build_slice

logger = logging.getLogger("pac.scripts.synthesize")

SPLITS: tuple[SynthSplit, ...] = ("train", "dev", "test")
RECORDINGS = rt.OUT / "recordings"
SEEDS = rt.OUT / "boundary_specs.json"
SINGLES = rt.OUT / "singles.jsonl"
LABELS = rt.OUT / "labels"


# =============================================================================================
# Guideline 1: boundary specs and seeds
# =============================================================================================

SEED_PLAN: dict[str, tuple[tuple[str, int], ...]] = {
    # split -> (generator model, seeds per rule) ... every rule gets seeds from two families in
    # train; dev and test alternate the family per rule.
    "train": ((rt.MINIMAX, 4), (rt.CLAUDE, 4)),
    "dev": ((rt.MINIMAX, 3), (rt.CLAUDE, 3)),
    "test": ((rt.MINIMAX, 3), (rt.CLAUDE, 3)),
}


async def seeds(runtime: rt.Runtime) -> list[Seed]:
    policy = runtime.policy.text
    jobs = []
    for split, plan in SEED_PLAN.items():
        world = build_slice(split)  # type: ignore[arg-type]
        for r, spec in enumerate(BOUNDARY_SPECS):
            chosen = plan if split == "train" else (plan[r % 2],)
            for model, n in chosen:
                generator = Generator(model, rt.FAMILY[model], runtime.cached(model, "seeds"))
                jobs.append(grow(spec, generator, world, policy, n=n, split=split))
    grown = [s for batch in await asyncio.gather(*jobs) for s in batch]
    grown = dedupe(grown)
    reviewed = await review(grown, runtime.cached(rt.GPT, "seeds"), policy, runtime.applies_to)
    payload = {
        "specs": [s.model_dump(mode="json") for s in BOUNDARY_SPECS],
        "spec_problems": [],
        "seeds": [s.model_dump(mode="json") for s in reviewed],
        "summary": {
            "grown": len(reviewed),
            "by_status": dict(Counter(s.status for s in reviewed)),
            "by_generator": dict(Counter(f"{s.generator}:{s.status}" for s in reviewed)),
            "rejections": [
                {"id": s.id, "rule": s.rule_id, "why": s.review}
                for s in reviewed
                if s.status == "rejected"
            ],
        },
    }
    rt.write_json(SEEDS, payload)
    return reviewed


def load_seeds() -> list[Seed]:
    data = json.loads(SEEDS.read_text(encoding="utf-8"))
    return [Seed.model_validate(s) for s in data["seeds"]]


# =============================================================================================
# Guideline 2: whole conversations in a separate world
# =============================================================================================


class FaultyOffice(BackOffice):
    """A world slice's back office, with one planned tool fault."""

    def __init__(self, world: WorldSlice, fault: ToolFault | None) -> None:
        super().__init__(
            customers=[
                {
                    "customer_id": c.customer_id,
                    "name": c.name,
                    "email": c.email,
                    "phone": c.phone,
                    "addresses": [c.address],
                    "payment_method": c.payment_method,
                }
                for c in world.customers
            ],
            orders=[o.record() for o in world.orders],
            articles=[{"title": a.title, "body": a.body} for a in world.articles],
            flaky_gateway=world.flaky_orders,
        )
        self._fault = fault
        self._counts: Counter[str] = Counter()

    def execute(self, call: TraceEvent) -> TraceEvent:
        response = super().execute(call)
        name = call.tool_name or ""
        self._counts[name] += 1
        if self._fault is not None and applies(self._fault, name, self._counts[name]):
            return faulted_response(response, self._fault)
        return response


BRIEF_PLAN: dict[str, dict[str, Any]] = {
    # Train and dev: MiniMax-M3 and DeepSeek V4 Flash as Lark. Test: only Mistral Small, held
    # out. (Llama 4 Scout was tried first and broke the action protocol in 16 of 18 chats.)
    "train": {
        "count": 26,
        "agents": (rt.MINIMAX, rt.DEEPSEEK),
        "offset": 0,
        "stride": 1,
        "shift": 0,
    },
    "dev": {"count": 10, "agents": (rt.MINIMAX, rt.DEEPSEEK), "offset": 1, "stride": 3, "shift": 2},
    "test": {"count": 12, "agents": (rt.MISTRAL,), "offset": 2, "stride": 7, "shift": 3},
}
CUSTOMERS = (rt.CLAUDE, rt.QWEN)
FLAWS = (
    "none",
    "retry_after_timeout",
    "none",
    "discloses_before_verification",
    "over_promises",
    "none",
    "follows_tool_text",
    "leaks_internal",
)


def briefs() -> list[Brief]:
    out: list[Brief] = []
    for split, plan in BRIEF_PLAN.items():
        out += plan_briefs(
            build_slice(split),  # type: ignore[arg-type]
            count=plan["count"],
            agents=plan["agents"],
            customers=CUSTOMERS,
            flaws=FLAWS,  # type: ignore[arg-type]
            offset=plan["offset"],
            stride=plan["stride"],
            shift=plan["shift"],
        )
    return out + pressure_briefs(out)


def pressure_briefs(plain: list[Brief]) -> list[Brief]:
    """Every brief whose agent has no flaw, again with the tool-encouraging line.

    The eval's agents-pressure split showed one line of tool guidance moving an agent to
    rule-breaking tool calls, the event kind our synthetic traffic had least of. Copies, not
    changes: the plain briefs and their recordings stay as they are.
    """
    return [
        b.model_copy(update={"id": f"{b.id}-tools", "agent_flaw": "tool_encouraging"})
        for b in plain
        if b.agent_flaw == "none"
    ]


def recording_path(brief: Brief) -> Path:
    safe = brief.agent_model.replace("/", "_")
    return RECORDINGS / brief.split / safe / f"{brief.id}.json"


async def record_one(runtime: rt.Runtime, brief: Brief, base_prompt: str) -> Path | None:
    path = recording_path(brief)
    if path.exists():
        return path  # recordings are evidence: never overwritten
    world = build_slice(brief.split)
    office = FaultyOffice(world, brief.fault)
    agent = SimulatedAgent(
        runtime.cached(brief.agent_model, "simulation"),
        agent_instructions(base_prompt, brief.agent_flaw),
        max_tokens=1024,
    )
    customer = SimulatedCustomer(runtime.cached(brief.customer_model, "simulation"))
    emails = {c.customer_id: c.email for c in world.customers}
    simulated = CustomerBrief(
        id=brief.id,
        brief=brief.text,
        verify_as=brief.verify_as,
        verified_from_start=brief.verified_from_start,
        language=brief.language,
    )
    try:
        recording = await record_conversation(simulated, customer, agent, office, emails)
    except Exception as exc:  # a failed conversation is reported, not fatal to the phase
        logger.warning("recording failed", extra={"brief": brief.id, "reason": str(exc)[:200]})
        return None
    payload = {
        "brief": brief.model_dump(mode="json"),
        "agent_model": brief.agent_model,
        "customer_model": brief.customer_model,
        "stopped": recording.stopped,
        "events": [
            {
                "session": session.model_dump(mode="json"),
                "event": event.model_dump(mode="json", exclude_none=True),
            }
            for session, event in recording.events
        ],
    }
    rt.write_json(path, payload)
    return path


async def record(
    runtime: rt.Runtime, only: Sequence[str] = (), parallel: int = 6, agent: str = ""
) -> list[Path]:
    base = (REPO_ROOT / "prompts" / "lark_agent.md").read_text(encoding="utf-8")
    plan = [
        b
        for b in briefs()
        if (not only or b.id in only or b.split in only) and agent in b.agent_model
    ]
    rt.write_json(rt.OUT / "briefs.json", [b.model_dump(mode="json") for b in briefs()])
    semaphore = asyncio.Semaphore(parallel)

    async def one(brief: Brief) -> Path | None:
        async with semaphore:
            path = await record_one(runtime, brief, base)
            logger.info("recorded", extra={"brief": brief.id, "ok": path is not None})
            return path

    return [p for p in await asyncio.gather(*(one(b) for b in plan)) if p is not None]


def load_recordings() -> list[dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(RECORDINGS.glob("*/*/*.json"))
    ]


# =============================================================================================
# Guideline 4 (pairs, hard negatives) and guideline 3 (targeted fill): single events
# =============================================================================================


class Single(ValueObject):
    """A single event to label, with where it came from."""

    id: str
    split: SynthSplit
    source: Source
    template: str
    request: ClassificationRequest
    rule: str
    expected: Verdict | None = None
    expected_with_history: Verdict | None = None
    family: str = ""
    member: str = ""
    note: str = ""


def singles_from_contrasts(split: SynthSplit) -> list[Single]:
    out = []
    for i, c in enumerate(contrasts(build_slice(split))):
        out.append(_single_from_contrast(split, i, c))
    return out


def _single_from_contrast(split: SynthSplit, i: int, c: Contrast) -> Single:
    return Single(
        id=f"syn-{split}-{'pair' if c.kind == 'pair' else 'hn'}-{i:03d}",
        split=split,
        source="pair" if c.kind == "pair" else "hard_negative",
        template=c.template,
        request=c.request,
        rule=c.rule,
        expected=c.expected,
        expected_with_history=c.expected_with_history,
        family=c.family,
        member=c.member,
        note=c.note,
    )


def singles_from_seeds(all_seeds: Sequence[Seed]) -> list[Single]:
    out = []
    for seed in all_seeds:
        if seed.status != "kept" or seed.split not in SPLITS:
            continue
        world = build_slice(seed.split)
        customer = next((c for c in world.customers if c.customer_id == seed.verified_as), None)
        session = (
            Session(
                verified=True,
                verified_customer_id=customer.customer_id,
                email_on_file=customer.email,
            )
            if customer
            else Session()
        )
        out.append(
            Single(
                id=seed.id,
                split=seed.split,
                source="targeted",
                template=f"seed-{seed.split}-{seed.rule_id.lower()}-{seed.generator.split('/')[-1]}",
                request=ClassificationRequest(
                    event=seed.event, session=session, history=seed.history
                ),
                rule=seed.rule_id,
                expected=seed.intended,
                note=seed.rationale[:200],
            )
        )
    return out


async def targeted(
    runtime: rt.Runtime, split: SynthSplit, cells: Sequence[tuple[str, ...]], model: str
) -> list[Single]:
    """One generated event per under-filled primary cell (kind, rule, verdict)."""
    world = build_slice(split)
    system = generator_system(runtime.policy.text, world)
    backend = runtime.cached(model, "targeted")

    async def one(i: int, cell: tuple[str, ...]) -> Single | None:
        kind, rule, verdict = cell
        spec = SPECS.get(rule)
        from pac.guardrail.application.ports import BackendError, LLMRequest  # noqa: PLC0415

        try:
            completion = await backend.complete(
                LLMRequest(
                    system=system,
                    user=targeted_user(kind, rule, verdict, spec_text(spec) if spec else "", []),  # type: ignore[arg-type]
                    json_schema={"type": "object"},
                    max_tokens=1200,
                    temperature=0.9,
                )
            )
            request, raw = parse_generated(completion.text, world, max_history=3)
        except (BackendError, ValueError) as exc:
            logger.info(
                "targeted generation failed",
                extra={"cell": "|".join(cell), "reason": str(exc)[:120]},
            )
            return None
        if request.event.kind != kind:
            return None
        return Single(
            id=f"syn-{split}-fill-{i:03d}",
            split=split,
            source="targeted",
            template=f"fill-{split}-{kind}-{rule.lower()}-{verdict}",
            request=request,
            rule=rule,
            expected=verdict,
            note=str(raw.get("idea") or "")[:200],
        )

    made = await asyncio.gather(*(one(i, c) for i, c in enumerate(cells)))
    return [s for s in made if s is not None]


# =============================================================================================
# Guideline 5: labels
# =============================================================================================


class Labelled(ValueObject):
    """A labelled event before splitting and deduplication."""

    id: str
    split: SynthSplit
    source: Source
    template: str
    request: ClassificationRequest
    turn_index: int | None = None
    rule_hint: str = "none"
    attack_style: AttackStyle = "none"
    agent_model: str | None = None
    consensus: Consensus
    consensus_with_history: Consensus | None = None
    ballots: tuple[Ballot, ...]
    expected: Verdict | None = None
    expected_with_history: Verdict | None = None
    family: str = ""
    member: str = ""


async def label_request(
    labeller: JuryLabeller, request: ClassificationRequest
) -> tuple[Consensus, Consensus | None, list[Ballot]]:
    """Single-event label from the jury and the labelling functions; with-history label from
    the same ballots once the fact store supplies what the event lacks."""
    alone = request.without_history()
    ballots = await labeller.ballots(alone)
    rules = rules_for(alone.event.kind, labeller.applies_to)
    single = aggregate(ballots, rules, labelling_functions(alone))
    if not request.history:
        return single, None, ballots
    hist = aggregate(ballots, rules, labelling_functions(request, use_history=True))
    if (hist.verdict, hist.rule_ids) == (single.verdict, single.rule_ids):
        return single, None, ballots
    return single, hist, ballots


def conversation_items() -> list[dict[str, Any]]:
    """Every recorded event as a labelling job, judged with everything before it as history."""
    items = []
    for rec in load_recordings():
        brief = Brief.model_validate(rec["brief"])
        history: list[TraceEvent] = []
        for i, step in enumerate(rec["events"]):
            event = TraceEvent.model_validate(step["event"])
            session = Session.model_validate(step["session"])
            style: AttackStyle = "none"
            if event.kind == "user_input":
                style = brief.attack_style
            elif event.kind == "tool_response" and _carries_injection(event, brief.split):
                style = "injection_in_tool_output"
            items.append(
                {
                    "id": f"{brief.id}-e{i:02d}",
                    "split": brief.split,
                    "source": "conversation",
                    "template": brief.template,
                    "request": ClassificationRequest(
                        event=event, session=session, history=tuple(history)
                    ),
                    "turn_index": i,
                    "attack_style": style,
                    "agent_model": brief.agent_model,
                }
            )
            history.append(event)
    return items


def _carries_injection(event: TraceEvent, split: str) -> bool:
    world = build_slice(split)  # type: ignore[arg-type]
    text = event.content or ""
    bodies = [a.body for a in world.articles if a.kind == "injection"]
    notes = [o.notes for o in world.orders if o.profile == "injected_note"]
    return any(b[-60:] in text for b in bodies) or any(n and n[-50:] in text for n in notes)


async def label_all(
    runtime: rt.Runtime, items: Sequence[dict[str, Any]], out: Path, *, parallel: int = 6
) -> list[Labelled]:
    labeller = runtime.jury()
    semaphore = asyncio.Semaphore(parallel)
    done = 0

    async def one(item: dict[str, Any]) -> Labelled:
        nonlocal done
        async with semaphore:
            single, hist, ballots = await label_request(labeller, item["request"])
        done += 1
        if done % 25 == 0:
            logger.info("labelled %d/%d", done, len(items))
        return Labelled(
            consensus=single, consensus_with_history=hist, ballots=tuple(ballots), **item
        )

    labelled = await asyncio.gather(*(one(i) for i in items))
    rt.write_jsonl(out, labelled)
    return list(labelled)


def load_labelled(path: Path) -> list[Labelled]:
    return rt.read_jsonl(path, Labelled)


# =============================================================================================
# Cells
# =============================================================================================

_TOPIC = {
    "issue_refund": "R5",
    "cancel_order": "R6",
    "update_shipping_address": "R6",
    "send_email": "R7",
    "lookup_order": "R1",
    "lookup_customer": "R1",
    "escalate_to_human": "R11",
    "search_kb": "R12",
}


def _topic(item: Labelled) -> str | None:
    """The rule an event is about by what it is: a refund call is about R5, and so on."""
    event = item.request.event
    if item.rule_hint != "none":
        return item.rule_hint
    if event.kind == "tool_call" and event.tool_name in _TOPIC:
        return _TOPIC[event.tool_name]
    return None


def primary_rule(item: Labelled) -> str:
    """The grid's rule axis: the rule the label rests on, preferring the event's own topic
    when it is among them (a silent refund call is an R5 event even though R2 is silent too)."""
    c = item.consensus
    topic = _topic(item)
    if c.verdict == "violation" and c.rule_ids:
        return topic if topic in c.rule_ids else c.rule_ids[0]
    if c.verdict == "policy_silent":
        silent = [o.rule_id for o in c.rules if o.verdict == "silent"]
        if silent:
            return topic if topic in silent else silent[0]
    if item.rule_hint != "none":
        return item.rule_hint
    flagged = [
        o.rule_id
        for o in c.rules
        if any(v != "allow" for v in o.votes.values())
        or (o.source == "lf" and o.verdict != "allow")
    ]
    if flagged:
        return flagged[0]
    event = item.request.event
    if event.kind in ("tool_call", "tool_response") and event.tool_name in _TOPIC:
        topic = _TOPIC[event.tool_name]
        return topic if event.kind == "tool_call" or topic == "R12" else "none"
    return "none"


def tool_outcome(request: ClassificationRequest) -> ToolOutcome:
    event = request.event
    if event.kind == "tool_response":
        mode = is_fault(event)
        return {None: "ok", "timeout_unknown": "timeout", "error": "error", "partial": "partial"}[
            mode
        ]  # type: ignore[return-value]
    if event.kind == "tool_call":
        last = next((e for e in reversed(request.history) if e.kind == "tool_response"), None)
        return "after_fault" if last is not None and is_fault(last) else "first_try"
    return "n/a"


def cell_of(item: Labelled) -> Cell:
    request = item.request
    return Cell(
        kind=request.event.kind,
        rule=primary_rule(item),
        verdict=item.consensus.verdict,
        position=position_of(item.turn_index),
        verification="verified" if request.session.verified else "unverified",
        language=event_language(request.event),
        attack_style=item.attack_style,
        tool_outcome=tool_outcome(request),
    )


def to_event(item: Labelled) -> SyntheticEvent:
    c, h = item.consensus, item.consensus_with_history
    lf = {o.rule_id: o.verdict for o in c.rules if o.source == "lf"}
    return SyntheticEvent(
        id=item.id,
        split=item.split,
        request=item.request,
        label=c.verdict,
        rule_ids=c.rule_ids,
        label_confidence=c.confidence,
        rationale=c.rationale,
        label_with_history=h.verdict if h else None,
        rule_ids_with_history=h.rule_ids if h else (),
        source=item.source,
        template=item.template,
        cell=cell_of(item),
        votes=c.juror_verdicts,
        lf=lf,
        agent_model=item.agent_model,
        tags=tuple(
            t
            for t in (
                f"family:{item.family}" if item.family else "",
                f"member:{item.member}" if item.member else "",
            )
            if t
        ),
    )
