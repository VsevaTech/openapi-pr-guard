"""Command-line interface.

Exit codes: 0 — no breaking changes, 1 — breaking changes found,
2 — the specification (or config) could not be read.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from openapi_pr_guard import __version__
from openapi_pr_guard.config import ConfigError, load_config
from openapi_pr_guard.diff import diff_specs
from openapi_pr_guard.loader import SpecError, load_spec
from openapi_pr_guard.models import DiffResult
from openapi_pr_guard.reporter import RENDERERS

EXIT_OK = 0
EXIT_BREAKING = 1
EXIT_ERROR = 2


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
    parser.add_argument("--config", type=Path, help="path to .openapi-pr-guard.yaml")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def run(base: Path, head: Path, ignore_rules: Sequence[str] = ()) -> DiffResult:
    """Load both specs and diff them. Raises :class:`SpecError` on unreadable input."""
    return diff_specs(load_spec(base), load_spec(head), ignore_rules=ignore_rules)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        result = run(args.base, args.head, ignore_rules=config.ignore_rules)
    except (SpecError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    report = RENDERERS[args.format](result)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)

    fail_on_breaking = config.fail_on_breaking if args.fail_on_breaking is None else args.fail_on_breaking
    if result.has_breaking and fail_on_breaking:
        return EXIT_BREAKING
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
