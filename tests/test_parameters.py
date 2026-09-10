from openapi_pr_guard.models import Severity
from tests.conftest import diff, find, rule_ids


def _get_params(spec):
    return spec["paths"]["/users"]["get"]["parameters"]


def test_required_parameter_added_is_breaking(base, head):
    _get_params(head).append({"name": "tenant", "in": "header", "required": True, "schema": {"type": "string"}})
    change = find(diff(base, head), "parameter.required-added")
    assert change.severity is Severity.BREAKING
    assert (change.method, change.path) == ("GET", "/users")
    assert change.location == "parameters.header.tenant"
    assert "tenant" in change.message


def test_optional_parameter_added_is_non_breaking(base, head):
    _get_params(head).append({"name": "cursor", "in": "query", "schema": {"type": "string"}})
    result = diff(base, head)
    assert not result.has_breaking
    change = find(result, "parameter.optional-added")
    assert change.severity is Severity.NON_BREAKING
    assert change.location == "parameters.query.cursor"


def test_parameter_removed_is_breaking(base, head):
    _get_params(head).clear()
    change = find(diff(base, head), "parameter.removed")
    assert change.severity is Severity.BREAKING
    assert "limit" in change.message


def test_parameter_became_required_is_breaking(base, head):
    _get_params(head)[0]["required"] = True
    assert find(diff(base, head), "parameter.became-required").severity is Severity.BREAKING


def test_parameter_became_optional_is_non_breaking(base, head):
    _get_params(base)[0]["required"] = True
    assert find(diff(base, head), "parameter.became-optional").severity is Severity.NON_BREAKING


def test_parameter_type_change_is_breaking(base, head):
    _get_params(head)[0]["schema"]["type"] = "string"
    change = find(diff(base, head), "schema.type-changed")
    assert change.severity is Severity.BREAKING
    assert change.location == "parameters.query.limit.schema"
    assert change.message == "Request type changed: integer -> string"


def test_parameter_type_widening_is_non_breaking(base, head):
    _get_params(head)[0]["schema"]["type"] = ["integer", "string"]
    result = diff(base, head)
    assert not result.has_breaking
    assert "schema.type-widened" in rule_ids(result)


def test_path_level_parameter_removed_is_breaking(base, head):
    # the path parameter is declared on the path item, not on the operation
    del head["paths"]["/users/{id}"]["parameters"]
    result = diff(base, head)
    assert {(c.method, c.rule_id) for c in result.breaking} == {
        ("GET", "parameter.removed"),
        ("DELETE", "parameter.removed"),
    }


def test_parameter_via_component_ref_is_resolved(base, head):
    head["components"]["parameters"] = {
        "Tenant": {"name": "tenant", "in": "header", "required": True, "schema": {"type": "string"}}
    }
    _get_params(head).append({"$ref": "#/components/parameters/Tenant"})
    assert find(diff(base, head), "parameter.required-added").severity is Severity.BREAKING


def test_enum_value_removed_from_request_parameter_is_breaking(base, head):
    _get_params(base)[0]["schema"] = {"type": "string", "enum": ["a", "b"]}
    _get_params(head)[0]["schema"] = {"type": "string", "enum": ["a"]}
    change = find(diff(base, head), "schema.enum-values-removed")
    assert change.severity is Severity.BREAKING
    assert "b" in change.message
