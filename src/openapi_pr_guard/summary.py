"""Endpoint-level roll-up of a diff: what was added, removed and changed.

The detailed report lists every finding; this module answers the question a
PR reviewer or release-notes reader actually asks — *which endpoints moved?*
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from openapi_pr_guard.diff import HTTP_METHODS
from openapi_pr_guard.models import Change, Severity

ADDED_RULES = frozenset({"endpoint.added", "operation.added"})
REMOVED_RULES = frozenset({"endpoint.removed", "operation.removed"})

_METHOD_ORDER = {m.upper(): i for i, m in enumerate(HTTP_METHODS)}


@dataclass(frozen=True, slots=True)
class Endpoint:
    path: str
    method: str | None

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}" if self.method else self.path

    @property
    def sort_key(self) -> tuple[str, int]:
        return (self.path, _METHOD_ORDER.get(self.method or "", -1))


@dataclass(slots=True)
class ChangedEndpoint:
    endpoint: Endpoint
    changes: list[Change] = field(default_factory=list)

    def count(self, severity: Severity) -> int:
        return sum(1 for c in self.changes if c.severity is severity)

    @property
    def worst(self) -> Severity:
        return min((c.severity for c in self.changes), key=lambda s: s.rank)


@dataclass(slots=True)
class EndpointSummary:
    added: list[Endpoint] = field(default_factory=list)
    removed: list[Endpoint] = field(default_factory=list)
    changed: list[ChangedEndpoint] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": [e.label for e in self.added],
            "removed": [e.label for e in self.removed],
            "changed": [
                {
                    "endpoint": c.endpoint.label,
                    "breaking": c.count(Severity.BREAKING),
                    "warnings": c.count(Severity.WARNING),
                    "non_breaking": c.count(Severity.NON_BREAKING),
                }
                for c in self.changed
            ],
        }


def summarize_endpoints(changes: Iterable[Change]) -> EndpointSummary:
    added: set[Endpoint] = set()
    removed: set[Endpoint] = set()
    changed: dict[Endpoint, ChangedEndpoint] = {}
    for change in changes:
        endpoint = Endpoint(change.path, change.method)
        if change.rule_id in ADDED_RULES:
            added.add(endpoint)
        elif change.rule_id in REMOVED_RULES:
            removed.add(endpoint)
        else:
            changed.setdefault(endpoint, ChangedEndpoint(endpoint)).changes.append(change)

    def by_key(e: Endpoint) -> tuple[str, int]:
        return e.sort_key

    return EndpointSummary(
        added=sorted(added, key=by_key),
        removed=sorted(removed, key=by_key),
        changed=sorted(changed.values(), key=lambda c: c.endpoint.sort_key),
    )
