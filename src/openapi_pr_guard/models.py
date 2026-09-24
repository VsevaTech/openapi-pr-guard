"""Core data model shared by the diff engine, rules and reporters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openapi_pr_guard.summary import EndpointSummary
    from openapi_pr_guard.versioning import VersionCheck


class Severity(StrEnum):
    BREAKING = "BREAKING"
    WARNING = "WARNING"
    NON_BREAKING = "NON-BREAKING"

    @property
    def rank(self) -> int:
        """Lower rank = more severe. Used for sorting."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {Severity.BREAKING: 0, Severity.WARNING: 1, Severity.NON_BREAKING: 2}


@dataclass(frozen=True, slots=True)
class Change:
    """A single detected difference between the base and head specifications.

    ``method`` is ``None`` for path-level changes (e.g. a whole path removed
    without any operations). ``location`` is a dotted path relative to the
    operation, e.g. ``requestBody.content.application/json.schema.properties.phone``.
    """

    severity: Severity
    rule_id: str
    path: str
    message: str
    method: str | None = None
    location: str = ""

    @property
    def sort_key(self) -> tuple[int, str, str, str]:
        return (self.severity.rank, self.path, self.method or "", self.location)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "rule_id": self.rule_id,
            "method": self.method,
            "path": self.path,
            "location": self.location,
            "message": self.message,
        }


@dataclass(slots=True)
class DiffResult:
    changes: list[Change] = field(default_factory=list)
    version: VersionCheck | None = None
    """Set when a version policy is enabled; see :mod:`openapi_pr_guard.versioning`."""

    def by_severity(self, severity: Severity) -> list[Change]:
        return [c for c in self.changes if c.severity is severity]

    @property
    def breaking(self) -> list[Change]:
        return self.by_severity(Severity.BREAKING)

    @property
    def warnings(self) -> list[Change]:
        return self.by_severity(Severity.WARNING)

    @property
    def non_breaking(self) -> list[Change]:
        return self.by_severity(Severity.NON_BREAKING)

    @property
    def has_breaking(self) -> bool:
        return bool(self.breaking)

    @property
    def version_blocking(self) -> bool:
        """The version policy is enabled with ``severity: error`` and was violated."""
        return self.version is not None and self.version.blocking

    def sorted(self) -> list[Change]:
        return sorted(self.changes, key=lambda c: c.sort_key)

    def endpoint_summary(self) -> EndpointSummary:
        from openapi_pr_guard.summary import summarize_endpoints

        return summarize_endpoints(self.changes)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "summary": {
                "breaking": len(self.breaking),
                "warnings": len(self.warnings),
                "non_breaking": len(self.non_breaking),
            },
            "endpoints": self.endpoint_summary().to_dict(),
            "changes": [c.to_dict() for c in self.sorted()],
        }
        if self.version is not None:
            data["version"] = self.version.to_dict()
        return data
