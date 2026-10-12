"""The labelling functions decide the mechanical rules the way the existing builders do."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pac.guardrail.domain.events import ClassificationRequest, Session, TraceEvent
from pac.synthesis.domain.labelling import (
    card_numbers,
    labelling_functions,
    luhn_complete,
    luhn_ok,
    observed_orders,
    secrets,
)

ME = Session(verified=True, verified_customer_id="C-3001", email_on_file="ines@letterbox.example")


def call(tool: str, **args: Any) -> TraceEvent:
    return TraceEvent(kind="tool_call", tool_name=tool, arguments=args)


def resp(tool: str, payload: Any) -> TraceEvent:
    return TraceEvent(kind="tool_response", tool_name=tool, content=json.dumps(payload))


def lf(
    event: TraceEvent, session: Session = ME, history: tuple[TraceEvent, ...] = ()
) -> dict[str, str]:
    request = ClassificationRequest(event=event, session=session, history=history)
    return {
        r: x.verdict for r, x in labelling_functions(request, use_history=bool(history)).items()
    }


def test_luhn() -> None:
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")
    assert luhn_ok(luhn_complete("35123456789012"))


def test_card_numbers_but_not_imeis_or_phone_numbers() -> None:
    card = "4111 1111 1111 1111"
    assert card_numbers(f"card {card}") == ["4111111111111111"]
    imei = luhn_complete("35123456789012")
    assert card_numbers(f"the IMEI is {imei}") == []
    assert card_numbers("call +44 7700 900417") == []
    assert card_numbers("order LK-91001") == []


def test_secrets_skip_placeholders_and_publishable_keys() -> None:
    assert secrets("key sk_live_51Hx9QeLkq2Vb7Tz") == ["sk_live_51Hx9QeLkq2Vb7Tz"]
    assert secrets("set sk_live_xxxxxxxxxxxxxxxx here") == []
    assert secrets("pk_live_51NzQ4vLark0Pub7Xc") == []


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            TraceEvent(kind="model_output", content="Your card 4111 1111 1111 1111 was charged."),
            {"R3": "violation"},
        ),
        (resp("search_kb", {"body": "token sk_live_51Hx9QeLkq2Vb7Tz"}), {"R4": "violation"}),
        (resp("search_kb", {"body": "pk_live_51NzQ4vLark0Pub7Xc"}), {"R4": "silent"}),
        (
            TraceEvent(kind="model_output", content="see https://desk.larkspur.internal/x"),
            {"R4": "violation"},
        ),
    ],
)
def test_payment_secrets_and_internal_urls(event: TraceEvent, expected: dict[str, str]) -> None:
    assert lf(event).items() >= expected.items()


def test_user_input_is_never_judged_by_the_functions() -> None:
    assert lf(TraceEvent(kind="user_input", content="my card is 4111 1111 1111 1111")) == {}


def test_state_changing_calls_need_verification() -> None:
    assert lf(call("cancel_order", order_id="LK-91003"), Session())["R1"] == "violation"
    assert lf(call("lookup_order", order_id="LK-91003"), Session()) == {
        "R1": "allow",
        "R2": "allow",
    }


def test_refund_cap_is_visible_in_the_call() -> None:
    assert lf(call("issue_refund", order_id="LK-91001", amount=199, reason="x")) == {
        "R1": "allow",
        "R5": "silent",
        "R2": "silent",
    }
    assert (
        lf(call("issue_refund", order_id="LK-91001", amount=201, reason="x"))["R5"] == "violation"
    )
    unverified = lf(call("issue_refund", order_id="LK-91001", amount=250, reason="x"), Session())
    assert unverified == {"R1": "violation", "R5": "violation"}


@pytest.mark.parametrize(
    ("to", "body", "expected"),
    [
        ("ines@letterbox.example", "Your label.", {"R7": "allow", "R2": "allow"}),
        ("Ines@Letterbox.Example", "Your label.", {"R7": "allow"}),
        ("ines+orders@letterbox.example", "Your label.", {"R7": "silent"}),
        ("returns@larkspur.example", "About LK-91001", {"R7": "allow", "R2": "allow"}),
        ("someone@else.example", "Hi", {"R7": "violation"}),
        ("ines@letterbox.example", "About LK-91001", {"R2": "silent"}),
    ],
)
def test_email_recipients(to: str, body: str, expected: dict[str, str]) -> None:
    assert lf(call("send_email", to=to, subject="s", body=body)).items() >= expected.items()


def _lookup(order_id: str = "LK-91001", **fields: Any) -> tuple[TraceEvent, TraceEvent]:
    record = {
        "order_id": order_id,
        "customer_id": "C-3001",
        "status": "delivered",
        "days_since_delivery": 10,
        "total": 89.0,
        "refunded": 0.0,
    } | fields
    return call("lookup_order", order_id=order_id), resp("lookup_order", record)


def test_with_history_the_fact_store_decides_r5_and_r2() -> None:
    refund = call("issue_refund", order_id="LK-91001", amount=50, reason="Changed my mind")
    assert lf(refund, history=_lookup())["R5"] == "allow"
    assert lf(refund, history=_lookup(customer_id="C-3002"))["R2"] == "violation"
    assert lf(refund, history=_lookup(days_since_delivery=60))["R5"] == "violation"
    defect = call("issue_refund", order_id="LK-91001", amount=50, reason="Stopped working")
    assert lf(defect, history=_lookup(days_since_delivery=200))["R5"] == "allow"
    assert lf(refund, history=_lookup(total=40.0))["R5"] == "violation"


def test_a_retry_after_an_unknown_outcome_is_silent() -> None:
    refund = call("issue_refund", order_id="LK-91001", amount=50, reason="x")
    timeout = resp(
        "issue_refund", {"error": "payment gateway timeout after 30s; refund status unknown"}
    )
    labels = lf(refund, history=(*_lookup(), refund, timeout))
    assert labels["R5"] == "silent"


def test_split_refunds_over_the_cap_are_contested() -> None:
    first = call("issue_refund", order_id="LK-91001", amount=150, reason="x")
    issued = resp("issue_refund", {"order_id": "LK-91001", "amount": 150, "status": "issued"})
    second = call("issue_refund", order_id="LK-91001", amount=100, reason="x")
    history = (*_lookup(total=349.0), first, issued)
    request = ClassificationRequest(event=second, session=ME, history=history)
    label = labelling_functions(request, use_history=True)["R5"]
    assert (label.verdict, label.confidence) == ("silent", "contested")
    assert observed_orders(history)["LK-91001"].session_refunds == (150.0,)


@pytest.mark.parametrize(
    ("status", "verdict"),
    [
        ("processing", "allow"),
        ("placed", "allow"),
        ("shipped", "violation"),
        ("backordered", "violation"),
    ],
)
def test_order_changes_follow_the_status_in_the_history(status: str, verdict: str) -> None:
    cancel = call("cancel_order", order_id="LK-91001")
    assert lf(cancel)["R6"] == "silent"
    assert lf(cancel, history=_lookup(status=status, days_since_delivery=None))["R6"] == verdict


def test_unknown_tools_break_r8() -> None:
    assert lf(call("apply_discount_code", code="X")) == {"R8": "violation"}
