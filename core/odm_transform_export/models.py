from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ChangeStatus(str, Enum):
    APPLIED = "applied"
    SKIPPED = "skipped"   # could not be auto-fixed; human review needed


@dataclass
class Change:
    status: ChangeStatus
    rule: str
    description: str
    location: str
    old_value: str | None = None
    new_value: str | None = None
    note: str | None = None   # why it was skipped, or a caveát on the fix


@dataclass
class TransformResult:
    source_file: str
    edc: str
    changes: list[Change] = field(default_factory=list)

    @property
    def applied(self) -> list[Change]:
        return [c for c in self.changes if c.status == ChangeStatus.APPLIED]

    @property
    def skipped(self) -> list[Change]:
        return [c for c in self.changes if c.status == ChangeStatus.SKIPPED]
