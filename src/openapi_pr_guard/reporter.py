"""Human-oriented renderers for a :class:`DiffResult`: text, Markdown, JSON and a PR change summary."""

from __future__ import annotations

import json
from collections.abc import Callable

from openapi_pr_guard.models import Change, DiffResult, Severity
from openapi_pr_guard.summary import ChangedEndpoint, EndpointSummary
from openapi_pr_guard.versioning import Bump, PolicySeverity, Scheme, VersionCheck

TITLE = "OpenAPI PR Guard"

_SECTIONS: tuple[tuple[Severity, str, str], ...] = (
    (Severity.BREAKING, "Breaking changes", "❌"),
    (Severity.WARNING, "Warnings", "⚠️"),
    (Severity.NON_BREAKING, "Non-breaking changes", "✅"),
)


def render_text(result: DiffResult) -> str:
    lines = [
        TITLE,
        "",
        f"BREAKING CHANGES: {len(result.breaking)}",
        f"WARNINGS: {len(result.warnings)}",
        f"NON-BREAKING: {len(result.non_breaking)}",
    ]
    if result.version is not None:
        lines.append(f"VERSION POLICY: {_version_headline(result.version)}")
        for violation in result.version.violations:
            lines.append(f"  {violation.rule_id}: {violation.message}")
    if not result.changes:
        lines += ["", "No changes detected."]
        return "\n".join(lines) + "\n"

    summary = result.endpoint_summary()
    lines += ["", "API CHANGE SUMMARY"]
    for label, endpoints in (("Added", summary.added), ("Removed", summary.removed)):
        if endpoints:
            lines.append(f"{label} ({len(endpoints)}): {', '.join(e.label for e in endpoints)}")
    if summary.changed:
        lines.append(f"Changed ({len(summary.changed)}):")
        lines += [f"- {c.endpoint.label}: {_counts(c, emoji=False)}" for c in summary.changed]

    for severity, _, _ in _SECTIONS:
        changes = sorted(result.by_severity(severity), key=lambda c: c.sort_key)
        if not changes:
            continue
        lines += ["", severity.value]
        for change in changes:
            lines.append(f"- {_endpoint(change)}")
            lines.append(f"  {change.message}")
            if change.location:
                lines.append(f"  {change.location}")
    return "\n".join(lines) + "\n"


def render_markdown(result: DiffResult) -> str:
    breaking = len(result.breaking)
    if breaking:
        verdict = f"❌ **{breaking} breaking change{'s' if breaking != 1 else ''} detected**"
    elif result.warnings:
        verdict = "⚠️ **No breaking changes, but some changes need review**"
    else:
        verdict = "✅ **No breaking changes detected**"
    if result.version is not None and not result.version.ok:
        verdict += f" · {_version_icon(result.version)} **version policy violated**"

    lines = [
        f"## {TITLE}",
        "",
        verdict,
        "",
        "| Severity | Count |",
        "|---|---:|",
        f"| ❌ Breaking | {breaking} |",
        f"| ⚠️ Warning | {len(result.warnings)} |",
        f"| ✅ Non-breaking | {len(result.non_breaking)} |",
    ]
    if result.version is not None:
        lines += ["", *_markdown_version(result.version)]
    if not result.changes:
        lines += ["", "_No changes detected in the specification._"]
        return "\n".join(lines) + "\n"

    lines += ["", *_markdown_endpoint_summary(result.endpoint_summary(), heading="### 🧭 API change summary")]

    for severity, heading, icon in _SECTIONS:
        changes = sorted(result.by_severity(severity), key=lambda c: c.sort_key)
        if not changes:
            continue
        collapsible = severity is Severity.NON_BREAKING
        lines += ["", f"### {icon} {heading} ({len(changes)})", ""]
        if collapsible:
            lines += ["<details>", "<summary>Show non-breaking changes</summary>", ""]
        for change in changes:
            lines.append(f"- **{_endpoint(change)}** — {change.message}")
            if change.location:
                lines.append(f"  `{change.location}`")
        if collapsible:
            lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def render_json(result: DiffResult) -> str:
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n"


def render_change_summary(result: DiffResult, spec: str | None = None) -> str:
    """Compact Markdown block meant for a pull-request description or release notes."""
    lines = ["### API change summary" + (f" — `{spec}`" if spec else ""), ""]
    if result.version is not None:
        check = result.version
        status = "policy satisfied" if check.ok else "; ".join(v.message for v in check.violations)
        lines += [f"**Version:** {_version_transition(check)} — {_version_icon(check)} {status}", ""]
    warnings = len(result.warnings)
    counts = (
        f"❌ {len(result.breaking)} breaking · ⚠️ {warnings} warning{'' if warnings == 1 else 's'} · "
        f"✅ {len(result.non_breaking)} non-breaking"
    )
    lines += [f"**Findings:** {counts}", ""]
    summary = result.endpoint_summary()
    if summary.empty:
        lines.append("_No endpoint changes._")
    else:
        lines += _markdown_endpoint_summary(summary, heading=None)
    return "\n".join(lines).rstrip() + "\n"


RENDERERS: dict[str, Callable[[DiffResult], str]] = {
    "text": render_text,
    "markdown": render_markdown,
    "json": render_json,
    "summary": render_change_summary,
}


def _markdown_endpoint_summary(summary: EndpointSummary, heading: str | None) -> list[str]:
    lines = [heading, ""] if heading else []
    groups = (
        ("➕ Added", [f"`{e.label}`" for e in summary.added]),
        ("➖ Removed", [f"`{e.label}`" for e in summary.removed]),
        ("✏️ Changed", [f"`{c.endpoint.label}` — {_counts(c, emoji=True)}" for c in summary.changed]),
    )
    for title, items in groups:
        if items:
            lines += [f"**{title} ({len(items)})**", "", *(f"- {item}" for item in items), ""]
    return lines[:-1] if lines and lines[-1] == "" else lines


def _counts(changed: ChangedEndpoint, *, emoji: bool) -> str:
    parts = []
    for severity, icon, word in (
        (Severity.BREAKING, "❌", "breaking"),
        (Severity.WARNING, "⚠️", "warning"),
        (Severity.NON_BREAKING, "✅", "non-breaking"),
    ):
        n = changed.count(severity)
        if n:
            noun = word + ("s" if n != 1 and severity is Severity.WARNING else "")
            parts.append(f"{icon} {n} {noun}" if emoji else f"{n} {noun}")
    return (" · " if emoji else ", ").join(parts)


def _markdown_version(check: VersionCheck) -> list[str]:
    requirement = (
        "no bump required"
        if check.required_bump is Bump.NONE
        else f"{check.change_level.value.replace('_', '-')} changes require a **{check.required_bump.value}** bump"
    )
    lines = [
        "### 🏷️ Version policy",
        "",
        f"{_version_icon(check)} {_version_transition(check)} — {requirement}",
    ]
    if check.violations:
        lines.append("")
        lines += [f"- {v.message} (`{v.rule_id}`)" for v in check.violations]
    if check.notes:
        lines.append("")
        lines += [f"- _{note}_" for note in check.notes]
    return lines


def _version_transition(check: VersionCheck) -> str:
    base = f"`{check.base_version}`" if check.base_version else "_missing_"
    head = f"`{check.head_version}`" if check.head_version else "_missing_"
    bump = f" ({check.actual_bump.value})" if check.actual_bump and check.scheme is Scheme.SEMVER else ""
    if check.scheme is Scheme.ANY and check.actual_bump is not None:
        bump = " (changed)" if check.actual_bump is not Bump.NONE else " (unchanged)"
    return f"{base} → {head}{bump}"


def _version_icon(check: VersionCheck) -> str:
    if check.ok:
        return "✅"
    return "❌" if check.severity is PolicySeverity.ERROR else "⚠️"


def _version_headline(check: VersionCheck) -> str:
    transition = f"{check.base_version or '<missing>'} -> {check.head_version or '<missing>'}"
    required = f"{check.required_bump.value} bump required"
    if check.ok:
        return f"OK ({transition}, {required})"
    word = "FAILED" if check.severity is PolicySeverity.ERROR else "WARNING"
    return f"{word} ({transition}, {required})"


def _endpoint(change: Change) -> str:
    return f"{change.method} {change.path}" if change.method else change.path
