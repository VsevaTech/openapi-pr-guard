import json
from pathlib import Path

import pytest

from openapi_pr_guard.cli import main
from openapi_pr_guard.models import Change, DiffResult, Severity
from openapi_pr_guard.reporter import render_json, render_markdown, render_text

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
V1 = str(EXAMPLES / "openapi-v1.yaml")
V2_BREAKING = str(EXAMPLES / "openapi-v2-breaking.yaml")
V2_COMPATIBLE = str(EXAMPLES / "openapi-v2-compatible.yaml")


def test_breaking_example_exits_1(capsys):
    assert main(["--base", V1, "--head", V2_BREAKING]) == 1
    out = capsys.readouterr().out
    assert out.startswith("OpenAPI PR Guard")
    assert "BREAKING CHANGES: 6" in out
    assert "Request property became required: phone" in out
    assert "Response property removed: address" in out
    assert "HTTP method removed" in out
    assert "Response code removed: 404" in out
    assert "New required request parameter: currency (query)" in out
    assert "Response type changed: number -> string" in out
    assert "could not be safely classified" in out


def test_compatible_example_exits_0(capsys):
    assert main(["--base", V1, "--head", V2_COMPATIBLE]) == 0
    out = capsys.readouterr().out
    assert "BREAKING CHANGES: 0" in out
    assert "WARNINGS: 0" in out
    assert "New endpoint added" in out


def test_identical_specs_exit_0(capsys):
    assert main(["--base", V1, "--head", V1]) == 0
    assert "No changes detected." in capsys.readouterr().out


def test_no_fail_on_breaking_flag(capsys):
    assert main(["--base", V1, "--head", V2_BREAKING, "--no-fail-on-breaking"]) == 0


def test_missing_or_invalid_spec_exits_2(tmp_path, capsys):
    assert main(["--base", V1, "--head", str(tmp_path / "missing.yaml")]) == 2
    assert "file not found" in capsys.readouterr().err

    broken = tmp_path / "broken.yaml"
    broken.write_text("openapi: 3.0.0\npaths: [\n")
    assert main(["--base", V1, "--head", str(broken)]) == 2
    assert "invalid YAML" in capsys.readouterr().err


def test_json_format_and_output_file(tmp_path):
    out = tmp_path / "report.json"
    assert main(["--base", V1, "--head", V2_BREAKING, "--format", "json", "--output", str(out)]) == 1
    data = json.loads(out.read_text())
    assert data["summary"] == {"breaking": 6, "warnings": 1, "non_breaking": 3}
    assert {"severity", "rule_id", "method", "path", "location", "message"} <= set(data["changes"][0])


def test_config_file_can_ignore_rules_and_disable_failure(tmp_path, monkeypatch, capsys):
    config = tmp_path / ".openapi-pr-guard.yaml"
    config.write_text("fail_on_breaking: false\nignore_rules:\n  - operation.docs-changed\n")
    assert main(["--base", V1, "--head", V2_BREAKING, "--config", str(config)]) == 0
    assert "Documentation changed" not in capsys.readouterr().out

    monkeypatch.chdir(tmp_path)  # default config location is picked up automatically
    assert main(["--base", V1, "--head", V2_BREAKING]) == 0


def test_invalid_config_exits_2(tmp_path, capsys):
    config = tmp_path / "bad.yaml"
    config.write_text("fail_on_breaking: sometimes\n")
    assert main(["--base", V1, "--head", V1, "--config", str(config)]) == 2
    assert "fail_on_breaking" in capsys.readouterr().err


@pytest.fixture
def sample_result() -> DiffResult:
    return DiffResult(
        [
            Change(Severity.NON_BREAKING, "endpoint.added", "/subscriptions", "New endpoint added", "GET"),
            Change(
                Severity.BREAKING,
                "schema.property-became-required",
                "/users",
                "Request property became required: phone",
                "POST",
                "requestBody.content.application/json.schema[User].properties.phone",
            ),
            Change(Severity.WARNING, "schema.composition-changed", "/payments", "Composed schema changed", "GET", "x"),
        ]
    )


def test_text_report_groups_by_severity_in_order(sample_result):
    text = render_text(sample_result)
    assert text.index("BREAKING\n") < text.index("WARNING\n") < text.index("NON-BREAKING\n")
    assert "- POST /users\n  Request property became required: phone\n  requestBody.content" in text


def test_markdown_report(sample_result):
    md = render_markdown(sample_result)
    assert md.startswith("## OpenAPI PR Guard")
    assert "**1 breaking change detected**" in md
    assert "- **POST /users** — Request property became required: phone" in md
    assert "`requestBody.content.application/json.schema[User].properties.phone`" in md
    assert "<details>" in md  # non-breaking section is collapsed


def test_markdown_report_without_changes():
    md = render_markdown(DiffResult())
    assert "✅ **No breaking changes detected**" in md
    assert "No changes detected" in md


def test_json_report_is_sorted_by_severity(sample_result):
    data = json.loads(render_json(sample_result))
    assert [c["severity"] for c in data["changes"]] == ["BREAKING", "WARNING", "NON-BREAKING"]
