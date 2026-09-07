"""
Check registry — maps CLI action names to check instances.

When adding a new feature, add a new entry here.
"""

from .failing_tests import FailingTestsCheck
from .needs_rebase import NeedsRebaseCheck
from .pr_template import PRTemplateCheck
from .stale_prs import StalePRCheck

CHECKS = {
    "pr-template": PRTemplateCheck(),
    "failing-tests": FailingTestsCheck(),
    "needs-rebase": NeedsRebaseCheck(),
    "stale": StalePRCheck(),
}
