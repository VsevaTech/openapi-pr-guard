import pytest

from openapi_pr_guard.loader import Resolver, SpecError, UnresolvableRef, load_spec, parse_spec


def test_load_yaml_and_json(tmp_path):
    (tmp_path / "spec.yaml").write_text("openapi: 3.1.0\ninfo: {title: t, version: '1'}\npaths: {}\n")
    (tmp_path / "spec.json").write_text('{"openapi": "3.0.0", "info": {"title": "t", "version": "1"}, "paths": {}}')
    assert load_spec(tmp_path / "spec.yaml")["openapi"] == "3.1.0"
    assert load_spec(tmp_path / "spec.json")["openapi"] == "3.0.0"


def test_missing_file_raises_spec_error(tmp_path):
    with pytest.raises(SpecError, match="file not found"):
        load_spec(tmp_path / "nope.yaml")


def test_invalid_yaml_raises_spec_error():
    with pytest.raises(SpecError, match="invalid YAML"):
        parse_spec("openapi: 3.0.0\npaths: [unclosed\n")


@pytest.mark.parametrize(
    "text",
    [
        "just a string",
        "- a\n- list\n",
        "swagger: '2.0'\npaths: {}\n",
        "openapi: 3.0.0\npaths: []\n",
        "openapi: 3.0.0\npaths:\n  users: {}\n",
    ],
)
def test_invalid_openapi_shape_raises_spec_error(text):
    with pytest.raises(SpecError):
        parse_spec(text)


def test_resolver_follows_local_refs_and_reports_name():
    doc = {
        "components": {
            "schemas": {
                "A": {"$ref": "#/components/schemas/B"},
                "B": {"type": "string"},
                "with/slash": {"type": "integer"},
            }
        }
    }
    resolver = Resolver(doc)
    resolved = resolver.resolve({"$ref": "#/components/schemas/A"})
    assert resolved.value == {"type": "string"}
    assert resolved.name == "B"
    assert resolver.resolve({"$ref": "#/components/schemas/with~1slash"}).value == {"type": "integer"}
    assert resolver.resolve({"type": "number"}) == ({"type": "number"}, None)


def test_resolver_rejects_external_and_missing_refs():
    resolver = Resolver({"components": {"schemas": {}}})
    with pytest.raises(UnresolvableRef):
        resolver.resolve({"$ref": "other.yaml#/components/schemas/X"})
    with pytest.raises(UnresolvableRef):
        resolver.resolve({"$ref": "#/components/schemas/Missing"})


def test_resolver_detects_ref_cycles():
    doc = {
        "components": {"schemas": {"A": {"$ref": "#/components/schemas/B"}, "B": {"$ref": "#/components/schemas/A"}}}
    }
    with pytest.raises(UnresolvableRef, match="too deep"):
        Resolver(doc).resolve({"$ref": "#/components/schemas/A"})
