from openapi_pr_guard.models import Severity
from tests.conftest import diff, find, rule_ids


def _user(spec):
    return spec["components"]["schemas"]["User"]


def test_response_property_removed_is_breaking(base, head):
    del _user(head)["properties"]["address"]
    result = diff(base, head)
    removed = [c for c in result.breaking if c.rule_id == "schema.property-removed"]
    # User is returned by three operations; each is reported with its own location
    assert {(c.method, c.path) for c in removed} == {("GET", "/users"), ("POST", "/users"), ("GET", "/users/{id}")}
    by_endpoint = {(c.method, c.path): c for c in removed}
    assert by_endpoint[("GET", "/users/{id}")].location == (
        "responses.200.content.application/json.schema[User].properties.address"
    )
    assert by_endpoint[("GET", "/users")].location == (
        "responses.200.content.application/json.schema.items[User].properties.address"
    )
    assert removed[0].message == "Response property removed: address"


def test_response_property_added_is_non_breaking(base, head):
    _user(head)["properties"]["email"] = {"type": "string"}
    result = diff(base, head)
    assert not result.has_breaking
    assert set(rule_ids(result)) == {"schema.property-added"}


def test_response_field_type_changed_is_breaking(base, head):
    _user(head)["properties"]["id"]["type"] = "integer"
    result = diff(base, head)
    change = next(c for c in result.breaking if c.rule_id == "schema.type-changed")
    assert change.message == "Response type changed: string -> integer"


def test_response_becoming_nullable_is_breaking(base, head):
    _user(head)["properties"]["name"]["nullable"] = True
    change = next(c for c in diff(base, head).breaking if c.rule_id == "schema.type-changed")
    assert change.message == "Response type changed: string -> null|string"


def test_response_code_removed_is_breaking(base, head):
    del head["paths"]["/users/{id}"]["get"]["responses"]["404"]
    change = find(diff(base, head), "response.code-removed")
    assert change.severity is Severity.BREAKING
    assert change.location == "responses.404"


def test_response_code_added_is_non_breaking(base, head):
    head["paths"]["/users"]["post"]["responses"]["429"] = {"description": "too many"}
    assert find(diff(base, head), "response.code-added").severity is Severity.NON_BREAKING


def test_response_required_property_became_optional_is_warning(base, head):
    _user(head)["required"] = []
    result = diff(base, head)
    assert not result.has_breaking
    assert all(c.rule_id == "schema.property-became-optional" for c in result.warnings)
    assert len(result.warnings) == 3


def test_response_media_type_removed_is_breaking(base, head):
    responses = base["paths"]["/users/{id}"]["get"]["responses"]
    responses["200"]["content"]["text/csv"] = {"schema": {"type": "string"}}
    assert find(diff(base, head), "response.media-type-removed").severity is Severity.BREAKING


def test_new_enum_value_in_response_is_warning(base, head):
    _user(base)["properties"]["status"] = {"type": "string", "enum": ["active"]}
    _user(head)["properties"]["status"] = {"type": "string", "enum": ["active", "blocked"]}
    result = diff(base, head)
    assert not result.has_breaking
    assert set(rule_ids(result, Severity.WARNING)) == {"schema.enum-values-added"}


def test_recursive_schema_does_not_loop(base, head):
    for spec in (base, head):
        spec["components"]["schemas"]["Node"] = {
            "type": "object",
            "properties": {"children": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}}},
        }
        spec["paths"]["/tree"] = {
            "get": {
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Node"}}},
                    }
                }
            }
        }
    head["components"]["schemas"]["Node"]["properties"]["label"] = {"type": "string"}
    result = diff(base, head)
    assert rule_ids(result) == ["schema.property-added"]


def test_unresolvable_ref_is_warning(base, head):
    head["paths"]["/users/{id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/Missing"
    }
    change = find(diff(base, head), "schema.unresolvable-ref")
    assert change.severity is Severity.WARNING
