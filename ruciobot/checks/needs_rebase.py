"""
Needs-rebase check: comment on PRs that cannot be merged due to conflicts.

A conflicted PR is flagged immediately with a comment and the ``needs-rebase``
label. If the conflicts remain and the PR sees no activity for
``NEEDS_REBASE_WARN_DAYS`` weekdays, the author is warned that the PR will be
closed; after ``NEEDS_REBASE_CLOSE_DAYS`` further weekdays of inactivity it is
closed. Whether the warning was already issued is tracked through a hidden
marker in the warning comment itself; the warning is deleted once the
conflicts are resolved, so a later conflict starts a fresh cycle.

A PR that also carries the ``failing-tests`` label is left to the
failing-tests check, which takes precedence: the escalation pauses while
that label is present, though flagging and label clearing still run.
"""

from datetime import UTC, datetime

from github.GithubException import RateLimitExceededException
from github.PullRequest import PullRequest
from github.Repository import Repository

from .base import BaseCheck, count_business_days, exclusion_reason
from .failing_tests import FAILING_TESTS_LABEL

NEEDS_REBASE_LABEL = "needs-rebase"
NEEDS_REBASE_WARN_DAYS = 5  # Weekdays of inactivity before the closure warning.
NEEDS_REBASE_CLOSE_DAYS = 5  # Weekdays of inactivity (after warning) before closing.

# Hidden marker embedded in the warning comment; its presence on a PR is how
# the bot remembers that the closure warning has already been issued.
WARNING_MARKER = "<!-- ruciobot:needs-rebase-warning -->"

REBASE_COMMENT = (
    "This PR currently has merge conflicts with the target branch. "
    "Please rebase it on top of the latest `master` (or target branch) so it can be merged. "
    "If you need help with rebasing, visit the "
    "[Rucio contributing guide](https://rucio.github.io/documentation/contributing/)."
)

REBASE_WARNING_COMMENT = (
    f"{WARNING_MARKER}\n"
    "This PR still has merge conflicts with the target branch and has had no "
    f"activity for {NEEDS_REBASE_WARN_DAYS} weekdays. It will be closed in "
    f"{NEEDS_REBASE_CLOSE_DAYS} weekdays unless the conflicts are resolved or "
    "new activity is recorded."
)

REBASE_CLOSE_COMMENT = (
    "Closing this PR because its merge conflicts have remained unresolved, with "
    f"no activity, for {NEEDS_REBASE_CLOSE_DAYS} weekdays after the warning. "
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
            _escalate_inactive_pr(pr)
    else:
        # Conflicts were resolved — remove the label and warning if still present.
        if already_labeled:
            _clear_needs_rebase_flag(pr)


def _is_labeled_needs_rebase(pr: PullRequest) -> bool:
    return NEEDS_REBASE_LABEL in [lbl.name for lbl in pr.labels]


def _flag_pr_needs_rebase(pr: PullRequest) -> None:
    print(f"  [WARN] PR #{pr.number} has merge conflicts. Commenting and labeling.")
    pr.create_issue_comment(REBASE_COMMENT)
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

    if not _has_warning_comment(pr):
        if inactive_days >= NEEDS_REBASE_WARN_DAYS:
            _warn_pr(pr, inactive_days)
    elif inactive_days >= NEEDS_REBASE_CLOSE_DAYS:
        _close_pr(pr)


def _has_warning_comment(pr: PullRequest) -> bool:
    """True if the closure warning has already been posted on this PR.

    Defaults to ``False`` when the comments cannot be fetched: the harmless
    failure mode is a repeated warning, never an unannounced close.
    """
    try:
        return any(WARNING_MARKER in (c.body or "") for c in pr.get_issue_comments())
    except RateLimitExceededException:
        raise  # rate limits are owned by BaseCheck: back off, or stop the run
    except Exception as e:
        print(f"  [WARN] Could not fetch comments for PR #{pr.number}: {e}")
        return False


def _warn_pr(pr: PullRequest, inactive_days: int) -> None:
    print(
        f"  [WARN] PR #{pr.number} still has merge conflicts after "
        f"{inactive_days} weekdays of inactivity. Warning the author."
    )
    pr.create_issue_comment(REBASE_WARNING_COMMENT)


def _close_pr(pr: PullRequest) -> None:
    print(f"  [CLOSE] PR #{pr.number} has had unresolved merge conflicts for too long. Closing.")
    pr.create_issue_comment(REBASE_CLOSE_COMMENT)
    pr.edit(state="closed")


def _clear_needs_rebase_flag(pr: PullRequest) -> None:
    print(f"  [INFO] PR #{pr.number} conflicts resolved. Removing '{NEEDS_REBASE_LABEL}' label.")
    pr.remove_from_labels(NEEDS_REBASE_LABEL)
    _delete_warning_comments(pr)


def _delete_warning_comments(pr: PullRequest) -> None:
    """Delete stale closure warnings so a future conflict starts a fresh cycle."""
    try:
        # Materialise the list first: deleting while iterating an offset-paginated
        # list shifts later pages and can silently skip comments.
        for comment in list(pr.get_issue_comments()):
            if WARNING_MARKER in (comment.body or ""):
                comment.delete()
    except RateLimitExceededException:
        raise  # rate limits are owned by BaseCheck: back off, or stop the run
    except Exception as e:
        print(f"  [WARN] Could not clean up warning comments for PR #{pr.number}: {e}")
