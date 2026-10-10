import pytest

from pac.policy import Policy
from pac.settings import Settings


def test_shipped_policy_parses() -> None:
    policy = Policy.load(Settings().policy_path)
    assert policy.version == "v0"
    assert policy.rule_ids == frozenset(f"R{i}" for i in range(1, 14))
    assert len(policy.sha256) == 64


def test_hash_changes_with_text() -> None:
    base = "**Version:** v9\n\n**R1 — Thing.** Do it."
    assert Policy.from_text(base).sha256 != Policy.from_text(base + " ").sha256


@pytest.mark.parametrize(
    "text",
    ["**R1 — Thing.** no version", "**Version:** v1\n no rules here"],
)
def test_malformed_policy_rejected(text: str) -> None:
    with pytest.raises(ValueError, match="policy"):
        Policy.from_text(text)
