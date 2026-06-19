"""
Needs-rebase check: comment on PRs that cannot be merged due to conflicts.
"""

from github.PullRequest import PullRequest
from github.Repository import Repository

from .base import BaseCheck, exclusion_reason

NEEDS_REBASE_LABEL = "needs-rebase"

REBASE_COMMENT = (
    "This PR currently has merge conflicts with the target branch. "
    "Please rebase it on top of the latest `master` (or target branch) so it can be merged. "
    "If you need help with rebasing, visit the "
    "[Rucio contributing guide](https://rucio.github.io/documentation/contributing/)."
)


class NeedsRebaseCheck(BaseCheck):
    """Comments on and labels PRs that have merge conflicts."""

    summary = "Checking for PRs that need rebasing"

    def process(self, pr: PullRequest, repo: Repository) -> None:
        process_needs_rebase_pr(pr)


# Helpers


def process_needs_rebase_pr(pr: PullRequest) -> None:
    """Comment on and label a PR if it has unresolved merge conflicts."""
    reason = exclusion_reason(pr)
    if reason:
        print(f"  [SKIP] PR #{pr.number} {reason}. Skipping.")
        return

    mergeable = pr.mergeable  # None = GitHub hasn't computed it yet; False = conflicts

    if mergeable is None:
        # GitHub hasn't determined mergeability yet — skip for now; next run will catch it.
        print(f"  [SKIP] PR #{pr.number} mergeability not yet determined. Skipping.")
        return

    already_labeled = _is_labeled_needs_rebase(pr)

    if not mergeable:
        if not already_labeled:
            _flag_pr_needs_rebase(pr)
        else:
            print(f"  [INFO] PR #{pr.number} already labeled '{NEEDS_REBASE_LABEL}'. Skipping.")
    else:
        # Conflicts were resolved — remove the label if it is still present.
        if already_labeled:
            _clear_needs_rebase_label(pr)


def _is_labeled_needs_rebase(pr: PullRequest) -> bool:
    return NEEDS_REBASE_LABEL in [lbl.name for lbl in pr.labels]


def _flag_pr_needs_rebase(pr: PullRequest) -> None:
    print(f"  [WARN] PR #{pr.number} has merge conflicts. Commenting and labeling.")
    pr.create_issue_comment(REBASE_COMMENT)
    pr.add_to_labels(NEEDS_REBASE_LABEL)


def _clear_needs_rebase_label(pr: PullRequest) -> None:
    print(f"  [INFO] PR #{pr.number} conflicts resolved. Removing '{NEEDS_REBASE_LABEL}' label.")
    pr.remove_from_labels(NEEDS_REBASE_LABEL)
