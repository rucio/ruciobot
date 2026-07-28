"""
Needs-rebase check: comment on PRs that cannot be merged due to conflicts.

A conflicted PR is flagged immediately with a comment and the ``needs-rebase``
label. If the conflicts remain and the PR sees no activity for
``NEEDS_REBASE_WARN_DAYS`` weekdays, the author is warned that the PR will be
closed; after ``NEEDS_REBASE_CLOSE_DAYS`` further weekdays of inactivity it is
closed. Whether the warning was already issued is tracked through the kind
marker of the bot's single comment; the comment is removed once the conflicts
are resolved, so a later conflict starts a fresh cycle.

A PR that also carries the ``failing-tests`` label is left to the
failing-tests check, which takes precedence: the escalation pauses while
that label is present, though flagging and label clearing still run.
"""

import time
from datetime import UTC, datetime

from github.PullRequest import PullRequest
from github.Repository import Repository

from .base import (
    BaseCheck,
    count_business_days,
    delete_bot_comments,
    exclusion_reason,
    latest_bot_comment,
    post_bot_comment,
)
from .failing_tests import FAILING_TESTS_LABEL

NEEDS_REBASE_LABEL = "needs-rebase"
NEEDS_REBASE_WARN_DAYS = 5  # Weekdays of inactivity before the closure warning.
NEEDS_REBASE_CLOSE_DAYS = 5  # Weekdays of inactivity (after warning) before closing.

# GitHub computes mergeability lazily: every push to the base branch drops the
# cached value, and the next API read returns None while a background job
# recomputes it. Reading ``mergeable`` is what triggers that job, and it
# usually finishes within seconds, so poll briefly instead of skipping the PR
# for a whole run.
MERGEABLE_POLL_ATTEMPTS = 3
MERGEABLE_POLL_SECONDS = 5

# Comment kinds; the shared prefix scopes cleanup to this check's comments.
KIND_PREFIX = "needs-rebase-"
KIND_FLAG = "needs-rebase-flag"
KIND_WARNING = "needs-rebase-warning"
KIND_CLOSE = "needs-rebase-close"

REBASE_COMMENT = (
    "This PR currently has merge conflicts with the target branch. "
    "Please rebase it on top of the latest `master` (or target branch) so it can be merged. "
    "If you need help with rebasing, visit the "
    "[Rucio contributing guide](https://rucio.github.io/documentation/contributing/)."
)

REBASE_WARNING_COMMENT = (
    "This PR still has merge conflicts with the target branch and has had no "
    f"activity for {NEEDS_REBASE_WARN_DAYS} weekdays. It will be closed in "
    f"{NEEDS_REBASE_CLOSE_DAYS} weekdays unless the conflicts are resolved or "
    "new activity is recorded."
)


def rebase_close_comment(warned_on: datetime | None) -> str:
    """The closing comment, with a one-line recap of the replaced warning."""
    recap = f"A closure warning was issued on {warned_on:%Y-%m-%d}. " if warned_on else ""
    return (
        "Closing this PR because its merge conflicts have remained unresolved, with "
        f"no activity, for {NEEDS_REBASE_CLOSE_DAYS} weekdays after the warning. "
        f"{recap}"
        "Feel free to reopen it once the conflicts are resolved. "
        "If you believe this action was a mistake, please reach out to a member of the "
        "[Rucio review team](https://rucio.github.io/documentation/component_leads) "
        "with an explanation."
    )


class NeedsRebaseCheck(BaseCheck):
    """Comments on, labels, and eventually closes PRs that have merge conflicts."""

    summary = "Checking for PRs that need rebasing"

    def process(self, pr: PullRequest, repo: Repository) -> None:
        process_needs_rebase_pr(pr)


# Helpers


def process_needs_rebase_pr(pr: PullRequest) -> None:
    """Flag, warn about, and eventually close a PR with unresolved merge conflicts."""
    reason = exclusion_reason(pr)
    if reason:
        print(f"  [SKIP] PR #{pr.number} {reason}. Skipping.")
        return

    mergeable = _resolve_mergeable(pr)  # None = GitHub couldn't compute it; False = conflicts

    if mergeable is None:
        # Still undetermined after polling — skip for now; next run will catch it.
        print(f"  [SKIP] PR #{pr.number} mergeability not yet determined. Skipping.")
        return

    already_labeled = _is_labeled_needs_rebase(pr)

    if not mergeable:
        if not already_labeled:
            _flag_pr_needs_rebase(pr)
        else:
            _escalate_inactive_pr(pr)
    else:
        # Conflicts were resolved — remove the label and comment if still present.
        if already_labeled:
            _clear_needs_rebase_flag(pr)


def _resolve_mergeable(pr: PullRequest) -> bool | None:
    """Return the PR's mergeability, polling briefly while GitHub computes it.

    The first read of ``mergeable`` kicks off GitHub's asynchronous
    computation; re-fetch the PR a few times before giving up so quiet-hours
    runs do not skip every PR whose cached value was invalidated by a recent
    push to the base branch.
    """
    mergeable = pr.mergeable
    for _ in range(MERGEABLE_POLL_ATTEMPTS):
        if mergeable is not None:
            return mergeable
        time.sleep(MERGEABLE_POLL_SECONDS)
        pr.update()
        mergeable = pr.mergeable
    return mergeable


def _is_labeled_needs_rebase(pr: PullRequest) -> bool:
    return NEEDS_REBASE_LABEL in [lbl.name for lbl in pr.labels]


def _flag_pr_needs_rebase(pr: PullRequest) -> None:
    print(f"  [WARN] PR #{pr.number} has merge conflicts. Commenting and labeling.")
    post_bot_comment(pr, KIND_FLAG, REBASE_COMMENT)
    pr.add_to_labels(NEEDS_REBASE_LABEL)


def _escalate_inactive_pr(pr: PullRequest) -> None:
    """Warn about, and eventually close, a labeled PR that stays inactive.

    The escalation clock runs on ``updated_at``: the initial flag comment, the
    warning comment, and any author activity each reset it.
    """
    if FAILING_TESTS_LABEL in [lbl.name for lbl in pr.labels]:
        print(
            f"  [SKIP] PR #{pr.number} has '{FAILING_TESTS_LABEL}' label; "
            "the failing-tests check takes precedence. Pausing escalation."
        )
        return

    now = datetime.now(UTC)
    assert pr.updated_at is not None, f"PR #{pr.number} has no updated_at timestamp"
    inactive_days = count_business_days(pr.updated_at.replace(tzinfo=UTC), now)

    if inactive_days < min(NEEDS_REBASE_WARN_DAYS, NEEDS_REBASE_CLOSE_DAYS):
        print(f"  [INFO] PR #{pr.number} already labeled '{NEEDS_REBASE_LABEL}'. Skipping.")
        return

    kind, comment = latest_bot_comment(pr)
    if kind != KIND_WARNING:
        if inactive_days >= NEEDS_REBASE_WARN_DAYS:
            _warn_pr(pr, inactive_days)
    elif inactive_days >= NEEDS_REBASE_CLOSE_DAYS:
        _close_pr(pr, comment.created_at if comment is not None else None)


def _warn_pr(pr: PullRequest, inactive_days: int) -> None:
    print(
        f"  [WARN] PR #{pr.number} still has merge conflicts after "
        f"{inactive_days} weekdays of inactivity. Warning the author."
    )
    post_bot_comment(pr, KIND_WARNING, REBASE_WARNING_COMMENT)


def _close_pr(pr: PullRequest, warned_on: datetime | None) -> None:
    print(f"  [CLOSE] PR #{pr.number} has had unresolved merge conflicts for too long. Closing.")
    post_bot_comment(pr, KIND_CLOSE, rebase_close_comment(warned_on))
    pr.edit(state="closed")


def _clear_needs_rebase_flag(pr: PullRequest) -> None:
    print(f"  [INFO] PR #{pr.number} conflicts resolved. Removing '{NEEDS_REBASE_LABEL}' label.")
    pr.remove_from_labels(NEEDS_REBASE_LABEL)
    # Only this check's comments: another check's active comment must survive.
    delete_bot_comments(pr, KIND_PREFIX)
