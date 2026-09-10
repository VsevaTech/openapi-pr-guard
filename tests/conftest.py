"""Shared helpers: build small specs in code and diff them."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from openapi_pr_guard.diff import diff_specs
from openapi_pr_guard.models import Change, DiffResult, Severity

USER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id"],
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "address": {"type": "string"},
    },
}

BASE_SPEC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Test", "version": "1.0.0"},
    "paths": {
        "/users": {
            "get": {
                "summary": "List users",
                "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer"}}],
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {"type": "array", "items": {"$ref": "#/components/schemas/User"}}
                            }
                        },
                    }
                },
            },
            "post": {
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CreateUser"}}},
                },
                "responses": {
                    "201": {
                        "description": "created",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}},
                    },
                    "400": {"description": "bad request"},
                },
            },
        },
        "/users/{id}": {
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
            "get": {
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/User"}}},
                    },
                    "404": {"description": "not found"},
                }
            },
            "delete": {"responses": {"204": {"description": "deleted"}}},
        },
    },
    "components": {
        "schemas": {
            "User": USER_SCHEMA,
            "CreateUser": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}, "phone": {"type": "string"}},
            },
        }
    },
}


@pytest.fixture
def base() -> dict[str, Any]:
    return copy.deepcopy(BASE_SPEC)


@pytest.fixture
def head() -> dict[str, Any]:
    return copy.deepcopy(BASE_SPEC)


def diff(base: dict[str, Any], head: dict[str, Any]) -> DiffResult:
    return diff_specs(base, head)


def rule_ids(result: DiffResult, severity: Severity | None = None) -> list[str]:
    changes = result.changes if severity is None else result.by_severity(severity)
    return sorted(c.rule_id for c in changes)


def find(result: DiffResult, rule_id: str) -> Change:
    matches = [c for c in result.changes if c.rule_id == rule_id]
    assert len(matches) == 1, f"expected exactly one {rule_id}, got {matches}"
    return matches[0]
