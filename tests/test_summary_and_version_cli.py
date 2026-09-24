"""Endpoint summary, the `summary` report format and version-policy wiring in the CLI."""

import json
from pathlib import Path

from openapi_pr_guard.cli import main
from openapi_pr_guard.diff import diff_specs
from openapi_pr_guard.github_action import merge_pr_description, summary_markers
from openapi_pr_guard.loader import load_spec
from openapi_pr_guard.reporter import render_change_summary, render_markdown, render_text
from openapi_pr_guard.versioning import VersionPolicy

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
V1 = str(EXAMPLES / "openapi-v1.yaml")
V2_BREAKING = str(EXAMPLES / "openapi-v2-breaking.yaml")
V2_UNVERSIONED = str(EXAMPLES / "openapi-v2-breaking-unversioned.yaml")
V2_COMPATIBLE = str(EXAMPLES / "openapi-v2-compatible.yaml")


def _result(head, policy=None):
    return diff_specs(load_spec(V1), load_spec(head), version_policy=policy)


# -- endpoint summary -----------------------------------------------------------


def test_endpoint_summary_of_breaking_example():
    summary = _result(V2_BREAKING).endpoint_summary()
    assert [e.label for e in summary.added] == ["GET /v1/subscriptions"]
    assert [e.label for e in summary.removed] == ["DELETE /v1/users/{id}"]
    changed = {c.endpoint.label: c for c in summary.changed}
    assert list(changed) == [
        "GET /v1/companies/{id}",
        "GET /v1/payments",
        "GET /v1/users",
        "POST /v1/users",
        "GET /v1/users/{id}",
    ]
    payments = changed["GET /v1/payments"]
    assert (payments.count(payments.worst), payments.worst.value) == (2, "BREAKING")


def test_endpoint_summary_in_reports():
    result = _result(V2_BREAKING)
    text = render_text(result)
    assert "API CHANGE SUMMARY\nAdded (1): GET /v1/subscriptions\nRemoved (1): DELETE /v1/users/{id}" in text
    assert "- GET /v1/payments: 2 breaking, 1 warning" in text

    md = render_markdown(result)
    assert "### 🧭 API change summary" in md
    assert "**➖ Removed (1)**\n\n- `DELETE /v1/users/{id}`" in md
    assert "- `GET /v1/payments` — ❌ 2 breaking · ⚠️ 1 warning" in md
    assert md.index("API change summary") < md.index("### ❌ Breaking changes")


def test_change_summary_format():
    summary = render_change_summary(_result(V2_UNVERSIONED, VersionPolicy(enabled=True)), "openapi.yaml")
    assert summary.startswith("### API change summary — `openapi.yaml`\n")
    assert "**Version:** `1.0.0` → `1.0.0` (none) — ❌ Breaking changes detected but info.version" in summary
    assert "**Findings:** ❌ 6 breaking · ⚠️ 1 warning · ✅ 3 non-breaking" in summary
    assert "**➕ Added (1)**\n\n- `GET /v1/subscriptions`" in summary


def test_change_summary_without_changes(capsys):
    assert main(["--base", V1, "--head", V1, "--format", "summary"]) == 0
    assert "_No endpoint changes._" in capsys.readouterr().out


# -- CLI + version policy -------------------------------------------------------


def test_unversioned_breaking_change_exits_3_when_breaking_is_allowed(capsys):
    args = ["--base", V1, "--head", V2_UNVERSIONED, "--no-fail-on-breaking", "--version-policy", "error"]
    assert main(args) == 3
    out = capsys.readouterr().out
    assert "VERSION POLICY: FAILED (1.0.0 -> 1.0.0, major bump required)" in out
    assert "version.not-bumped: Breaking changes detected but info.version is still 1.0.0" in out


def test_breaking_exit_code_takes_precedence_over_version_policy():
    assert main(["--base", V1, "--head", V2_UNVERSIONED, "--version-policy", "error"]) == 1


def test_properly_versioned_breaking_change_passes_when_breaking_is_allowed(capsys):
    assert main(["--base", V1, "--head", V2_BREAKING, "--no-fail-on-breaking", "--version-policy", "error"]) == 0
    assert "VERSION POLICY: OK (1.0.0 -> 2.0.0, major bump required)" in capsys.readouterr().out


def test_version_policy_warning_mode_never_fails(capsys):
    args = ["--base", V1, "--head", V2_UNVERSIONED, "--no-fail-on-breaking", "--version-policy", "warning"]
    assert main(args) == 0
    assert "VERSION POLICY: WARNING" in capsys.readouterr().out


def test_version_policy_from_config_and_cli_override(tmp_path):
    config = tmp_path / "cfg.yaml"
    config.write_text("fail_on_breaking: false\nversion_policy:\n  severity: error\n")
    base = ["--base", V1, "--head", V2_UNVERSIONED, "--config", str(config)]
    assert main(base) == 3
    assert main([*base, "--version-policy", "off"]) == 0


def test_compatible_example_satisfies_default_policy():
    # 1.0.0 -> 1.1.0 with additive changes only
    assert main(["--base", V1, "--head", V2_COMPATIBLE, "--version-policy", "error"]) == 0


def test_json_contains_endpoints_and_version(capsys):
    main(["--base", V1, "--head", V2_UNVERSIONED, "--format", "json", "--version-policy", "error"])
    data = json.loads(capsys.readouterr().out)
    assert data["endpoints"]["removed"] == ["DELETE /v1/users/{id}"]
    assert data["version"]["ok"] is False
    assert data["version"]["required_bump"] == "major"
    assert data["version"]["violations"][0]["rule_id"] == "version.not-bumped"


def test_json_without_policy_has_no_version_key(capsys):
    main(["--base", V1, "--head", V2_BREAKING, "--format", "json"])
    assert "version" not in json.loads(capsys.readouterr().out)


def test_markdown_version_section():
    md = render_markdown(_result(V2_UNVERSIONED, VersionPolicy(enabled=True)))
    assert "· ❌ **version policy violated**" in md
    assert "### 🏷️ Version policy" in md
    assert "❌ `1.0.0` → `1.0.0` (none) — breaking changes require a **major** bump" in md


# -- PR description merge ---------------------------------------------------------


def test_merge_pr_description_appends_then_replaces():
    start, end = summary_markers("openapi.yaml")
    body = merge_pr_description("Adds payments.\r\n", "openapi.yaml", "### API change summary\n\nv1\n")
    assert body == f"Adds payments.\n\n{start}\n### API change summary\n\nv1\n{end}\n"

    edited = body.replace("Adds payments.", "Adds payments (edited by author).")
    updated = merge_pr_description(edited, "openapi.yaml", "### API change summary\n\nv2\n")
    assert "edited by author" in updated
    assert "v2" in updated and "v1" not in updated
    assert updated.count(start) == 1


def test_merge_pr_description_on_empty_body_and_multiple_specs():
    body = merge_pr_description("", "a.yaml", "A")
    body = merge_pr_description(body, "b.yaml", "B")
    assert body.index("A") < body.index("B")
    assert merge_pr_description(body, "a.yaml", "A") == body  # idempotent -> no PATCH needed
