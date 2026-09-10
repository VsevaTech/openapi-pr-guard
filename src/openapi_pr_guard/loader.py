"""Loading OpenAPI documents and resolving local ``$ref`` pointers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

import yaml

MAX_REF_HOPS = 32


class SpecError(Exception):
    """The specification could not be read or is not a usable OpenAPI 3.x document."""


class UnresolvableRef(SpecError):
    """A ``$ref`` points outside the document or to a missing location."""


def load_spec(path: str | Path) -> dict[str, Any]:
    """Read a YAML or JSON OpenAPI document from disk and validate its shape."""
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SpecError(f"{file}: file not found") from exc
    except OSError as exc:
        raise SpecError(f"{file}: {exc.strerror}") from exc
    return parse_spec(text, source=str(file))


def parse_spec(text: str, source: str = "<string>") -> dict[str, Any]:
    """Parse OpenAPI text (YAML or JSON — JSON is valid YAML) and validate its shape."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError(f"{source}: invalid YAML/JSON: {exc}") from exc
    validate_spec(document, source)
    return document


def validate_spec(document: Any, source: str = "<document>") -> None:
    """Minimal structural validation: enough to guarantee the diff engine can walk it."""
    if not isinstance(document, dict):
        raise SpecError(f"{source}: document root must be a mapping")
    version = document.get("openapi")
    if not isinstance(version, str) or not version.startswith("3."):
        raise SpecError(f"{source}: 'openapi' must be a version string starting with '3.'")
    paths = document.get("paths", {})
    if paths is None:
        paths = {}
    if not isinstance(paths, dict):
        raise SpecError(f"{source}: 'paths' must be a mapping")
    for path, item in paths.items():
        if not isinstance(path, str) or not path.startswith("/"):
            raise SpecError(f"{source}: path key {path!r} must be a string starting with '/'")
        if item is not None and not isinstance(item, dict):
            raise SpecError(f"{source}: path item for {path!r} must be a mapping")


class Resolved(NamedTuple):
    value: Any
    name: str | None
    """Human-friendly name of the referenced component (last pointer segment), if any."""


class Resolver:
    """Resolves local JSON pointers (``#/components/schemas/User``) inside one document."""

    def __init__(self, document: dict[str, Any]) -> None:
        self._document = document

    def resolve(self, node: Any) -> Resolved:
        """Follow ``$ref`` chains until a concrete node is reached.

        Returns the node unchanged when it is not a reference. Raises
        :class:`UnresolvableRef` for external or dangling references.
        """
        name: str | None = None
        current = node
        for _ in range(MAX_REF_HOPS):
            ref = _ref_of(current)
            if ref is None:
                return Resolved(current, name)
            name = ref.rsplit("/", 1)[-1]
            current = self._lookup(ref)
        raise UnresolvableRef(f"reference chain too deep starting at {_ref_of(node)!r}")

    def _lookup(self, ref: str) -> Any:
        if not ref.startswith("#/"):
            raise UnresolvableRef(f"only local references are supported, got {ref!r}")
        current: Any = self._document
        for raw_segment in ref[2:].split("/"):
            segment = raw_segment.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict) and segment in current:
                current = current[segment]
            elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
                current = current[int(segment)]
            else:
                raise UnresolvableRef(f"reference {ref!r} does not exist in the document")
        return current


def _ref_of(node: Any) -> str | None:
    if isinstance(node, dict) and isinstance(node.get("$ref"), str):
        return node["$ref"]
    return None
