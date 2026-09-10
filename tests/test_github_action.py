"""End-to-end test of the GitHub Action entry point against a throwaway git repository."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openapi_pr_guard import github_action

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo whose first commit holds openapi-v1.yaml and whose working tree holds the head version."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    shutil.copy(EXAMPLES / "openapi-v1.yaml", repo / "openapi.yaml")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base_sha = _git(repo, "rev-parse", "HEAD")

    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "base": {"sha": base_sha}}}))
    summary = tmp_path / "summary.md"
    output = tmp_path / "output.txt"

    monkeypatch.chdir(repo)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("INPUT_SPEC", "openapi.yaml")
    monkeypatch.delenv("INPUT_BASE_REF", raising=False)
    monkeypatch.delenv("INPUT_FAIL_ON_BREAKING", raising=False)
    return repo, summary, output


def test_breaking_change_fails_and_writes_summary(repo, capsys):
    repo_dir, summary, output = repo
    shutil.copy(EXAMPLES / "openapi-v2-breaking.yaml", repo_dir / "openapi.yaml")

    assert github_action.main() == 1

    assert "## OpenAPI PR Guard" in summary.read_text()
    assert "6 breaking changes detected" in summary.read_text()
    assert "breaking-count=6" in output.read_text()
    assert "has-breaking=true" in output.read_text()
    captured = capsys.readouterr()
    assert "BREAKING CHANGES: 6" in captured.out
    assert "::error::" in captured.err


def test_fail_on_breaking_false_exits_zero(repo, monkeypatch):
    repo_dir, _, _ = repo
    shutil.copy(EXAMPLES / "openapi-v2-breaking.yaml", repo_dir / "openapi.yaml")
    monkeypatch.setenv("INPUT_FAIL_ON_BREAKING", "false")
    assert github_action.main() == 0


def test_compatible_change_passes(repo):
    repo_dir, summary, output = repo
    shutil.copy(EXAMPLES / "openapi-v2-compatible.yaml", repo_dir / "openapi.yaml")
    assert github_action.main() == 0
    assert "No breaking changes detected" in summary.read_text()
    assert "has-breaking=false" in output.read_text()


def test_new_spec_file_is_skipped(repo, monkeypatch):
    repo_dir, summary, _ = repo
    shutil.copy(EXAMPLES / "openapi-v1.yaml", repo_dir / "brand-new.yaml")
    monkeypatch.setenv("INPUT_SPEC", "brand-new.yaml")
    assert github_action.main() == 0
    assert "does not exist in the base revision" in summary.read_text()


def test_missing_base_information_is_an_error(repo, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_EVENT_PATH")
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    assert github_action.main() == 2
    assert "cannot determine the base revision" in capsys.readouterr().err


def test_explicit_base_ref_is_used(repo, monkeypatch):
    repo_dir, _, output = repo
    _git(repo_dir, "branch", "release")
    shutil.copy(EXAMPLES / "openapi-v2-breaking.yaml", repo_dir / "openapi.yaml")
    monkeypatch.delenv("GITHUB_EVENT_PATH")
    monkeypatch.setenv("INPUT_BASE_REF", "release")
    assert github_action.main() == 1
    assert "breaking-count=6" in output.read_text()
