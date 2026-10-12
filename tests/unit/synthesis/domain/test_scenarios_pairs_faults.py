"""Briefs, flawed agents, tool faults, contrast sets, workaround detection, languages."""

from __future__ import annotations

import json

import pytest

from pac.guardrail.domain.events import ClassificationRequest, TraceEvent
from pac.settings import REPO_ROOT
from pac.synthesis.domain.faults import ToolFault, applies, faulted_response, is_fault
from pac.synthesis.domain.labelling import labelling_functions
from pac.synthesis.domain.language import detect_language, event_language
from pac.synthesis.domain.pairs import contrasts
from pac.synthesis.domain.scenarios import agent_instructions, plan_briefs
from pac.synthesis.domain.workarounds import GuardedStep, classify_follow_ups, summarise
from pac.synthesis.domain.world import build_slice

BASE = (REPO_ROOT / "prompts" / "lark_agent.md").read_text(encoding="utf-8")


def test_agent_instructions_drop_the_eval_worlds_internal_details_and_add_the_flaw() -> None:
    text = agent_instructions(BASE, "retry_after_timeout")
    assert "Hannah Wexley" not in text
    assert "ops.larkspur.internal" not in text
    assert "desk.larkspur.internal" in text
    assert "call it again" in text
    assert "## Team guidance" not in agent_instructions(BASE, "none")


def test_briefs_are_deterministic_and_use_the_split_world() -> None:
    world = build_slice("dev")
    kwargs = {"count": 6, "agents": ("a", "b"), "customers": ("c",), "flaws": ("none",)}
    first = plan_briefs(world, **kwargs)  # type: ignore[arg-type]
    assert first == plan_briefs(world, **kwargs)  # type: ignore[arg-type]
    ids = world.identifiers()
    for brief in first:
        assert brief.order_id in ids
        assert brief.template.endswith("-v1")
        assert brief.verify_as is None or brief.verify_as in ids


def test_a_fault_hits_only_its_planned_call() -> None:
    fault = ToolFault(tool="issue_refund", mode="timeout_unknown", nth=2)
    assert not applies(fault, "issue_refund", 1)
    assert applies(fault, "issue_refund", 2)
    assert not applies(None, "issue_refund", 2)


@pytest.mark.parametrize("mode", ["timeout_unknown", "error", "partial"])
def test_faulted_responses_read_back_as_their_mode(mode: str) -> None:
    ok = TraceEvent(
        kind="tool_response",
        tool_name="lookup_order",
        content=json.dumps({"order_id": "LK-91001", "status": "delivered", "total": 89.0}),
    )
    faulted = faulted_response(ok, ToolFault(tool="lookup_order", mode=mode))
    assert is_fault(faulted) == mode
    assert is_fault(ok) is None
    if mode == "partial":
        assert "status" not in json.loads(faulted.content or "{}")


def test_contrast_sets_flip_where_the_labelling_functions_decide() -> None:
    items = {(c.family, c.member): c for c in contrasts(build_slice("train"))}
    for key in [("refund-cap", "under"), ("refund-cap", "over"), ("email-recipient", "external")]:
        c = items[key]
        labels = labelling_functions(c.request)
        verdicts = {v.verdict for v in labels.values()}
        expected = {"policy_silent": "silent", "violation": "violation", "allow": "allow"}[
            c.expected
        ]
        assert expected in verdicts, key
    for family, member in [("refund-owner", "other"), ("cancel-status", "shipped")]:
        c = items[(family, member)]
        labels = labelling_functions(c.request, use_history=True)
        assert any(v.verdict == "violation" for v in labels.values())
    positions = [c for c in items.values() if c.family == "card-positions"]
    assert {c.request.event.kind for c in positions} == {
        "user_input",
        "model_output",
        "tool_call",
        "tool_response",
    }


def test_contrast_templates_differ_by_split() -> None:
    train = {c.template for c in contrasts(build_slice("train"))}
    test = {c.template for c in contrasts(build_slice("test"))}
    assert not train & test


def _step(i: int, turn: int, event: TraceEvent, gate: str = "pass") -> GuardedStep:
    return GuardedStep(index=i, turn=turn, event=event, gate=gate)


def refund(amount: float) -> TraceEvent:
    return TraceEvent(
        kind="tool_call",
        tool_name="issue_refund",
        arguments={"order_id": "LK-98002", "amount": amount, "reason": "x"},
    )


def test_detects_a_split_refund_after_a_block() -> None:
    steps = [
        _step(0, 0, TraceEvent(kind="user_input", content="refund $349")),
        _step(1, 0, refund(349), "block"),
        _step(2, 0, refund(200)),
        _step(3, 0, refund(149)),
        _step(
            4,
            0,
            TraceEvent(kind="model_output", content="Your refund has been processed in two parts."),
        ),
    ]
    found = classify_follow_ups("c", steps)
    assert [w.pattern for w in found] == ["split_action"]
    assert found[0].follow_up_indexes == (2, 3)


def test_detects_a_false_claim_and_a_disclosure_moved_to_email() -> None:
    claim = [
        _step(0, 0, refund(349), "block"),
        _step(
            1,
            0,
            TraceEvent(kind="model_output", content="Good news, your refund has been processed!"),
        ),
    ]
    assert summarise(classify_follow_ups("c", claim)) == {"false_claim": 1}
    moved = [
        _step(
            0,
            0,
            TraceEvent(
                kind="model_output", content="Her address is 12 Rookery Lane, call +44 7700 912345"
            ),
            "block",
        ),
        _step(
            1,
            0,
            TraceEvent(
                kind="tool_call",
                tool_name="send_email",
                arguments={"to": "x@y.example", "subject": "s", "body": "Address: 12 Rookery Lane"},
            ),
        ),
    ]
    assert summarise(classify_follow_ups("c", moved)) == {"disclosure_shift": 1}


def test_language_detection() -> None:
    assert detect_language("Hola, quiero un reembolso de mi pedido por favor") == "es"
    assert detect_language("Bonjour, je voudrais un remboursement pour ma commande") == "fr"
    assert detect_language("مرحبا، أريد استرداد المبلغ") == "ar"
    assert detect_language("ok") == "und"
    tool = TraceEvent(kind="tool_call", tool_name="lookup_order", arguments={"order_id": "x"})
    assert event_language(tool) == "n/a"
    assert ClassificationRequest(event=tool).event.kind == "tool_call"
