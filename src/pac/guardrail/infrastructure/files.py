"""Reading the policy and prompt documents from disk."""

from __future__ import annotations

from pathlib import Path

from pac.guardrail.application.prompting import PromptTemplate
from pac.guardrail.domain.policy import Policy


def load_policy(path: Path) -> Policy:
    return Policy.from_text(path.read_text(encoding="utf-8"))


def load_prompt(
    path: Path, policy: Policy, *, max_tokens: int = 1024, temperature: float | None = None
) -> PromptTemplate:
    """Load a prompt template; its file stem names the version (``classifier_v2@<hash>``)."""
    return PromptTemplate.from_text(
        path.read_text(encoding="utf-8"),
        name=path.stem,
        policy=policy,
        max_tokens=max_tokens,
        temperature=temperature,
    )
