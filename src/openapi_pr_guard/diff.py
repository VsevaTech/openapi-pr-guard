"""Diff engine: pairs up paths/operations of two specs and runs the rules."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from openapi_pr_guard.loader import Resolver
from openapi_pr_guard.models import Change, DiffResult

if TYPE_CHECKING:
    from openapi_pr_guard.rules.base import Rule
    from openapi_pr_guard.versioning import VersionPolicy

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

_PATH_PARAM = re.compile(r"\{[^}]*\}")


def normalize_path(path: str) -> str:
    """``/users/{id}`` and ``/users/{userId}`` describe the same endpoint."""
    return _PATH_PARAM.sub("{}", path)


@dataclass(frozen=True, slots=True)
class PathEntry:
    raw_path: str
    item: dict[str, Any]

    def operations(self) -> dict[str, dict[str, Any]]:
        return {m: op for m in HTTP_METHODS if isinstance(op := self.item.get(m), dict)}


@dataclass(frozen=True, slots=True)
class OperationPair:
    """A single operation present in both specs."""

    path: str
    method: str
    base: dict[str, Any]
    head: dict[str, Any]
    base_path_item: dict[str, Any]
    head_path_item: dict[str, Any]

    @property
    def method_upper(self) -> str:
        return self.method.upper()


@dataclass(frozen=True, slots=True)
class DiffContext:
    """Everything a rule needs: both documents, resolvers and matched paths."""

    base: dict[str, Any]
    head: dict[str, Any]
    base_resolver: Resolver
    head_resolver: Resolver
    base_paths: dict[str, PathEntry]
    head_paths: dict[str, PathEntry]

    @classmethod
    def from_specs(cls, base: dict[str, Any], head: dict[str, Any]) -> DiffContext:
        return cls(
            base=base,
            head=head,
            base_resolver=Resolver(base),
            head_resolver=Resolver(head),
            base_paths=_index_paths(base),
            head_paths=_index_paths(head),
        )

    def iter_operation_pairs(self) -> Iterator[OperationPair]:
        for key in sorted(self.base_paths.keys() & self.head_paths.keys()):
            base_entry, head_entry = self.base_paths[key], self.head_paths[key]
            base_ops, head_ops = base_entry.operations(), head_entry.operations()
            for method in HTTP_METHODS:
                if method in base_ops and method in head_ops:
                    yield OperationPair(
                        path=head_entry.raw_path,
                        method=method,
                        base=base_ops[method],
                        head=head_ops[method],
                        base_path_item=base_entry.item,
                        head_path_item=head_entry.item,
                    )


def diff_specs(
    base: dict[str, Any],
    head: dict[str, Any],
    rules: Sequence[Rule] | None = None,
    ignore_rules: Sequence[str] = (),
    version_policy: VersionPolicy | None = None,
) -> DiffResult:
    """Compare two parsed OpenAPI documents and return every detected change.

    With an enabled ``version_policy`` the result also carries a
    :class:`~openapi_pr_guard.versioning.VersionCheck` for ``info.version``.
    """
    from openapi_pr_guard.rules import DEFAULT_RULES  # local import: avoid cycle at module load
    from openapi_pr_guard.versioning import check_version

    context = DiffContext.from_specs(base, head)
    ignored = set(ignore_rules)
    changes: list[Change] = []
    for rule in rules if rules is not None else DEFAULT_RULES:
        changes.extend(c for c in rule.check(context) if c.rule_id not in ignored)
    result = DiffResult(changes=changes)
    if version_policy is not None and version_policy.enabled:
        result.version = check_version(base, head, changes, version_policy, ignored)
    return result


def _index_paths(document: dict[str, Any]) -> dict[str, PathEntry]:
    paths = document.get("paths") or {}
    index: dict[str, PathEntry] = {}
    for raw_path, item in paths.items():
        index[normalize_path(raw_path)] = PathEntry(raw_path=raw_path, item=item or {})
    return index
