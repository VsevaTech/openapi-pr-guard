"""Rule registry. Add a new rule class here to enable it."""

from openapi_pr_guard.rules.base import Rule
from openapi_pr_guard.rules.endpoints import EndpointPresenceRule, OperationMetadataRule
from openapi_pr_guard.rules.parameters import ParametersRule
from openapi_pr_guard.rules.request_body import RequestBodyRule
from openapi_pr_guard.rules.responses import ResponsesRule
from openapi_pr_guard.rules.security import SecurityRule

DEFAULT_RULES: tuple[Rule, ...] = (
    EndpointPresenceRule(),
    ParametersRule(),
    RequestBodyRule(),
    ResponsesRule(),
    SecurityRule(),
    OperationMetadataRule(),
)

__all__ = ["DEFAULT_RULES", "Rule"]
