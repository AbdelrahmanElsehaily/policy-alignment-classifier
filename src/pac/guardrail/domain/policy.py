"""The policy a guardrail enforces: its text, version, and the rules it defines."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_VERSION = re.compile(r"\*\*Version:\*\*\s*(\S+)")
_RULE_HEADING = re.compile(r"\*\*(R\d{1,2})\s+—")


@dataclass(frozen=True, slots=True)
class Policy:
    """A policy document, parsed. The hash ties every result to the exact text it was judged by."""

    text: str
    version: str
    sha256: str
    rule_ids: frozenset[str]

    @classmethod
    def from_text(cls, text: str) -> Policy:
        version = _VERSION.search(text)
        if version is None:
            raise ValueError("policy has no '**Version:**' line")
        rule_ids = frozenset(_RULE_HEADING.findall(text))
        if not rule_ids:
            raise ValueError("policy defines no rules ('**R<n> — ...**')")
        return cls(
            text=text,
            version=version.group(1),
            sha256=hashlib.sha256(text.encode()).hexdigest(),
            rule_ids=rule_ids,
        )

    def ordered_rule_ids(self) -> list[str]:
        """Rule ids in numeric order (R1, R2, …, R13)."""
        return sorted(self.rule_ids, key=lambda rule_id: int(rule_id[1:]))

    def defines(self, rule_id: str) -> bool:
        return rule_id in self.rule_ids
