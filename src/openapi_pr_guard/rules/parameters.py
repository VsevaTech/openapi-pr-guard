"""Rules for request parameters (query, path, header, cookie)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from openapi_pr_guard.diff import DiffContext, OperationPair
from openapi_pr_guard.loader import Resolver, UnresolvableRef
from openapi_pr_guard.models import Change, Severity
from openapi_pr_guard.rules.base import resolve_or_warn
from openapi_pr_guard.schema_diff import Direction, SchemaDiffer, type_set

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
            changes.extend(_serialization_changes(context, key, old, new, path, method))
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


# OpenAPI defaults: form for query/cookie, simple for path/header; explode defaults to true only for form.
_DEFAULT_STYLE = {"query": "form", "cookie": "form", "path": "simple", "header": "simple"}
# Styles that change how even a primitive value is written (".5", ";id=5").
_PRIMITIVE_SENSITIVE_STYLES = frozenset({"label", "matrix"})


def _serialization_changes(
    context: DiffContext, key: ParamKey, old: dict[str, Any], new: dict[str, Any], path: str, method: str
) -> list[Change]:
    """``style`` / ``explode`` / ``allowReserved``: same value, different bytes on the wire.

    ``merchant_ids=a&merchant_ids=b`` (form, explode) vs ``merchant_ids=a,b`` (form, no explode)
    is breaking for arrays and objects, and a no-op for a primitive. When the kind of value
    cannot be determined, the change is reported as a WARNING.
    """
    if "schema" not in old or "schema" not in new:
        return []  # `content`-serialized parameters are compared through their media types
    changes: list[Change] = []
    location = key[0]
    old_style = old.get("style", _DEFAULT_STYLE.get(location))
    new_style = new.get("style", _DEFAULT_STYLE.get(location))
    old_explode = old.get("explode", old_style == "form")
    new_explode = new.get("explode", new_style == "form")
    kind = _value_kind(context, old.get("schema"), new.get("schema"))

    def add(severity: Severity, rule: str, message: str, field: str) -> None:
        changes.append(Change(severity, f"parameter.{rule}", path, message, method, f"{_loc(key)}.{field}"))

    if old_style != new_style:
        if kind == "composite" or {old_style, new_style} & _PRIMITIVE_SENSITIVE_STYLES:
            add(
                Severity.BREAKING,
                "style-changed",
                f"Parameter serialization style changed: {_label(key)}: {old_style} -> {new_style}",
                "style",
            )
        elif kind == "unknown":
            add(
                Severity.WARNING,
                "style-changed",
                f"Parameter serialization style changed: {_label(key)}: {old_style} -> {new_style}",
                "style",
            )
    if old_explode != new_explode:
        message = f"Parameter explode changed: {_label(key)}: {str(old_explode).lower()} -> {str(new_explode).lower()}"
        if kind == "composite":
            add(Severity.BREAKING, "explode-changed", message, "explode")
        elif kind == "unknown":
            add(Severity.WARNING, "explode-changed", message, "explode")
    if location == "query" and old.get("allowReserved") is True and new.get("allowReserved") is not True:
        add(
            Severity.WARNING,
            "allow-reserved-removed",
            f"Parameter no longer allows unencoded reserved characters: {_label(key)}",
            "allowReserved",
        )
    return changes


def _value_kind(context: DiffContext, old_schema: Any, new_schema: Any) -> str:
    """``composite`` (array/object on either side), ``primitive`` or ``unknown``."""
    kinds = set()
    for resolver, schema in ((context.base_resolver, old_schema), (context.head_resolver, new_schema)):
        try:
            resolved = resolver.resolve(schema).value
        except UnresolvableRef:
            return "unknown"
        types = type_set(resolved) if isinstance(resolved, dict) else None
        if not types:
            kinds.add("unknown")
        elif types & {"array", "object"}:
            return "composite"
        else:
            kinds.add("primitive")
    return "unknown" if "unknown" in kinds else "primitive"


def _is_required(param: dict[str, Any]) -> bool:
    return param.get("in") == "path" or param.get("required") is True


def _label(key: ParamKey) -> str:
    location, name = key
    return f"{name} ({location})"


def _loc(key: ParamKey) -> str:
    location, name = key
    return f"parameters.{location}.{name}"
