"""
Needs-rebase check: comment on PRs that cannot be merged due to conflicts.

While the conflicts remain unresolved, the author is reminded with a comment
after every ``REMIND_DAYS`` weekdays of inactivity. The PR is never closed by
this check.
"""

from datetime import UTC, datetime

from github.PullRequest import PullRequest
from github.Repository import Repository

from .base import BaseCheck, count_business_days, exclusion_reason

NEEDS_REBASE_LABEL = "needs-rebase"
REMIND_DAYS = 5  # Weekdays of inactivity before the author is reminded again.

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
            _remind_if_inactive(pr)
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


def _remind_if_inactive(pr: PullRequest) -> None:
    """Re-ping the author of an already-labeled PR that has seen no activity.

    Each reminder bumps the PR's ``updated_at``, so the next reminder fires
    only after another ``REMIND_DAYS`` weekdays of silence. Any author
    activity also bumps ``updated_at`` and thereby pushes the reminder back.
    """
    now = datetime.now(UTC)
    assert pr.updated_at is not None, f"PR #{pr.number} has no updated_at timestamp"
    last_updated = pr.updated_at.replace(tzinfo=UTC)
    inactive_days = count_business_days(last_updated, now)

    if inactive_days < REMIND_DAYS:
        print(f"  [INFO] PR #{pr.number} already labeled '{NEEDS_REBASE_LABEL}'. Skipping.")
        return

    print(
        f"  [REMIND] PR #{pr.number} still has merge conflicts after "
        f"{inactive_days} weekdays of inactivity. Reminding the author."
    )
    pr.create_issue_comment(_rebase_reminder(pr))


def _rebase_reminder(pr: PullRequest) -> str:
    reminder = (
        "Friendly reminder: this PR still has merge conflicts with the "
        "target branch and cannot be merged. "
        "Please rebase it on top of the latest `master` (or target branch). "
        "If you need help with rebasing, visit the "
        "[Rucio contributing guide](https://rucio.github.io/documentation/contributing/)."
    )
    login = pr.user.login if pr.user else None
    return f"@{login} {reminder}" if login else reminder


def _clear_needs_rebase_label(pr: PullRequest) -> None:
    print(f"  [INFO] PR #{pr.number} conflicts resolved. Removing '{NEEDS_REBASE_LABEL}' label.")
    pr.remove_from_labels(NEEDS_REBASE_LABEL)
