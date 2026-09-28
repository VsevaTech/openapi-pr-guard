"""Response header compatibility and parameter serialization (style / explode / allowReserved)."""

from openapi_pr_guard.models import Severity
from tests.conftest import diff, find, rule_ids


def _headers(spec, code="201"):
    return spec["paths"]["/users"]["post"]["responses"][code].setdefault("headers", {})


def _retry_after(schema_type="integer", **extra):
    return {"schema": {"type": schema_type}, **extra}


# -- response headers ---------------------------------------------------------------


def test_response_header_removed_is_breaking(base, head):
    _headers(base, "400")["Retry-After"] = _retry_after()
    head["paths"]["/users"]["post"]["responses"]["400"] = {"description": "bad request"}
    change = find(diff(base, head), "response.header-removed")
    assert change.severity is Severity.BREAKING
    assert change.message == "Response header removed: Retry-After"
    assert change.location == "responses.400.headers.Retry-After"


def test_response_header_added_is_non_breaking(base, head):
    _headers(head)["Location"] = {"schema": {"type": "string", "format": "uri"}}
    assert rule_ids(diff(base, head)) == ["response.header-added"]
    assert not diff(base, head).has_breaking


def test_response_header_type_changed_is_breaking(base, head):
    _headers(base)["X-Request-ID"] = _retry_after("string")
    _headers(head)["X-Request-ID"] = _retry_after("integer")
    change = find(diff(base, head), "schema.type-changed")
    assert change.severity is Severity.BREAKING
    assert change.location == "responses.201.headers.X-Request-ID.schema"


def test_response_header_names_are_case_insensitive(base, head):
    _headers(base)["X-Correlation-ID"] = _retry_after("string")
    _headers(head)["x-correlation-id"] = _retry_after("string")
    assert diff(base, head).changes == []


def test_response_header_no_longer_required_is_warning(base, head):
    _headers(base)["Idempotency-Key"] = _retry_after("string", required=True)
    _headers(head)["Idempotency-Key"] = _retry_after("string")
    assert find(diff(base, head), "response.header-no-longer-required").severity is Severity.WARNING


def test_response_header_via_ref(base, head):
    for spec, header_type in ((base, "integer"), (head, "string")):
        spec["components"]["headers"] = {"RetryAfter": _retry_after(header_type)}
        _headers(spec)["Retry-After"] = {"$ref": "#/components/headers/RetryAfter"}
    assert find(diff(base, head), "schema.type-changed").message == "Response type changed: integer -> string"


def test_unchanged_headers_no_finding(base, head):
    _headers(base)["Retry-After"] = _retry_after(description="old")
    _headers(head)["Retry-After"] = _retry_after(description="new")
    assert diff(base, head).changes == []


# -- parameter serialization ---------------------------------------------------------


def _ids_param(spec, **extra):
    param = {"name": "merchant_ids", "in": "query", "schema": {"type": "array", "items": {"type": "string"}}, **extra}
    spec["paths"]["/users"]["get"]["parameters"].append(param)
    return param


def test_explode_changed_on_array_is_breaking(base, head):
    # merchant_ids=a&merchant_ids=b  ->  merchant_ids=a,b
    _ids_param(base)
    _ids_param(head, explode=False)
    change = find(diff(base, head), "parameter.explode-changed")
    assert change.severity is Severity.BREAKING
    assert change.message == "Parameter explode changed: merchant_ids (query): true -> false"
    assert change.location == "parameters.query.merchant_ids.explode"


def test_explicit_defaults_are_not_a_change(base, head):
    _ids_param(base)
    _ids_param(head, style="form", explode=True)
    assert diff(base, head).changes == []


def test_style_changed_on_array_is_breaking(base, head):
    _ids_param(base)
    _ids_param(head, style="pipeDelimited")
    assert find(diff(base, head), "parameter.style-changed").severity is Severity.BREAKING


def test_explode_changed_on_primitive_is_not_reported(base, head):
    head["paths"]["/users"]["get"]["parameters"][0]["explode"] = False  # limit: integer
    assert diff(base, head).changes == []


def test_path_label_style_is_breaking_even_for_primitives(base, head):
    head["paths"]["/users/{id}"]["parameters"][0]["style"] = "label"
    changes = [c for c in diff(base, head).changes if c.rule_id == "parameter.style-changed"]
    # path-level parameter: every operation of the path is affected
    assert {(c.severity, c.method) for c in changes} == {(Severity.BREAKING, "GET"), (Severity.BREAKING, "DELETE")}
    assert changes[0].message == "Parameter serialization style changed: id (path): simple -> label"


def test_style_change_with_unknown_schema_is_warning(base, head):
    base["paths"]["/users"]["get"]["parameters"].append({"name": "filter", "in": "query", "schema": {}})
    head["paths"]["/users"]["get"]["parameters"].append(
        {"name": "filter", "in": "query", "schema": {}, "style": "deepObject"}
    )
    assert find(diff(base, head), "parameter.style-changed").severity is Severity.WARNING


def test_allow_reserved_removed_is_warning(base, head):
    base["paths"]["/users"]["get"]["parameters"][0]["allowReserved"] = True
    assert find(diff(base, head), "parameter.allow-reserved-removed").severity is Severity.WARNING
    # the other direction only widens what the server accepts
    assert diff(head, base).changes == []
