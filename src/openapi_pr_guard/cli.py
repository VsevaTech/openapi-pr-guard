"""Command-line interface.

Exit codes: 0 — no breaking changes, 1 — breaking changes found,
2 — the specification (or config) could not be read,
3 — the version policy was violated (only with ``severity: error``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from openapi_pr_guard import __version__
from openapi_pr_guard.config import Config, ConfigError, load_config
from openapi_pr_guard.diff import diff_specs
from openapi_pr_guard.loader import SpecError, load_spec
from openapi_pr_guard.models import DiffResult
from openapi_pr_guard.reporter import RENDERERS
from openapi_pr_guard.versioning import PolicySeverity, VersionPolicy

EXIT_OK = 0
EXIT_BREAKING = 1
EXIT_ERROR = 2
EXIT_VERSION_POLICY = 3

VERSION_POLICY_MODES = ("off", "warning", "error")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openapi-pr-guard",
        description="Compare two OpenAPI specifications and report breaking changes.",
    )
    parser.add_argument("--base", required=True, type=Path, help="OpenAPI file of the base (old) version")
    parser.add_argument("--head", required=True, type=Path, help="OpenAPI file of the head (new) version")
    parser.add_argument("--format", choices=sorted(RENDERERS), default="text", help="report format (default: text)")
    parser.add_argument("--output", type=Path, help="write the report to this file instead of stdout")
    parser.add_argument(
        "--fail-on-breaking",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="exit with code 1 when breaking changes are found (default: true)",
    )
    parser.add_argument(
        "--version-policy",
        choices=VERSION_POLICY_MODES,
        help="check info.version against the detected changes: 'error' fails with exit code 3, "
        "'warning' only reports, 'off' disables (default: from config, else off)",
    )
    parser.add_argument("--config", type=Path, help="path to .openapi-pr-guard.yaml")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def run(
    base: Path, head: Path, ignore_rules: Sequence[str] = (), version_policy: VersionPolicy | None = None
) -> DiffResult:
    """Load both specs and diff them. Raises :class:`SpecError` on unreadable input."""
    return diff_specs(load_spec(base), load_spec(head), ignore_rules=ignore_rules, version_policy=version_policy)


def apply_version_policy_mode(config: Config, mode: str | None) -> None:
    """``--version-policy`` / the ``version-policy`` action input override the config file."""
    if not mode:
        return
    mode = {"warn": "warning"}.get(mode, mode)
    if mode not in VERSION_POLICY_MODES:
        raise ConfigError(f"version policy mode must be one of: {', '.join(VERSION_POLICY_MODES)} (got {mode!r})")
    policy = config.version_policy
    policy.enabled = mode != "off"
    if policy.enabled:
        policy.severity = PolicySeverity(mode)


def exit_code(result: DiffResult, fail_on_breaking: bool) -> int:
    if result.has_breaking and fail_on_breaking:
        return EXIT_BREAKING
    if result.version_blocking:
        return EXIT_VERSION_POLICY
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        apply_version_policy_mode(config, args.version_policy)
        result = run(args.base, args.head, ignore_rules=config.ignore_rules, version_policy=config.version_policy)
    except (SpecError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    report = RENDERERS[args.format](result)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)

    fail_on_breaking = config.fail_on_breaking if args.fail_on_breaking is None else args.fail_on_breaking
    return exit_code(result, fail_on_breaking)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
