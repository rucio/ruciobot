"""
Stale PR check: warn after WARN_DAYS of inactivity, close after CLOSE_DAYS more.
"""

from datetime import UTC, datetime

from github.PullRequest import PullRequest
from github.Repository import Repository

from .base import NO_BOT_LABEL, BaseCheck, count_business_days, is_excluded_from_bot

STALE_LABEL = "stale"
WARN_DAYS = 14
CLOSE_DAYS = 7  # Days after warning to close


class StalePRCheck(BaseCheck):
    """Marks inactive PRs as stale and closes them if they remain inactive."""

    summary = "Checking for stale PRs"

    def __init__(self, days_until_stale: int = WARN_DAYS):
        self.days_until_stale = days_until_stale

    def process(self, pr: PullRequest, repo: Repository) -> None:
        process_pr(pr, self.days_until_stale)


# Helpers


def process_pr(pr: PullRequest, days_until_stale: int) -> None:
    """Process a single PR to check for staleness."""
    if is_excluded_from_bot(pr):
        print(f"  [SKIP] PR #{pr.number} has '{NO_BOT_LABEL}' label. Skipping.")
        return
    now = datetime.now(UTC)
    assert pr.updated_at is not None, f"PR #{pr.number} has no updated_at timestamp"
    last_updated = pr.updated_at.replace(tzinfo=UTC)

    inactive_business_days = count_business_days(last_updated, now)

    if _is_labeled_stale(pr):
        # Activity resumed — lift the stale label instead of closing.
        if _is_awaiting_review(pr) or _is_approved(pr):
            _clear_stale_label(pr)
        elif inactive_business_days >= CLOSE_DAYS:
            _close_stale_pr(pr)
    else:
        if inactive_business_days >= days_until_stale:
            if _is_awaiting_review(pr):
                print(f"  [SKIP] PR #{pr.number} is awaiting reviewer response. Skipping.")
            elif _is_approved(pr):
                print(f"  [SKIP] PR #{pr.number} has an approved review. Skipping.")
            else:
                _mark_pr_stale(pr, days_until_stale)


def _is_labeled_stale(pr: PullRequest) -> bool:
    return STALE_LABEL in [lbl.name for lbl in pr.labels]


def _is_awaiting_review(pr: PullRequest) -> bool:
    users_requested, teams_requested = pr.get_review_requests()
    return users_requested.totalCount > 0 or teams_requested.totalCount > 0


def _is_approved(pr: PullRequest) -> bool:
    """Return True if the PR has at least one approved review."""
    return any(review.state == "APPROVED" for review in pr.get_reviews())


def _clear_stale_label(pr: PullRequest) -> None:
    print(f"  [INFO] PR #{pr.number} has new activity. Removing '{STALE_LABEL}' label.")
    pr.remove_from_labels(STALE_LABEL)


def _mark_pr_stale(pr: PullRequest, days: int) -> None:
    print(f"  [WARN] PR #{pr.number} is inactive for {days}+ weekdays. Marking stale.")
    pr.create_issue_comment(
        f"This PR has had no activity for {days} weekdays and has no pending review requests. "
        f"It has been marked as **stale** and will be closed in {CLOSE_DAYS} weekdays unless "
        f"there is new activity or a reviewer is assigned."
    )
    pr.add_to_labels(STALE_LABEL)


def _close_stale_pr(pr: PullRequest) -> None:
    print(f"  [CLOSE] PR #{pr.number} has been stale for too long. Closing.")
    pr.create_issue_comment(
        "Closing this PR due to prolonged inactivity. "
        "Feel free to reopen it if you would like to continue working on it. "
        "If you believe this action was a mistake, please reach out to a member of the "
        "[Rucio review team](https://rucio.github.io/documentation/component_leads) "
        "with an explanation."
    )
    pr.edit(state="closed")
