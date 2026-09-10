"""Rules for operation responses: status codes, media types and response schemas."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from openapi_pr_guard.diff import DiffContext, OperationPair
from openapi_pr_guard.loader import Resolver
from openapi_pr_guard.models import Change, Severity
from openapi_pr_guard.rules.base import resolve_or_warn
from openapi_pr_guard.schema_diff import Direction, SchemaDiffer


class ResponsesRule:
    name = "responses"

    def check(self, context: DiffContext) -> Iterable[Change]:
        for pair in context.iter_operation_pairs():
            yield from self._check_pair(context, pair)

    def _check_pair(self, context: DiffContext, pair: OperationPair) -> list[Change]:
        changes: list[Change] = []
        method, path = pair.method_upper, pair.path
        old_responses = _responses(pair.base)
        new_responses = _responses(pair.head)

        for code in sorted(old_responses.keys() - new_responses.keys()):
            changes.append(
                Change(
                    Severity.BREAKING,
                    "response.code-removed",
                    path,
                    f"Response code removed: {code}",
                    method,
                    f"responses.{code}",
                )
            )
        for code in sorted(new_responses.keys() - old_responses.keys()):
            changes.append(
                Change(
                    Severity.NON_BREAKING,
                    "response.code-added",
                    path,
                    f"New response code added: {code}",
                    method,
                    f"responses.{code}",
                )
            )

        for code in sorted(old_responses.keys() & new_responses.keys()):
            location = f"responses.{code}"
            old = _resolve(context.base_resolver, old_responses[code], path, method, location, changes)
            new = _resolve(context.head_resolver, new_responses[code], path, method, location, changes)
            if old is None or new is None:
                continue
            old_content, new_content = _content(old), _content(new)
            for media in sorted(old_content.keys() - new_content.keys()):
                changes.append(
                    Change(
                        Severity.BREAKING,
                        "response.media-type-removed",
                        path,
                        f"Response media type removed: {media}",
                        method,
                        f"{location}.content.{media}",
                    )
                )
            for media in sorted(new_content.keys() - old_content.keys()):
                changes.append(
                    Change(
                        Severity.NON_BREAKING,
                        "response.media-type-added",
                        path,
                        f"Response media type added: {media}",
                        method,
                        f"{location}.content.{media}",
                    )
                )
            for media in sorted(old_content.keys() & new_content.keys()):
                differ = SchemaDiffer(context.base_resolver, context.head_resolver, path, method, Direction.RESPONSE)
                differ.compare(
                    old_content[media].get("schema"),
                    new_content[media].get("schema"),
                    f"{location}.content.{media}.schema",
                )
                changes.extend(differ.changes)
        return changes


def _responses(operation: dict[str, Any]) -> dict[str, Any]:
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return {}
    return {str(code): value for code, value in responses.items()}


def _resolve(
    resolver: Resolver, node: Any, path: str, method: str, location: str, changes: list[Change]
) -> dict[str, Any] | None:
    resolved = resolve_or_warn(resolver, node, path=path, method=method, location=location, changes=changes)
    return resolved if isinstance(resolved, dict) else None


def _content(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    content = response.get("content")
    if not isinstance(content, dict):
        return {}
    return {k: v for k, v in content.items() if isinstance(v, dict)}
