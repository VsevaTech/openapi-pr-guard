"""GitHub Actions entry point.

This is the only module that knows about GitHub: it locates the base revision
of the spec with ``git``, writes the report to ``$GITHUB_STEP_SUMMARY``,
exposes step outputs and (optionally) upserts a pull-request comment.
The diff engine itself is untouched by any of this.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from openapi_pr_guard.cli import EXIT_BREAKING, EXIT_ERROR, EXIT_OK
from openapi_pr_guard.config import ConfigError, load_config
from openapi_pr_guard.diff import diff_specs
from openapi_pr_guard.loader import SpecError, load_spec, parse_spec
from openapi_pr_guard.models import DiffResult
from openapi_pr_guard.reporter import render_markdown, render_text

COMMENT_MARKER = "<!-- openapi-pr-guard -->"


def main() -> int:
    spec = os.environ.get("INPUT_SPEC", "").strip()
    if not spec:
        _error("input 'spec' is required")
        return EXIT_ERROR
    fail_on_breaking = _truthy(os.environ.get("INPUT_FAIL_ON_BREAKING", "true"))
    want_comment = _truthy(os.environ.get("INPUT_COMMENT", "false"))

    base_sha = _resolve_base_sha(os.environ.get("INPUT_BASE_REF", "").strip())
    if base_sha is None:
        _error("cannot determine the base revision: not a pull_request event and no 'base-ref' input given")
        return EXIT_ERROR

    try:
        config = load_config(os.environ.get("INPUT_CONFIG") or None)
        head_doc = load_spec(spec)
        base_text = _git_show(base_sha, spec)
        if base_text is None:
            _write_summary(
                f"## OpenAPI PR Guard\n\n`{spec}` does not exist in the base revision — nothing to compare.\n"
            )
            _notice(f"{spec} is new in this PR; skipping comparison")
            return EXIT_OK
        base_doc = parse_spec(base_text, source=f"{base_sha[:12]}:{spec}")
        result = diff_specs(base_doc, head_doc, ignore_rules=config.ignore_rules)
    except (SpecError, ConfigError) as exc:
        _error(str(exc))
        return EXIT_ERROR

    markdown = render_markdown(result)
    sys.stdout.write(render_text(result))
    _write_summary(markdown)
    _write_outputs(result)
    _annotate(result)
    if want_comment:
        _upsert_pr_comment(markdown)

    if result.has_breaking and (fail_on_breaking and config.fail_on_breaking):
        return EXIT_BREAKING
    return EXIT_OK


# -- git ------------------------------------------------------------------


def _resolve_base_sha(base_ref: str) -> str | None:
    if base_ref:
        _git("fetch", "--no-tags", "--depth=1", "origin", base_ref, check=False)
        return (
            _git("rev-parse", f"origin/{base_ref}", check=False)
            or _git("rev-parse", base_ref, check=False)
            or _git("rev-parse", "FETCH_HEAD", check=False)
            or None
        )
    event = _event_payload()
    sha = event.get("pull_request", {}).get("base", {}).get("sha")
    if isinstance(sha, str) and sha:
        _git("fetch", "--no-tags", "--depth=1", "origin", sha, check=False)
        return sha
    github_base_ref = os.environ.get("GITHUB_BASE_REF", "")
    if github_base_ref:
        return _resolve_base_sha(github_base_ref)
    return None


def _git_show(sha: str, path: str) -> str | None:
    """Content of ``path`` at ``sha``; ``None`` when the file did not exist there."""
    completed = subprocess.run(
        ["git", "show", f"{sha}:{path}"], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if completed.returncode != 0:
        if "does not exist" in completed.stderr or "exists on disk, but not in" in completed.stderr:
            return None
        raise SpecError(f"git show {sha[:12]}:{path} failed: {completed.stderr.strip()}")
    return completed.stdout


def _git(*args: str, check: bool) -> str:
    completed = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", check=False)
    if completed.returncode != 0:
        if check:
            raise SpecError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
        return ""
    return completed.stdout.strip()


def _event_payload() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not Path(path).is_file():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# -- GitHub Actions plumbing ------------------------------------------------


def _write_summary(markdown: str) -> None:
    _append_to_env_file("GITHUB_STEP_SUMMARY", markdown)


def _write_outputs(result: DiffResult) -> None:
    lines = (
        f"breaking-count={len(result.breaking)}\n"
        f"warning-count={len(result.warnings)}\n"
        f"non-breaking-count={len(result.non_breaking)}\n"
        f"has-breaking={'true' if result.has_breaking else 'false'}\n"
    )
    _append_to_env_file("GITHUB_OUTPUT", lines)


def _append_to_env_file(variable: str, content: str) -> None:
    path = os.environ.get(variable)
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(content)


def _annotate(result: DiffResult) -> None:
    """Workflow annotations so breaking changes are visible on the Checks tab."""
    for change in result.breaking:
        endpoint = f"{change.method} {change.path}" if change.method else change.path
        _error(f"{endpoint}: {change.message}" + (f" ({change.location})" if change.location else ""))


def _upsert_pr_comment(markdown: str) -> None:
    token = os.environ.get("INPUT_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    number = _event_payload().get("pull_request", {}).get("number")
    if not (token and repo and isinstance(number, int)):
        _warning("PR comment skipped: token, repository or pull request number unavailable")
        return
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    body = f"{COMMENT_MARKER}\n{markdown}"
    try:
        comments = _api(token, "GET", f"{api}/repos/{repo}/issues/{number}/comments?per_page=100")
        existing = next((c for c in comments if COMMENT_MARKER in (c.get("body") or "")), None)
        if existing:
            _api(token, "PATCH", f"{api}/repos/{repo}/issues/comments/{existing['id']}", {"body": body})
        else:
            _api(token, "POST", f"{api}/repos/{repo}/issues/{number}/comments", {"body": body})
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _warning(f"PR comment failed (needs 'pull-requests: write' permission): {exc}")


def _api(token: str, method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "openapi-pr-guard",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed GitHub API host
        return json.loads(response.read().decode("utf-8") or "null")


# -- logging ----------------------------------------------------------------


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _error(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)


def _warning(message: str) -> None:
    print(f"::warning::{message}", file=sys.stderr)


def _notice(message: str) -> None:
    print(f"::notice::{message}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
