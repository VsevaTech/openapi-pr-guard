"""Security contract diff: effective security, OR/AND semantics, scopes and scheme definitions."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from openapi_pr_guard.cli import main
from openapi_pr_guard.diff import diff_specs
from openapi_pr_guard.loader import load_spec
from openapi_pr_guard.models import DiffResult, Severity
from openapi_pr_guard.reporter import render_json, render_markdown, render_text
from openapi_pr_guard.rules.security import (
    EffectiveSecurity,
    SchemeUse,
    covers,
    effective_security,
    normalize_security,
)
from openapi_pr_guard.versioning import VersionPolicy

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SEC_BASE = EXAMPLES / "security-base.yaml"
SEC_BREAKING = EXAMPLES / "security-breaking.yaml"
SEC_BREAKING_MAJOR = EXAMPLES / "security-breaking-major.yaml"
SEC_COMPATIBLE = EXAMPLES / "security-compatible.yaml"

OK = {"description": "ok"}

SCHEMES: dict[str, Any] = {
    "oauth2": {
        "type": "oauth2",
        "flows": {
            "clientCredentials": {
                "tokenUrl": "https://auth.example.com/token",
                "scopes": {"payments:read": "read", "payments:write": "write"},
            }
        },
    },
    "apiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
    "bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
    "oidc": {"type": "openIdConnect", "openIdConnectUrl": "https://auth.example.com/.well-known/openid-configuration"},
}


def make_spec(
    post_security: Any = None,
    root_security: Any = None,
    get_security: Any = None,
    version: str = "3.0.3",
    schemes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Two operations on /payments. ``None`` = no ``security`` key at that level."""
    post: dict[str, Any] = {"responses": {"201": OK}}
    get: dict[str, Any] = {"responses": {"200": OK}}
    if post_security is not None:
        post["security"] = post_security
    if get_security is not None:
        get["security"] = get_security
    spec: dict[str, Any] = {
        "openapi": version,
        "info": {"title": "Payments", "version": "1.0.0"},
        "paths": {"/payments": {"post": post, "get": get}},
        "components": {"securitySchemes": copy.deepcopy(SCHEMES if schemes is None else schemes)},
    }
    if root_security is not None:
        spec["security"] = root_security
    return spec


def security_changes(result: DiffResult) -> list[tuple[str, str, str, str]]:
    return sorted(
        (c.severity.value, c.rule_id, f"{c.method} {c.path}", c.message)
        for c in result.changes
        if c.rule_id.startswith("security.")
    )


def only(result: DiffResult):
    changes = [c for c in result.changes if c.rule_id.startswith("security.")]
    assert len(changes) == 1, changes
    return changes[0]


OAUTH_READ = [{"oauth2": ["payments:read"]}]
OAUTH_READ_WRITE = [{"oauth2": ["payments:read", "payments:write"]}]
OAUTH_OR_KEY = [{"oauth2": ["payments:read"]}, {"apiKey": []}]
OAUTH_AND_KEY = [{"oauth2": ["payments:read"], "apiKey": []}]


# -- normalized representation -------------------------------------------------


def test_normalization_keeps_or_of_and():
    alts = normalize_security([{"oauth2": ["read"], "apiKey": []}, {"partnerKey": []}])
    assert alts == frozenset(
        {
            frozenset({SchemeUse("oauth2", frozenset({"read"})), SchemeUse("apiKey", frozenset())}),
            frozenset({SchemeUse("partnerKey", frozenset())}),
        }
    )


def test_normalization_distinguishes_missing_from_empty():
    assert normalize_security(None) is None
    assert normalize_security([]) == frozenset()
    assert normalize_security("oops") is None  # malformed -> treated as not declared


def test_covers_is_subset_of_schemes_and_scopes():
    both = frozenset({SchemeUse("a", frozenset({"x", "y"})), SchemeUse("b", frozenset())})
    assert covers(both, frozenset({SchemeUse("a", frozenset({"x"}))}))
    assert not covers(frozenset({SchemeUse("a", frozenset({"x"}))}), both)


# -- inheritance ----------------------------------------------------------------


def test_root_security_is_inherited():
    spec = make_spec(root_security=OAUTH_READ)
    effective = effective_security(spec, spec["paths"]["/payments"]["get"])
    assert effective.inherited and not effective.is_public
    assert effective.scheme_names() == {"oauth2"}


def test_operation_security_overrides_root():
    spec = make_spec(root_security=OAUTH_READ, get_security=[{"apiKey": []}])
    effective = effective_security(spec, spec["paths"]["/payments"]["get"])
    assert not effective.inherited
    assert effective.scheme_names() == {"apiKey"}


def test_empty_operation_security_overrides_root_as_public():
    spec = make_spec(root_security=OAUTH_READ, get_security=[])
    effective = effective_security(spec, spec["paths"]["/payments"]["get"])
    assert effective == EffectiveSecurity(frozenset(), inherited=False)
    assert effective.is_public


def test_empty_requirement_object_makes_auth_optional():
    spec = make_spec(get_security=[{}, {"oauth2": []}])
    assert effective_security(spec, spec["paths"]["/payments"]["get"]).is_public


def test_root_scope_added_reported_for_every_inheriting_operation():
    result = diff_specs(make_spec(root_security=OAUTH_READ), make_spec(root_security=OAUTH_READ_WRITE))
    assert security_changes(result) == [
        ("BREAKING", "security.scope-added", "GET /payments", "Required OAuth scope added: payments:write"),
        ("BREAKING", "security.scope-added", "POST /payments", "Required OAuth scope added: payments:write"),
    ]


def test_operation_override_unaffected_by_unrelated_root_change():
    base = make_spec(root_security=OAUTH_READ, get_security=[{"apiKey": []}])
    head = make_spec(root_security=OAUTH_READ_WRITE, get_security=[{"apiKey": []}])
    changes = security_changes(diff_specs(base, head))
    assert [c[2] for c in changes] == ["POST /payments"]


def test_moving_same_requirement_from_root_to_operation_is_no_change():
    base = make_spec(root_security=OAUTH_READ)
    head = make_spec(post_security=OAUTH_READ, get_security=OAUTH_READ)
    assert security_changes(diff_specs(base, head)) == []


def test_public_override_under_root_change_stays_public():
    base = make_spec(root_security=OAUTH_READ, get_security=[])
    head = make_spec(root_security=OAUTH_AND_KEY, get_security=[])
    assert {c[2] for c in security_changes(diff_specs(base, head))} == {"POST /payments"}


# -- public / protected ------------------------------------------------------------


def test_public_to_protected_is_breaking():
    change = only(diff_specs(make_spec(post_security=[]), make_spec(post_security=OAUTH_READ)))
    assert (change.severity, change.rule_id) == (Severity.BREAKING, "security.authentication-required")
    assert change.message == "Authentication is now required"
    assert (change.method, change.path, change.location) == ("POST", "/payments", "security")


def test_no_security_anywhere_to_root_security_is_breaking_for_all_operations():
    result = diff_specs(make_spec(), make_spec(root_security=OAUTH_READ))
    assert [c[1] for c in security_changes(result)] == ["security.authentication-required"] * 2


def test_protected_to_public_is_warning():
    change = only(diff_specs(make_spec(post_security=OAUTH_READ), make_spec(post_security=[])))
    assert (change.severity, change.rule_id) == (Severity.WARNING, "security.authentication-removed")
    assert change.message.startswith("Authentication requirement removed")


def test_adding_anonymous_alternative_is_warning():
    change = only(diff_specs(make_spec(post_security=OAUTH_READ), make_spec(post_security=[*OAUTH_READ, {}])))
    assert change.rule_id == "security.authentication-removed"


def test_public_to_public_no_finding():
    assert security_changes(diff_specs(make_spec(post_security=[]), make_spec())) == []


def test_same_protected_requirement_no_finding():
    base = make_spec(post_security=OAUTH_OR_KEY, root_security=OAUTH_READ)
    assert security_changes(diff_specs(base, copy.deepcopy(base))) == []


def test_order_of_alternatives_and_scopes_is_irrelevant():
    base = make_spec(post_security=[{"oauth2": ["payments:read", "payments:write"]}, {"apiKey": []}])
    head = make_spec(post_security=[{"apiKey": []}, {"oauth2": ["payments:write", "payments:read"]}])
    assert security_changes(diff_specs(base, head)) == []


# -- OAuth scopes -------------------------------------------------------------------


def test_scope_added_is_breaking():
    change = only(diff_specs(make_spec(post_security=OAUTH_READ), make_spec(post_security=OAUTH_READ_WRITE)))
    assert change.severity is Severity.BREAKING
    assert change.rule_id == "security.scope-added"
    assert change.message == "Required OAuth scope added: payments:write"
    assert change.location == "security.oauth2"


def test_scope_removed_is_warning():
    change = only(diff_specs(make_spec(post_security=OAUTH_READ_WRITE), make_spec(post_security=OAUTH_READ)))
    assert (change.severity, change.rule_id) == (Severity.WARNING, "security.scope-removed")
    assert change.message == "OAuth scope requirement removed: payments:write"


def test_scope_unchanged_no_finding():
    assert security_changes(diff_specs(make_spec(post_security=OAUTH_READ), make_spec(post_security=OAUTH_READ))) == []


def test_multiple_scopes_added_in_one_finding():
    head = make_spec(post_security=[{"oauth2": ["payments:read", "payments:write", "refunds:write"]}])
    change = only(diff_specs(make_spec(post_security=OAUTH_READ), head))
    assert change.message == "Required OAuth scopes added: payments:write, refunds:write"


def test_scope_swapped_is_breaking_and_weakening():
    base = make_spec(post_security=[{"oauth2": ["payments:read"]}])
    head = make_spec(post_security=[{"oauth2": ["payments:write"]}])
    assert [c[:2] for c in security_changes(diff_specs(base, head))] == [
        ("BREAKING", "security.scope-added"),
        ("WARNING", "security.scope-removed"),
    ]


def test_non_oauth_scopes_are_called_scopes():
    base = make_spec(post_security=[{"bearer": []}])
    head = make_spec(post_security=[{"bearer": ["admin"]}])
    assert only(diff_specs(base, head)).message == "Required scope added: admin"


def test_openid_connect_scopes():
    base = make_spec(post_security=[{"oidc": ["openid"]}])
    head = make_spec(post_security=[{"oidc": ["openid", "payments"]}])
    assert only(diff_specs(base, head)).message == "Required OAuth scope added: payments"


# -- OR -------------------------------------------------------------------------------


def test_or_alternative_removed_is_breaking():
    change = only(diff_specs(make_spec(post_security=OAUTH_OR_KEY), make_spec(post_security=OAUTH_READ)))
    assert (change.severity, change.rule_id) == (Severity.BREAKING, "security.alternative-removed")
    assert change.message == "Authentication alternative removed: apiKey"
    assert change.location == "security.apiKey"


def test_or_alternative_added_is_non_breaking():
    change = only(diff_specs(make_spec(post_security=OAUTH_READ), make_spec(post_security=OAUTH_OR_KEY)))
    assert (change.severity, change.rule_id) == (Severity.NON_BREAKING, "security.alternative-added")
    assert change.message == "Authentication alternative added: apiKey"


def test_redundant_alternative_removed_is_not_breaking():
    # (oauth2 AND apiKey) clients still satisfy the remaining oauth2 alternative
    base = make_spec(post_security=[*OAUTH_READ, *OAUTH_AND_KEY])
    head = make_spec(post_security=OAUTH_READ)
    assert security_changes(diff_specs(base, head)) == []


def test_scheme_rename_is_not_guessed():
    base = make_spec(post_security=[{"apiKey": []}])
    head_schemes = {**SCHEMES, "partnerKey": SCHEMES["apiKey"]}
    del head_schemes["apiKey"]
    head = make_spec(post_security=[{"partnerKey": []}], schemes=head_schemes)
    assert [c[:2] for c in security_changes(diff_specs(base, head))] == [
        ("BREAKING", "security.alternative-removed"),
        ("NON-BREAKING", "security.alternative-added"),
    ]


# -- AND --------------------------------------------------------------------------------


def test_and_requirement_added_is_breaking():
    change = only(diff_specs(make_spec(post_security=OAUTH_READ), make_spec(post_security=OAUTH_AND_KEY)))
    assert (change.severity, change.rule_id) == (Severity.BREAKING, "security.requirement-added")
    assert change.message == "Additional authentication requirement: apiKey"
    assert change.location == "security.apiKey"


def test_and_requirement_removed_is_weakening():
    change = only(diff_specs(make_spec(post_security=OAUTH_AND_KEY), make_spec(post_security=OAUTH_READ)))
    assert (change.severity, change.rule_id) == (Severity.WARNING, "security.requirement-removed")
    assert change.message == "Authentication requirement no longer needed: apiKey"


def test_and_split_into_or_is_weakening_not_breaking():
    base = make_spec(post_security=OAUTH_AND_KEY)
    head = make_spec(post_security=OAUTH_OR_KEY)
    result = diff_specs(base, head)
    assert not result.has_breaking
    assert {c[1] for c in security_changes(result)} == {"security.requirement-removed"}


def test_or_merged_into_and_is_breaking_for_both_client_groups():
    result = diff_specs(make_spec(post_security=OAUTH_OR_KEY), make_spec(post_security=OAUTH_AND_KEY))
    assert [c[3] for c in security_changes(result)] == [
        "Additional authentication requirement: apiKey",
        "Additional authentication requirement: oauth2 [payments:read]",
    ]


# -- security scheme definitions ----------------------------------------------------------


def _with_scheme(scheme: str, /, **changes: Any) -> dict[str, Any]:
    schemes = copy.deepcopy(SCHEMES)
    schemes[scheme].update(changes)
    return schemes


def test_used_scheme_removed_is_breaking_without_exception():
    schemes = copy.deepcopy(SCHEMES)
    del schemes["apiKey"]
    base = make_spec(post_security=[{"apiKey": []}])
    head = make_spec(post_security=[{"apiKey": []}], schemes=schemes)
    change = only(diff_specs(base, head))
    assert (change.severity, change.rule_id) == (Severity.BREAKING, "security.scheme-removed")
    assert change.message == "Security scheme removed: apiKey"
    assert change.location == "components.securitySchemes.apiKey"


def test_unused_scheme_changes_are_ignored():
    schemes = _with_scheme("apiKey", **{"in": "query", "name": "api_key"})
    del schemes["bearer"]
    base = make_spec(post_security=OAUTH_READ)
    head = make_spec(post_security=OAUTH_READ, schemes=schemes)
    assert security_changes(diff_specs(base, head)) == []


def test_scheme_type_changed_is_breaking():
    head = make_spec(post_security=[{"apiKey": []}], schemes=_with_scheme("apiKey", type="http", scheme="bearer"))
    change = only(diff_specs(make_spec(post_security=[{"apiKey": []}]), head))
    assert (change.rule_id, change.message) == (
        "security.scheme-type-changed",
        "Security scheme type changed: apiKey -> http",
    )
    assert change.location == "components.securitySchemes.apiKey.type"


def test_apikey_header_name_changed_is_breaking():
    head = make_spec(post_security=[{"apiKey": []}], schemes=_with_scheme("apiKey", name="X-Partner-Key"))
    change = only(diff_specs(make_spec(post_security=[{"apiKey": []}]), head))
    assert (change.severity, change.rule_id) == (Severity.BREAKING, "security.apikey-name-changed")
    assert change.message == "API key header changed: X-API-Key -> X-Partner-Key"
    assert change.location == "components.securitySchemes.apiKey.name"


def test_apikey_header_name_case_change_is_not_a_change():
    head = make_spec(post_security=[{"apiKey": []}], schemes=_with_scheme("apiKey", name="x-api-key"))
    assert security_changes(diff_specs(make_spec(post_security=[{"apiKey": []}]), head)) == []


def test_apikey_header_to_query_is_breaking():
    head = make_spec(
        post_security=[{"apiKey": []}], schemes=_with_scheme("apiKey", **{"in": "query", "name": "api_key"})
    )
    changes = security_changes(diff_specs(make_spec(post_security=[{"apiKey": []}]), head))
    assert changes == [
        ("BREAKING", "security.apikey-location-changed", "POST /payments", "API key location changed: header -> query"),
        ("BREAKING", "security.apikey-name-changed", "POST /payments", "API key query changed: X-API-Key -> api_key"),
    ]


def test_http_bearer_to_basic_is_breaking():
    head = make_spec(post_security=[{"bearer": []}], schemes=_with_scheme("bearer", scheme="basic"))
    change = only(diff_specs(make_spec(post_security=[{"bearer": []}]), head))
    assert (change.severity, change.rule_id) == (Severity.BREAKING, "security.http-scheme-changed")
    assert change.message == "HTTP authentication scheme changed: bearer -> basic"


def test_http_scheme_is_case_insensitive():
    head = make_spec(post_security=[{"bearer": []}], schemes=_with_scheme("bearer", scheme="Bearer"))
    assert security_changes(diff_specs(make_spec(post_security=[{"bearer": []}]), head)) == []


def test_bearer_format_changed_is_warning():
    head = make_spec(post_security=[{"bearer": []}], schemes=_with_scheme("bearer", bearerFormat="opaque"))
    change = only(diff_specs(make_spec(post_security=[{"bearer": []}]), head))
    assert (change.severity, change.rule_id) == (Severity.WARNING, "security.bearer-format-changed")


def test_oauth_flow_removed_and_token_url_changed_are_warnings():
    schemes = copy.deepcopy(SCHEMES)
    schemes["oauth2"]["flows"] = {
        "authorizationCode": {"authorizationUrl": "https://a", "tokenUrl": "https://t", "scopes": {}}
    }
    base_schemes = copy.deepcopy(SCHEMES)
    base_schemes["oauth2"]["flows"]["authorizationCode"] = {
        "authorizationUrl": "https://a",
        "tokenUrl": "https://old",
        "scopes": {},
    }
    base = make_spec(post_security=OAUTH_READ, schemes=base_schemes)
    head = make_spec(post_security=OAUTH_READ, schemes=schemes)
    assert [c[:2] + (c[3],) for c in security_changes(diff_specs(base, head))] == [
        (
            "WARNING",
            "security.oauth-flow-changed",
            "OAuth authorizationCode tokenUrl changed: https://old -> https://t",
        ),
        ("WARNING", "security.oauth-flow-removed", "OAuth flow removed: clientCredentials"),
    ]


def test_oidc_url_changed_is_warning():
    head = make_spec(post_security=[{"oidc": []}], schemes=_with_scheme("oidc", openIdConnectUrl="https://new"))
    assert only(diff_specs(make_spec(post_security=[{"oidc": []}]), head)).rule_id == "security.oidc-url-changed"


def test_documentation_only_scheme_change_no_finding():
    schemes = _with_scheme("apiKey", description="Issued in the partner portal")
    schemes["oauth2"]["flows"]["clientCredentials"]["scopes"]["payments:read"] = "Read payments and refunds"
    base = make_spec(post_security=OAUTH_OR_KEY)
    head = make_spec(post_security=OAUTH_OR_KEY, schemes=schemes)
    assert diff_specs(base, head).changes == []


def test_scheme_via_ref_is_resolved():
    base_schemes = {**SCHEMES, "apiKey": {"$ref": "#/components/x-schemes/key"}}
    base = make_spec(post_security=[{"apiKey": []}], schemes=base_schemes)
    base["components"]["x-schemes"] = {"key": copy.deepcopy(SCHEMES["apiKey"])}
    head = copy.deepcopy(base)
    head["components"]["x-schemes"]["key"]["in"] = "cookie"
    assert only(diff_specs(base, head)).rule_id == "security.apikey-location-changed"


def test_scheme_change_reported_for_each_operation_using_it():
    base = make_spec(root_security=[{"apiKey": []}])
    head = make_spec(root_security=[{"apiKey": []}], schemes=_with_scheme("apiKey", name="X-Key"))
    changes = security_changes(diff_specs(base, head))
    assert [(c[1], c[2]) for c in changes] == [
        ("security.apikey-name-changed", "GET /payments"),
        ("security.apikey-name-changed", "POST /payments"),
    ]


# -- edge cases ------------------------------------------------------------------------------


def test_missing_components_and_security_schemes():
    base = make_spec(post_security=OAUTH_READ)
    head = make_spec(post_security=OAUTH_READ)
    del base["components"]
    head["components"] = {}
    assert security_changes(diff_specs(base, head)) == []
    head2 = make_spec(post_security=OAUTH_READ_WRITE)
    del head2["components"]
    assert only(diff_specs(base, head2)).rule_id == "security.scope-added"


def test_scheme_undefined_in_base_is_not_reported_as_removed():
    base = make_spec(post_security=[{"customKey": []}])
    head = make_spec(post_security=[{"customKey": []}])
    del head["components"]["securitySchemes"]
    assert security_changes(diff_specs(base, head)) == []


def test_custom_looking_scheme_name():
    schemes = {"x-Partner.Auth v2": {"type": "apiKey", "in": "header", "name": "X-P"}}
    base = make_spec(post_security=[{"x-Partner.Auth v2": []}, *OAUTH_READ], schemes={**SCHEMES, **schemes})
    head = make_spec(post_security=OAUTH_READ, schemes={**SCHEMES, **schemes})
    change = only(diff_specs(base, head))
    assert change.message == "Authentication alternative removed: x-Partner.Auth v2"


def test_malformed_security_does_not_crash():
    base = make_spec(post_security=[{"oauth2": "not-a-list"}, "junk"])
    head = make_spec(post_security=[{"oauth2": None}])
    assert security_changes(diff_specs(base, head)) == []


def test_added_or_removed_operations_produce_no_security_findings():
    base = make_spec(root_security=OAUTH_READ)
    head = copy.deepcopy(base)
    del head["paths"]["/payments"]["get"]
    assert security_changes(diff_specs(base, head)) == []


def test_security_rules_can_be_ignored():
    result = diff_specs(
        make_spec(post_security=OAUTH_READ),
        make_spec(post_security=OAUTH_READ_WRITE),
        ignore_rules=["security.scope-added"],
    )
    assert not result.has_breaking


# -- OpenAPI 3.0 / 3.1 ------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
def test_examples_in_both_openapi_versions(version):
    base, head = load_spec(SEC_BASE), load_spec(SEC_BREAKING)
    base["openapi"] = head["openapi"] = version
    assert security_changes(diff_specs(base, head)) == [
        (
            "BREAKING",
            "security.alternative-removed",
            "GET /payments/{id}",
            "Authentication alternative removed: ApiKeyAuth",
        ),
        ("BREAKING", "security.scope-added", "POST /payments", "Required OAuth scope added: payments:write"),
    ]


def test_openapi_31_mutual_tls_type_change():
    schemes = {"mtls": {"type": "mutualTLS"}}
    base = make_spec(post_security=[{"mtls": []}], version="3.1.0", schemes=schemes)
    head = make_spec(
        post_security=[{"mtls": []}], version="3.1.0", schemes={"mtls": {"type": "http", "scheme": "basic"}}
    )
    assert only(diff_specs(base, head)).message == "Security scheme type changed: mutualTLS -> http"


# -- examples, reports, version policy, CLI -----------------------------------------------------


def test_compatible_example_has_no_breaking_or_warning():
    result = diff_specs(load_spec(SEC_BASE), load_spec(SEC_COMPATIBLE))
    assert result.breaking == [] and result.warnings == []
    assert [c.rule_id for c in result.changes] == ["security.alternative-added"]


def test_reports_and_summary_include_security_findings():
    result = diff_specs(load_spec(SEC_BASE), load_spec(SEC_BREAKING))
    text = render_text(result)
    assert "BREAKING CHANGES: 2" in text
    assert "Changed (2):\n- POST /payments: 1 breaking\n- GET /payments/{id}: 1 breaking" in text
    assert "- POST /payments\n  Required OAuth scope added: payments:write\n  security.OAuth2" in text

    md = render_markdown(result)
    assert "**POST /payments** — Required OAuth scope added: payments:write" in md
    assert "`GET /payments/{id}` — ❌ 1 breaking" in md

    data = json.loads(render_json(result))
    assert data["summary"] == {"breaking": 2, "warnings": 0, "non_breaking": 0}
    assert {
        "severity": "BREAKING",
        "rule_id": "security.scope-added",
        "method": "POST",
        "path": "/payments",
        "location": "security.OAuth2",
        "message": "Required OAuth scope added: payments:write",
    } in data["changes"]


def test_version_policy_requires_major_for_security_breaking():
    policy = VersionPolicy(enabled=True)
    minor = diff_specs(load_spec(SEC_BASE), load_spec(SEC_BREAKING), version_policy=policy)
    assert minor.version is not None and not minor.version.ok
    assert minor.version.required_bump.value == "major"
    assert minor.version.violations[0].rule_id == "version.insufficient-bump"

    major = diff_specs(load_spec(SEC_BASE), load_spec(SEC_BREAKING_MAJOR), version_policy=policy)
    assert major.version is not None and major.version.ok
    assert len(major.breaking) == 2  # policy satisfied, findings stay visible


def test_cli_exit_codes_on_security_examples(capsys):
    assert main(["--base", str(SEC_BASE), "--head", str(SEC_BREAKING)]) == 1
    assert "Authentication alternative removed: ApiKeyAuth" in capsys.readouterr().out
    assert main(["--base", str(SEC_BASE), "--head", str(SEC_COMPATIBLE)]) == 0
    args = ["--base", str(SEC_BASE), "--no-fail-on-breaking", "--version-policy", "error", "--head"]
    assert main([*args, str(SEC_BREAKING)]) == 3
    assert main([*args, str(SEC_BREAKING_MAJOR)]) == 0


def test_existing_examples_have_no_security_findings():
    for head in ("openapi-v2-breaking.yaml", "openapi-v2-compatible.yaml", "openapi-v2-breaking-unversioned.yaml"):
        result = diff_specs(load_spec(EXAMPLES / "openapi-v1.yaml"), load_spec(EXAMPLES / head))
        assert security_changes(result) == []
