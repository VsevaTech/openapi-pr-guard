"""OpenAPI PR Guard — detect breaking changes between two OpenAPI specifications."""

from openapi_pr_guard.diff import diff_specs
from openapi_pr_guard.loader import SpecError, load_spec
from openapi_pr_guard.models import Change, DiffResult, Severity
from openapi_pr_guard.versioning import VersionCheck, VersionPolicy

__version__ = "0.2.0"

__all__ = [
    "Change",
    "DiffResult",
    "Severity",
    "SpecError",
    "VersionCheck",
    "VersionPolicy",
    "__version__",
    "diff_specs",
    "load_spec",
]
