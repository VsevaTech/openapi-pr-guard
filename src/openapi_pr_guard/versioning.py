"""Optional version policy: does ``info.version`` move in step with the contract?

The diff engine says *what* changed; this module says whether the version
number in the head specification acknowledges it. The policy is deliberately
configurable instead of hard-wiring SemVer:

* ``scheme: semver`` compares ``MAJOR.MINOR.PATCH`` and requires a minimum bump
  per change level (breaking → major, additive → minor, ...).
* ``scheme: any`` only requires the version string to *change* — for teams that
  use dates, build numbers or ``v3``-style labels.

The check never looks at git tags or package metadata: the OpenAPI document is
the contract, so ``info.version`` is the version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from openapi_pr_guard.models import Change, Severity


class Bump(StrEnum):
    NONE = "none"
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"

    @property
    def rank(self) -> int:
        return _BUMP_RANK[self]


_BUMP_RANK = {Bump.NONE: 0, Bump.PATCH: 1, Bump.MINOR: 2, Bump.MAJOR: 3}


class ChangeLevel(StrEnum):
    """The most significant kind of change in a diff, used to pick the required bump."""

    NONE = "none"
    DOCS = "docs"
    NON_BREAKING = "non_breaking"
    WARNING = "warning"
    BREAKING = "breaking"


class Scheme(StrEnum):
    SEMVER = "semver"
    ANY = "any"


class PolicySeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


# Changes that do not touch the wire contract at all. Everything else that is
# NON-BREAKING (new endpoints, optional fields, deprecations, ...) is "additive".
DOC_RULES = frozenset({"operation.docs-changed"})

DEFAULT_REQUIREMENTS: dict[ChangeLevel, Bump] = {
    ChangeLevel.BREAKING: Bump.MAJOR,
    ChangeLevel.WARNING: Bump.MINOR,
    ChangeLevel.NON_BREAKING: Bump.MINOR,
    ChangeLevel.DOCS: Bump.NONE,
}


@dataclass(slots=True)
class VersionPolicy:
    enabled: bool = False
    scheme: Scheme = Scheme.SEMVER
    severity: PolicySeverity = PolicySeverity.ERROR
    require: dict[ChangeLevel, Bump] = field(default_factory=lambda: dict(DEFAULT_REQUIREMENTS))
    # SemVer §4: 0.y.z is initial development. "shift" (the Cargo convention) lets
    # a minor bump stand in for major and a patch bump for minor while MAJOR is 0.
    pre_1_0: str = "shift"

    def required_for(self, level: ChangeLevel) -> Bump:
        return Bump.NONE if level is ChangeLevel.NONE else self.require.get(level, Bump.NONE)


@dataclass(frozen=True, slots=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    _PATTERN = re.compile(
        r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
        r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
        r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
    )

    @classmethod
    def parse(cls, text: str) -> SemVer | None:
        match = cls._PATTERN.match(text.strip())
        if not match:
            return None
        pre = tuple(match["pre"].split(".")) if match["pre"] else ()
        return cls(int(match["major"]), int(match["minor"]), int(match["patch"]), pre)

    @property
    def core(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    @property
    def core_str(self) -> str:
        return ".".join(map(str, self.core))

    def precedence_key(self) -> tuple[Any, ...]:
        """SemVer §11 ordering; a release sorts after its pre-releases."""
        if not self.prerelease:
            return (*self.core, 1, ())
        ids = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in self.prerelease)
        return (*self.core, 0, ids)


@dataclass(frozen=True, slots=True)
class Violation:
    rule_id: str
    message: str


@dataclass(slots=True)
class VersionCheck:
    """Outcome of applying a :class:`VersionPolicy` to one diff."""

    base_version: str | None
    head_version: str | None
    scheme: Scheme
    severity: PolicySeverity
    change_level: ChangeLevel
    required_bump: Bump
    actual_bump: Bump | None
    """``None`` when the bump cannot be determined (unparseable or missing version)."""
    violations: list[Violation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def blocking(self) -> bool:
        return bool(self.violations) and self.severity is PolicySeverity.ERROR

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "severity": self.severity.value,
            "scheme": self.scheme.value,
            "base_version": self.base_version,
            "head_version": self.head_version,
            "change_level": self.change_level.value,
            "required_bump": self.required_bump.value,
            "actual_bump": self.actual_bump.value if self.actual_bump else None,
            "violations": [{"rule_id": v.rule_id, "message": v.message} for v in self.violations],
            "notes": list(self.notes),
        }


def change_level(changes: list[Change]) -> ChangeLevel:
    if not changes:
        return ChangeLevel.NONE
    severities = {c.severity for c in changes}
    if Severity.BREAKING in severities:
        return ChangeLevel.BREAKING
    if Severity.WARNING in severities:
        return ChangeLevel.WARNING
    if any(c.rule_id not in DOC_RULES for c in changes):
        return ChangeLevel.NON_BREAKING
    return ChangeLevel.DOCS


def check_version(
    base: dict[str, Any],
    head: dict[str, Any],
    changes: list[Change],
    policy: VersionPolicy,
    ignore_rules: frozenset[str] | set[str] = frozenset(),
) -> VersionCheck:
    base_version, head_version = _info_version(base), _info_version(head)
    level = change_level(changes)
    required = policy.required_for(level)
    check = VersionCheck(
        base_version=base_version,
        head_version=head_version,
        scheme=policy.scheme,
        severity=policy.severity,
        change_level=level,
        required_bump=required,
        actual_bump=None,
    )

    def violate(rule_id: str, message: str) -> None:
        if rule_id not in ignore_rules:
            check.violations.append(Violation(rule_id, message))

    if head_version is None:
        if required is not Bump.NONE:
            violate("version.missing", "info.version is missing in the head specification")
        return check

    if policy.scheme is Scheme.ANY:
        changed = base_version != head_version
        check.actual_bump = Bump.MAJOR if changed else Bump.NONE  # "changed" satisfies any requirement
        if required is not Bump.NONE and not changed:
            violate("version.not-bumped", _not_bumped_message(level, head_version, "the version must change"))
        return check

    head_sv = SemVer.parse(head_version)
    if head_sv is None:
        violate("version.invalid", f"info.version '{head_version}' is not a valid semantic version (MAJOR.MINOR.PATCH)")
        return check
    base_sv = SemVer.parse(base_version) if base_version is not None else None
    if base_sv is None:
        # Nothing reliable to compare against (e.g. the base used another scheme).
        check.notes.append(
            f"base info.version {base_version!r} is not a semantic version; only the head version was validated"
        )
        return check

    if head_sv.precedence_key() < base_sv.precedence_key():
        violate("version.downgraded", f"info.version went backwards: {base_version} -> {head_version}")
        return check

    actual = _semver_bump(base_sv, head_sv)
    check.actual_bump = actual

    effective_required = required
    if head_sv.major == 0 and base_sv.major == 0 and policy.pre_1_0 == "shift" and required is not Bump.NONE:
        effective_required = {Bump.MAJOR: Bump.MINOR, Bump.MINOR: Bump.PATCH}.get(required, required)
        if effective_required is not required:
            check.required_bump = effective_required
            check.notes.append(
                f"0.x version: a {effective_required.value} bump is accepted for {level.value.replace('_', '-')} "
                "changes (pre_1_0: shift)"
            )

    if base_sv.prerelease and head_sv.core == base_sv.core and head_sv != base_sv:
        # Iterating on an unreleased version (1.0.0-rc.1 -> 1.0.0-rc.2 / 1.0.0):
        # SemVer §9 gives pre-releases no compatibility promise.
        check.notes.append(f"{base_version} is a pre-release; any forward step within {head_sv.core_str} is accepted")
        return check

    if actual.rank < effective_required.rank:
        if actual is Bump.NONE:
            violate(
                "version.not-bumped",
                _not_bumped_message(level, head_version, f"a {effective_required.value} bump is required"),
            )
        else:
            violate(
                "version.insufficient-bump",
                f"{_level_phrase(level)} require at least a {effective_required.value} version bump, "
                f"but {base_version} -> {head_version} is a {actual.value} bump",
            )
    return check


def _semver_bump(base: SemVer, head: SemVer) -> Bump:
    if head.major != base.major:
        return Bump.MAJOR
    if head.minor != base.minor:
        return Bump.MINOR
    if head.patch != base.patch:
        return Bump.PATCH
    return Bump.NONE


def _info_version(document: dict[str, Any]) -> str | None:
    info = document.get("info")
    if not isinstance(info, dict):
        return None
    version = info.get("version")
    if version is None:
        return None
    return str(version).strip() or None  # YAML may parse `version: 2` as an int


def _level_phrase(level: ChangeLevel) -> str:
    return {
        ChangeLevel.BREAKING: "Breaking changes",
        ChangeLevel.WARNING: "Changes that need review",
        ChangeLevel.NON_BREAKING: "Non-breaking contract changes",
        ChangeLevel.DOCS: "Documentation changes",
    }.get(level, "Changes")


def _not_bumped_message(level: ChangeLevel, version: str, needed: str) -> str:
    return f"{_level_phrase(level)} detected but info.version is still {version}; {needed}"
