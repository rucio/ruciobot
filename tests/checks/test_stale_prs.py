"""
Tests for the stale PR check.

The check now distinguishes who a PR is waiting on:

  * AUTHOR_BLOCKED  - a reviewer engaged and the author has not responded since;
                      this is the only state that is marked stale and closed.
  * AWAITING_REVIEW - never reviewed, a pending review request, or the author
                      acted most recently; this is labeled ``needs-review`` and
                      never closed for inactivity.
  * APPROVED        - waiting on a merge; left alone.

All timestamps are pinned to a concrete Monday (2026-03-09) so that
``count_business_days`` is deterministic regardless of when the suite runs.
"""

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ruciobot.checks.base import NO_BOT_LABEL
from ruciobot.checks.failing_tests import FAILING_TESTS_LABEL
from ruciobot.checks.needs_rebase import NEEDS_REBASE_LABEL
from ruciobot.checks.stale_prs import (
    CLOSE_DAYS,
    NEEDS_REVIEW_LABEL,
    STALE_LABEL,
    WARN_DAYS,
    process_pr,
)

# Pinned "now": a Monday at noon UTC.
NOW = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def business_days_before(n: int, anchor: datetime = NOW) -> datetime:
    """Return the datetime exactly *n* business days before *anchor*."""
    current = anchor
    counted = 0
    while counted < n:
        current -= timedelta(days=1)
        if current.weekday() < 5:  # Mon-Fri
            counted += 1
    return current


# Anchored timestamps.
RECENT = business_days_before(2)  # well within the stale threshold
PAST_CLOSE = business_days_before(CLOSE_DAYS + 1)  # past the close threshold (8 bd)
PAST_STALE = business_days_before(WARN_DAYS + 1)  # past the stale threshold (15 bd)
OLD_REVIEW = business_days_before(20)
OLD_COMMIT = business_days_before(40)


# Mock builders


class _PagedList(list):
    """A list that also exposes PyGithub's ``totalCount`` attribute."""

    @property
    def totalCount(self) -> int:
        return len(self)


def _user(login):
    return SimpleNamespace(login=login)


def _review(state, login, submitted_at):
    return SimpleNamespace(state=state, user=_user(login), submitted_at=submitted_at)


def _commit(date):
    return SimpleNamespace(commit=SimpleNamespace(committer=SimpleNamespace(date=date)))


def _comment(login, created_at):
    return SimpleNamespace(user=_user(login), created_at=created_at)


def make_pr(
    *,
    updated_at,
    labels=None,
    author="alice",
    reviews=None,
    requested_users=None,
    requested_teams=0,
    commits=None,
    comments=None,
    number=1,
):
    pr = MagicMock()
    pr.number = number
    pr.title = f"PR {number}"
    pr.updated_at = updated_at
    pr.user = _user(author) if author else None
    pr.labels = [SimpleNamespace(name=name) for name in (labels or [])]
    pr.get_reviews.return_value = list(reviews or [])
    users = _PagedList(_user(u) for u in (requested_users or []))
    teams = _PagedList(None for _ in range(requested_teams))
    pr.get_review_requests.return_value = (users, teams)
    pr.get_commits.return_value = list(commits or [])
    pr.get_issue_comments.return_value = list(comments or [])
    return pr


def run_check(pr, now=NOW):
    with patch("ruciobot.checks.stale_prs.datetime") as mock_dt:
        mock_dt.now.return_value = now
        process_pr(pr, WARN_DAYS)


class TestStalePRs(unittest.TestCase):
    # Author-blocked: the only state that is staled and closed.

    def test_marks_author_blocked_pr_stale(self):
        """Reviewer requested changes, author silent since: mark stale."""
        pr = make_pr(
            updated_at=PAST_STALE,
            reviews=[_review("CHANGES_REQUESTED", "bob", PAST_STALE)],
            commits=[_commit(OLD_COMMIT)],
        )
        run_check(pr)
        pr.add_to_labels.assert_called_once_with(STALE_LABEL)
        pr.create_issue_comment.assert_called_once()
        pr.edit.assert_not_called()

    def test_closes_author_blocked_stale_pr(self):
        """A stale-labeled, still author-blocked PR past CLOSE_DAYS is closed."""
        pr = make_pr(
            updated_at=PAST_CLOSE,
            labels=[STALE_LABEL],
            reviews=[_review("CHANGES_REQUESTED", "bob", OLD_REVIEW)],
            commits=[_commit(OLD_COMMIT)],
        )
        run_check(pr)
        pr.edit.assert_called_once_with(state="closed")
        pr.create_issue_comment.assert_called_once()

    def test_does_not_close_author_blocked_pr_before_threshold(self):
        """A stale-labeled author-blocked PR not yet past CLOSE_DAYS is left open."""
        pr = make_pr(
            updated_at=business_days_before(CLOSE_DAYS - 1),
            labels=[STALE_LABEL],
            reviews=[_review("CHANGES_REQUESTED", "bob", OLD_REVIEW)],
            commits=[_commit(OLD_COMMIT)],
        )
        run_check(pr)
        pr.edit.assert_not_called()

    # Awaiting review: never closed for inactivity.

    def test_never_reviewed_pr_is_flagged_not_staled(self):
        """An inactive PR that has never been reviewed is labeled needs-review, not stale."""
        pr = make_pr(updated_at=PAST_STALE, commits=[_commit(PAST_STALE)])
        run_check(pr)
        pr.add_to_labels.assert_called_once_with(NEEDS_REVIEW_LABEL)
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_author_responded_after_review_is_not_staled(self):
        """Author pushed after the last review (the #8516 case): awaiting review, not stale."""
        pr = make_pr(
            updated_at=PAST_STALE,
            reviews=[_review("COMMENTED", "bob", OLD_REVIEW)],
            commits=[_commit(PAST_STALE)],  # author push is more recent than the review
        )
        run_check(pr)
        pr.add_to_labels.assert_called_once_with(NEEDS_REVIEW_LABEL)
        pr.edit.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        # The stale path must not run.
        self.assertNotIn(((STALE_LABEL,), {}), [c for c in pr.add_to_labels.call_args_list])

    def test_pending_review_request_is_labeled_not_staled(self):
        """An inactive PR with a pending review request is labeled needs-review, not stale."""
        pr = make_pr(updated_at=PAST_STALE, requested_users=["bob"])
        run_check(pr)
        pr.add_to_labels.assert_called_once_with(NEEDS_REVIEW_LABEL)
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_already_labeled_awaiting_review_is_left_alone(self):
        """A PR already labeled needs-review gets no further label or comment."""
        pr = make_pr(
            updated_at=PAST_STALE,
            labels=[NEEDS_REVIEW_LABEL],
            commits=[_commit(PAST_STALE)],
        )
        run_check(pr)
        pr.create_issue_comment.assert_not_called()
        pr.add_to_labels.assert_not_called()
        pr.edit.assert_not_called()

    # Label transitions.

    def test_clears_stale_label_when_author_responds(self):
        """A stale PR where the author pushed after the review has the stale label lifted."""
        pr = make_pr(
            updated_at=RECENT,
            labels=[STALE_LABEL],
            reviews=[_review("CHANGES_REQUESTED", "bob", business_days_before(10))],
            commits=[_commit(RECENT)],
        )
        run_check(pr)
        pr.remove_from_labels.assert_called_once_with(STALE_LABEL)
        pr.edit.assert_not_called()
        pr.add_to_labels.assert_not_called()  # still within threshold, so no needs-review yet
        pr.create_issue_comment.assert_not_called()

    def test_clears_needs_review_label_when_author_becomes_blocked(self):
        """A needs-review PR that gets a fresh changes-request has the label removed."""
        pr = make_pr(
            updated_at=business_days_before(1),
            labels=[NEEDS_REVIEW_LABEL],
            reviews=[_review("CHANGES_REQUESTED", "bob", business_days_before(1))],
            commits=[_commit(business_days_before(5))],
        )
        run_check(pr)
        pr.remove_from_labels.assert_called_once_with(NEEDS_REVIEW_LABEL)
        pr.add_to_labels.assert_not_called()
        pr.edit.assert_not_called()
        pr.create_issue_comment.assert_not_called()

    # Approved: waiting on a merge.

    def test_approved_pr_is_left_alone(self):
        """An approved but unmerged PR is neither staled nor flagged."""
        pr = make_pr(updated_at=PAST_STALE, reviews=[_review("APPROVED", "bob", OLD_REVIEW)])
        run_check(pr)
        pr.add_to_labels.assert_not_called()
        pr.remove_from_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_approved_pr_clears_needs_review_label(self):
        """An approved PR still carrying needs-review has that label removed."""
        pr = make_pr(
            updated_at=PAST_STALE,
            labels=[NEEDS_REVIEW_LABEL],
            reviews=[_review("APPROVED", "bob", OLD_REVIEW)],
        )
        run_check(pr)
        pr.remove_from_labels.assert_called_once_with(NEEDS_REVIEW_LABEL)
        pr.add_to_labels.assert_not_called()
        pr.edit.assert_not_called()

    # Exclusions and gating.

    def test_skips_pr_with_no_bot_label(self):
        """A no-bot PR is skipped entirely, before any classification."""
        pr = make_pr(
            updated_at=PAST_STALE,
            labels=[NO_BOT_LABEL],
            reviews=[_review("CHANGES_REQUESTED", "bob", OLD_REVIEW)],
        )
        run_check(pr)
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()
        pr.get_reviews.assert_not_called()

    def test_skips_dependabot_pr(self):
        """A Dependabot PR is skipped entirely, before any classification."""
        pr = make_pr(
            updated_at=PAST_STALE,
            author="dependabot[bot]",
            reviews=[_review("CHANGES_REQUESTED", "bob", OLD_REVIEW)],
        )
        run_check(pr)
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()
        pr.get_reviews.assert_not_called()

    def test_skips_needs_rebase_labeled_pr(self):
        """A conflicted PR is owned by the needs-rebase check and skipped here entirely."""
        pr = make_pr(
            updated_at=PAST_STALE,
            labels=[NEEDS_REBASE_LABEL],
            reviews=[_review("CHANGES_REQUESTED", "bob", OLD_REVIEW)],
        )
        run_check(pr)
        pr.add_to_labels.assert_not_called()
        pr.remove_from_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()
        pr.get_reviews.assert_not_called()

    def test_skips_failing_tests_labeled_pr(self):
        """A failing-tests PR is owned by that check; lingering stale labels are lifted."""
        pr = make_pr(
            updated_at=PAST_STALE,
            labels=[FAILING_TESTS_LABEL, STALE_LABEL],
            reviews=[_review("CHANGES_REQUESTED", "bob", OLD_REVIEW)],
        )
        run_check(pr)
        removed = [c.args[0] for c in pr.remove_from_labels.call_args_list]
        self.assertEqual(removed, [STALE_LABEL])
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()
        pr.get_reviews.assert_not_called()

    def test_needs_rebase_skip_clears_lingering_bot_labels(self):
        """Skipping a conflicted PR lifts lingering stale/needs-review labels on the way out."""
        pr = make_pr(
            updated_at=PAST_STALE,
            labels=[NEEDS_REBASE_LABEL, STALE_LABEL, NEEDS_REVIEW_LABEL],
            reviews=[_review("CHANGES_REQUESTED", "bob", OLD_REVIEW)],
        )
        run_check(pr)
        removed = [c.args[0] for c in pr.remove_from_labels.call_args_list]
        self.assertEqual(sorted(removed), sorted([STALE_LABEL, NEEDS_REVIEW_LABEL]))
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()
        pr.get_reviews.assert_not_called()

    def test_active_pr_short_circuits_without_api_calls(self):
        """A recently active, unlabeled PR returns before any review/commit lookups."""
        pr = make_pr(updated_at=RECENT)
        run_check(pr)
        pr.get_reviews.assert_not_called()
        pr.get_commits.assert_not_called()
        pr.add_to_labels.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        pr.edit.assert_not_called()

    def test_weekend_only_gap_takes_no_action(self):
        """A Friday-to-Sunday gap is 0 business days, so nothing happens."""
        friday = datetime(2026, 3, 6, 17, 0, tzinfo=UTC)
        sunday = datetime(2026, 3, 8, 9, 0, tzinfo=UTC)
        pr = make_pr(
            updated_at=friday,
            reviews=[_review("CHANGES_REQUESTED", "bob", business_days_before(10, friday))],
        )
        run_check(pr, now=sunday)
        pr.get_reviews.assert_not_called()  # gated out by the business-day check
        pr.add_to_labels.assert_not_called()
        pr.edit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
