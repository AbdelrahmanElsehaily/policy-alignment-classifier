import pytest

from pac.evaluation.domain.agreement import cohen_kappa
from pac.guardrail.domain.decision import Verdict


def test_perfect_agreement() -> None:
    labels: list[Verdict] = ["allow", "violation", "policy_silent", "allow"]
    result = cohen_kappa(labels, labels)
    assert result.observed == 1.0
    assert result.kappa == pytest.approx(1.0)


def test_textbook_value() -> None:
    # 2x2 example: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no -> po=0.7, pe=0.5, kappa=0.4
    yes: list[Verdict] = ["violation"]
    no: list[Verdict] = ["allow"]
    a = yes * 25 + no * 25
    b = yes * 20 + no * 5 + yes * 10 + no * 15
    result = cohen_kappa(a, b)
    assert result.observed == pytest.approx(0.7)
    assert result.expected == pytest.approx(0.5)
    assert result.kappa == pytest.approx(0.4)
    assert result.confusion["violation"]["allow"] == 5


def test_undefined_when_single_label() -> None:
    labels: list[Verdict] = ["allow", "allow"]
    assert cohen_kappa(labels, labels).kappa is None


def test_input_validation() -> None:
    with pytest.raises(ValueError, match="same items"):
        cohen_kappa(["allow"], [])
    with pytest.raises(ValueError, match="no items"):
        cohen_kappa([], [])
