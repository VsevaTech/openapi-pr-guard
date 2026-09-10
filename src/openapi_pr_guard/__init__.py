"""OpenAPI PR Guard — detect breaking changes between two OpenAPI specifications."""

from openapi_pr_guard.diff import diff_specs
from openapi_pr_guard.loader import SpecError, load_spec
from openapi_pr_guard.models import Change, DiffResult, Severity

__version__ = "0.1.0"

__all__ = [
    "Change",
    "DiffResult",
    "Severity",
    "SpecError",
    "__version__",
    "diff_specs",
    "load_spec",
]
