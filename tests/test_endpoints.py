from openapi_pr_guard.models import Severity
from tests.conftest import diff, find, rule_ids


def test_identical_specs_produce_no_changes(base, head):
    assert diff(base, head).changes == []


def test_endpoint_removed_is_breaking_per_method(base, head):
    del head["paths"]["/users/{id}"]
    result = diff(base, head)
    removed = [c for c in result.breaking if c.rule_id == "endpoint.removed"]
    assert {(c.method, c.path) for c in removed} == {("GET", "/users/{id}"), ("DELETE", "/users/{id}")}


def test_endpoint_added_is_non_breaking(base, head):
    head["paths"]["/subscriptions"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    result = diff(base, head)
    assert not result.has_breaking
    change = find(result, "endpoint.added")
    assert (change.method, change.path, change.severity) == ("GET", "/subscriptions", Severity.NON_BREAKING)


def test_method_removed_is_breaking(base, head):
    del head["paths"]["/users/{id}"]["delete"]
    change = find(diff(base, head), "operation.removed")
    assert change.severity is Severity.BREAKING
    assert (change.method, change.path) == ("DELETE", "/users/{id}")


def test_method_added_is_non_breaking(base, head):
    head["paths"]["/users/{id}"]["patch"] = {"responses": {"200": {"description": "ok"}}}
    change = find(diff(base, head), "operation.added")
    assert change.severity is Severity.NON_BREAKING
    assert change.method == "PATCH"


def test_path_parameter_rename_is_the_same_endpoint(base, head):
    head["paths"]["/users/{userId}"] = head["paths"].pop("/users/{id}")
    head["paths"]["/users/{userId}"]["parameters"][0]["name"] = "userId"
    result = diff(base, head)
    assert "endpoint.removed" not in rule_ids(result)
    # the renamed path parameter is reported, but as a parameter change, not as a lost endpoint
    assert not any(c.rule_id.startswith("endpoint.") for c in result.changes)


def test_description_only_change_is_non_breaking(base, head):
    head["paths"]["/users"]["get"]["summary"] = "List all users"
    head["paths"]["/users"]["get"]["description"] = "Now with a description"
    head["components"]["schemas"]["User"]["properties"]["name"]["description"] = "Full name"
    head["components"]["schemas"]["User"]["properties"]["name"]["example"] = "Jane"
    result = diff(base, head)
    assert not result.has_breaking
    assert result.warnings == []
    assert rule_ids(result) == ["operation.docs-changed"]


def test_operation_id_change_is_warning(base, head):
    base["paths"]["/users"]["get"]["operationId"] = "listUsers"
    head["paths"]["/users"]["get"]["operationId"] = "getUsers"
    change = find(diff(base, head), "operation.operation-id-changed")
    assert change.severity is Severity.WARNING


def test_deprecation_is_non_breaking(base, head):
    head["paths"]["/users"]["get"]["deprecated"] = True
    change = find(diff(base, head), "operation.deprecated")
    assert change.severity is Severity.NON_BREAKING
