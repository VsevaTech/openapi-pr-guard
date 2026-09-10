"""Rule interface. A rule inspects a :class:`DiffContext` and yields changes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Protocol

from openapi_pr_guard.loader import Resolver, UnresolvableRef
from openapi_pr_guard.models import Change, Severity

if TYPE_CHECKING:
    from openapi_pr_guard.diff import DiffContext


class Rule(Protocol):
    """Anything with a ``check`` method that yields :class:`Change` objects.

    To add a new rule: implement this protocol in ``rules/`` and append an
    instance to ``rules.DEFAULT_RULES``. The diff engine does not need to change.
    """

    name: str

    def check(self, context: DiffContext) -> Iterable[Change]: ...


def resolve_or_warn(
    resolver: Resolver,
    node: Any,
    *,
    path: str,
    method: str | None,
    location: str,
    changes: list[Change],
) -> Any:
    """Resolve ``node``; on failure record a WARNING and return ``None``."""
    try:
        return resolver.resolve(node).value
    except UnresolvableRef as exc:
        changes.append(
            Change(
                severity=Severity.WARNING,
                rule_id="schema.unresolvable-ref",
                path=path,
                method=method,
                location=location,
                message=str(exc),
            )
        )
        return None
