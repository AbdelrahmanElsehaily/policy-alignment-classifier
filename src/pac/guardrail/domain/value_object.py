"""Base class for immutable domain values."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ValueObject(BaseModel):
    """Immutable, validated on construction, and compared by value.

    Pydantic is the one framework the domain depends on: it does validation, not I/O, and it
    lets the same types serve as the wire format without a mapping layer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
