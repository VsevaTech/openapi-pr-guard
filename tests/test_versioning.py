import pytest

from openapi_pr_guard.config import Config, ConfigError, parse_version_policy
from openapi_pr_guard.diff import diff_specs
from openapi_pr_guard.versioning import Bump, ChangeLevel, PolicySeverity, Scheme, SemVer, VersionPolicy

POLICY = VersionPolicy(enabled=True)


def _set_version(spec, version):
    spec["info"]["version"] = version
    return spec


def _make_breaking(spec):
    spec["paths"]["/users/{id}"].pop("delete", None)
    return spec


def _make_additive(spec):
    spec["paths"]["/users"]["get"]["parameters"].append({"name": "cursor", "in": "query", "schema": {"type": "string"}})
    return spec


def _make_docs_only(spec):
    spec["paths"]["/users"]["get"]["summary"] = "List all users"
    return spec


def check(base, head, policy=POLICY, **kwargs):
    result = diff_specs(base, head, version_policy=policy, **kwargs)
    assert result.version is not None
    return result.version


def violation_ids(version_check):
    return [v.rule_id for v in version_check.violations]


# -- SemVer parsing -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.2.3", SemVer(1, 2, 3)),
        ("v10.0.1", SemVer(10, 0, 1)),
        ("2.0.0-rc.1+build.5", SemVer(2, 0, 0, ("rc", "1"))),
    ],
)
def test_semver_parse(text, expected):
    assert SemVer.parse(text) == expected


@pytest.mark.parametrize("text", ["1.2", "01.2.3", "latest", "2024-05-01", "1.2.3.4"])
def test_semver_parse_rejects_non_semver(text):
    assert SemVer.parse(text) is None


def test_semver_precedence_puts_prereleases_before_release():
    ordered = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1"]
    keys = [SemVer.parse(v).precedence_key() for v in ordered]
    assert keys == sorted(keys)


# -- policy outcomes ------------------------------------------------------------


def test_disabled_policy_produces_no_version_check(base, head):
    assert diff_specs(base, _make_breaking(head)).version is None
    assert diff_specs(base, _make_breaking(head), version_policy=VersionPolicy()).version is None


def test_breaking_change_without_version_bump_is_a_blocking_violation(base, head):
    result = check(base, _make_breaking(head))
    assert result.change_level is ChangeLevel.BREAKING
    assert result.required_bump is Bump.MAJOR
    assert result.actual_bump is Bump.NONE
    assert violation_ids(result) == ["version.not-bumped"]
    assert "info.version is still 1.0.0" in result.violations[0].message
    assert result.blocking


def test_breaking_change_with_minor_bump_is_insufficient(base, head):
    result = check(base, _set_version(_make_breaking(head), "1.1.0"))
    assert violation_ids(result) == ["version.insufficient-bump"]
    assert "1.0.0 -> 1.1.0 is a minor bump" in result.violations[0].message


def test_breaking_change_with_major_bump_passes(base, head):
    result = check(base, _set_version(_make_breaking(head), "2.0.0"))
    assert result.ok and result.actual_bump is Bump.MAJOR


def test_additive_change_needs_minor(base, head):
    assert violation_ids(check(base, _set_version(_make_additive(head), "1.0.1"))) == ["version.insufficient-bump"]


def test_additive_change_with_minor_passes(base, head):
    result = check(base, _set_version(_make_additive(head), "1.1.0"))
    assert result.ok and result.change_level is ChangeLevel.NON_BREAKING


def test_docs_only_change_needs_no_bump(base, head):
    result = check(base, _make_docs_only(head))
    assert result.ok
    assert result.change_level is ChangeLevel.DOCS
    assert result.required_bump is Bump.NONE


def test_no_changes_need_no_bump(base, head):
    result = check(base, head)
    assert result.ok and result.change_level is ChangeLevel.NONE


def test_bigger_bump_than_required_is_fine(base, head):
    assert check(base, _set_version(_make_docs_only(head), "3.0.0")).ok


def test_downgrade_is_a_violation_even_without_changes(base, head):
    result = check(_set_version(base, "1.4.0"), _set_version(head, "1.3.9"))
    assert violation_ids(result) == ["version.downgraded"]


def test_invalid_head_version(base, head):
    result = check(base, _set_version(_make_breaking(head), "next"))
    assert violation_ids(result) == ["version.invalid"]
    assert result.actual_bump is None


def test_missing_head_version_only_matters_when_a_bump_is_required(base, head):
    del head["info"]["version"]
    assert check(base, head).ok
    assert violation_ids(check(base, _make_breaking(head))) == ["version.missing"]


def test_non_semver_base_only_validates_head(base, head):
    result = check(_set_version(base, "legacy"), _set_version(_make_breaking(head), "1.0.0"))
    assert result.ok
    assert "not a semantic version" in result.notes[0]


def test_yaml_integer_version_is_accepted(base, head):
    # `version: 2` in YAML is an int; with scheme `any` it still compares as a string
    result = check(_set_version(base, 1), _set_version(_make_breaking(head), 2), VersionPolicy(True, Scheme.ANY))
    assert result.ok and result.head_version == "2"


def test_pre_1_0_shift_accepts_minor_for_breaking(base, head):
    result = check(_set_version(base, "0.3.1"), _set_version(_make_breaking(head), "0.4.0"))
    assert result.ok
    assert "0.x version" in result.notes[0]
    assert violation_ids(check(_set_version(base, "0.3.1"), _set_version(_make_breaking(head), "0.3.2"))) == [
        "version.insufficient-bump"
    ]


def test_pre_1_0_strict_requires_major(base, head):
    policy = VersionPolicy(enabled=True, pre_1_0="strict")
    result = check(_set_version(base, "0.3.1"), _set_version(_make_breaking(head), "0.4.0"), policy)
    assert violation_ids(result) == ["version.insufficient-bump"]


def test_iterating_on_a_prerelease_is_accepted(base, head):
    base = _set_version(base, "2.0.0-rc.1")
    assert check(base, _set_version(_make_breaking(head), "2.0.0-rc.2")).ok
    assert check(base, _set_version(_make_breaking(head), "2.0.0")).ok


def test_scheme_any_requires_only_a_change(base, head):
    policy = VersionPolicy(enabled=True, scheme=Scheme.ANY)
    base = _set_version(base, "2024-05-01")
    assert check(base, _set_version(_make_breaking(head), "2024-06-12"), policy).ok
    assert violation_ids(check(base, _set_version(_make_breaking(head), "2024-05-01"), policy)) == [
        "version.not-bumped"
    ]


def test_custom_requirements(base, head):
    policy = VersionPolicy(enabled=True)
    policy.require[ChangeLevel.BREAKING] = Bump.MINOR
    policy.require[ChangeLevel.NON_BREAKING] = Bump.NONE
    assert check(base, _set_version(_make_breaking(head), "1.1.0"), policy).ok
    assert check(base, _make_additive(head), policy).ok


def test_warning_severity_reports_but_does_not_block(base, head):
    policy = VersionPolicy(enabled=True, severity=PolicySeverity.WARNING)
    result = check(base, _make_breaking(head), policy)
    assert not result.ok and not result.blocking


def test_ignore_rules_apply_to_version_rules(base, head):
    assert check(base, _make_breaking(head), ignore_rules=["version.not-bumped"]).ok


def test_warning_level_changes_need_minor(base, head):
    head["components"]["schemas"]["User"]["properties"]["name"]["maxLength"] = 10
    result = check(base, head)
    assert result.change_level is ChangeLevel.WARNING
    assert violation_ids(result) == ["version.not-bumped"]


# -- config -------------------------------------------------------------------


def test_config_bool_shorthand():
    assert Config.from_dict({"version_policy": True}, "cfg").version_policy.enabled
    assert not Config.from_dict({}, "cfg").version_policy.enabled


def test_config_full_policy():
    policy = parse_version_policy(
        {
            "scheme": "any",
            "severity": "warn",
            "pre-1-0": "strict",
            "require": {"breaking": "minor", "non-breaking": "patch", "docs": "none"},
        },
        "cfg",
    )
    assert policy.enabled
    assert policy.scheme is Scheme.ANY
    assert policy.severity is PolicySeverity.WARNING
    assert policy.pre_1_0 == "strict"
    assert policy.require[ChangeLevel.BREAKING] is Bump.MINOR
    assert policy.require[ChangeLevel.NON_BREAKING] is Bump.PATCH
    assert policy.require[ChangeLevel.WARNING] is Bump.MINOR  # untouched default


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ("yes please", "boolean or a mapping"),
        ({"schema": "semver"}, "unknown version_policy key"),
        ({"scheme": "calver"}, "version_policy.scheme"),
        ({"require": {"breaking": "huge"}}, "version_policy.require.breaking"),
        ({"require": {"cosmetic": "none"}}, "version_policy.require' key"),
        ({"pre_1_0": "loose"}, "pre_1_0"),
        ({"enabled": "on"}, "enabled"),
    ],
)
def test_config_errors(data, fragment):
    with pytest.raises(ConfigError, match=fragment.replace(".", r"\.")):
        parse_version_policy(data, "cfg")


def test_pre_1_0_shift_reports_the_effective_requirement(base, head):
    result = check(_set_version(base, "0.3.1"), _set_version(_make_breaking(head), "0.3.1"))
    assert result.required_bump is Bump.MINOR
    assert "a minor bump is required" in result.violations[0].message
