"""Recursive comparison of two JSON Schema objects.

The same structural change means different things depending on the direction
of the data: a new required property in a *request* body breaks clients that
do not send it, while a new required property in a *response* is harmless.
:class:`Direction` captures that.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openapi_pr_guard.loader import Resolver, UnresolvableRef
from openapi_pr_guard.models import Change, Severity

COMPOSITION_KEYS = ("allOf", "oneOf", "anyOf", "not", "discriminator")
CONSTRAINT_KEYS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "const",
)


class Direction(Enum):
    REQUEST = "request"
    RESPONSE = "response"


@dataclass(slots=True)
class SchemaDiffer:
    """Compares schemas for one operation and accumulates :class:`Change` objects."""

    base_resolver: Resolver
    head_resolver: Resolver
    path: str
    method: str | None
    direction: Direction
    changes: list[Change] = field(default_factory=list)
    _visited: set[tuple[str, str]] = field(default_factory=set)

    # -- public -----------------------------------------------------------

    def compare(self, old: Any, new: Any, location: str) -> None:
        if old is None and new is None:
            return
        if old is None or new is None:
            what = "added" if old is None else "removed"
            self._add(Severity.WARNING, "schema.changed", location, f"Schema {what}")
            return

        try:
            old_resolved = self.base_resolver.resolve(old)
            new_resolved = self.head_resolver.resolve(new)
        except UnresolvableRef as exc:
            self._add(Severity.WARNING, "schema.unresolvable-ref", location, str(exc))
            return

        key = _ref_key(old, new)
        if key is not None and key in self._visited:
            return  # recursive schema: already being compared higher up this branch

        if new_resolved.name:
            location = f"{location}[{new_resolved.name}]"
        old_schema, new_schema = old_resolved.value, new_resolved.value
        if not isinstance(old_schema, dict) or not isinstance(new_schema, dict):
            if old_schema != new_schema:
                self._add(Severity.WARNING, "schema.changed", location, "Schema changed")
            return

        if key is not None:
            self._visited.add(key)
        try:
            self._compare_dicts(old_schema, new_schema, location)
        finally:
            if key is not None:
                self._visited.discard(key)

    # -- comparison steps -------------------------------------------------

    def _compare_dicts(self, old: dict[str, Any], new: dict[str, Any], location: str) -> None:
        if self._compare_types(old, new, location) is _Stop.STOP:
            return
        if self._compare_composition(old, new, location) is _Stop.STOP:
            return
        self._compare_enum(old, new, location)
        self._compare_format_and_constraints(old, new, location)
        self._compare_properties(old, new, location)
        self._compare_additional_properties(old, new, location)
        self.compare(old.get("items"), new.get("items"), f"{location}.items")

    def _compare_types(self, old: dict[str, Any], new: dict[str, Any], location: str) -> _Stop:
        old_types, new_types = type_set(old), type_set(new)
        if old_types is None or new_types is None or old_types == new_types:
            return _Stop.CONTINUE

        old_label, new_label = _types_label(old_types), _types_label(new_types)
        if self.direction is Direction.REQUEST and new_types > old_types:
            self._add(
                Severity.NON_BREAKING,
                "schema.type-widened",
                location,
                f"Request type widened: {old_label} -> {new_label}",
            )
            return _Stop.CONTINUE
        if self.direction is Direction.RESPONSE and new_types < old_types:
            self._add(
                Severity.NON_BREAKING,
                "schema.type-narrowed",
                location,
                f"Response type narrowed: {old_label} -> {new_label}",
            )
            return _Stop.CONTINUE

        self._add(
            Severity.BREAKING,
            "schema.type-changed",
            location,
            f"{self._side} type changed: {old_label} -> {new_label}",
        )
        return _Stop.STOP  # nested comparison is meaningless after a type change

    def _compare_composition(self, old: dict[str, Any], new: dict[str, Any], location: str) -> _Stop:
        changed = [k for k in COMPOSITION_KEYS if old.get(k) != new.get(k)]
        if not changed:
            return _Stop.CONTINUE
        self._add(
            Severity.WARNING,
            "schema.composition-changed",
            location,
            f"Composed schema changed ({', '.join(changed)}) and could not be safely classified",
        )
        return _Stop.STOP  # anything below is already covered by the warning

    def _compare_enum(self, old: dict[str, Any], new: dict[str, Any], location: str) -> None:
        old_enum, new_enum = old.get("enum"), new.get("enum")
        if old_enum == new_enum:
            return
        if not isinstance(old_enum, list) and not isinstance(new_enum, list):
            return

        if old_enum is None:  # enum introduced: a new restriction
            if self.direction is Direction.REQUEST:
                self._add(
                    Severity.BREAKING,
                    "schema.enum-added",
                    location,
                    f"Request field now restricted to enum: {_values(new_enum)}",
                )
            return
        if new_enum is None:  # enum dropped: restriction lifted
            if self.direction is Direction.RESPONSE:
                self._add(
                    Severity.WARNING,
                    "schema.enum-removed",
                    location,
                    "Response field is no longer restricted to an enum",
                )
            return

        removed = [v for v in old_enum if v not in new_enum]
        added = [v for v in new_enum if v not in old_enum]
        if removed:
            severity = Severity.BREAKING if self.direction is Direction.REQUEST else Severity.NON_BREAKING
            self._add(severity, "schema.enum-values-removed", location, f"Enum values removed: {_values(removed)}")
        if added:
            severity = Severity.WARNING if self.direction is Direction.RESPONSE else Severity.NON_BREAKING
            note = " (clients may not handle them)" if self.direction is Direction.RESPONSE else ""
            self._add(severity, "schema.enum-values-added", location, f"Enum values added{note}: {_values(added)}")

    def _compare_format_and_constraints(self, old: dict[str, Any], new: dict[str, Any], location: str) -> None:
        if old.get("format") != new.get("format"):
            self._add(
                Severity.WARNING,
                "schema.format-changed",
                location,
                f"Format changed: {old.get('format')} -> {new.get('format')}",
            )
        changed = [k for k in CONSTRAINT_KEYS if old.get(k) != new.get(k)]
        if changed:
            details = ", ".join(f"{k}: {old.get(k)!r} -> {new.get(k)!r}" for k in changed)
            self._add(Severity.WARNING, "schema.constraint-changed", location, f"Constraint changed ({details})")

    def _compare_properties(self, old: dict[str, Any], new: dict[str, Any], location: str) -> None:
        old_props = _properties(old)
        new_props = _properties(new)
        old_required = _required(old)
        new_required = _required(new)

        for name in old_props.keys() - new_props.keys():
            loc = f"{location}.properties.{name}"
            if self.direction is Direction.RESPONSE:
                self._add(Severity.BREAKING, "schema.property-removed", loc, f"Response property removed: {name}")
            else:
                self._add(
                    Severity.WARNING,
                    "schema.property-removed",
                    loc,
                    f"Request property removed: {name} (clients still sending it may be rejected)",
                )

        for name in new_props.keys() - old_props.keys():
            loc = f"{location}.properties.{name}"
            if self.direction is Direction.REQUEST and name in new_required:
                self._add(
                    Severity.BREAKING, "schema.required-property-added", loc, f"New required request property: {name}"
                )
            else:
                kind = "Optional request" if self.direction is Direction.REQUEST else "Response"
                self._add(Severity.NON_BREAKING, "schema.property-added", loc, f"{kind} property added: {name}")

        for name in sorted(old_props.keys() & new_props.keys()):
            loc = f"{location}.properties.{name}"
            self._compare_required_flag(name, name in old_required, name in new_required, loc)
            self.compare(old_props[name], new_props[name], loc)

    def _compare_required_flag(self, name: str, was_required: bool, is_required: bool, loc: str) -> None:
        if was_required == is_required:
            return
        if self.direction is Direction.REQUEST:
            if is_required:
                self._add(
                    Severity.BREAKING,
                    "schema.property-became-required",
                    loc,
                    f"Request property became required: {name}",
                )
            else:
                self._add(
                    Severity.NON_BREAKING,
                    "schema.property-became-optional",
                    loc,
                    f"Request property became optional: {name}",
                )
        elif is_required:
            self._add(
                Severity.NON_BREAKING,
                "schema.property-became-required",
                loc,
                f"Response property became required: {name}",
            )
        else:
            self._add(
                Severity.WARNING,
                "schema.property-became-optional",
                loc,
                f"Response property is no longer required and may be absent: {name}",
            )

    def _compare_additional_properties(self, old: dict[str, Any], new: dict[str, Any], location: str) -> None:
        old_ap, new_ap = old.get("additionalProperties"), new.get("additionalProperties")
        loc = f"{location}.additionalProperties"
        if isinstance(old_ap, dict) and isinstance(new_ap, dict):
            self.compare(old_ap, new_ap, loc)
        elif old_ap != new_ap:
            self._add(
                Severity.WARNING,
                "schema.constraint-changed",
                loc,
                f"additionalProperties changed: {_short(old_ap)} -> {_short(new_ap)}",
            )

    # -- helpers ----------------------------------------------------------

    @property
    def _side(self) -> str:
        return "Request" if self.direction is Direction.REQUEST else "Response"

    def _add(self, severity: Severity, rule_id: str, location: str, message: str) -> None:
        self.changes.append(
            Change(
                severity=severity,
                rule_id=rule_id,
                path=self.path,
                method=self.method,
                location=location,
                message=message,
            )
        )


class _Stop(Enum):
    CONTINUE = 0
    STOP = 1


def _ref_key(old: Any, new: Any) -> tuple[str, str] | None:
    """Identity of a (base ref, head ref) pair, used to stop infinite recursion."""
    old_ref = old.get("$ref") if isinstance(old, dict) else None
    new_ref = new.get("$ref") if isinstance(new, dict) else None
    if old_ref is None and new_ref is None:
        return None
    return (str(old_ref), str(new_ref))


def type_set(schema: dict[str, Any]) -> frozenset[str] | None:
    """Normalise ``type`` (string, 3.1 list, ``nullable``) into a set. ``None`` when unknown."""
    raw = schema.get("type")
    if isinstance(raw, str):
        types = {raw}
    elif isinstance(raw, list):
        types = {t for t in raw if isinstance(t, str)}
    elif "properties" in schema:
        types = {"object"}
    elif "items" in schema:
        types = {"array"}
    else:
        return None
    if schema.get("nullable") is True:
        types.add("null")
    return frozenset(types)


def _types_label(types: frozenset[str]) -> str:
    return "|".join(sorted(types))


def _properties(schema: dict[str, Any]) -> dict[str, Any]:
    props = schema.get("properties")
    return props if isinstance(props, dict) else {}


def _required(schema: dict[str, Any]) -> set[str]:
    required = schema.get("required")
    return {r for r in required if isinstance(r, str)} if isinstance(required, list) else set()


def _values(values: list[Any]) -> str:
    return ", ".join(str(v) for v in values)


def _short(value: Any) -> str:
    return "<schema>" if isinstance(value, dict) else repr(value)
