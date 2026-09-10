from openapi_pr_guard.models import Severity
from tests.conftest import diff, find, rule_ids


def _create_user(spec):
    return spec["components"]["schemas"]["CreateUser"]


def test_optional_property_became_required_is_breaking(base, head):
    _create_user(head)["required"].append("phone")
    change = find(diff(base, head), "schema.property-became-required")
    assert change.severity is Severity.BREAKING
    assert (change.method, change.path) == ("POST", "/users")
    assert change.location == "requestBody.content.application/json.schema[CreateUser].properties.phone"
    assert change.message == "Request property became required: phone"


def test_new_required_request_property_is_breaking(base, head):
    _create_user(head)["properties"]["email"] = {"type": "string"}
    _create_user(head)["required"].append("email")
    change = find(diff(base, head), "schema.required-property-added")
    assert change.severity is Severity.BREAKING
    assert change.location.endswith("properties.email")


def test_new_optional_request_property_is_non_breaking(base, head):
    _create_user(head)["properties"]["email"] = {"type": "string"}
    result = diff(base, head)
    assert not result.has_breaking
    assert find(result, "schema.property-added").severity is Severity.NON_BREAKING


def test_request_property_type_changed_is_breaking(base, head):
    _create_user(head)["properties"]["phone"]["type"] = "integer"
    change = find(diff(base, head), "schema.type-changed")
    assert change.severity is Severity.BREAKING
    assert change.message == "Request type changed: string -> integer"


def test_request_property_removed_is_warning(base, head):
    del _create_user(head)["properties"]["phone"]
    change = find(diff(base, head), "schema.property-removed")
    assert change.severity is Severity.WARNING


def test_request_body_became_required_is_breaking(base, head):
    base["paths"]["/users"]["post"]["requestBody"]["required"] = False
    assert find(diff(base, head), "request-body.became-required").severity is Severity.BREAKING


def test_request_media_type_removed_is_breaking(base, head):
    content = base["paths"]["/users"]["post"]["requestBody"]["content"]
    content["application/xml"] = {"schema": {"type": "object"}}
    change = find(diff(base, head), "request-body.media-type-removed")
    assert change.severity is Severity.BREAKING
    assert "application/xml" in change.message


def test_nested_required_property_is_detected(base, head):
    for spec in (base, head):
        _create_user(spec)["properties"]["address"] = {
            "type": "object",
            "properties": {"street": {"type": "string"}, "zip": {"type": "string"}},
        }
    _create_user(head)["properties"]["address"]["required"] = ["zip"]
    change = find(diff(base, head), "schema.property-became-required")
    assert change.location.endswith("properties.address.properties.zip")


def test_composition_change_is_warning_not_breaking(base, head):
    _create_user(head)["properties"]["phone"] = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    result = diff(base, head)
    assert not result.has_breaking
    assert rule_ids(result, Severity.WARNING) == ["schema.composition-changed"]


def test_constraint_tightening_is_warning(base, head):
    _create_user(head)["properties"]["name"]["maxLength"] = 10
    change = find(diff(base, head), "schema.constraint-changed")
    assert change.severity is Severity.WARNING
    assert "maxLength" in change.message
