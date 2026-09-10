"""Human-oriented renderers for a :class:`DiffResult`: text, Markdown and JSON."""

from __future__ import annotations

import json
from collections.abc import Callable

from openapi_pr_guard.models import Change, DiffResult, Severity

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
    if not result.changes:
        lines += ["", "No changes detected."]
        return "\n".join(lines) + "\n"

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
    if not result.changes:
        lines += ["", "_No changes detected in the specification._"]
        return "\n".join(lines) + "\n"

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


RENDERERS: dict[str, Callable[[DiffResult], str]] = {
    "text": render_text,
    "markdown": render_markdown,
    "json": render_json,
}


def _endpoint(change: Change) -> str:
    return f"{change.method} {change.path}" if change.method else change.path
