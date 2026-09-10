"""Optional project configuration read from ``.openapi-pr-guard.yaml``.

Deliberately tiny for the MVP; the file is a natural place for future options
(per-rule severity overrides, ignored paths, ...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_FILE = ".openapi-pr-guard.yaml"


class ConfigError(Exception):
    """The configuration file is present but malformed."""


@dataclass(slots=True)
class Config:
    fail_on_breaking: bool = True
    ignore_rules: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any], source: str) -> Config:
        config = cls()
        fail = data.get("fail_on_breaking", data.get("fail-on-breaking", config.fail_on_breaking))
        if not isinstance(fail, bool):
            raise ConfigError(f"{source}: 'fail_on_breaking' must be a boolean")
        ignore = data.get("ignore_rules", data.get("ignore-rules", []))
        if not isinstance(ignore, list) or not all(isinstance(r, str) for r in ignore):
            raise ConfigError(f"{source}: 'ignore_rules' must be a list of rule ids")
        config.fail_on_breaking = fail
        config.ignore_rules = list(ignore)
        return config


def load_config(path: str | Path | None) -> Config:
    """Load ``path`` if given, else ``.openapi-pr-guard.yaml`` from the CWD if it exists."""
    file = Path(path) if path else Path(DEFAULT_CONFIG_FILE)
    if not file.exists():
        if path:
            raise ConfigError(f"{file}: config file not found")
        return Config()
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{file}: cannot read config: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{file}: config root must be a mapping")
    return Config.from_dict(data, str(file))
