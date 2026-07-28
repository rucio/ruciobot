"""
Stale PR check.

A PR is only marked stale and eventually closed when it is waiting on its
*author*: a reviewer has engaged and the author has not pushed or replied
since. A PR that is waiting on the *maintainers* is never closed for
inactivity. That covers a PR that has never been reviewed, one with a pending
review request, and one where the author has already responded to the last
review and is waiting for another look. Such PRs are surfaced with a
``needs-review`` label so reviewers can pick them up.

PRs labeled ``failing-tests`` or ``needs-rebase`` are skipped: those checks
run their own warn-and-close escalations and take precedence. Lingering
``stale`` or ``needs-review`` labels are cleared on the way out so they do
not outlive the countdown they belonged to.
"""

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
from .needs_rebase import NEEDS_REBASE_LABEL

STALE_LABEL = "stale"
NEEDS_REVIEW_LABEL = "needs-review"

# Comment kinds; the shared prefix scopes cleanup to this check's comments.
KIND_PREFIX = "stale-"
KIND_WARNING = "stale-warning"
KIND_CLOSE = "stale-close"
WARN_DAYS = 14
CLOSE_DAYS = 7  # Weekdays after the stale warning before the PR is closed.

# Which side a PR is waiting on.
AWAITING_REVIEW = "awaiting_review"  # waiting on the maintainers
AUTHOR_BLOCKED = "author_blocked"  # waiting on the author
APPROVED = "approved"  # waiting on a merge

COMPONENT_LEADS = "https://rucio.github.io/documentation/component_leads"


class StalePRCheck(BaseCheck):
    """Marks author-blocked PRs stale and closes them; labels review-blocked PRs."""

    summary = "Checking for stale PRs"

    def __init__(self, days_until_stale: int = WARN_DAYS):
        self.days_until_stale = days_until_stale

    def process(self, pr: PullRequest, repo: Repository) -> None:
        process_pr(pr, self.days_until_stale)


# Helpers


def process_pr(pr: PullRequest, days_until_stale: int) -> None:
    """Apply the stale / needs-review logic to a single PR."""
    reason = exclusion_reason(pr)
    if reason:
        print(f"  [SKIP] PR #{pr.number} {reason}. Skipping.")
        return

    # Failing-tests and conflicted PRs are owned by their respective checks,
    # which run their own warn-and-close escalations, so this check's
    # countdown must not compete with them. Lingering labels from this check
    # are lifted on the way out so they do not outlive the countdown they
    # belonged to.
    for owner_label in (FAILING_TESTS_LABEL, NEEDS_REBASE_LABEL):
        if _has_label(pr, owner_label):
            print(
                f"  [SKIP] PR #{pr.number} has '{owner_label}' label; "
                "that check runs its own escalation. Skipping."
            )
            if _has_label(pr, STALE_LABEL):
                _clear_stale_label(pr, f"is handled by the {owner_label} check")
            if _has_label(pr, NEEDS_REVIEW_LABEL):
                _clear_label(pr, NEEDS_REVIEW_LABEL, f"is handled by the {owner_label} check")
            return

    now = datetime.now(UTC)
    assert pr.updated_at is not None, f"PR #{pr.number} has no updated_at timestamp"
    inactive_days = count_business_days(_to_utc(pr.updated_at), now)

    labeled_stale = _has_label(pr, STALE_LABEL)
    labeled_needs_review = _has_label(pr, NEEDS_REVIEW_LABEL)

    # Determining the responsible side is API-heavy (reviews, commits, comments),
    # so skip it until there is something to act on: either the PR has been
    # inactive long enough, or it carries a bot label that may need clearing
    # after fresh activity.
    if inactive_days < days_until_stale and not labeled_stale and not labeled_needs_review:
        return

    court = _court_of_responsibility(pr)

    if court != AUTHOR_BLOCKED:
        # Not the author's turn, so this PR is never closed for staleness.
        if labeled_stale:
            _clear_stale_label(pr, "has new activity or is awaiting review")
        if court == AWAITING_REVIEW:
            if inactive_days >= days_until_stale and not labeled_needs_review:
                _flag_awaiting_review(pr, inactive_days)
        elif labeled_needs_review:
            # APPROVED: it is now waiting on a merge, not a review.
            _clear_label(pr, NEEDS_REVIEW_LABEL, "is approved")
        return

    # The author owes a response.
    if labeled_needs_review:
        _clear_label(pr, NEEDS_REVIEW_LABEL, "is now waiting on the author")

    if labeled_stale:
        if inactive_days >= CLOSE_DAYS:
            _close_stale_pr(pr)
    elif inactive_days >= days_until_stale:
        _mark_pr_stale(pr, days_until_stale)


def _court_of_responsibility(pr: PullRequest) -> str:
    """Decide whether an open PR is waiting on the author, the maintainers, or a merge.

    - ``APPROVED``: an approving review exists, so it is waiting on a merge.
    - ``AUTHOR_BLOCKED``: a reviewer engaged more recently than the author last
      pushed or commented, so the author owes a response.
    - ``AWAITING_REVIEW``: anything else (never reviewed, a pending review
      request, or the author acted most recently), so it is waiting on review.
    """
    author = _login(pr.user)
    reviews = _safe_list(pr.get_reviews, "reviews")

    if any(r.state == "APPROVED" and _is_other(r, author) for r in reviews):
        return APPROVED

    if _has_pending_review_request(pr):
        return AWAITING_REVIEW

    review_times = [
        _to_utc(r.submitted_at)
        for r in reviews
        if r.state in ("CHANGES_REQUESTED", "COMMENTED")
        and _is_other(r, author)
        and r.submitted_at is not None
    ]
    last_review = max(review_times, default=None)
    if last_review is None:
        return AWAITING_REVIEW  # never substantively reviewed

    last_author = _last_author_activity(pr, author)
    if last_author is None or last_author >= last_review:
        return AWAITING_REVIEW  # the author acted most recently

    return AUTHOR_BLOCKED


def _last_author_activity(pr: PullRequest, author: str | None) -> datetime | None:
    """Most recent time the author pushed a commit or left a comment."""
    times: list[datetime] = []
    for commit in _safe_list(pr.get_commits, "commits"):
        committer = commit.commit.committer if commit.commit else None
        if committer is not None and committer.date is not None:
            times.append(_to_utc(committer.date))
    for comment in _safe_list(pr.get_issue_comments, "comments"):
        if _is_author(comment, author) and comment.created_at is not None:
            times.append(_to_utc(comment.created_at))
    return max(times, default=None)


def _flag_awaiting_review(pr: PullRequest, inactive_days: int) -> None:
    """Label a review-blocked PR so reviewers can find it (no comment is posted)."""
    print(
        f"  [REVIEW] PR #{pr.number} has waited {inactive_days} weekdays for review. "
        f"Labeling '{NEEDS_REVIEW_LABEL}'."
    )
    pr.add_to_labels(NEEDS_REVIEW_LABEL)


def _mark_pr_stale(pr: PullRequest, days: int) -> None:
    print(f"  [WARN] PR #{pr.number} is inactive for {days}+ weekdays. Marking stale.")
    post_bot_comment(
        pr,
        KIND_WARNING,
        f"This PR has had no activity for {days} weekdays and is waiting on its author. "
        f"It has been marked as **stale** and will be closed in {CLOSE_DAYS} weekdays "
        "unless there is new activity.",
    )
    pr.add_to_labels(STALE_LABEL)


def _close_stale_pr(pr: PullRequest) -> None:
    print(f"  [CLOSE] PR #{pr.number} has been stale for too long. Closing.")
    kind, comment = latest_bot_comment(pr)
    warned_on = comment.created_at if kind == KIND_WARNING and comment is not None else None
    recap = f"A stale warning was issued on {warned_on:%Y-%m-%d}. " if warned_on else ""
    post_bot_comment(
        pr,
        KIND_CLOSE,
        "Closing this PR due to prolonged inactivity. "
        f"{recap}"
        "Feel free to reopen it if you would like to continue working on it. "
        "If you believe this action was a mistake, please reach out to a member of the "
        f"[Rucio review team]({COMPONENT_LEADS}) with an explanation.",
    )
    pr.edit(state="closed")


# Small GitHub helpers


def _has_label(pr: PullRequest, label: str) -> bool:
    return label in [lbl.name for lbl in pr.labels]


def _clear_label(pr: PullRequest, label: str, reason: str) -> None:
    print(f"  [INFO] PR #{pr.number} {reason}. Removing '{label}' label.")
    pr.remove_from_labels(label)


def _clear_stale_label(pr: PullRequest, reason: str) -> None:
    """Lift the stale label and the warning comment that came with it."""
    _clear_label(pr, STALE_LABEL, reason)
    # Only this check's comments: another check's active comment must survive.
    delete_bot_comments(pr, KIND_PREFIX)


def _has_pending_review_request(pr: PullRequest) -> bool:
    users_requested, teams_requested = pr.get_review_requests()
    return users_requested.totalCount > 0 or teams_requested.totalCount > 0


def _login(user) -> str | None:
    return user.login if user else None


def _is_other(obj, author: str | None) -> bool:
    """True if obj (a review/comment) was made by someone other than the author."""
    login = _login(getattr(obj, "user", None))
    return login is not None and login != author


def _is_author(obj, author: str | None) -> bool:
    """True if obj (a review/comment) was made by the PR author."""
    return author is not None and _login(getattr(obj, "user", None)) == author


def _safe_list(getter, label: str) -> list:
    """Call a PyGithub list endpoint, returning [] on any error."""
    try:
        return list(getter())
    except Exception as e:
        print(f"  [WARN] Could not fetch {label}: {e}")
        return []


def _to_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
