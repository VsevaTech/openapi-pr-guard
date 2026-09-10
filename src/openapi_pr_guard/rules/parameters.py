"""Rules for request parameters (query, path, header, cookie)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from openapi_pr_guard.diff import DiffContext, OperationPair
from openapi_pr_guard.loader import Resolver
from openapi_pr_guard.models import Change, Severity
from openapi_pr_guard.rules.base import resolve_or_warn
from openapi_pr_guard.schema_diff import Direction, SchemaDiffer

ParamKey = tuple[str, str]  # (in, name)


class ParametersRule:
    name = "parameters"

    def check(self, context: DiffContext) -> Iterable[Change]:
        for pair in context.iter_operation_pairs():
            yield from self._check_pair(context, pair)

    def _check_pair(self, context: DiffContext, pair: OperationPair) -> list[Change]:
        changes: list[Change] = []
        method, path = pair.method_upper, pair.path
        base_params = _collect(context.base_resolver, pair.base_path_item, pair.base, path, method, changes)
        head_params = _collect(context.head_resolver, pair.head_path_item, pair.head, path, method, changes)

        for key in sorted(base_params.keys() - head_params.keys()):
            changes.append(
                Change(
                    Severity.BREAKING,
                    "parameter.removed",
                    path,
                    f"Request parameter removed: {_label(key)}",
                    method,
                    _loc(key),
                )
            )

        for key in sorted(head_params.keys() - base_params.keys()):
            if _is_required(head_params[key]):
                changes.append(
                    Change(
                        Severity.BREAKING,
                        "parameter.required-added",
                        path,
                        f"New required request parameter: {_label(key)}",
                        method,
                        _loc(key),
                    )
                )
            else:
                changes.append(
                    Change(
                        Severity.NON_BREAKING,
                        "parameter.optional-added",
                        path,
                        f"New optional request parameter: {_label(key)}",
                        method,
                        _loc(key),
                    )
                )

        for key in sorted(base_params.keys() & head_params.keys()):
            old, new = base_params[key], head_params[key]
            was_required, is_required = _is_required(old), _is_required(new)
            if not was_required and is_required:
                changes.append(
                    Change(
                        Severity.BREAKING,
                        "parameter.became-required",
                        path,
                        f"Request parameter became required: {_label(key)}",
                        method,
                        _loc(key),
                    )
                )
            elif was_required and not is_required:
                changes.append(
                    Change(
                        Severity.NON_BREAKING,
                        "parameter.became-optional",
                        path,
                        f"Request parameter became optional: {_label(key)}",
                        method,
                        _loc(key),
                    )
                )
            differ = SchemaDiffer(context.base_resolver, context.head_resolver, path, method, Direction.REQUEST)
            differ.compare(old.get("schema"), new.get("schema"), f"{_loc(key)}.schema")
            changes.extend(differ.changes)
        return changes


def _collect(
    resolver: Resolver,
    path_item: dict[str, Any],
    operation: dict[str, Any],
    path: str,
    method: str,
    changes: list[Change],
) -> dict[ParamKey, dict[str, Any]]:
    """Path-level parameters overridden by operation-level ones, keyed by (in, name)."""
    result: dict[ParamKey, dict[str, Any]] = {}
    for source in (path_item.get("parameters"), operation.get("parameters")):
        if not isinstance(source, list):
            continue
        for index, raw in enumerate(source):
            param = resolve_or_warn(
                resolver, raw, path=path, method=method, location=f"parameters[{index}]", changes=changes
            )
            if isinstance(param, dict) and isinstance(param.get("name"), str) and isinstance(param.get("in"), str):
                result[(param["in"], param["name"])] = param
    return result


def _is_required(param: dict[str, Any]) -> bool:
    return param.get("in") == "path" or param.get("required") is True


def _label(key: ParamKey) -> str:
    location, name = key
    return f"{name} ({location})"


def _loc(key: ParamKey) -> str:
    location, name = key
    return f"parameters.{location}.{name}"
