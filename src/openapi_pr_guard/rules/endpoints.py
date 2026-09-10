"""Rules about the presence of paths and operations, plus operation metadata."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from openapi_pr_guard.diff import DiffContext, OperationPair, PathEntry
from openapi_pr_guard.models import Change, Severity

DOC_KEYS = ("summary", "description", "tags", "externalDocs")


class EndpointPresenceRule:
    """Removed paths/methods are breaking; added ones are not."""

    name = "endpoint-presence"

    def check(self, context: DiffContext) -> Iterable[Change]:
        base, head = context.base_paths, context.head_paths
        for key in sorted(base.keys() - head.keys()):
            yield from _path_changes(base[key], Severity.BREAKING, "endpoint.removed", "Endpoint removed")
        for key in sorted(head.keys() - base.keys()):
            yield from _path_changes(head[key], Severity.NON_BREAKING, "endpoint.added", "New endpoint added")
        for key in sorted(base.keys() & head.keys()):
            yield from _method_changes(base[key], head[key])


class OperationMetadataRule:
    """Documentation-only edits, deprecation and operationId renames."""

    name = "operation-metadata"

    def check(self, context: DiffContext) -> Iterable[Change]:
        for pair in context.iter_operation_pairs():
            yield from self._check_pair(pair)

    def _check_pair(self, pair: OperationPair) -> Iterator[Change]:
        method, path = pair.method_upper, pair.path
        if pair.base.get("operationId") != pair.head.get("operationId"):
            yield Change(
                Severity.WARNING,
                "operation.operation-id-changed",
                path,
                f"operationId changed: {pair.base.get('operationId')} -> {pair.head.get('operationId')} "
                "(generated clients may break)",
                method,
                "operationId",
            )
        if not pair.base.get("deprecated") and pair.head.get("deprecated"):
            yield Change(
                Severity.NON_BREAKING, "operation.deprecated", path, "Operation marked deprecated", method, "deprecated"
            )
        changed_docs = [k for k in DOC_KEYS if pair.base.get(k) != pair.head.get(k)]
        if changed_docs:
            yield Change(
                Severity.NON_BREAKING,
                "operation.docs-changed",
                path,
                f"Documentation changed ({', '.join(changed_docs)})",
                method,
                changed_docs[0],
            )


def _path_changes(entry: PathEntry, severity: Severity, rule_id: str, message: str) -> Iterator[Change]:
    operations = entry.operations()
    if not operations:
        yield Change(severity, rule_id, entry.raw_path, message)
        return
    for method in operations:
        yield Change(severity, rule_id, entry.raw_path, message, method.upper())


def _method_changes(base: PathEntry, head: PathEntry) -> Iterator[Change]:
    base_ops, head_ops = base.operations(), head.operations()
    for method in base_ops.keys() - head_ops.keys():
        yield Change(Severity.BREAKING, "operation.removed", base.raw_path, "HTTP method removed", method.upper())
    for method in head_ops.keys() - base_ops.keys():
        yield Change(Severity.NON_BREAKING, "operation.added", head.raw_path, "New HTTP method added", method.upper())
