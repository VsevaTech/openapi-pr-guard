# OpenAPI PR Guard

**Prevent breaking API changes from reaching production.**

[![CI](https://github.com/VsevaTech/openapi-pr-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/VsevaTech/openapi-pr-guard/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

OpenAPI PR Guard is a GitHub Action and CLI that compares the OpenAPI specification in a pull request with the one on the base branch, classifies every difference as **BREAKING**, **WARNING** or **NON-BREAKING**, and publishes a human-readable report to the Actions summary (and optionally as a PR comment). Breaking changes fail the check.

```
BREAKING
- POST /v1/users
  Request property became required: phone
  requestBody.content.application/json.schema[CreateUserRequest].properties.phone
```

## Why

An OpenAPI file is a contract. Removing a field from a response, making a query parameter required or changing `amount` from `number` to `string` compiles fine, passes unit tests and breaks every mobile app and partner integration in production. Code review catches some of this; a 400-line YAML diff hides the rest.

OpenAPI PR Guard reads the *semantics* of the change instead of the text diff, so reviewers see "response property removed: `company.address`" rather than a red line somewhere in `components/schemas`.

Design goals:

- **Zero infrastructure.** Pure Python, PyYAML is the only dependency. No Docker, no database, no SaaS, no LLM.
- **Human-first reports.** Every finding names the HTTP method, the path, what changed and where in the document.
- **Conservative classification.** When a change cannot be classified safely (e.g. a `oneOf` rewrite), it is reported as a WARNING rather than silently passed or wrongly failed.
- **Usable outside GitHub.** The diff engine is a plain library with a CLI; the Action is a thin wrapper.

## Features

- Detects removed endpoints and methods, removed or newly required parameters, request/response schema changes (types, required flags, removed properties, enums, formats, constraints), removed response codes and media types.
- Understands request vs. response direction: a new required property is breaking in a request body and harmless in a response.
- Resolves local `$ref`s (including recursive schemas), merges path-level and operation-level parameters, treats `/users/{id}` and `/users/{userId}` as the same endpoint, handles OpenAPI 3.0 `nullable` and 3.1 `type: [..]`.
- Optional **version policy**: checks that `info.version` moves in step with the contract (breaking change → major bump, …). SemVer-aware, but configurable and not SemVer-only.
- **API change summary**: endpoints added / removed / changed, as a report section, a `summary` output format and a self-updating section in the PR description.
- Text, Markdown, JSON and summary reports.
- GitHub Action with step summary, step outputs, workflow annotations and an optional self-updating PR comment.
- Exit codes suited for CI: `0` clean, `1` breaking changes, `2` unreadable spec, `3` version policy violated.

## Quick Start

```bash
git clone https://github.com/VsevaTech/openapi-pr-guard.git
cd openapi-pr-guard
pip install -e .

openapi-pr-guard --base examples/openapi-v1.yaml --head examples/openapi-v2-breaking.yaml    # exit 1
openapi-pr-guard --base examples/openapi-v1.yaml --head examples/openapi-v2-compatible.yaml  # exit 0
```

## GitHub Action usage

```yaml
# .github/workflows/openapi-guard.yml
name: OpenAPI PR Guard

on:
  pull_request:
    paths:
      - "openapi.yaml"

permissions:
  contents: read
  pull-requests: write   # only needed for `comment` / `pr-description`

jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: VsevaTech/openapi-pr-guard@v1
        with:
          spec: openapi.yaml
          fail-on-breaking: true
          comment: true
          # optional: require info.version to reflect the changes, and
          # keep an "API change summary" section in the PR description
          version-policy: error
          pr-description: true
```

The action reads the spec from the pull request's base commit with `git show`, compares it with the file in the checkout, appends the Markdown report to `$GITHUB_STEP_SUMMARY`, emits one `::error::` annotation per breaking change and exits with code 1 when breaking changes are found (unless `fail-on-breaking: false`).

### Inputs

| Input | Default | Description |
|---|---|---|
| `spec` | — | Path to the OpenAPI file (YAML or JSON). **Required.** |
| `fail-on-breaking` | `true` | Fail the step when breaking changes are found. |
| `base-ref` | PR base commit | Git ref to compare against, e.g. `main`. Useful outside `pull_request` events. |
| `comment` | `false` | Post a PR comment with the report and keep it updated on subsequent pushes. |
| `version-policy` | from config (off) | `error` — fail when `info.version` does not reflect the changes; `warning` — report only; `off`. See [Version policy](#version-policy). |
| `pr-description` | `false` | Keep an "API change summary" section in the PR description up to date. |
| `github-token` | `${{ github.token }}` | Token used for the comment and the PR description. |
| `config` | `.openapi-pr-guard.yaml` | Path to the config file. |
| `python-version` | `3.12` | Python version used to run the checker. |

### Outputs

`breaking-count`, `warning-count`, `non-breaking-count`, `has-breaking`, `change-summary` (Markdown), and — with the version policy enabled — `version-ok`, `base-version`, `head-version`, `required-bump`, `actual-bump`. Handy for conditional follow-up steps:

```yaml
      - uses: VsevaTech/openapi-pr-guard@v1
        id: guard
        with:
          spec: openapi.yaml
          fail-on-breaking: false
      - if: steps.guard.outputs.has-breaking == 'true'
        run: echo "::warning::API contract changed — remember to bump the major version"
```

## CLI usage

```
usage: openapi-pr-guard [-h] --base BASE --head HEAD [--format {json,markdown,summary,text}]
                        [--output OUTPUT] [--fail-on-breaking | --no-fail-on-breaking]
                        [--version-policy {off,warning,error}] [--config CONFIG] [--version]
```

```bash
# text report to stdout
openapi-pr-guard --base old.yaml --head new.yaml

# Markdown report to a file, never fail the process
openapi-pr-guard --base old.yaml --head new.yaml --format markdown --output report.md --no-fail-on-breaking

# machine-readable
openapi-pr-guard --base old.yaml --head new.yaml --format json | jq '.summary'

# breaking changes are fine as long as info.version says so
openapi-pr-guard --base old.yaml --head new.yaml --no-fail-on-breaking --version-policy error

# just the "API change summary" block, e.g. for release notes
openapi-pr-guard --base old.yaml --head new.yaml --format summary

# without installing the console script
python -m openapi_pr_guard --base old.yaml --head new.yaml
```

Exit codes: `0` — no breaking changes, `1` — breaking changes found, `2` — a specification or the config could not be read or parsed, `3` — the version policy was violated (only with `severity: error`; `1` takes precedence when breaking changes also fail the run).

## Example output

`openapi-pr-guard --base examples/openapi-v1.yaml --head examples/openapi-v2-breaking.yaml`:

```
OpenAPI PR Guard

BREAKING CHANGES: 6
WARNINGS: 1
NON-BREAKING: 3

API CHANGE SUMMARY
Added (1): GET /v1/subscriptions
Removed (1): DELETE /v1/users/{id}
Changed (5):
- GET /v1/companies/{id}: 1 breaking
- GET /v1/payments: 2 breaking, 1 warning
- GET /v1/users: 2 non-breaking
- POST /v1/users: 1 breaking
- GET /v1/users/{id}: 1 breaking

BREAKING
- GET /v1/companies/{id}
  Response property removed: address
  responses.200.content.application/json.schema[Company].properties.address
- GET /v1/payments
  New required request parameter: currency (query)
  parameters.query.currency
- GET /v1/payments
  Response type changed: number -> string
  responses.200.content.application/json.schema.items[Payment].properties.amount
- POST /v1/users
  Request property became required: phone
  requestBody.content.application/json.schema[CreateUserRequest].properties.phone
- DELETE /v1/users/{id}
  HTTP method removed
- GET /v1/users/{id}
  Response code removed: 404
  responses.404

WARNING
- GET /v1/payments
  Composed schema changed (oneOf) and could not be safely classified
  responses.200.content.application/json.schema.items[Payment].properties.metadata

NON-BREAKING
- GET /v1/subscriptions
  New endpoint added
- GET /v1/users
  New optional request parameter: cursor (query)
  parameters.query.cursor
- GET /v1/users
  Documentation changed (summary)
  summary
```

The Markdown version (step summary / PR comment) shows the same findings with a summary table; non-breaking changes are collapsed.

## Breaking changes currently detected

| Change | Severity |
|---|---|
| Endpoint (path) removed | BREAKING |
| HTTP method removed from an existing path | BREAKING |
| Request parameter removed | BREAKING |
| New **required** request parameter | BREAKING |
| Request parameter became required | BREAKING |
| Request body became required / required body added | BREAKING |
| Request media type removed | BREAKING |
| Parameter / property type changed (incl. becoming nullable in a response) | BREAKING |
| Request property became required / new required request property | BREAKING |
| Enum values removed from a request field, or a request field newly restricted to an enum | BREAKING |
| Property removed from a response schema | BREAKING |
| Response code removed | BREAKING |
| Response media type removed | BREAKING |
| `oneOf` / `anyOf` / `allOf` / `not` / `discriminator` changed | WARNING |
| Constraint changed (`minimum`, `maxLength`, `pattern`, `additionalProperties`, …) | WARNING |
| `format` changed | WARNING |
| Request property removed | WARNING |
| Response property no longer required | WARNING |
| New enum values in a response field | WARNING |
| `operationId` changed | WARNING |
| Unresolvable or external `$ref` | WARNING |
| Request body removed | WARNING |
| New endpoint / method / response code / media type | NON-BREAKING |
| New optional parameter / optional request property / response property | NON-BREAKING |
| Request type widened (`integer` → `integer \| string`), response type narrowed | NON-BREAKING |
| Parameter or property became optional in a request | NON-BREAKING |
| Operation deprecated; `summary`, `description`, `tags`, `example` edits | NON-BREAKING |

Changes to `description`, `title`, `example(s)` inside schemas are ignored entirely; documentation edits on an operation are reported once as `operation.docs-changed`.

Every finding carries a stable `rule_id` (e.g. `schema.property-removed`, `parameter.required-added`) which is included in the JSON output and can be used in `ignore_rules`.

## Configuration

Most projects need nothing beyond the `spec` input. For the rest, put an `.openapi-pr-guard.yaml` in the repository root (or point `--config` / the `config` input at it):

```yaml
# .openapi-pr-guard.yaml
fail_on_breaking: true
ignore_rules:
  - operation.docs-changed
  - schema.format-changed
```

`--fail-on-breaking` / `--no-fail-on-breaking` on the command line override the file.

## Version policy

Detecting a breaking change is half the job; the other half is making sure it is *announced*. With the version policy enabled, the guard also compares `info.version` of the base and head specs and checks that the bump matches the most significant change in the diff:

| Change level in the diff | Default minimum bump |
|---|---|
| `breaking` — any BREAKING finding | `major` |
| `warning` — any WARNING finding (could not be classified safely) | `minor` |
| `non_breaking` — additive contract changes (new endpoints, optional fields, deprecations, …) | `minor` |
| `docs` — only `summary` / `description` / `tags` edits | `none` |

Violations get their own rule ids and are reported separately from the contract findings:

| Rule | When |
|---|---|
| `version.not-bumped` | A bump is required but `info.version` is unchanged |
| `version.insufficient-bump` | The version moved, but not enough (e.g. `1.4.0 → 1.5.0` with a breaking change) |
| `version.downgraded` | `info.version` went backwards |
| `version.invalid` | `scheme: semver` and the head version is not `MAJOR.MINOR.PATCH` |
| `version.missing` | A bump is required and the head spec has no `info.version` |

The policy is opt-in and configurable rather than strict SemVer:

```yaml
# .openapi-pr-guard.yaml
fail_on_breaking: false   # breaking changes are allowed…
version_policy:           # …as long as the version says so
  scheme: semver          # semver | any — `any`: the version string only has to change (dates, build numbers, "v3")
  severity: error         # error → exit code 3 / failed check; warning → annotate and report only
  pre_1_0: shift          # 0.x.y: minor bump stands in for major, patch for minor (Cargo convention) | strict
  require:                # override any level: none | patch | minor | major
    breaking: major
    warning: minor
    non_breaking: minor
    docs: none
```

`version_policy: true` enables the defaults. The `--version-policy` CLI flag and the `version-policy` action input (`off` / `warning` / `error`) override `enabled` and `severity` from the file. Other details:

- Pre-releases carry no compatibility promise (SemVer §9): `2.0.0-rc.1 → 2.0.0-rc.2` or `→ 2.0.0` is accepted regardless of the changes.
- `v1.2.3` is accepted; build metadata (`+build.5`) is ignored.
- If the base version is not SemVer (e.g. the project is switching schemes), only the head version is validated.
- `version.*` rule ids can be listed in `ignore_rules` like any other rule.

With `fail_on_breaking: false` plus `severity: error` the check expresses a common team rule: *"you may break the API, but only with a major version bump"*.

## API change summary

Every Markdown report contains an endpoint-level roll-up above the detailed findings, and the same block is available on its own:

- `--format summary` on the CLI;
- the `change-summary` step output;
- `pr-description: true` in the Action — the block is inserted into the pull request description between `<!-- openapi-pr-guard:summary:<spec>:start/end -->` markers and replaced in place on every push, leaving the author's text untouched.

```markdown
### API change summary — `openapi.yaml`

**Version:** `1.0.0` → `1.0.0` (none) — ❌ Breaking changes detected but info.version is still 1.0.0; a major bump is required

**Findings:** ❌ 6 breaking · ⚠️ 1 warning · ✅ 3 non-breaking

**➕ Added (1)**

- `GET /v1/subscriptions`

**➖ Removed (1)**

- `DELETE /v1/users/{id}`

**✏️ Changed (5)**

- `GET /v1/companies/{id}` — ❌ 1 breaking
- `GET /v1/payments` — ❌ 2 breaking · ⚠️ 1 warning
- `GET /v1/users` — ✅ 2 non-breaking
- `POST /v1/users` — ❌ 1 breaking
- `GET /v1/users/{id}` — ❌ 1 breaking
```

Updating the description needs `pull-requests: write`. It does not re-trigger the workflow unless you subscribe to the `edited` pull-request event.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                 # unit + end-to-end tests (including the Action against a temp git repo)
ruff check . && ruff format --check .
```

Project layout:

```
src/openapi_pr_guard/
├── models.py          Change / DiffResult / Severity
├── loader.py          YAML/JSON loading, validation, local $ref resolver
├── diff.py            pairs paths & operations, runs rules
├── schema_diff.py     recursive JSON Schema comparison (request vs response aware)
├── rules/             one class per concern; register in rules/__init__.py
├── versioning.py      info.version policy (SemVer parsing, required vs actual bump)
├── summary.py         endpoint-level roll-up: added / removed / changed
├── reporter.py        text / markdown / json / summary
├── config.py          .openapi-pr-guard.yaml
├── cli.py             argparse entry point, exit codes
└── github_action.py   the only GitHub-aware module
```

Adding a rule: implement a class with a `check(context) -> Iterable[Change]` method (see `rules/base.py`), add it to `DEFAULT_RULES`, write a test. The diff engine does not need to change.

## Roadmap

- Per-rule severity overrides in the config (`schema.enum-values-added: breaking`).
- Ignore paths/operations by glob or by `x-openapi-pr-guard: ignore` vendor extension.
- Response headers and parameter `content`/`style` comparison.
- Security requirement changes (new required auth scheme = breaking).
- Multi-file specs / external `$ref`s.
- Support for several spec files in one Action run (the PR-description markers are already per spec).

## License

[MIT](LICENSE)
