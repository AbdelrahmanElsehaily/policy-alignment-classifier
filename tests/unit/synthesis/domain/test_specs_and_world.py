"""Boundary specs follow the policy; the synthetic world shares nothing with the eval world."""

from __future__ import annotations

import pytest

from pac.guardrail.domain.events import TraceEvent
from pac.guardrail.domain.policy import Policy
from pac.settings import REPO_ROOT
from pac.synthesis.domain.specs import (
    BOUNDARY_SPECS,
    Seed,
    applies_to_from_policy,
    check_seed,
    check_specs_against_policy,
    rules_for,
)
from pac.synthesis.domain.world import SLICES, all_slices, build_slice


def test_every_rule_has_a_spec_with_the_policys_event_kinds(policy: Policy) -> None:
    assert check_specs_against_policy(policy.text) == []
    assert {s.rule_id for s in BOUNDARY_SPECS} == set(policy.rule_ids)


def test_applies_to_is_read_from_the_policy(policy: Policy) -> None:
    table = applies_to_from_policy(policy.text)
    assert table["R13"] == ("user_input",)
    assert table["R12"] == ("tool_response",)
    assert set(table["R3"]) == {"model_output", "tool_call", "tool_response"}
    assert rules_for("user_input", table) == ("R13",)
    assert rules_for("tool_call", table)[0] == "R1"


def test_lookalike_kinds_extend_but_never_replace_applies_to() -> None:
    for spec in BOUNDARY_SPECS:
        assert set(spec.applies_to) <= set(spec.kinds())
        assert not set(spec.lookalike_kinds) & set(spec.applies_to), spec.rule_id


def _seed(**kw: object) -> Seed:
    base: dict[str, object] = {
        "id": "s",
        "rule_id": "R3",
        "intended": "violation",
        "event": TraceEvent(kind="model_output", content="Your card is 4111 1111 1111 1111"),
        "rationale": "R3 forbids Lark outputting payment data.",
        "generator": "g",
    }
    return Seed.model_validate(base | kw)


def test_seed_check_rejects_invented_rules_wrong_kinds_and_eval_ids(policy: Policy) -> None:
    table = applies_to_from_policy(policy.text)
    assert check_seed(_seed(), table) is None
    invented = _seed(rationale="R14 bans storing cards", cites=("R14",))
    assert "R14" in (check_seed(invented, table) or "")
    wrong_kind = _seed(event=TraceEvent(kind="user_input", content="my card 4111 1111 1111 1111"))
    assert "can't judge" in (check_seed(wrong_kind, table) or "")
    leak = _seed(event=TraceEvent(kind="model_output", content="Order LK-50211 shipped"))
    assert "eval" in (check_seed(leak, table) or "")


def test_world_is_deterministic() -> None:
    assert build_slice("train") == build_slice("train")


@pytest.mark.parametrize("name", SLICES)
def test_each_slice_uses_its_own_id_ranges(name: str) -> None:
    world = build_slice(name)  # type: ignore[arg-type]
    assert all(c.customer_id.startswith("C-3") for c in world.customers)
    assert all(o.order_id.startswith("LK-9") for o in world.orders)
    assert {o.profile for o in world.orders} >= {"recent", "recent_over_cap", "flaky_refund"}
    assert world.flaky_orders


def test_slices_share_no_identifier_product_or_article() -> None:
    slices = list(all_slices().values())
    for i, a in enumerate(slices):
        for b in slices[i + 1 :]:
            assert not a.identifiers() & b.identifiers(), (a.name, b.name)
            assert not a.products() & b.products(), (a.name, b.name)
            assert not {x.body for x in a.articles} & {x.body for x in b.articles}


def test_no_world_identifier_or_product_appears_in_the_eval_or_train_data() -> None:
    files = [
        *(REPO_ROOT / "data" / "eval").glob("*.jsonl"),
        *(REPO_ROOT / "data" / "train").glob("*"),
        *(REPO_ROOT / "data" / "sources").rglob("*.json"),
    ]
    text = "\n".join(p.read_text(encoding="utf-8") for p in files)
    for world in all_slices().values():
        for identifier in world.identifiers() | world.products():
            assert identifier not in text, identifier
