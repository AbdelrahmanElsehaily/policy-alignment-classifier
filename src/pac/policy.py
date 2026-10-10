"""Loading the policy document the classifier is measured against."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_VERSION = re.compile(r"\*\*Version:\*\*\s*(\S+)")
_RULE_HEADING = re.compile(r"\*\*(R\d{1,2})\s+—")


@dataclass(frozen=True, slots=True)
class Policy:
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

    @classmethod
    def load(cls, path: Path) -> Policy:
        return cls.from_text(path.read_text(encoding="utf-8"))
