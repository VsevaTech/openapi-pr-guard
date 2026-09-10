"""Rules for the request body of an operation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from openapi_pr_guard.diff import DiffContext, OperationPair
from openapi_pr_guard.loader import Resolver
from openapi_pr_guard.models import Change, Severity
from openapi_pr_guard.rules.base import resolve_or_warn
from openapi_pr_guard.schema_diff import Direction, SchemaDiffer

LOCATION = "requestBody"


class RequestBodyRule:
    name = "request-body"

    def check(self, context: DiffContext) -> Iterable[Change]:
        for pair in context.iter_operation_pairs():
            yield from self._check_pair(context, pair)

    def _check_pair(self, context: DiffContext, pair: OperationPair) -> list[Change]:
        changes: list[Change] = []
        method, path = pair.method_upper, pair.path
        old = _body(pair.base, context.base_resolver, path, method, changes)
        new = _body(pair.head, context.head_resolver, path, method, changes)

        if old is None and new is None:
            return changes
        if old is None:
            if new.get("required") is True:
                changes.append(
                    Change(
                        Severity.BREAKING,
                        "request-body.required-added",
                        path,
                        "Request body is now required",
                        method,
                        LOCATION,
                    )
                )
            else:
                changes.append(
                    Change(
                        Severity.NON_BREAKING,
                        "request-body.optional-added",
                        path,
                        "Optional request body added",
                        method,
                        LOCATION,
                    )
                )
            return changes
        if new is None:
            changes.append(
                Change(
                    Severity.WARNING,
                    "request-body.removed",
                    path,
                    "Request body removed (clients still sending it may be rejected)",
                    method,
                    LOCATION,
                )
            )
            return changes

        if old.get("required") is not True and new.get("required") is True:
            changes.append(
                Change(
                    Severity.BREAKING,
                    "request-body.became-required",
                    path,
                    "Request body became required",
                    method,
                    LOCATION,
                )
            )
        elif old.get("required") is True and new.get("required") is not True:
            changes.append(
                Change(
                    Severity.NON_BREAKING,
                    "request-body.became-optional",
                    path,
                    "Request body became optional",
                    method,
                    LOCATION,
                )
            )

        old_content, new_content = _content(old), _content(new)
        for media in sorted(old_content.keys() - new_content.keys()):
            changes.append(
                Change(
                    Severity.BREAKING,
                    "request-body.media-type-removed",
                    path,
                    f"Request media type no longer accepted: {media}",
                    method,
                    f"{LOCATION}.content.{media}",
                )
            )
        for media in sorted(new_content.keys() - old_content.keys()):
            changes.append(
                Change(
                    Severity.NON_BREAKING,
                    "request-body.media-type-added",
                    path,
                    f"Request media type added: {media}",
                    method,
                    f"{LOCATION}.content.{media}",
                )
            )
        for media in sorted(old_content.keys() & new_content.keys()):
            differ = SchemaDiffer(context.base_resolver, context.head_resolver, path, method, Direction.REQUEST)
            differ.compare(
                old_content[media].get("schema"), new_content[media].get("schema"), f"{LOCATION}.content.{media}.schema"
            )
            changes.extend(differ.changes)
        return changes


def _body(
    operation: dict[str, Any], resolver: Resolver, path: str, method: str, changes: list[Change]
) -> dict[str, Any] | None:
    raw = operation.get("requestBody")
    if raw is None:
        return None
    body = resolve_or_warn(resolver, raw, path=path, method=method, location=LOCATION, changes=changes)
    return body if isinstance(body, dict) else None


def _content(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    content = body.get("content")
    if not isinstance(content, dict):
        return {}
    return {k: v for k, v in content.items() if isinstance(v, dict)}
