"""Rules for the documented authentication / authorization contract of an operation.

OpenAPI security semantics, as modelled here:

* The *effective* security of an operation is its own ``security`` array when
  present, otherwise the root-level ``security`` array. Operation-level
  ``security`` replaces the root requirement entirely; ``security: []`` is an
  explicit "anonymous access allowed", not "no information".
* Each Security Requirement Object in the array is an *alternative* (OR).
* The schemes inside one Security Requirement Object are all required (AND),
  each with its own set of required scopes.
* An empty Security Requirement Object (``{}``) makes authentication optional.

A client that could call an operation before the change holds the credentials
of at least one old alternative. It keeps access iff some new alternative
asks for a subset of those credentials (same schemes, subset of scopes). That
single ``covers`` relation drives every classification below; the per-scheme
findings only *explain* which scheme or scope made the difference.

Scheme definitions (``components.securitySchemes``) are compared only for the
schemes an operation actually uses in both specs: type, API-key name and
location, HTTP scheme and bearer format, OAuth flows and the OIDC URL.
Documentation fields (``description``, scope descriptions) are ignored.

This rule reads the contract only — it never contacts an identity provider.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from openapi_pr_guard.diff import DiffContext, OperationPair
from openapi_pr_guard.loader import UnresolvableRef
from openapi_pr_guard.models import Change, Severity
from openapi_pr_guard.rules.base import resolve_or_warn

OAUTH_TYPES = frozenset({"oauth2", "openIdConnect"})
OAUTH_FLOW_URLS = ("authorizationUrl", "tokenUrl", "refreshUrl")


@dataclass(frozen=True, slots=True, order=True)
class SchemeUse:
    """One scheme inside a Security Requirement Object, with the scopes it requires."""

    name: str
    scopes: frozenset[str]


# AND: every scheme in the requirement must be satisfied.
Requirement = frozenset[SchemeUse]


@dataclass(frozen=True, slots=True)
class EffectiveSecurity:
    """OR of AND-requirements. ``inherited`` tells whether it came from the root."""

    alternatives: frozenset[Requirement]
    inherited: bool

    @property
    def is_public(self) -> bool:
        """No requirement at all, or an explicit empty ``{}`` alternative."""
        return not self.alternatives or frozenset() in self.alternatives

    def scheme_names(self) -> set[str]:
        return {use.name for requirement in self.alternatives for use in requirement}


def normalize_security(raw: Any) -> frozenset[Requirement] | None:
    """Turn a ``security`` array into a set of requirements; ``None`` if absent/malformed."""
    if not isinstance(raw, list):
        return None
    alternatives: set[Requirement] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        uses = frozenset(
            SchemeUse(str(name), frozenset(str(s) for s in scopes) if isinstance(scopes, list) else frozenset())
            for name, scopes in entry.items()
        )
        alternatives.add(uses)
    return frozenset(alternatives)


def effective_security(document: dict[str, Any], operation: dict[str, Any]) -> EffectiveSecurity:
    """Operation ``security`` if present (even ``[]``), otherwise the root ``security``."""
    own = normalize_security(operation.get("security"))
    if own is not None:
        return EffectiveSecurity(own, inherited=False)
    root = normalize_security(document.get("security"))
    return EffectiveSecurity(root or frozenset(), inherited=True)


def covers(credentials: Requirement, requirement: Requirement) -> bool:
    """Does a client holding ``credentials`` satisfy ``requirement``?"""
    held = {use.name: use.scopes for use in credentials}
    return all(use.name in held and use.scopes <= held[use.name] for use in requirement)


class SecurityRule:
    name = "security"

    def check(self, context: DiffContext) -> Iterable[Change]:
        for pair in context.iter_operation_pairs():
            yield from self._check_pair(context, pair)

    def _check_pair(self, context: DiffContext, pair: OperationPair) -> list[Change]:
        old = effective_security(context.base, pair.base)
        new = effective_security(context.head, pair.head)
        schemes = _Schemes(context, pair)
        changes = compare_security(old, new, pair.path, pair.method_upper, schemes.type_of)
        changes += schemes.compare_used(old.scheme_names() & new.scheme_names())
        return changes


def compare_security(
    old: EffectiveSecurity,
    new: EffectiveSecurity,
    path: str,
    method: str,
    type_of: Callable[[str], str | None] = lambda _name: None,
) -> list[Change]:
    """Classify the difference between two effective security requirements."""
    changes: list[Change] = []

    def add(severity: Severity, rule: str, message: str, location: str = "security") -> None:
        changes.append(Change(severity, f"security.{rule}", path, message, method, location))

    if old.is_public and new.is_public:
        return changes
    if old.is_public:
        add(Severity.BREAKING, "authentication-required", "Authentication is now required")
        return changes
    if new.is_public:
        add(
            Severity.WARNING,
            "authentication-removed",
            "Authentication requirement removed (anonymous access is now allowed)",
        )
        return changes

    old_alts, new_alts = _ordered(old.alternatives), _ordered(new.alternatives)

    # Clients of an old alternative that no new alternative accepts lose access.
    for requirement in old_alts:
        if any(covers(requirement, candidate) for candidate in new_alts):
            continue
        match = _closest(requirement, new_alts)
        if match is None:
            add(
                Severity.BREAKING,
                "alternative-removed",
                f"Authentication alternative removed: {_label(requirement)}",
                _loc(requirement),
            )
            continue
        old_by_name, new_by_name = _by_name(requirement), _by_name(match)
        for name in sorted(new_by_name.keys() - old_by_name.keys()):
            add(
                Severity.BREAKING,
                "requirement-added",
                f"Additional authentication requirement: {_use_label(new_by_name[name])}",
                f"security.{name}",
            )
        for name in sorted(new_by_name.keys() & old_by_name.keys()):
            added = new_by_name[name].scopes - old_by_name[name].scopes
            if added:
                add(
                    Severity.BREAKING,
                    "scope-added",
                    f"Required {_scope_noun(type_of(name), len(added))} added: {_list(added)}",
                    f"security.{name}",
                )

    # New alternatives that some clients could not satisfy before: access widened.
    for requirement in new_alts:
        if any(covers(requirement, candidate) for candidate in old_alts):
            continue
        match = _closest(requirement, old_alts)
        if match is None:
            add(
                Severity.NON_BREAKING,
                "alternative-added",
                f"Authentication alternative added: {_label(requirement)}",
                _loc(requirement),
            )
            continue
        old_by_name, new_by_name = _by_name(match), _by_name(requirement)
        for name in sorted(old_by_name.keys() - new_by_name.keys()):
            add(
                Severity.WARNING,
                "requirement-removed",
                f"Authentication requirement no longer needed: {_use_label(old_by_name[name])}",
                f"security.{name}",
            )
        for name in sorted(new_by_name.keys() & old_by_name.keys()):
            removed = old_by_name[name].scopes - new_by_name[name].scopes
            if removed:
                add(
                    Severity.WARNING,
                    "scope-removed",
                    f"{_scope_noun(type_of(name), len(removed), capital=True)} requirement removed: {_list(removed)}",
                    f"security.{name}",
                )
    return changes


class _Schemes:
    """``components.securitySchemes`` of both specs, resolved on demand for one operation."""

    def __init__(self, context: DiffContext, pair: OperationPair) -> None:
        self._context = context
        self._pair = pair
        self._base = _raw_schemes(context.base)
        self._head = _raw_schemes(context.head)

    def type_of(self, name: str) -> str | None:
        for resolver, schemes in ((self._context.head_resolver, self._head), (self._context.base_resolver, self._base)):
            if name in schemes:
                try:
                    scheme = resolver.resolve(schemes[name]).value
                except UnresolvableRef:
                    continue
                if isinstance(scheme, dict) and isinstance(scheme.get("type"), str):
                    return scheme["type"]
        return None

    def compare_used(self, names: set[str]) -> list[Change]:
        changes: list[Change] = []
        path, method = self._pair.path, self._pair.method_upper
        for name in sorted(names):
            if name not in self._base:
                continue  # undefined before as well: nothing reliable to compare
            location = f"components.securitySchemes.{name}"
            if name not in self._head:
                changes.append(
                    Change(
                        Severity.BREAKING,
                        "security.scheme-removed",
                        path,
                        f"Security scheme removed: {name}",
                        method,
                        location,
                    )
                )
                continue
            old = resolve_or_warn(
                self._context.base_resolver,
                self._base[name],
                path=path,
                method=method,
                location=location,
                changes=changes,
            )
            new = resolve_or_warn(
                self._context.head_resolver,
                self._head[name],
                path=path,
                method=method,
                location=location,
                changes=changes,
            )
            if isinstance(old, dict) and isinstance(new, dict):
                changes.extend(compare_security_scheme(name, old, new, path, method))
        return changes


def compare_security_scheme(
    name: str, old: dict[str, Any], new: dict[str, Any], path: str, method: str
) -> Iterator[Change]:
    """Compare two definitions of the same, still used security scheme."""
    base_loc = f"components.securitySchemes.{name}"

    def change(severity: Severity, rule: str, message: str, field: str) -> Change:
        return Change(severity, f"security.{rule}", path, message, method, f"{base_loc}.{field}")

    old_type, new_type = old.get("type"), new.get("type")
    if old_type != new_type:
        yield change(
            Severity.BREAKING, "scheme-type-changed", f"Security scheme type changed: {old_type} -> {new_type}", "type"
        )
        return

    if old_type == "apiKey":
        old_in, new_in = old.get("in"), new.get("in")
        old_name, new_name = old.get("name"), new.get("name")
        if old_in != new_in:
            yield change(
                Severity.BREAKING, "apikey-location-changed", f"API key location changed: {old_in} -> {new_in}", "in"
            )
        if old_name != new_name and not _same_header(old_in, new_in, old_name, new_name):
            yield change(
                Severity.BREAKING,
                "apikey-name-changed",
                f"API key {new_in or 'name'} changed: {old_name} -> {new_name}",
                "name",
            )
    elif old_type == "http":
        old_scheme, new_scheme = _lower(old.get("scheme")), _lower(new.get("scheme"))
        if old_scheme != new_scheme:
            yield change(
                Severity.BREAKING,
                "http-scheme-changed",
                f"HTTP authentication scheme changed: {old_scheme} -> {new_scheme}",
                "scheme",
            )
        elif old.get("bearerFormat") != new.get("bearerFormat"):
            yield change(
                Severity.WARNING,
                "bearer-format-changed",
                f"Bearer token format changed: {old.get('bearerFormat')} -> {new.get('bearerFormat')}",
                "bearerFormat",
            )
    elif old_type == "oauth2":
        yield from _compare_flows(old, new, change)
    elif old_type == "openIdConnect" and old.get("openIdConnectUrl") != new.get("openIdConnectUrl"):
        yield change(
            Severity.WARNING,
            "oidc-url-changed",
            f"OpenID Connect discovery URL changed: {old.get('openIdConnectUrl')} -> {new.get('openIdConnectUrl')}",
            "openIdConnectUrl",
        )


def _compare_flows(old: dict[str, Any], new: dict[str, Any], change: Any) -> Iterator[Change]:
    old_flows, new_flows = _mapping(old.get("flows")), _mapping(new.get("flows"))
    for flow in sorted(old_flows.keys() - new_flows.keys()):
        # The documented way to obtain a token disappeared; the IdP may still
        # support it at runtime, so this is flagged for review, not failed.
        yield change(Severity.WARNING, "oauth-flow-removed", f"OAuth flow removed: {flow}", f"flows.{flow}")
    for flow in sorted(old_flows.keys() & new_flows.keys()):
        old_flow, new_flow = _mapping(old_flows[flow]), _mapping(new_flows[flow])
        for url in OAUTH_FLOW_URLS:
            if old_flow.get(url) != new_flow.get(url):
                yield change(
                    Severity.WARNING,
                    "oauth-flow-changed",
                    f"OAuth {flow} {url} changed: {old_flow.get(url)} -> {new_flow.get(url)}",
                    f"flows.{flow}.{url}",
                )


# -- helpers ----------------------------------------------------------------


def _raw_schemes(document: dict[str, Any]) -> dict[str, Any]:
    schemes = _mapping(_mapping(document.get("components")).get("securitySchemes"))
    return {str(name): raw for name, raw in schemes.items()}


def _ordered(alternatives: frozenset[Requirement]) -> list[Requirement]:
    return sorted(alternatives, key=lambda r: sorted((u.name, sorted(u.scopes)) for u in r))


def _closest(requirement: Requirement, candidates: list[Requirement]) -> Requirement | None:
    """The candidate sharing the most scheme names (first in order on ties); ``None`` if none share any."""
    names = {use.name for use in requirement}
    best, best_overlap = None, 0
    for candidate in candidates:
        overlap = len(names & {use.name for use in candidate})
        if overlap > best_overlap:
            best, best_overlap = candidate, overlap
    return best


def _by_name(requirement: Requirement) -> dict[str, SchemeUse]:
    return {use.name: use for use in requirement}


def _use_label(use: SchemeUse) -> str:
    return f"{use.name} [{_list(use.scopes)}]" if use.scopes else use.name


def _label(requirement: Requirement) -> str:
    return " + ".join(_use_label(use) for use in sorted(requirement))


def _loc(requirement: Requirement) -> str:
    return "security." + "+".join(sorted(use.name for use in requirement))


def _list(values: Iterable[str]) -> str:
    return ", ".join(sorted(values))


def _scope_noun(scheme_type: str | None, count: int, capital: bool = False) -> str:
    # Scopes are OAuth/OIDC-only in 3.0; 3.1 allows role names for other types.
    oauth = scheme_type is None or scheme_type in OAUTH_TYPES
    noun = ("OAuth scope" if oauth else "scope") + ("s" if count != 1 else "")
    if capital and not oauth:
        noun = noun.capitalize()
    return noun


def _same_header(old_in: Any, new_in: Any, old_name: Any, new_name: Any) -> bool:
    """HTTP header names are case-insensitive: ``X-API-Key`` -> ``x-api-key`` is not a change on the wire."""
    return (
        old_in == new_in == "header"
        and isinstance(old_name, str)
        and isinstance(new_name, str)
        and old_name.lower() == new_name.lower()
    )


def _lower(value: Any) -> Any:
    return value.lower() if isinstance(value, str) else value


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
