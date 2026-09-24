"""Optional project configuration read from ``.openapi-pr-guard.yaml``."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from openapi_pr_guard.versioning import Bump, ChangeLevel, PolicySeverity, Scheme, VersionPolicy

DEFAULT_CONFIG_FILE = ".openapi-pr-guard.yaml"


class ConfigError(Exception):
    """The configuration file is present but malformed."""


@dataclass(slots=True)
class Config:
    fail_on_breaking: bool = True
    ignore_rules: list[str] = field(default_factory=list)
    version_policy: VersionPolicy = field(default_factory=VersionPolicy)

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
        policy = data.get("version_policy", data.get("version-policy"))
        if policy is not None:
            config.version_policy = parse_version_policy(policy, source)
        return config


_POLICY_KEYS = {"enabled", "scheme", "severity", "require", "pre_1_0"}


def parse_version_policy(data: Any, source: str) -> VersionPolicy:
    """``version_policy: true`` enables the defaults; a mapping tunes them."""
    if isinstance(data, bool):
        return VersionPolicy(enabled=data)
    if not isinstance(data, dict):
        raise ConfigError(f"{source}: 'version_policy' must be a boolean or a mapping")
    data = {str(k).replace("-", "_"): v for k, v in data.items()}
    unknown = sorted(data.keys() - _POLICY_KEYS)
    if unknown:
        raise ConfigError(f"{source}: unknown version_policy key(s): {', '.join(unknown)}")

    policy = VersionPolicy(enabled=True)
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"{source}: 'version_policy.enabled' must be a boolean")
    policy.enabled = enabled
    policy.scheme = _enum(Scheme, data.get("scheme", policy.scheme), f"{source}: 'version_policy.scheme'")
    severity = data.get("severity", policy.severity)
    if isinstance(severity, str) and severity.strip().lower() == "warn":
        severity = PolicySeverity.WARNING
    policy.severity = _enum(PolicySeverity, severity, f"{source}: 'version_policy.severity'")
    pre = data.get("pre_1_0", policy.pre_1_0)
    if pre not in ("shift", "strict"):
        raise ConfigError(f"{source}: 'version_policy.pre_1_0' must be 'shift' or 'strict'")
    policy.pre_1_0 = pre

    require = data.get("require", {})
    if not isinstance(require, dict):
        raise ConfigError(f"{source}: 'version_policy.require' must be a mapping of change level to bump")
    for raw_level, raw_bump in require.items():
        level = _enum(
            ChangeLevel, str(raw_level).replace("-", "_"), f"{source}: 'version_policy.require' key", skip={"none"}
        )
        policy.require[level] = _enum(Bump, raw_bump, f"{source}: 'version_policy.require.{raw_level}'")
    return policy


def _enum[E: StrEnum](kind: type[E], value: Any, what: str, skip: frozenset[str] | set[str] = frozenset()) -> E:
    choices = [m.value for m in kind if m.value not in skip]
    if isinstance(value, str) and value.strip().lower() in choices:
        return kind(value.strip().lower())
    raise ConfigError(f"{what} must be one of: {', '.join(choices)} (got {value!r})")


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
